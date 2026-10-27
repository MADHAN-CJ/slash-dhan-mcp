from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from slashdhan.markets import HOURS, hours_for, is_open

from .config import Settings
from .db import Database, Row, now_iso, parse_iso
from .emails import feed_expired, feed_expiring, watcher_alert
from .feed import Creds, Feed
from .mail import Mailer
from .pages import LOGOMARK_PATHS, ist_now_text
from .runner import Runner

log = logging.getLogger(__name__)

INTERVAL_MIN, INTERVAL_MAX = 1, 120
EXPIRY_MIN_D, EXPIRY_MAX_D = 1, 30
ERROR_ESCALATION = 5
ACTIVE = ("ARMED", "PAUSED", "ERROR")
FINAL = ("EXPIRED", "CANCELLED")
MARKETS = tuple(HOURS)
SCRIPT_GAP_S = 1.2

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MCX_RE = re.compile(
    r"\bMCX(_COMM)?\b|\b(GOLD|SILVER|CRUDEOIL|NATURALGAS|COPPER|ZINC|ALUMINIUM|NICKEL|LEAD|"
    r"MCXBULLDEX|MCXMETLDEX|MCXENRGDEX)\b"
)

FEED_REASONS = {
    "unset": ("The /markets Dhan feed has no token yet; the operator sets it up. Try again later."),
    "expired": (
        "The /markets Dhan feed token expired and is being refreshed by the operator. "
        "Watchers resume by themselves once it is back."
    ),
}


def infer_market(script: str, market: str | None) -> str:
    if market and market.strip().lower() != "auto":
        m = market.strip().upper()
        if m == "BSE":
            m = "NSE"
        if m not in MARKETS:
            raise ValueError(f"market must be one of {', '.join(MARKETS)}, BSE or auto")
        return m
    return "MCX" if _MCX_RE.search(script) else "NSE"


def hours_text(market: str) -> str:
    start, end = hours_for(market)
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} IST, Mon-Fri"


def parse_result(output: str) -> dict[str, Any]:
    """The last non-empty printed line must be {"met": bool, "value"?: ..., "detail"?: ...}."""
    lines = [ln for ln in (output or "").strip().splitlines() if ln.strip()]
    if not lines:
        return {
            "ok": False,
            "error": "script printed nothing; it must end by printing the JSON result",
        }
    last = lines[-1].strip()
    try:
        data = json.loads(last)
    except ValueError:
        return {"ok": False, "error": f"last printed line is not JSON: {last[:200]}"}
    if not isinstance(data, dict) or not isinstance(data.get("met"), bool):
        return {
            "ok": False,
            "error": f'JSON result must be an object with a boolean "met": {last[:200]}',
        }
    value = data.get("value")
    if value is not None and not isinstance(value, int | float | str):
        value = str(value)
    detail = data.get("detail")
    return {
        "ok": True,
        "met": data["met"],
        "value": value,
        "detail": None if detail is None else str(detail),
    }


def apply_check(baseline_done: bool, latched: bool, met: bool) -> tuple[bool, bool]:
    """Edge semantics → (fired, latched). The first check only records a baseline."""
    if not baseline_done:
        return False, met
    if not met:
        return False, False
    if latched:
        return False, True
    return True, True


def _now() -> datetime:
    return datetime.now(UTC)


class Watchers:
    def __init__(
        self, settings: Settings, db: Database, feed: Feed, runner: Runner, mailer: Mailer
    ) -> None:
        self._settings = settings
        self._db = db
        self._feed = feed
        self._runner = runner
        self._mailer = mailer

    @property
    def page_url(self) -> str:
        return self._settings.watchers_url

    def _update(self, wid: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._db.execute(
            f"UPDATE watchers SET {cols}, updated_at = ? WHERE id = ?",
            (*fields.values(), now_iso(), wid),
        )

    def create(
        self,
        symbol: str,
        label: str,
        script: str,
        notify_email: str,
        check_every_minutes: int = 1,
        expires_in_days: int = 30,
        market: str = "auto",
    ) -> dict[str, Any] | str:
        symbol = symbol.strip().upper()[:40]
        label = " ".join(label.split())[:140]
        email = notify_email.strip().lower()
        if not symbol or not label or not script.strip():
            return "symbol, label and script are all required."
        if not _EMAIL_RE.match(email):
            return "notify_email must be a valid email address; ask the user which address should receive alerts."
        if not INTERVAL_MIN <= check_every_minutes <= INTERVAL_MAX:
            return f"check_every_minutes must be between {INTERVAL_MIN} and {INTERVAL_MAX}."
        if not EXPIRY_MIN_D <= expires_in_days <= EXPIRY_MAX_D:
            return f"expires_in_days must be between {EXPIRY_MIN_D} and {EXPIRY_MAX_D}."
        try:
            mkt = infer_market(script, market)
        except ValueError as e:
            return str(e)
        creds = self._feed.creds()
        if not isinstance(creds, Creds):
            return FEED_REASONS[creds]
        armed = self._db.one("SELECT count(*) AS n FROM watchers WHERE status = 'ARMED'")
        limit = self._settings.watchers_max_armed
        if armed and int(armed["n"]) >= limit:
            return f"Watcher limit reached ({limit} armed on this host). Cancel one on the watchers page first: {self.page_url}"
        result = self._runner.run(script)
        res = parse_result(result.stdout)
        if not res["ok"]:
            return (
                "Watcher NOT created: the dry run violated the result contract "
                f"({res['error']}). Fix the script and call create_watcher again. Output:\n\n"
                f"{result.text()[:2000]}"
            )
        wid = secrets.token_hex(8)
        now = _now()
        expires = now + timedelta(days=expires_in_days)
        self._db.execute(
            "INSERT INTO watchers (id, notify_email, symbol, label, script, market, check_every_s, "
            "status, baseline_done, latched, last_met, last_value, last_detail, last_checked_at, "
            "expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'ARMED', 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wid,
                email,
                symbol,
                label,
                script,
                mkt,
                check_every_minutes * 60,
                int(res["met"]),
                int(res["met"]),
                None if res["value"] is None else str(res["value"]),
                res["detail"],
                now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
            ),
        )
        note = "Show the user the pageUrl; that is where anyone can pause, resume or cancel it."
        if res["met"]:
            note += " The condition is ALREADY met, so it must reset before the first alert."
        return {
            "id": wid,
            "status": "ARMED",
            "symbol": symbol,
            "label": label,
            "notifyEmail": email,
            "checkEveryMinutes": check_every_minutes,
            "market": mkt,
            "hours": f"{mkt} {hours_text(mkt)}",
            "expiresAt": expires.date().isoformat(),
            "dryRun": {"met": res["met"], "value": res["value"], "detail": res["detail"]},
            "alreadyMet": res["met"],
            "pageUrl": self.page_url,
            "note": note,
        }

    def rows(self) -> list[Row]:
        return self._db.query("SELECT * FROM watchers ORDER BY created_at DESC")

    def row(self, wid: str) -> Row | None:
        return self._db.one("SELECT * FROM watchers WHERE id = ?", (wid,))

    def fires_for(self, wid: str, limit: int = 3) -> list[Row]:
        return self._db.query(
            "SELECT fired_at, value, detail FROM watcher_fires WHERE watcher_id = ? "
            "ORDER BY fired_at DESC LIMIT ?",
            (wid, limit),
        )

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "symbol": r["symbol"],
                "label": r["label"],
                "notifyEmail": r["notify_email"],
                "status": r["status"],
                "statusReason": r["status_reason"],
                "pausedBy": r["paused_by"],
                "market": r["market"],
                "checkEveryMinutes": int(r["check_every_s"]) // 60,
                "lastCheckedAt": r["last_checked_at"],
                "lastValue": r["last_value"],
                "lastDetail": r["last_detail"],
                "lastError": r["last_error"],
                "alertCount": r["fired_count"],
                "lastAlertAt": r["last_fired_at"],
                "expiresAt": r["expires_at"],
                "recentAlerts": [
                    {"at": f["fired_at"], "value": f["value"], "detail": f["detail"]}
                    for f in self.fires_for(str(r["id"]))
                ],
            }
            for r in self.rows()
        ]

    def page_action(self, wid: str, action: str) -> str | None:
        """Pause, resume or cancel from the page; returns an error message or None."""
        row = self.row(wid)
        if row is None:
            return "not found"
        status = str(row["status"])
        if action == "pause" and status == "ARMED":
            self._update(wid, status="PAUSED", paused_by="user", status_reason=None)
        elif action == "resume" and status in ("PAUSED", "ERROR"):
            if not isinstance(self._feed.creds(), Creds):
                return "The Dhan feed is not available right now; resume once it is back."
            self._update(
                wid,
                status="ARMED",
                paused_by=None,
                status_reason=None,
                error_count=0,
                last_error=None,
            )
        elif action == "cancel" and status in ACTIVE:
            self._update(wid, status="CANCELLED", paused_by=None)
        else:
            return f"cannot {action} a {status} watcher"
        return None

    def pause_for_feed(self, reason: str) -> int:
        return self._db.execute(
            "UPDATE watchers SET status = 'PAUSED', paused_by = 'feed', status_reason = ?, "
            "updated_at = ? WHERE status = 'ARMED'",
            (reason, now_iso()),
        )

    def resume_after_feed(self) -> int:
        return self._db.execute(
            "UPDATE watchers SET status = 'ARMED', paused_by = NULL, status_reason = NULL, "
            "error_count = 0, updated_at = ? WHERE status = 'PAUSED' AND paused_by = 'feed' "
            "AND expires_at > ?",
            (now_iso(), now_iso()),
        )

    def sweep(self, force: bool = False, min_gap_minutes: int | None = None) -> dict[str, Any]:
        """One sweeper tick: operator mails, unsent-alert retries, expiry, due checks."""
        open_markets = {m for m in MARKETS if is_open(m)}
        report: dict[str, Any] = {
            "ranAt": _now().isoformat(timespec="seconds"),
            "marketsOpen": sorted(open_markets),
            "expired": 0,
            "checked": 0,
            "fired": 0,
            "deferred": 0,
            "errored": 0,
            "pausedByFeed": 0,
            "operatorMails": 0,
            "emailRetries": 0,
            "notes": [],
        }
        self._notify_operators(report)
        self._retry_unsent(report)
        now = now_iso()
        report["expired"] = self._db.execute(
            "UPDATE watchers SET status = 'EXPIRED', paused_by = NULL, updated_at = ? "
            "WHERE status IN ('ARMED', 'PAUSED') AND expires_at < ?",
            (now, now),
        )
        due = [
            r
            for r in self._db.query(
                "SELECT * FROM watchers WHERE status = 'ARMED' ORDER BY created_at"
            )
            if _is_due(r) and (force or r["market"] in open_markets)
        ]
        gap = min_gap_minutes if force and min_gap_minutes is not None else None
        gap_s = (gap if gap is not None else self._settings.watchers_min_gap_minutes) * 60
        try:
            self._check(due, gap_s, report)
        except Exception as e:
            log.exception("sweep failed")
            report["notes"].append(str(e))
        return report

    def _notify_operators(self, report: dict[str, Any]) -> None:
        recipients = self._settings.operator_emails
        if not recipients:
            return
        expired = self._feed.expiry_mail_due()
        if expired is not None:
            mail = feed_expired(
                expired["dhan_client_id"],
                ist_now_text(parse_iso(expired["updated_at"]) or _now()),
                str(expired["last_error"] or "token rejected"),
                LOGOMARK_PATHS,
            )
            if all(self._mailer.send(to, mail) for to in recipients):
                self._feed.record_mail("expiry", expired["issued_at"] or expired["updated_at"])
                report["operatorMails"] += 1
        warning = self._feed.warning_mail_due(self._settings.feed_warning_hours)
        if warning is not None:
            expires = parse_iso(warning["expires_at"]) or _now()
            mail = feed_expiring(warning["dhan_client_id"], ist_now_text(expires), LOGOMARK_PATHS)
            if all(self._mailer.send(to, mail) for to in recipients):
                self._feed.record_mail("warning", warning["issued_at"])
                report["operatorMails"] += 1

    def _retry_unsent(self, report: dict[str, Any]) -> None:
        unsent = self._db.query(
            "SELECT f.id, f.value, f.detail, w.id AS wid FROM watcher_fires f "
            "JOIN watchers w ON w.id = f.watcher_id WHERE f.emailed = 0 ORDER BY f.fired_at LIMIT 20"
        )
        for f in unsent:
            row = self.row(str(f["wid"]))
            if row and self._mail_fire(row, f["value"], f["detail"]):
                self._db.execute("UPDATE watcher_fires SET emailed = 1 WHERE id = ?", (f["id"],))
                report["emailRetries"] += 1

    def _mail_fire(self, row: Row, value: Any, detail: str | None) -> bool:
        mail = watcher_alert(
            str(row["symbol"]),
            str(row["label"]),
            None if value is None else str(value),
            detail,
            ist_now_text(_now()),
            self.page_url,
            LOGOMARK_PATHS,
        )
        return self._mailer.send(str(row["notify_email"]), mail)

    def _check(self, rows: list[Row], gap_s: int, report: dict[str, Any]) -> None:
        if not rows:
            return
        creds = self._feed.creds()
        if not isinstance(creds, Creds):
            report["pausedByFeed"] += self.pause_for_feed(FEED_REASONS[creds])
            return
        first = True
        for row in rows:
            last_fired = parse_iso(row["last_fired_at"])
            if last_fired and (_now() - last_fired).total_seconds() < gap_s:
                report["deferred"] += 1
                continue
            if not first:
                time.sleep(SCRIPT_GAP_S)
            first = False
            result = self._runner.run(str(row["script"]))
            feed_now = self._feed.creds()
            if not isinstance(feed_now, Creds):
                report["pausedByFeed"] += self.pause_for_feed(FEED_REASONS[feed_now])
                return
            report["checked"] += 1
            res = parse_result(result.stdout)
            now = now_iso()
            if not res["ok"]:
                error = f"{res['error']}; {result.stderr.strip()[-300:]}".strip("; ")
                count = int(row["error_count"]) + 1
                if count >= ERROR_ESCALATION:
                    self._update(
                        str(row["id"]),
                        status="ERROR",
                        status_reason=f"Check failed {count} times in a row. Last error: {error}",
                        last_error=error,
                        error_count=count,
                        last_checked_at=now,
                    )
                    report["errored"] += 1
                else:
                    self._update(
                        str(row["id"]), last_error=error, error_count=count, last_checked_at=now
                    )
                continue
            fired, latched = apply_check(
                bool(row["baseline_done"]), bool(row["latched"]), res["met"]
            )
            fields: dict[str, Any] = {
                "baseline_done": 1,
                "latched": int(latched),
                "last_met": int(res["met"]),
                "last_value": None if res["value"] is None else str(res["value"]),
                "last_detail": res["detail"],
                "last_error": None,
                "error_count": 0,
                "last_checked_at": now,
            }
            if fired:
                fire_id = secrets.token_hex(12)
                self._db.execute(
                    "INSERT INTO watcher_fires (id, watcher_id, fired_at, value, detail) VALUES (?, ?, ?, ?, ?)",
                    (fire_id, row["id"], now, fields["last_value"], res["detail"]),
                )
                if self._mail_fire(row, res["value"], res["detail"]):
                    self._db.execute(
                        "UPDATE watcher_fires SET emailed = 1 WHERE id = ?", (fire_id,)
                    )
                fields["last_fired_at"] = now
                fields["fired_count"] = int(row["fired_count"]) + 1
                report["fired"] += 1
            self._update(str(row["id"]), **fields)


def _is_due(row: Row) -> bool:
    last = parse_iso(row["last_checked_at"])
    if last is None:
        return True
    return (_now() - last).total_seconds() >= int(row["check_every_s"])
