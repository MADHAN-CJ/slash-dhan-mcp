from __future__ import annotations

import base64
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
        }

    def _headers(self, client_id: str, token: str) -> dict[str, str]:
        return {"Accept": "application/json", "access-token": token, "client-id": client_id}

    def set_token(self, client_id: str, token: str) -> dict[str, Any]:
        client_id, token = client_id.strip(), token.strip()
        if not client_id.isdigit() or len(token) < 20:
            raise FeedError("dhan_client_id must be numeric and access_token looks too short")
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
        log.info("feed token accepted for client %s, expires %s", client_id, expires.isoformat())
        return self.status()

    def clear(self) -> dict[str, Any]:
        self._db.execute(
            "UPDATE feed SET access_token_enc = NULL, status = 'unset', issued_at = NULL, "
            "expires_at = NULL, last_error = NULL, updated_at = ? WHERE id = 1",
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
