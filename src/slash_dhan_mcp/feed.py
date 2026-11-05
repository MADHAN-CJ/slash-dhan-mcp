from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from slashdhan.config import IST
from slashdhan.errors import error_message

from .config import Settings
from .crypto import decrypt, encrypt
from .db import Database, now_iso, parse_iso

log = logging.getLogger(__name__)

Reason = Literal["unset", "expired"]
TOKEN_LIFETIME = timedelta(hours=24)
PROBE_TIMEOUT_S = 15.0
RENEW_BACKOFF = timedelta(minutes=5)
RENEW_CLAIM_WINDOW = timedelta(minutes=4)


@dataclass(frozen=True)
class Creds:
    client_id: str
    access_token: str
    fingerprint: str


def fingerprint(token: str) -> str:
    """Short, non-reversible id of a token: lets a 401 name the token it was seen with."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class FeedError(RuntimeError):
    """A token the operator pushed was rejected; the message is safe to return to them."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def jwt_expiry(token: str) -> datetime | None:
    """`exp` of a JWT payload without verifying it (Dhan tokens are JWTs); None if absent."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        exp = json.loads(raw).get("exp")
        return datetime.fromtimestamp(int(exp), UTC) if exp else None
    except (ValueError, TypeError, AttributeError):
        return None


class Feed:
    """The single operator-owned Dhan session every user shares."""

    def __init__(
        self, settings: Settings, db: Database, client: httpx.Client | None = None
    ) -> None:
        self._settings = settings
        self._db = db
        self._client = client or httpx.Client(timeout=PROBE_TIMEOUT_S)

    def _row(self) -> dict[str, Any]:
        row = self._db.one("SELECT * FROM feed WHERE id = 1")
        assert row is not None
        return row

    def creds(self) -> Creds | Reason:
        row = self._row()
        if row["status"] == "active" and row["access_token_enc"]:
            token = decrypt(self._settings.creds_encryption_key, str(row["access_token_enc"]))
            return Creds(str(row["dhan_client_id"]), token, fingerprint(token))
        return "expired" if row["status"] == "expired" else "unset"

    def status(self) -> dict[str, Any]:
        row = self._row()
        return {
            "status": row["status"],
            "dhanClientId": row["dhan_client_id"],
            "issuedAt": row["issued_at"],
            "expiresAt": row["expires_at"],
            "expiresAtIst": _ist(row["expires_at"]),
            "tokenValidity": row["token_validity"],
            "dataPlan": row["data_plan"],
            "dataValidity": row["data_validity"],
            "lastOkAt": row["last_ok_at"],
            "lastError": row["last_error"],
            "updatedAt": row["updated_at"],
            "renewAfter": row["renew_after"],
            "renewAfterIst": _ist(row["renew_after"]),
            "lastRenewedAt": row["last_renewed_at"],
            "lastRenewAttemptAt": row["last_renew_attempt_at"],
            "renewFailures": int(row["renew_failures"]),
            "lastRenewError": row["last_renew_error"],
        }

    def _headers(self, client_id: str, token: str) -> dict[str, str]:
        return {"Accept": "application/json", "access-token": token, "client-id": client_id}

    def set_token(self, client_id: str, token: str) -> dict[str, Any]:
        """Accept an operator-pushed token after validating it; the renewal chain starts here."""
        client_id, token = client_id.strip(), token.strip()
        if not client_id.isdigit() or len(token) < 20:
            raise FeedError("dhan_client_id must be numeric and access_token looks too short")
        profile = self._validate(client_id, token)
        self._store_token(client_id, token, profile)
        log.info("feed token accepted for client %s", client_id)
        return self.status()

    def renew_due(self) -> bool:
        """Whether the sweep should renew now: a live token past `renew_after` (or without
        one: rows from before renewal existed), not claimed in the last 4 min and not inside
        the 5 min backoff that follows a failed attempt."""
        row = self._row()
        if row["status"] != "active" or not row["access_token_enc"]:
            return False
        now = datetime.now(UTC)
        attempted = parse_iso(row["last_renew_attempt_at"])
        if attempted is not None:
            gap = RENEW_BACKOFF if int(row["renew_failures"]) > 0 else RENEW_CLAIM_WINDOW
            if now - attempted < gap:
                return False
        after = parse_iso(row["renew_after"])
        return after is None or after <= now

    def renew(self) -> dict[str, Any]:
        """Swap the live token for a fresh 24 h one through `GET /v2/RenewToken`.

        Dhan expires the old token the moment the new one is issued, so the new one is
        stored before anything else happens. A refusal (4xx) means the chain is broken:
        the feed is marked expired and the usual operator mail follows. Network trouble or
        a 5xx counts as a failed attempt and is retried by a later sweep.
        """
        now = datetime.now(UTC)
        claimed = self._db.execute(
            "UPDATE feed SET last_renew_attempt_at = ? WHERE id = 1 AND status = 'active' "
            "AND access_token_enc IS NOT NULL "
            "AND (last_renew_attempt_at IS NULL OR last_renew_attempt_at < ?)",
            (
                now.isoformat(timespec="seconds"),
                (now - RENEW_CLAIM_WINDOW).isoformat(timespec="seconds"),
            ),
        )
        if not claimed:
            row = self._row()
            if row["status"] != "active" or not row["access_token_enc"]:
                raise FeedError("nothing to renew: the feed has no live token", 409)
            raise FeedError("a renewal attempt is already in progress", 409)
        row = self._row()
        client_id = str(row["dhan_client_id"])
        token = decrypt(self._settings.creds_encryption_key, str(row["access_token_enc"]))
        headers = {"access-token": token, "dhanClientId": client_id, "Accept": "application/json"}
        try:
            r = self._client.get(f"{self._settings.dhan_base_url}/RenewToken", headers=headers)
        except httpx.HTTPError as e:
            raise self._renew_failed(f"Dhan unreachable: {e}", 502) from e
        try:
            payload = r.json()
        except ValueError:
            payload = None
        detail = error_message(payload) or f"HTTP {r.status_code}"
        if 400 <= r.status_code < 500:
            error = f"RenewToken refused: {detail}"
            self.mark_expired(error, fingerprint(token))
            log.warning("feed renewal chain broken for client %s: %s", client_id, detail[:200])
            raise FeedError(error)
        if r.status_code >= 400 or not isinstance(payload, dict):
            raise self._renew_failed(f"RenewToken failed: {detail}", 502)
        new = payload.get("token") or payload.get("accessToken")
        if not isinstance(new, str) or len(new) < 20:
            raise self._renew_failed("RenewToken answered without a token", 502)
        try:
            profile = self._validate(client_id, new)
        except FeedError as e:
            raise self._renew_failed(f"renewed token failed validation: {e}", e.status) from e
        self._store_token(client_id, new, profile, renewed=True)
        log.info(
            "feed token renewed for client %s, expires %s", client_id, self.status()["expiresAt"]
        )
        return self.status()

    def _renew_failed(self, error: str, status: int) -> FeedError:
        self._db.execute(
            "UPDATE feed SET renew_failures = renew_failures + 1, last_renew_error = ? "
            "WHERE id = 1",
            (error[:500],),
        )
        log.warning("feed renewal attempt failed: %s", error[:200])
        return FeedError(error, status)

    def _validate(self, client_id: str, token: str) -> dict[str, Any]:
        """`/fundlimit` must accept the token; `/profile` adds plan details when it answers."""
        headers = self._headers(client_id, token)
        try:
            r = self._client.get(f"{self._settings.dhan_base_url}/fundlimit", headers=headers)
        except httpx.HTTPError as e:
            raise FeedError(f"Dhan unreachable while validating the token: {e}", 502) from e
        if r.status_code >= 400:
            try:
                payload = r.json()
            except ValueError:
                payload = None
            detail = error_message(payload) or f"HTTP {r.status_code}"
            raise FeedError(f"Dhan rejected the token: {detail}")
        profile: dict[str, Any] = {}
        try:
            p = self._client.get(f"{self._settings.dhan_base_url}/profile", headers=headers)
            if p.status_code < 400 and isinstance(p.json(), dict):
                profile = p.json()
        except (httpx.HTTPError, ValueError):
            profile = {}
        return profile

    def _store_token(
        self, client_id: str, token: str, profile: dict[str, Any], renewed: bool = False
    ) -> None:
        now = datetime.now(UTC)
        expires = jwt_expiry(token) or (now + TOKEN_LIFETIME)
        renew_after = now + timedelta(hours=self._settings.feed_renew_after_hours)
        stamp = now.isoformat(timespec="seconds")
        self._db.execute(
            "UPDATE feed SET dhan_client_id = ?, access_token_enc = ?, status = 'active', "
            "issued_at = ?, expires_at = ?, token_validity = ?, data_plan = ?, data_validity = ?, "
            "last_ok_at = ?, last_error = NULL, expiry_mail_sent_for = NULL, "
            "warning_mail_sent_for = NULL, renew_after = ?, last_renew_attempt_at = NULL, "
            "last_renewed_at = CASE WHEN ? THEN ? ELSE NULL END, renew_failures = 0, "
            "last_renew_error = NULL, updated_at = ? WHERE id = 1",
            (
                client_id,
                encrypt(self._settings.creds_encryption_key, token),
                stamp,
                expires.isoformat(timespec="seconds"),
                _str(profile.get("tokenValidity")),
                _str(profile.get("dataPlan")),
                _str(profile.get("dataValidity")),
                stamp,
                renew_after.isoformat(timespec="seconds"),
                1 if renewed else 0,
                stamp,
                now_iso(),
            ),
        )

    def clear(self) -> dict[str, Any]:
        self._db.execute(
            "UPDATE feed SET access_token_enc = NULL, status = 'unset', issued_at = NULL, "
            "expires_at = NULL, last_error = NULL, renew_after = NULL, "
            "last_renew_attempt_at = NULL, last_renewed_at = NULL, renew_failures = 0, "
            "last_renew_error = NULL, updated_at = ? WHERE id = 1",
            (now_iso(),),
        )
        return self.status()

    def mark_expired(self, error: str, seen_fingerprint: str | None = None) -> bool:
        """Mark the feed dead. With `seen_fingerprint`, only if the stored token is still the
        one that failed: a 401 earned by a token that has since been renewed is stale and
        must not take the fresh token down."""
        if seen_fingerprint is not None:
            row = self._row()
            stored = row["access_token_enc"]
            current = (
                fingerprint(decrypt(self._settings.creds_encryption_key, str(stored)))
                if stored
                else None
            )
            if current != seen_fingerprint:
                log.info("ignoring a 401 from a token that is no longer the feed's")
                return False
        changed = self._db.execute(
            "UPDATE feed SET status = 'expired', access_token_enc = NULL, last_error = ?, "
            "updated_at = ? WHERE id = 1 AND status = 'active'",
            (error[:500], now_iso()),
        )
        if changed:
            log.warning("feed token marked expired: %s", error[:200])
        return changed > 0

    def record_ok(self) -> None:
        self._db.execute(
            "UPDATE feed SET last_ok_at = ? WHERE id = 1 AND status = 'active'", (now_iso(),)
        )

    def expiry_mail_due(self) -> dict[str, Any] | None:
        """The expired feed row when the operator has not been told about THIS token yet."""
        row = self._row()
        if row["status"] != "expired":
            return None
        marker = row["issued_at"] or row["updated_at"]
        if row["expiry_mail_sent_for"] == marker:
            return None
        return row

    def warning_mail_due(self, hours: int) -> dict[str, Any] | None:
        row = self._row()
        if row["status"] != "active" or not row["expires_at"]:
            return None
        expires = parse_iso(row["expires_at"])
        if expires is None or expires - datetime.now(UTC) > timedelta(hours=hours):
            return None
        if row["warning_mail_sent_for"] == row["issued_at"]:
            return None
        return row

    def record_mail(self, kind: Literal["expiry", "warning"], marker: str | None) -> None:
        column = "expiry_mail_sent_for" if kind == "expiry" else "warning_mail_sent_for"
        self._db.execute(f"UPDATE feed SET {column} = ? WHERE id = 1", (marker,))


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _ist(value: Any) -> str | None:
    parsed = parse_iso(value)
    return parsed.astimezone(IST).strftime("%d %b %Y, %H:%M IST") if parsed else None
