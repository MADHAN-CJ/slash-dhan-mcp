from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from .config import IST, now_ist

HOURS: dict[str, tuple[time, time]] = {
    "NSE": (time(9, 15), time(15, 30)),
    "MCX": (time(9, 0), time(23, 30)),
}
COVERS = {
    "NSE": "NSE/BSE cash, F&O and indices (NSE_EQ, BSE_EQ, IDX_I, NSE_FNO, BSE_FNO)",
    "MCX": "MCX commodities and MCX indices (MCX_COMM); till 23:55 in US winter months",
}


def market_for(exchange_segment: str) -> str:
    """The market whose hours apply to a Dhan exchange segment: MCX for MCX_COMM, else NSE."""
    return "MCX" if exchange_segment.strip().upper() == "MCX_COMM" else "NSE"


def hours_for(market: str) -> tuple[time, time]:
    return HOURS.get(market.strip().upper(), HOURS["NSE"])


def is_open(market: str, at: datetime | None = None) -> bool:
    now = (at or now_ist()).astimezone(IST)
    if now.weekday() >= 5:
        return False
    start, end = hours_for(market)
    return start <= now.time() <= end


def _parse_at(at: str) -> datetime:
    text = at.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    raise ValueError("at must look like 'YYYY-MM-DD HH:MM' (IST)")


def market_status(at: str | None = None) -> dict[str, Any]:
    """Open/closed for NSE (09:15-15:30) and MCX (09:00-23:30) now or at an IST time.

    Mon-Fri only; exchange holidays are not modelled. Returns {now_ist, weekday,
    markets: {NSE: {open, hours, covers}, MCX: {...}}, open}.
    """
    now = (_parse_at(at) if at else now_ist()).astimezone(IST)
    markets = {
        m: {
            "open": is_open(m, now),
            "hours": f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')} IST Mon-Fri",
            "covers": COVERS[m],
        }
        for m, (s, e) in HOURS.items()
    }
    return {
        "now_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": now.strftime("%A"),
        "markets": markets,
        "open": any(m["open"] for m in markets.values()),
        "holiday_aware": False,
    }


def today() -> str:
    """Today's date in IST as YYYY-MM-DD."""
    return now_ist().date().isoformat()


def days_ago(n: int) -> str:
    """The date n days before today (IST) as YYYY-MM-DD."""
    return (now_ist().date() - timedelta(days=n)).isoformat()


def ymd(d: date | datetime) -> str:
    """Format a date or datetime as YYYY-MM-DD."""
    return d.strftime("%Y-%m-%d")


def to_ist(epoch_seconds: float) -> datetime:
    """Convert a Dhan epoch timestamp (seconds) into an aware IST datetime."""
    return datetime.fromtimestamp(float(epoch_seconds), IST)
