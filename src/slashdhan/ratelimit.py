from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DHAN_429_HOLD_S = 5.0
CHAIN_GAP_S = 3.0
_EPS = 1e-9
_MIN_WAIT_S = 0.001
_MAX_CHAIN_KEYS = 500

LIMITS: dict[str, tuple[int, float]] = {
    "quote": (1, 1 / 1.2),
    "data": (4, 4.0),
    "general": (10, 10.0),
}


def kind_for(path: str) -> str:
    p = path.lstrip("/")
    if p.startswith("marketfeed"):
        return "quote"
    if p.startswith("optionchain"):
        return "chain"
    if p.startswith("charts"):
        return "data"
    return "general"


def chain_key(body: Any) -> str:
    """One Dhan option-chain throttle key per (underlying, segment, expiry)."""
    if not isinstance(body, dict):
        return ""
    seg, scrip, expiry = (
        body.get("UnderlyingSeg", ""),
        body.get("UnderlyingScrip", ""),
        body.get("Expiry", ""),
    )
    return f"{seg}:{scrip}:{expiry}"


@dataclass
class _Bucket:
    capacity: int
    refill_per_s: float
    tokens: float
    last: float


class RateLimiter:
    """Token buckets per Dhan endpoint class plus the per-key option-chain gap.

    Dhan applies its limits per access token and the feed has exactly one, so a single
    limiter instance must front every caller of the process. A 429 parks every bucket.
    """

    def __init__(
        self,
        limits: dict[str, tuple[int, float]] | None = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._hold_until = 0.0
        self._chain_last: dict[str, float] = {}
        src = limits or LIMITS
        t = now()
        self._buckets = {
            k: _Bucket(capacity=c, refill_per_s=r, tokens=float(c), last=t)
            for k, (c, r) in src.items()
        }

    def hold(self, seconds: float = DHAN_429_HOLD_S) -> None:
        with self._lock:
            self._hold_until = max(self._hold_until, self._now() + seconds)

    def holding_for(self) -> float:
        with self._lock:
            return max(0.0, self._hold_until - self._now())

    def acquire(self, kind: str, key: str = "") -> float:
        waited = 0.0
        if kind == "chain":
            waited += self._acquire_chain(key)
            kind = "data"
        while True:
            with self._lock:
                t = self._now()
                hold = self._hold_until - t
                if hold > 0:
                    wait = hold
                else:
                    b = self._buckets.get(kind) or self._buckets["general"]
                    b.tokens = min(float(b.capacity), b.tokens + (t - b.last) * b.refill_per_s)
                    b.last = t
                    if b.tokens >= 1.0 - _EPS:
                        b.tokens -= 1.0
                        return waited
                    wait = max((1.0 - b.tokens) / b.refill_per_s, _MIN_WAIT_S)
            self._sleep(wait)
            waited += wait

    def _acquire_chain(self, key: str) -> float:
        with self._lock:
            t = self._now()
            last = self._chain_last.get(key)
            wait = (last + CHAIN_GAP_S - t) if last is not None else 0.0
            if len(self._chain_last) >= _MAX_CHAIN_KEYS:
                self._chain_last.pop(next(iter(self._chain_last)))
            self._chain_last[key] = t + max(wait, 0.0)
        if wait > 0:
            self._sleep(wait)
            return wait
        return 0.0
