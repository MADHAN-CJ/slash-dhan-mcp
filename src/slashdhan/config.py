from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30), "IST")

DEFAULT_BASE_URL = "https://api.dhan.co/v2"
DEFAULT_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DEFAULT_CALL_BUDGET = 40
DEFAULT_TIMEOUT_S = 15.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_url: str
    client_id: str
    access_token: str
    gateway_url: str
    run_token: str
    cache_dir: Path
    scrip_master_url: str
    call_budget: int
    timeout_s: float

    @property
    def uses_gateway(self) -> bool:
        return bool(self.gateway_url)


def settings() -> Settings:
    return Settings(
        base_url=os.environ.get("DHAN_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        client_id=os.environ.get("DHAN_CLIENT_ID", "").strip(),
        access_token=os.environ.get("DHAN_ACCESS_TOKEN", "").strip(),
        gateway_url=os.environ.get("SLASHDHAN_GATEWAY_URL", "").strip().rstrip("/"),
        run_token=os.environ.get("SLASHDHAN_RUN_TOKEN", "").strip(),
        cache_dir=Path(os.environ.get("SLASHDHAN_CACHE_DIR") or "~/.slashdhan").expanduser(),
        scrip_master_url=os.environ.get("SLASHDHAN_SCRIP_MASTER_URL", "").strip()
        or DEFAULT_SCRIP_MASTER_URL,
        call_budget=_env_int("SLASHDHAN_CALL_BUDGET", DEFAULT_CALL_BUDGET),
        timeout_s=_env_float("SLASHDHAN_TIMEOUT_S", DEFAULT_TIMEOUT_S),
    )


def now_ist() -> datetime:
    fixed = os.environ.get("SLASHDHAN_NOW", "").strip()
    if fixed:
        parsed = datetime.fromisoformat(fixed)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    fixed_day = os.environ.get("SLASHDHAN_TODAY", "").strip()
    if fixed_day:
        return datetime.combine(date.fromisoformat(fixed_day), datetime.min.time(), IST).replace(
            hour=12
        )
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()
