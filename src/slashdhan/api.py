"""The functions the model's scripts call. Signatures and first doc lines are the schema."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any, Literal

from . import client, instruments
from .config import IST, now_ist, today_ist
from .errors import (
    CallBudgetExceeded,
    DataPlanMissing,
    DhanError,
    InstrumentNotFound,
    RateLimited,
    SlashDhanError,
    TokenExpired,
)
from .http import session
from .indicators import atr, bollinger, closes, ema, macd, pct_change, rsi, sma, vwap
from .instruments import (
    SUPPORTED_SEGMENTS,
    check_segment,
    contract_info,
    find_option_contracts,
    instrument_for,
    list_underlyings,
    lookup,
    master_status,
    resolve_symbol,
    resolve_underlying,
    search_instruments,
)
from .markets import days_ago, market_status, to_ist, today, ymd

Row = dict[str, Any]
Targets = dict[str, Sequence[int | str]] | Iterable[str | int]

INTRADAY_INTERVALS = (1, 5, 15, 25, 60)
INTRADAY_MAX_DAYS = 90
MAX_CHAIN_STRIKES_AROUND = 60


# ---------------------------------------------------------------- account


def profile() -> Row:
    """Feed account profile: dhanClientId, tokenValidity, activeSegment, ddpi, mtf, dataPlan, dataValidity."""
    return client.profile()


def positions() -> list[Row]:
    """Open positions (today's trading book), NOT delivery holdings.

    Rows carry tradingSymbol, securityId, exchangeSegment, productType, positionType
    LONG/SHORT/CLOSED, netQty, buyAvg, sellAvg, realizedProfit, unrealizedProfit,
    costPrice, drvExpiryDate, drvOptionType, drvStrikePrice.
    """
    return client.positions()


def holdings() -> list[Row]:
    """Demat holdings: tradingSymbol, securityId, isin, exchange, totalQty, availableQty, avgCostPrice, lastTradedPrice."""
    return client.holdings()


def funds() -> Row:
    """Fund limits: availabelBalance (Dhan's spelling), withdrawableBalance, utilizedAmount, collateralAmount, sodLimit."""
    return client.fundlimit()


def orders() -> list[Row]:
    """Today's order book, every status (TRANSIT/PENDING/TRADED/REJECTED/CANCELLED/PART_TRADED/EXPIRED).

    Rows: orderId, tradingSymbol, securityId, exchangeSegment, transactionType, orderType,
    productType, quantity, filledQty, price, triggerPrice, orderStatus, omsErrorDescription.
    """
    return client.orders()


def trades() -> list[Row]:
    """Today's executed fills: orderId, tradingSymbol, transactionType, tradedQuantity, tradedPrice, exchangeTime."""
    return client.trades()


def position_for(
    security_id: int | str,
    exchange_segment: str | None = None,
    product_type: str | None = None,
) -> Row | None:
    """One position row by security id, else the matching demat holding, else None.

    Position rows add "_source": "positions" and "closed": True when netQty is 0; holdings
    rows add "_source": "holdings" (delivery holdings never appear in positions() unless they
    were traded today).
    """
    sid = str(security_id)
    for p in positions():
        if str(p.get("securityId")) != sid:
            continue
        if exchange_segment and str(p.get("exchangeSegment")) != exchange_segment.upper():
            continue
        if product_type and str(p.get("productType")) != product_type.upper():
            continue
        return {**p, "_source": "positions", "closed": _float(p.get("netQty")) == 0}
    for h in holdings():
        if str(h.get("securityId")) == sid:
            return {**h, "_source": "holdings"}
    return None


# ---------------------------------------------------------------- quotes


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _targets(
    instruments_: Targets | None,
    security_id: Any,
    exchange_segment: str | None,
) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    if security_id is not None:
        if not exchange_segment:
            raise ValueError("security_id needs exchange_segment (e.g. 'NSE_EQ').")
        ids: list[Any] = (
            list(security_id) if isinstance(security_id, list | tuple | set) else [security_id]
        )
        out[check_segment(exchange_segment)] = [int(i) for i in ids]
        return out
    if isinstance(instruments_, dict):
        for seg, seg_ids in instruments_.items():
            out.setdefault(check_segment(seg), []).extend(int(i) for i in seg_ids)
        return out
    if instruments_ is None:
        raise ValueError(
            "Pass instruments={'NSE_EQ': [2885]}, a list of symbols/keys like ['TCS', "
            "'IDX_I:13'], or security_id=... with exchange_segment=...."
        )
    for ref in instruments_:
        sid, seg = lookup(ref)
        out.setdefault(seg, []).append(int(sid))
    return out


def ltp(
    instruments: Targets | None = None,
    *,
    security_id: Any = None,
    exchange_segment: str | None = None,
) -> dict[str, dict[str, float]]:
    """Batched last prices → {segment: {security_id: price}}; ≤1000 ids per Dhan call, 1 call/s.

    Forms: ltp({"NSE_EQ": [2885, 1333], "IDX_I": [13], "MCX_COMM": [483079]}),
    ltp(["TCS", "NIFTY", "GOLD", "NSE_EQ:2885"]) (symbols resolved via resolve_symbol), or
    ltp(security_id=2885, exchange_segment="NSE_EQ"). Instruments Dhan returns nothing for
    are omitted.
    """
    data = client.ltp(_targets(instruments, security_id, exchange_segment))
    out: dict[str, dict[str, float]] = {}
    for seg, rows in data.items():
        out[seg] = {}
        for sid, row in (rows or {}).items():
            price = (row or {}).get("last_price") if isinstance(row, dict) else None
            if price is not None:
                out[seg][str(sid)] = float(price)
    return out


def ltp_of(instrument: str | int, exchange_segment: str | None = None) -> float:
    """Last price of ONE instrument: ltp_of("TCS"), ltp_of("NIFTY"), ltp_of(2885, "NSE_EQ").

    Without the Data-API subscription, *_EQ instruments fall back to the holdings'
    lastTradedPrice when the feed account holds them.
    """
    sid, seg = lookup(instrument, exchange_segment)
    try:
        prices = ltp(security_id=sid, exchange_segment=seg)
    except DataPlanMissing:
        if not seg.endswith("_EQ"):
            raise
        for h in holdings():
            if str(h.get("securityId")) == sid and h.get("lastTradedPrice") is not None:
                return float(h["lastTradedPrice"])
        raise
    try:
        return prices[seg][sid]
    except KeyError:
        raise InstrumentNotFound(
            f"Dhan returned no price for {sid} on {seg}: the feed may be closed or the "
            "instrument unsupported."
        ) from None


def ohlc(
    instruments: Targets | None = None,
    *,
    security_id: Any = None,
    exchange_segment: str | None = None,
) -> dict[str, dict[str, Row]]:
    """Day OHLC + last price per instrument → {segment: {security_id: {last_price, ohlc}}}. Same forms as ltp."""
    data = client.ohlc(_targets(instruments, security_id, exchange_segment))
    return {
        seg: {str(k): v for k, v in rows.items() if isinstance(v, dict)}
        for seg, rows in data.items()
    }


def quote(
    instruments: Targets | None = None,
    *,
    security_id: Any = None,
    exchange_segment: str | None = None,
) -> dict[str, dict[str, Row]]:
    """Full quotes → {segment: {security_id: quote}}; same forms as ltp.

    Per instrument: last_price, volume, average_price, net_change, buy_quantity, sell_quantity,
    ohlc {open, high, low, close}, last_trade_time, oi, oi_day_high, oi_day_low,
    upper_circuit_limit, lower_circuit_limit, depth {buy: [{price, quantity, orders}] x5, sell}.
    """
    data = client.quote(_targets(instruments, security_id, exchange_segment))
    return {
        seg: {str(k): v for k, v in rows.items() if isinstance(v, dict)}
        for seg, rows in data.items()
    }


# ---------------------------------------------------------------- candles


def _interval(interval: str | int) -> int:
    text = str(interval).strip().lower()
    if text in ("day", "1d", "d", "daily"):
        raise ValueError("Use daily_candles() for the day interval")
    for suffix in ("minute", "min", "m"):
        text = text.removesuffix(suffix)
    if text in ("1h", "hour"):
        text = "60"
    try:
        value = int(text)
    except ValueError:
        raise ValueError(
            f"Unknown interval {interval!r}. Dhan serves 1, 5, 15, 25 or 60 minutes (or 'day')."
        ) from None
    if value not in INTRADAY_INTERVALS:
        raise ValueError(f"Dhan serves intraday intervals {INTRADAY_INTERVALS}, not {value}.")
    return value


def _candle_rows(d: Row) -> list[Row]:
    ts = d.get("timestamp") or []
    cols = {k: (d.get(k) or []) for k in ("open", "high", "low", "close", "volume")}
    oi = d.get("open_interest") or []
    out: list[Row] = []
    for i, t in enumerate(ts):
        stamp = int(float(t))
        row: Row = {
            "time": to_ist(stamp).isoformat(),
            "timestamp": stamp,
            "open": _float(cols["open"][i]) if i < len(cols["open"]) else None,
            "high": _float(cols["high"][i]) if i < len(cols["high"]) else None,
            "low": _float(cols["low"][i]) if i < len(cols["low"]) else None,
            "close": _float(cols["close"][i]) if i < len(cols["close"]) else None,
            "volume": int(_float(cols["volume"][i])) if i < len(cols["volume"]) else 0,
        }
        if oi:
            row["oi"] = int(_float(oi[i])) if i < len(oi) else 0
        out.append(row)
    return out


def _window(days_back: int, from_date: str | None, to_date: str | None) -> tuple[date, date]:
    end = date.fromisoformat(to_date) if to_date else today_ist()
    start = (
        date.fromisoformat(from_date) if from_date else end - timedelta(days=max(0, int(days_back)))
    )
    if start > end:
        raise ValueError("from_date must not be after to_date")
    return start, end


def _chart_body(
    sid: str, seg: str, chart_instrument: str | None, oi: bool | None
) -> tuple[Row, str]:
    inst = (chart_instrument or instrument_for(sid, seg)).upper()
    use_oi = inst not in ("EQUITY", "INDEX") if oi is None else bool(oi)
    return {"securityId": sid, "exchangeSegment": seg, "instrument": inst, "oi": use_oi}, inst


def intraday_candles(
    instrument: str | int,
    exchange_segment: str | None = None,
    interval: str | int = 15,
    days_back: int = 5,
    from_date: str | None = None,
    to_date: str | None = None,
    oi: bool | None = None,
    chart_instrument: str | None = None,
) -> list[Row]:
    """Intraday candles [{time (IST ISO), timestamp, open, high, low, close, volume, oi?}] oldest first.

    `instrument` is a symbol ('TCS', 'NIFTY', 'GOLD'), a 'SEGMENT:ID' key or a security id with
    exchange_segment. interval: 1, 5, 15, 25 or 60 minutes. Dhan serves 90 days per call;
    longer windows are fetched in chunks (each one Dhan call against the budget). oi defaults
    to True for every derivative. Ignore a trailing zero-volume bar after the close.
    """
    sid, seg = lookup(instrument, exchange_segment)
    minutes = _interval(interval)
    start, end = _window(days_back, from_date, to_date)
    base, _ = _chart_body(sid, seg, chart_instrument, oi)
    out: list[Row] = []
    seen: set[int] = set()
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=INTRADAY_MAX_DAYS - 1), end)
        body = {
            **base,
            "interval": str(minutes),
            "fromDate": cursor.isoformat(),
            "toDate": (chunk_end + timedelta(days=1)).isoformat(),
        }
        for row in _candle_rows(client.intraday(body)):
            if row["timestamp"] not in seen:
                seen.add(row["timestamp"])
                out.append(row)
        cursor = chunk_end + timedelta(days=1)
    out.sort(key=lambda r: int(r["timestamp"]))
    return out


def daily_candles(
    instrument: str | int,
    exchange_segment: str | None = None,
    days_back: int = 365,
    from_date: str | None = None,
    to_date: str | None = None,
    oi: bool | None = None,
    chart_instrument: str | None = None,
    expiry_code: int | None = None,
) -> list[Row]:
    """Daily candles [{time, timestamp, open, high, low, close, volume, oi?}] oldest first.

    Same `instrument` forms as intraday_candles; today's row is partial while the market is
    open. expiry_code 0/1/2 selects a futures series (current, next, far).
    """
    sid, seg = lookup(instrument, exchange_segment)
    start, end = _window(days_back, from_date, to_date)
    body, _ = _chart_body(sid, seg, chart_instrument, oi)
    body.update({"fromDate": start.isoformat(), "toDate": (end + timedelta(days=1)).isoformat()})
    if expiry_code is not None:
        body["expiryCode"] = int(expiry_code)
    rows = _candle_rows(client.historical(body))
    rows.sort(key=lambda r: int(r["timestamp"]))
    return rows


# ---------------------------------------------------------------- options


def expiry_list(underlying: str, exchange: str | None = None) -> list[str]:
    """Option expiries (YYYY-MM-DD, ascending) of an underlying: index, NSE/BSE F&O stock or MCX commodity.

    NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX, RELIANCE, GOLD, CRUDEOIL,
    NATURALGAS...; exchange NSE|BSE|MCX picks the listing when several exist.
    """
    u = resolve_underlying(underlying, exchange)
    return sorted(client.expiry_list({"UnderlyingScrip": u["scrip"], "UnderlyingSeg": u["seg"]}))


def nearest_expiry(underlying: str, exchange: str | None = None) -> str:
    """The closest option expiry (today or later) of an underlying."""
    expiries = expiry_list(underlying, exchange)
    if not expiries:
        raise InstrumentNotFound(f"Dhan returned no option expiries for {underlying!r}")
    t = today()
    future = [e for e in expiries if e >= t]
    return (future or expiries)[0]


def futures_list(underlying: str, exchange: str | None = None) -> list[Row]:
    """Live futures of an underlying (index, stock, MCX commodity), nearest expiry first; rows like resolve_symbol."""
    return instruments.futures_contracts(underlying, exchange)


def _side(s: Row | None) -> Row | None:
    if not s:
        return None
    g = s.get("greeks") or {}
    return {
        "ltp": s.get("last_price"),
        "iv": s.get("implied_volatility"),
        "oi": s.get("oi"),
        "oiPrev": s.get("previous_oi"),
        "volume": s.get("volume"),
        "bid": s.get("top_bid_price"),
        "bidQty": s.get("top_bid_quantity"),
        "ask": s.get("top_ask_price"),
        "askQty": s.get("top_ask_quantity"),
        "avgPrice": s.get("average_price"),
        "prevClose": s.get("previous_close_price"),
        "securityId": s.get("security_id"),
        "delta": g.get("delta"),
        "theta": g.get("theta"),
        "gamma": g.get("gamma"),
        "vega": g.get("vega"),
    }


def _max_pain(rows: list[Row]) -> float | None:
    best: float | None = None
    best_pain: float | None = None
    for r in rows:
        k = float(r["strike"])
        pain = 0.0
        for o in rows:
            s = float(o["strike"])
            ce_oi = _float((o["ce"] or {}).get("oi"))
            pe_oi = _float((o["pe"] or {}).get("oi"))
            pain += ce_oi * max(k - s, 0.0) + pe_oi * max(s - k, 0.0)
        if best_pain is None or pain < best_pain:
            best, best_pain = k, pain
    return best


def _lot_size(u: Row, expiry: str) -> float | None:
    rows = find_option_contracts(u["name"], expiry_date=expiry, limit=1, exchange=u["exchange"])
    if not rows:
        rows = find_option_contracts(u["name"], limit=1, exchange=u["exchange"])
    return rows[0].get("lot_size") if rows else None


def option_chain(
    underlying: str,
    expiry: str | None = None,
    strikes_around: int | None = 10,
    exchange: str | None = None,
) -> Row:
    """Dhan's option chain with IV and greeks, trimmed to ±strikes_around strikes of ATM.

    Returns {underlying, exchange, contractSegment, expiry, underlyingLastPrice, lot_size,
    strikes: [{strike, ce, pe}], pcr, max_pain}. Each side: ltp, iv, oi, oiPrev, volume, bid,
    bidQty, ask, askQty, avgPrice, prevClose, securityId, delta, theta, gamma, vega. expiry
    defaults to the nearest; strikes_around=None returns every strike (large). Dhan allows ONE
    chain call per (underlying, expiry) every 3 s, so keep it to one or two calls per script.
    """
    u = resolve_underlying(underlying, exchange)
    exp = expiry or nearest_expiry(u["name"], u["exchange"])
    d = client.option_chain(
        {"UnderlyingScrip": u["scrip"], "UnderlyingSeg": u["seg"], "Expiry": exp}
    )
    oc = d.get("oc")
    if not isinstance(oc, dict) or not oc:
        raise DhanError("Dhan /optionchain returned no chain", 502, body=d)
    spot = _float(d.get("last_price"))
    keyed: dict[float, Row] = {}
    for k, row in oc.items():
        try:
            keyed[float(k)] = row or {}
        except (TypeError, ValueError):
            continue
    strikes = sorted(keyed)
    if strikes_around is not None and strikes:
        n = max(0, min(int(strikes_around), MAX_CHAIN_STRIKES_AROUND))
        atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        strikes = strikes[max(0, atm - n) : atm + n + 1]
    rows: list[Row] = [
        {"strike": k, "ce": _side(keyed[k].get("ce")), "pe": _side(keyed[k].get("pe"))}
        for k in strikes
    ]
    ce_oi = sum(_float((r["ce"] or {}).get("oi")) for r in rows)
    pe_oi = sum(_float((r["pe"] or {}).get("oi")) for r in rows)
    return {
        "underlying": u["name"],
        "exchange": u["exchange"],
        "contractSegment": u["contract_segment"],
        "expiry": exp,
        "underlyingLastPrice": spot,
        "lot_size": _lot_size(u, exp),
        "strikes": rows,
        "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None,
        "max_pain": _max_pain(rows),
    }


def option_candles(
    contract: int | str,
    underlying: str | None = None,
    interval: str | int = 15,
    days_back: int = 5,
    from_date: str | None = None,
    to_date: str | None = None,
    exchange: str | None = None,
) -> list[Row]:
    """Intraday premium + OI candles of ONE option contract by securityId (from a chain row or find_option_contracts).

    Segment and instrument come from the master; pass `underlying` (+exchange) only when the id
    is not in it.
    """
    if underlying:
        u = resolve_underlying(underlying, exchange)
        seg, inst = str(u["contract_segment"]), str(u["contract_instrument"])
    else:
        c = contract_info(contract)
        if not c or not str(c["instrument"]).startswith("OPT"):
            raise InstrumentNotFound(
                f"security_id {contract} is not an option contract in the master; pass "
                "underlying= (and exchange=) so the segment can be derived."
            )
        seg, inst = str(c["exchange_segment"]), str(c["instrument"])
    return intraday_candles(
        contract, seg, interval, days_back, from_date, to_date, oi=True, chart_instrument=inst
    )


def expired_options(
    underlying: str,
    option_type: Literal["CALL", "PUT"],
    from_date: str,
    to_date: str,
    *,
    strike: str = "ATM",
    expiry_flag: Literal["WEEK", "MONTH"] | None = None,
    expiry_code: int = 1,
    interval: Literal[1, 5, 15, 60] = 60,
    fields: Sequence[str] | None = None,
    exchange: str | None = None,
) -> Row:
    """History of EXPIRED options (Dhan rolling-option data) at ATM / ATM±N, rolling across past expiries.

    For "how did IV behave into past expiries" questions; indices, stocks and MCX commodities.
    SLOW (~15 s) and ≤7 days per call: chunk longer ranges. strike 'ATM', 'ATM+2', 'ATM-3'
    (indices ±10, stocks ±3); expiry_flag defaults WEEK for NSE/BSE indices, MONTH otherwise;
    expiry_code 1 = nearest series; to_date is NOT inclusive; fields ⊆ {open, high, low,
    close, iv, volume, strike, oi, spot}. Returns {underlying, exchange, optionType,
    expiryFlag, bars: [{time, timestamp, ...fields}]}.
    """
    u = resolve_underlying(underlying, exchange)
    if expiry_flag is None:
        expiry_flag = (
            "WEEK" if (u["kind"] == "index" and u["contract_segment"] != "MCX_COMM") else "MONTH"
        )
    span = (date.fromisoformat(to_date) - date.fromisoformat(from_date)).days
    if span <= 0:
        raise ValueError("to_date must be after from_date.")
    if span > 7:
        raise ValueError("Window too large: Dhan times out beyond 7 days. Chunk the range.")
    if int(expiry_code) < 1:
        raise ValueError("expiry_code starts at 1 (Dhan rejects 0).")
    body = {
        "exchangeSegment": u["contract_segment"],
        "securityId": str(u["scrip"]),
        "instrument": u["contract_instrument"],
        "expiryCode": int(expiry_code),
        "expiryFlag": expiry_flag,
        "drvOptionType": option_type,
        "strike": strike,
        "interval": int(interval),
        "requiredData": list(fields or ("close", "iv", "oi", "strike", "spot")),
        "fromDate": from_date,
        "toDate": to_date,
    }
    d = client.rolling_option(body)
    side = d.get("pe") if option_type == "PUT" else d.get("ce")
    side = side or d.get("ce") or d.get("pe") or {}
    ts = side.get("timestamp") or []
    keys = [k for k in side if k != "timestamp"]
    bars: list[Row] = []
    for i, t in enumerate(ts):
        stamp = int(float(t))
        bar: Row = {"time": to_ist(stamp).isoformat(), "timestamp": stamp}
        for k in keys:
            seq = side.get(k) or []
            if i < len(seq) and seq[i] is not None:
                bar[k] = _float(seq[i])
        bars.append(bar)
    return {
        "underlying": u["name"],
        "exchange": u["exchange"],
        "optionType": option_type,
        "expiryFlag": expiry_flag,
        "bars": bars,
    }


# ---------------------------------------------------------------- instruments


def instrument(ref: str | int, exchange_segment: str | None = None) -> Row:
    """Master row for a symbol, a 'SEGMENT:ID' key or a security id + exchange_segment; raises if none."""
    sid, seg = lookup(ref, exchange_segment)
    row = instruments.by_id(sid, seg)
    if row is None:
        raise InstrumentNotFound(f"No instrument {sid} on {seg} in the scrip master.")
    return row


# ---------------------------------------------------------------- helpers


def now() -> str:
    """Current IST time as 'YYYY-MM-DD HH:MM:SS'."""
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


def dhan_get(path: str) -> Any:
    """Escape hatch: GET any other Dhan v2 read endpoint (e.g. '/ledger') → its data."""
    return session().get(path)


def dhan_post(path: str, body: Any = None) -> Any:
    """Escape hatch: POST to any other Dhan v2 READ endpoint; the gateway refuses order endpoints."""
    return session().post(path, body)


def pp(value: Any, limit: int | None = 4000) -> None:
    """Print a value as compact JSON, truncated to `limit` characters (None = no limit)."""
    text = json.dumps(value, default=str, ensure_ascii=False)
    if limit is not None and len(text) > limit:
        text = text[:limit] + f"… [{len(text) - limit} more chars; print less]"
    print(text)


def help_api() -> str:
    """One line per function: the full slashdhan reference."""
    from .schema import reference

    return reference(brief=True)


def is_nan(x: Any) -> bool:
    """True for None or NaN, handy when filtering indicator output."""
    return x is None or (isinstance(x, float) and math.isnan(x))


__all__ = [
    # account
    "profile",
    "positions",
    "holdings",
    "funds",
    "orders",
    "trades",
    "position_for",
    # quotes and candles
    "ltp",
    "ltp_of",
    "ohlc",
    "quote",
    "intraday_candles",
    "daily_candles",
    # options and futures
    "expiry_list",
    "nearest_expiry",
    "futures_list",
    "option_chain",
    "option_candles",
    "expired_options",
    # instruments
    "resolve_symbol",
    "search_instruments",
    "find_option_contracts",
    "resolve_underlying",
    "instrument",
    "instrument_for",
    "contract_info",
    "list_underlyings",
    "master_status",
    "market_status",
    # indicators
    "closes",
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger",
    "atr",
    "vwap",
    "pct_change",
    # time
    "now",
    "today",
    "days_ago",
    "ymd",
    "to_ist",
    "IST",
    "SUPPORTED_SEGMENTS",
    # misc
    "dhan_get",
    "dhan_post",
    "pp",
    "help_api",
    "is_nan",
    "check_segment",
    # errors
    "SlashDhanError",
    "DhanError",
    "TokenExpired",
    "DataPlanMissing",
    "RateLimited",
    "CallBudgetExceeded",
    "InstrumentNotFound",
]
