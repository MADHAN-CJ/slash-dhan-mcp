from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

Row = dict[str, Any]
Params = Sequence[Any]

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def now_iso() -> str:
    """UTC timestamps are stored as ISO-8601 text so they compare lexicographically in SQL."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


class Database:
    """One SQLite connection in WAL mode, serialised by a lock (the server is a single process)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def query(self, sql: str, params: Params = ()) -> list[Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: Params = ()) -> Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Params = ()) -> int:
        with self._lock:
            return int(self._conn.execute(sql, tuple(params)).rowcount)

    def migrate(self) -> list[str]:
        """Apply packaged SQL migrations that are not yet recorded; returns the applied names."""
        applied: list[str] = []
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            done = {r["name"] for r in self._conn.execute("SELECT name FROM schema_migrations")}
            for name, sql in _migrations():
                if name in done:
                    continue
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (name, now_iso()),
                )
                applied.append(name)
        return applied


def _migrations() -> list[tuple[str, str]]:
    folder = resources.files("slash_dhan_mcp").joinpath("migrations")
    found = [(p.name, p.read_text()) for p in folder.iterdir() if _MIGRATION_NAME.match(p.name)]
    return sorted(found)
