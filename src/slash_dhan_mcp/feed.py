from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import pyotp

from slashdhan.config import IST
from slashdhan.errors import error_message

from .config import Settings
from .crypto import decrypt, encrypt
from .db import Database, now_iso, parse_iso

log = logging.getLogger(__name__)

Reason = Literal["unset", "expired"]
Mode = Literal["manual", "totp"]
TOKEN_LIFETIME = timedelta(hours=24)
PROBE_TIMEOUT_S = 15.0
RENEW_MAX_FAILURES = 3
RENEW_BACKOFF = timedelta(minutes=5)


@dataclass(frozen=True)
class Creds:
    client_id: str
    access_token: str


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
        self._last_totp: tuple[str, str] | None = None

    def _row(self) -> dict[str, Any]:
        row = self._db.one("SELECT * FROM feed WHERE id = 1")
        assert row is not None
        return row

    def creds(self) -> Creds | Reason:
        row = self._row()
        if row["status"] == "active" and row["access_token_enc"]:
            return Creds(
                str(row["dhan_client_id"]),
                decrypt(self._settings.creds_encryption_key, str(row["access_token_enc"])),
            )
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
            "mode": row["mode"],
            "totpConfigured": bool(row["pin_enc"] and row["totp_secret_enc"]),
            "renewFailures": int(row["renew_failures"]),
            "lastRenewedAt": row["last_renewed_at"],
            "lastRenewAttemptAt": row["last_renew_attempt_at"],
        }

    def _headers(self, client_id: str, token: str) -> dict[str, str]:
        return {"Accept": "application/json", "access-token": token, "client-id": client_id}

    def set_token(self, client_id: str, token: str) -> dict[str, Any]:
        """Accept an operator-pushed token; switches the feed to manual mode (PIN + seed wiped)."""
        client_id, token = client_id.strip(), token.strip()
        if not client_id.isdigit() or len(token) < 20:
            raise FeedError("dhan_client_id must be numeric and access_token looks too short")
        profile = self._validate(client_id, token)
        self._store_token(client_id, token, profile)
        self._db.execute(
            "UPDATE feed SET mode = 'manual', pin_enc = NULL, totp_secret_enc = NULL, "
            "renew_failures = 0, renew_mail_sent_for = NULL WHERE id = 1"
        )
        log.info("feed token accepted for client %s (manual mode)", client_id)
        return self.status()

    def set_totp(self, client_id: str, pin: str, totp_secret: str) -> dict[str, Any]:
        """Store the login PIN + TOTP seed and mint the first token with them (totp mode)."""
        client_id, pin = client_id.strip(), pin.strip()
        totp_secret = totp_secret.strip().replace(" ", "").upper()
        if not client_id.isdigit():
            raise FeedError("dhan_client_id must be numeric")
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise FeedError("pin must be 4 to 8 digits")
        try:
            pyotp.TOTP(totp_secret).now()
        except Exception as e:
            raise FeedError("totp_secret is not a valid base32 TOTP seed") from e
        token = self._mint(client_id, pin, totp_secret)
        profile = self._validate(client_id, token)
        self._store_token(client_id, token, profile)
        key = self._settings.creds_encryption_key
        self._db.execute(
            "UPDATE feed SET mode = 'totp', pin_enc = ?, totp_secret_enc = ?, renew_failures = 0, "
            "last_renew_attempt_at = NULL, last_renewed_at = ?, renew_mail_sent_for = NULL "
            "WHERE id = 1",
            (encrypt(key, pin), encrypt(key, totp_secret), now_iso()),
        )
        log.info("feed switched to totp mode for client %s", client_id)
        return self.status()

    def renew_due(self, hours: int) -> bool:
        """True when totp mode should mint a new token now (expired, or < `hours` left)."""
        row = self._row()
        if row["mode"] != "totp" or not (row["pin_enc"] and row["totp_secret_enc"]):
            return False
        now = datetime.now(UTC)
        if int(row["renew_failures"]) > 0:
            last = parse_iso(row["last_renew_attempt_at"])
            if last is not None and now - last < RENEW_BACKOFF:
                return False
        if row["status"] != "active":
            return True
        expires = parse_iso(row["expires_at"])
        return expires is None or expires - now < timedelta(hours=hours)

    def renew(self) -> dict[str, Any]:
        """Mint and validate a fresh token from the stored PIN + seed (totp mode only)."""
        row = self._row()
        if row["mode"] != "totp" or not (row["pin_enc"] and row["totp_secret_enc"]):
            raise FeedError("the feed is not in totp mode", 409)
        key = self._settings.creds_encryption_key
        client_id = str(row["dhan_client_id"])
        self._db.execute("UPDATE feed SET last_renew_attempt_at = ? WHERE id = 1", (now_iso(),))
        try:
            token = self._mint(
                client_id, decrypt(key, row["pin_enc"]), decrypt(key, row["totp_secret_enc"])
            )
            profile = self._validate(client_id, token)
        except FeedError as e:
            self._db.execute(
                "UPDATE feed SET renew_failures = renew_failures + 1, last_error = ?, "
                "updated_at = ? WHERE id = 1",
                (f"auto-renew failed: {e}"[:500], now_iso()),
            )
            log.warning("feed auto-renew failed for client %s: %s", client_id, str(e)[:200])
            raise
        self._store_token(client_id, token, profile)
        self._db.execute(
            "UPDATE feed SET renew_failures = 0, last_renewed_at = ?, renew_mail_sent_for = NULL "
            "WHERE id = 1",
            (now_iso(),),
        )
        log.info("feed token auto-renewed for client %s", client_id)
        return self.status()

    def _mint(self, client_id: str, pin: str, totp_secret: str) -> str:
        """`POST auth.dhan.co/app/generateAccessToken` with the current TOTP code.

        Per Dhan's docs the three fields travel as query parameters, not a body.
        """
        totp = pyotp.TOTP(totp_secret)
        code = totp.now()
        if self._last_totp == (client_id, code):
            # Dhan accepts each code once: wait for the next 30 s window instead of failing.
            time.sleep(totp.interval - time.time() % totp.interval + 0.5)
            code = totp.now()
        params = {"dhanClientId": client_id, "pin": pin, "totp": code}
        try:
            r = self._client.post(
                f"{self._settings.dhan_auth_base_url}/app/generateAccessToken",
                params=params,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise FeedError(f"Dhan auth unreachable while generating a token: {e}", 502) from e
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if r.status_code >= 400 or not isinstance(payload, dict):
            detail = error_message(payload) or f"HTTP {r.status_code}"
            raise FeedError(f"Dhan refused to generate a token: {detail}")
        token = payload.get("accessToken") or payload.get("access_token")
        if not isinstance(token, str) or len(token) < 20:
            said = (
                error_message(payload)
                or json.dumps(
                    {k: v for k, v in payload.items() if "token" not in k.lower()}, default=str
                )[:300]
            )
            raise FeedError(f"Dhan returned no access token (check the PIN and TOTP seed): {said}")
        self._last_totp = (client_id, code)
        return token

    def _validate(self, client_id: str, token: str) -> dict[str, Any]:
        """Probe `/fundlimit` (must succeed) and `/profile` (best effort) with the token."""
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

    def _store_token(self, client_id: str, token: str, profile: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        expires = jwt_expiry(token) or (now + TOKEN_LIFETIME)
        self._db.execute(
            "UPDATE feed SET dhan_client_id = ?, access_token_enc = ?, status = 'active', "
            "issued_at = ?, expires_at = ?, token_validity = ?, data_plan = ?, data_validity = ?, "
            "last_ok_at = ?, last_error = NULL, expiry_mail_sent_for = NULL, "
            "warning_mail_sent_for = NULL, updated_at = ? WHERE id = 1",
            (
                client_id,
                encrypt(self._settings.creds_encryption_key, token),
                now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
                _str(profile.get("tokenValidity")),
                _str(profile.get("dataPlan")),
                _str(profile.get("dataValidity")),
                now.isoformat(timespec="seconds"),
                now_iso(),
            ),
        )
        log.info("feed token stored for client %s, expires %s", client_id, expires.isoformat())

    def clear(self) -> dict[str, Any]:
        self._db.execute(
            "UPDATE feed SET access_token_enc = NULL, status = 'unset', issued_at = NULL, "
            "expires_at = NULL, last_error = NULL, mode = 'manual', pin_enc = NULL, "
            "totp_secret_enc = NULL, renew_failures = 0, renew_mail_sent_for = NULL, "
            "updated_at = ? WHERE id = 1",
            (now_iso(),),
        )
        return self.status()

    def mark_expired(self, error: str) -> bool:
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
        if row["status"] != "active" or not row["expires_at"] or row["mode"] == "totp":
            return None
        expires = parse_iso(row["expires_at"])
        if expires is None or expires - datetime.now(UTC) > timedelta(hours=hours):
            return None
        if row["warning_mail_sent_for"] == row["issued_at"]:
            return None
        return row

    def renew_mail_due(self) -> dict[str, Any] | None:
        """The feed row once auto-renew has failed RENEW_MAX_FAILURES times for this token."""
        row = self._row()
        if row["mode"] != "totp" or int(row["renew_failures"]) < RENEW_MAX_FAILURES:
            return None
        marker = row["issued_at"] or row["updated_at"]
        if row["renew_mail_sent_for"] == marker:
            return None
        return row

    def record_mail(self, kind: Literal["expiry", "warning", "renew"], marker: str | None) -> None:
        column = {
            "expiry": "expiry_mail_sent_for",
            "warning": "warning_mail_sent_for",
            "renew": "renew_mail_sent_for",
        }[kind]
        self._db.execute(f"UPDATE feed SET {column} = ? WHERE id = 1", (marker,))


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _ist(value: Any) -> str | None:
    parsed = parse_iso(value)
    return parsed.astimezone(IST).strftime("%d %b %Y, %H:%M IST") if parsed else None
