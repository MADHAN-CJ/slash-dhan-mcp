"""Plain-Python indicators over candle dicts. Series align to their input (None = warm-up)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

Series = list[float | None]


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def closes(candles: Sequence[dict[str, Any]], field: str = "close") -> list[float]:
    """Pull one numeric column out of a candle list."""
    return [_num(c.get(field)) for c in candles]


def sma(values: Sequence[float], period: int) -> Series:
    """Simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Exponential moving average, seeded with the first `period` SMA."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """Relative strength index (Wilder smoothing)."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, Series]:
    """MACD line, signal line and histogram, all aligned to `values`."""
    f, s = ema(values, fast), ema(values, slow)
    line: Series = [
        (f[i] - s[i]) if (f[i] is not None and s[i] is not None) else None  # type: ignore[operator]
        for i in range(len(values))
    ]
    start = next((i for i, v in enumerate(line) if v is not None), len(values))
    sig_dense = ema([v for v in line[start:] if v is not None], signal)
    sig: Series = [None] * len(values)
    for j, v in enumerate(sig_dense):
        sig[start + j] = v
    hist: Series = [
        (line[i] - sig[i]) if (line[i] is not None and sig[i] is not None) else None  # type: ignore[operator]
        for i in range(len(values))
    ]
    return {"macd": line, "signal": sig, "histogram": hist}


def bollinger(values: Sequence[float], period: int = 20, stddev: float = 2.0) -> dict[str, Series]:
    """Bollinger bands {upper, middle, lower} using the population standard deviation."""
    mid = sma(values, period)
    up: Series = [None] * len(values)
    lo: Series = [None] * len(values)
    for i in range(len(values)):
        m = mid[i]
        if m is None:
            continue
        window = values[i - period + 1 : i + 1]
        sd = (sum((v - m) ** 2 for v in window) / period) ** 0.5
        up[i], lo[i] = m + stddev * sd, m - stddev * sd
    return {"upper": up, "middle": mid, "lower": lo}


def atr(candles: Sequence[dict[str, Any]], period: int = 14) -> Series:
    """Average true range (Wilder) over candle dicts with high/low/close."""
    n = len(candles)
    out: Series = [None] * n
    if n <= period:
        return out
    tr: list[float] = [_num(candles[0].get("high")) - _num(candles[0].get("low"))]
    for i in range(1, n):
        h, lo = _num(candles[i].get("high")), _num(candles[i].get("low"))
        pc = _num(candles[i - 1].get("close"))
        tr.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    prev = sum(tr[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def vwap(candles: Sequence[dict[str, Any]]) -> Series:
    """Running VWAP over typical price; slice per session yourself for a daily reset."""
    out: Series = [None] * len(candles)
    pv = vol = 0.0
    for i, c in enumerate(candles):
        v = _num(c.get("volume") or 0)
        tp = (_num(c.get("high")) + _num(c.get("low")) + _num(c.get("close"))) / 3
        pv += tp * v
        vol += v
        out[i] = (pv / vol) if vol else None
    return out


def pct_change(values: Sequence[float]) -> Series:
    """Bar-over-bar percentage change (first element is None)."""
    out: Series = [None] * len(values)
    for i in range(1, len(values)):
        prev = values[i - 1]
        out[i] = ((values[i] - prev) / prev * 100) if prev else None
    return out
