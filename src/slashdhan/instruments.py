"""Dhan's detailed scrip master, cached in SQLite per IST day, and symbol resolution on top.

Only segments Dhan's API can serve are stored: NSE_EQ, BSE_EQ, IDX_I, NSE_FNO, BSE_FNO and
MCX_COMM. Currency derivatives (no live contracts on Dhan) and NSE's commodity segment (no API
enum) are dropped at ingest so they can never resolve to something the API rejects.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sqlite3
import threading
from typing import Any

import httpx

from .config import now_ist, settings, today_ist
from .errors import InstrumentNotFound

Row = dict[str, Any]

SCHEMA_VERSION = 1
SEGMENT_MAP = {
    ("NSE", "E"): "NSE_EQ",
    ("NSE", "D"): "NSE_FNO",
    ("NSE", "I"): "IDX_I",
    ("BSE", "E"): "BSE_EQ",
    ("BSE", "D"): "BSE_FNO",
    ("BSE", "I"): "IDX_I",
    ("MCX", "M"): "MCX_COMM",
}
SUPPORTED_SEGMENTS = tuple(sorted(set(SEGMENT_MAP.values())))
DROPPED_SEGMENTS = {
    "NSE_CURRENCY": "currency derivatives have no live contracts on Dhan any more",
    "BSE_CURRENCY": "currency derivatives have no live contracts on Dhan any more",
    "NSE_COMM": "NSE commodity derivatives are not served by Dhan's API (use MCX_COMM)",
}
DERIVATIVE_INSTRUMENTS = ("OPTIDX", "OPTSTK", "OPTFUT", "FUTIDX", "FUTSTK", "FUTCOM")
OPTION_INSTRUMENTS = ("OPTIDX", "OPTSTK", "OPTFUT")
FUTURE_INSTRUMENTS = ("FUTIDX", "FUTSTK", "FUTCOM")

ALIASES = {
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "NIFTY-50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "BANK NIFTY": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY FINANCIAL SERVICES": "FINNIFTY",
    "FIN NIFTY": "FINNIFTY",
    "NIFTY MIDCAP SELECT": "MIDCPNIFTY",
    "MIDCAP NIFTY": "MIDCPNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50",
    "NIFTY NXT 50": "NIFTYNXT50",
    "NIFTYNEXT50": "NIFTYNXT50",
    "INDIA VIX": "INDIAVIX",
    "VIX": "INDIAVIX",
    "SENSEX50": "SNSX50",
    "SENSEX 50": "SNSX50",
    "BSE SENSEX 50": "SNSX50",
    "BSE SENSEX": "SENSEX",
    "BSE BANKEX": "BANKEX",
    "MCX GOLD": "GOLD",
    "MCX SILVER": "SILVER",
    "MCX CRUDE": "CRUDEOIL",
    "CRUDE": "CRUDEOIL",
    "CRUDE OIL": "CRUDEOIL",
    "NATURAL GAS": "NATURALGAS",
    "NAT GAS": "NATURALGAS",
}

_COLUMNS = (
    "exch_id",
    "segment",
    "security_id",
    "isin",
    "instrument",
    "underlying_security_id",
    "underlying_symbol",
    "symbol_name",
    "display_name",
    "instrument_type",
    "series",
    "lot_size",
    "expiry_date",
    "strike_price",
    "option_type",
    "tick_size",
    "expiry_flag",
    "exchange_segment",
)
_CSV_TO_COL = {
    "EXCH_ID": "exch_id",
    "SEGMENT": "segment",
    "SECURITY_ID": "security_id",
    "ISIN": "isin",
    "INSTRUMENT": "instrument",
    "UNDERLYING_SECURITY_ID": "underlying_security_id",
    "UNDERLYING_SYMBOL": "underlying_symbol",
    "SYMBOL_NAME": "symbol_name",
    "DISPLAY_NAME": "display_name",
    "INSTRUMENT_TYPE": "instrument_type",
    "SERIES": "series",
    "LOT_SIZE": "lot_size",
    "SM_EXPIRY_DATE": "expiry_date",
    "STRIKE_PRICE": "strike_price",
    "OPTION_TYPE": "option_type",
    "TICK_SIZE": "tick_size",
    "EXPIRY_FLAG": "expiry_flag",
}
_KIND = {
    "EQUITY": "equity",
    "INDEX": "index",
    "FUTCOM": "commodity_future",
    "FUTIDX": "index_future",
    "FUTSTK": "stock_future",
    "OPTIDX": "index_option",
    "OPTSTK": "stock_option",
    "OPTFUT": "commodity_option",
}
_EXCHANGE_TO_SEGMENT = {"NSE": "NSE_FNO", "BSE": "BSE_FNO", "MCX": "MCX_COMM"}
_SEGMENT_PREFERENCE = ("NSE_FNO", "BSE_FNO", "MCX_COMM")
_KIND_FILTER = {
    "equity": "instrument = 'EQUITY'",
    "index": "instrument = 'INDEX'",
    "commodity": "instrument = 'FUTCOM'",
    "future": "instrument IN ('FUTSTK','FUTIDX','FUTCOM')",
}

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_key = ""
_client: httpx.Client | None = None
_underlying_cache: dict[tuple[str, str | None, str], Row] = {}


def configure(client: httpx.Client | None) -> None:
    """Use an explicit HTTP client for the scrip-master download (tests)."""
    global _client
    _client = client


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=180.0, follow_redirects=True)
    return _client


def _norm_expiry(v: str) -> str:
    v = (v or "").strip()
    return "" if v in ("", "NA") or v.startswith("0001") else v[:10]


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "", "NA") else None
    except ValueError:
        return None


def _parse(csv_text: str) -> tuple[list[tuple[str, ...]], dict[str, int]]:
    rows: list[tuple[str, ...]] = []
    skipped: dict[str, int] = {}
    for rec in csv.DictReader(io.StringIO(csv_text)):
        row = {col: (rec.get(csv_col) or "").strip() for csv_col, col in _CSV_TO_COL.items()}
        seg = SEGMENT_MAP.get((row["exch_id"].upper(), row["segment"].upper()))
        if not seg:
            key = f"{row['exch_id']}:{row['segment']}"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        row["exchange_segment"] = seg
        row["expiry_date"] = _norm_expiry(row["expiry_date"])
        row["underlying_symbol"] = row["underlying_symbol"].upper()
        rows.append(tuple(row[c] for c in _COLUMNS))
    return rows, skipped


def _build(path: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    r = _http().get(settings().scrip_master_url)
    r.raise_for_status()
    rows, skipped = _parse(r.text)
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.execute(f"CREATE TABLE instruments ({', '.join(c + ' TEXT' for c in _COLUMNS)})")
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany(f"INSERT INTO instruments VALUES ({','.join('?' * len(_COLUMNS))})", rows)
        con.execute("CREATE INDEX ix_sec ON instruments(security_id, exchange_segment)")
        con.execute(
            "CREATE INDEX ix_und ON instruments(underlying_symbol, instrument, exchange_segment)"
        )
        con.execute("CREATE INDEX ix_disp ON instruments(display_name)")
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("built_at", now_ist().strftime("%Y-%m-%d %H:%M:%S")),
                ("rows", str(len(rows))),
                ("skipped", json.dumps(skipped, sort_keys=True)),
                ("source", settings().scrip_master_url),
            ],
        )
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)


def _prune(cache_dir: str, keep: str) -> None:
    for name in os.listdir(cache_dir):
        if name.startswith("instruments-") and name.endswith(".sqlite3") and name != keep:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(cache_dir, name))


def db() -> sqlite3.Connection:
    """Open today's scrip-master cache, downloading and indexing the CSV on first use."""
    global _conn, _conn_key
    key = f"v{SCHEMA_VERSION}-{today_ist().isoformat()}"
    with _lock:
        if _conn is not None and _conn_key == key:
            return _conn
        cache_dir = str(settings().cache_dir)
        os.makedirs(cache_dir, exist_ok=True)
        filename = f"instruments-{key}.sqlite3"
        path = os.path.join(cache_dir, filename)
        if not os.path.exists(path):
            _build(path)
            _prune(cache_dir, filename)
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn_key = key
        _underlying_cache.clear()
        return _conn


def ensure() -> Row:
    """Build today's cache if missing (the server calls this at boot and daily) and report it."""
    db()
    return master_status()


def reset() -> None:
    global _conn, _conn_key
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _conn_key = None, ""
        _underlying_cache.clear()


def master_status() -> Row:
    """Scrip-master cache info: {schema_version, built_at, rows, skipped, segments}."""
    meta = {r["key"]: r["value"] for r in db().execute("SELECT key, value FROM meta")}
    return {
        "schema_version": int(meta.get("schema_version", 0)),
        "built_at": meta.get("built_at"),
        "rows": int(meta.get("rows", 0)),
        "skipped": json.loads(meta.get("skipped") or "{}"),
        "segments": list(SUPPORTED_SEGMENTS),
    }


def _row(r: sqlite3.Row | None) -> Row | None:
    if r is None:
        return None
    d = dict(r)
    inst = d["instrument"]
    out: Row = {
        "security_id": d["security_id"],
        "exchange_segment": d["exchange_segment"],
        "symbol": d["underlying_symbol"] or d["symbol_name"],
        "display_name": d["display_name"],
        "symbol_name": d["symbol_name"],
        "instrument": inst,
        "kind": _KIND.get(inst, inst.lower()),
        "instrument_type": d["instrument_type"],
        "series": d["series"] or None,
        "isin": d["isin"] if d["isin"] and d["isin"] != "NA" else None,
        "lot_size": _num(d["lot_size"]),
        "tick_size": _num(d["tick_size"]),
    }
    if inst in DERIVATIVE_INSTRUMENTS:
        out["expiry_date"] = d["expiry_date"] or None
        out["strike_price"] = _num(d["strike_price"]) if inst.startswith("OPT") else None
        out["option_type"] = d["option_type"] if inst.startswith("OPT") else None
        out["underlying_symbol"] = d["underlying_symbol"]
        out["underlying_security_id"] = d["underlying_security_id"] or None
    return out


def _rows(cur: sqlite3.Cursor) -> list[Row]:
    return [row for r in cur.fetchall() if (row := _row(r)) is not None]


def _today() -> str:
    return today_ist().isoformat()


def _live_sql(include_expired: bool) -> tuple[str, list[Any]]:
    if include_expired:
        return "", []
    return " AND (expiry_date = '' OR expiry_date >= ?)", [_today()]


def canonical(raw: str) -> str:
    """Normalise a typed name to the master's ticker ('Nifty 50'→NIFTY, 'Crude'→CRUDEOIL)."""
    name = " ".join((raw or "").strip().upper().split())
    return ALIASES.get(name, name)


def check_segment(exchange_segment: str) -> str:
    """Upper-cased segment, or ValueError for unknown names and segments Dhan cannot serve."""
    seg = (exchange_segment or "").strip().upper()
    if seg in DROPPED_SEGMENTS:
        raise ValueError(
            f"{seg} is not available on Dhan: {DROPPED_SEGMENTS[seg]}. Supported segments: "
            f"{', '.join(SUPPORTED_SEGMENTS)}."
        )
    if seg not in SUPPORTED_SEGMENTS:
        raise ValueError(
            f"Unknown exchange_segment {exchange_segment!r}. Supported: "
            f"{', '.join(SUPPORTED_SEGMENTS)}."
        )
    return seg


def is_derivative(row: Row) -> bool:
    return row.get("instrument") in DERIVATIVE_INSTRUMENTS


def _names(name: str) -> list[str]:
    return [name, *[k for k, v in ALIASES.items() if v == name and k != name]]


def resolve_symbol(
    symbol: str, exchange_segment: str | None = None, kind: str | None = None
) -> Row | None:
    """Map one ticker to the instrument to quote or chart, or None.

    'TCS' → NSE share (NSE_EQ); 'NIFTY' / 'SENSEX' → index (IDX_I); 'GOLD' / 'CRUDEOIL' →
    nearest live MCX future (MCX_COMM). Ranking: index > listed share (NSE, then BSE) >
    commodity future > index/stock future > ETF/bond; steer with exchange_segment and/or
    kind (equity | index | commodity | future). Rows carry security_id, exchange_segment,
    symbol, display_name, instrument, kind, isin, lot_size, tick_size and expiry_date for
    futures. For options use find_option_contracts or option_chain.
    """
    name = canonical(symbol)
    if not name:
        return None
    if kind and kind not in _KIND_FILTER:
        raise ValueError(
            f"kind must be one of {sorted(_KIND_FILTER)} (options: use find_option_contracts)."
        )
    sql = (
        "SELECT * FROM instruments WHERE (upper(underlying_symbol) = ? OR upper(display_name) = ?)"
    )
    args: list[Any] = [name, name]
    if exchange_segment:
        sql += " AND exchange_segment = ?"
        args.append(check_segment(exchange_segment))
    if kind:
        sql += f" AND {_KIND_FILTER[kind]}"
    else:
        sql += " AND instrument IN ('INDEX','EQUITY','FUTCOM','FUTIDX','FUTSTK')"
    live, largs = _live_sql(False)
    sql += live
    args += largs
    sql += """ ORDER BY
        CASE WHEN instrument = 'INDEX' THEN 0
             WHEN instrument = 'EQUITY' AND instrument_type = 'ES' THEN 1
             WHEN instrument = 'FUTCOM' THEN 2
             WHEN instrument = 'FUTIDX' THEN 3
             WHEN instrument = 'FUTSTK' THEN 4
             WHEN instrument = 'EQUITY' THEN 5 ELSE 9 END,
        (upper(underlying_symbol) = ?) DESC,
        (exchange_segment IN ('NSE_EQ','NSE_FNO')) DESC,
        (series = 'EQ') DESC, (instrument_type = 'ES') DESC,
        CASE WHEN expiry_date = '' THEN '9999' ELSE expiry_date END,
        CAST(security_id AS INTEGER)
      LIMIT 1"""
    args.append(name)
    return _row(db().execute(sql, args).fetchone())


def lookup(ref: str | int, exchange_segment: str | None = None) -> tuple[str, str]:
    """(security_id, exchange_segment) for an id + segment, a 'SEGMENT:ID' key or a symbol."""
    if exchange_segment:
        return str(ref).strip(), check_segment(exchange_segment)
    text = str(ref).strip()
    if ":" in text:
        seg, _, sid = text.partition(":")
        if sid.strip().isdigit():
            return sid.strip(), check_segment(seg)
    if text.isdigit():
        raise ValueError(
            f"{text!r} looks like a security id: pass exchange_segment= as well (or use "
            "'NSE_EQ:2885')."
        )
    row = resolve_symbol(text)
    if row is None:
        raise InstrumentNotFound(
            f"No instrument matches {ref!r}. Try search_instruments({text!r}) or a security "
            "id with its exchange_segment."
        )
    return str(row["security_id"]), str(row["exchange_segment"])


def search_instruments(
    query: str,
    exchange_segment: str | None = None,
    instrument: str | None = None,
    limit: int = 20,
    include_expired: bool = False,
) -> list[Row]:
    """Substring search over the master (ticker, display name) when resolve_symbol is not enough.

    Filter by exchange_segment and/or instrument (EQUITY, INDEX, FUTSTK, FUTIDX, FUTCOM, OPTSTK,
    OPTIDX, OPTFUT). Expired contracts are hidden unless include_expired. Rows look like
    resolve_symbol's, plus expiry_date / strike_price / option_type for derivatives.
    """
    raw = (query or "").strip().upper()
    name = canonical(raw)
    like = f"%{raw}%"
    sql = (
        "SELECT * FROM instruments WHERE (upper(underlying_symbol) LIKE ? OR "
        "upper(display_name) LIKE ? OR upper(symbol_name) LIKE ? OR upper(underlying_symbol) = ?)"
    )
    args: list[Any] = [like, like, like, name]
    if exchange_segment:
        sql += " AND exchange_segment = ?"
        args.append(check_segment(exchange_segment))
    if instrument:
        sql += " AND instrument = ?"
        args.append(instrument.upper())
    live, largs = _live_sql(include_expired)
    sql += live
    args += largs
    sql += """ ORDER BY (upper(underlying_symbol) = ?) DESC, (upper(underlying_symbol) = ?) DESC,
        length(display_name), CASE WHEN expiry_date = '' THEN '9999' ELSE expiry_date END,
        CAST(strike_price AS REAL) LIMIT ?"""
    args += [name, raw, max(1, min(int(limit), 500))]
    return _rows(db().execute(sql, args))


def _option_listings(name: str, include_expired: bool = False) -> list[sqlite3.Row]:
    live, largs = _live_sql(include_expired)
    names = _names(name)
    return (
        db()
        .execute(
            "SELECT exchange_segment, instrument, underlying_security_id, count(*) AS n, "
            "min(expiry_date) AS nearest FROM instruments WHERE instrument IN "
            f"('OPTIDX','OPTSTK','OPTFUT') AND upper(underlying_symbol) IN "
            f"({','.join('?' * len(names))}){live} GROUP BY exchange_segment, instrument "
            "ORDER BY n DESC",
            [*names, *largs],
        )
        .fetchall()
    )


def _index_id(name: str, exchange: str) -> str | None:
    names = _names(name)
    ph = ",".join("?" * len(names))
    r = (
        db()
        .execute(
            "SELECT security_id FROM instruments WHERE instrument = 'INDEX' AND exch_id = ? AND "
            f"(upper(underlying_symbol) IN ({ph}) OR upper(display_name) IN ({ph})) LIMIT 1",
            [exchange, *names, *names],
        )
        .fetchone()
    )
    return str(r["security_id"]) if r else None


def resolve_underlying(underlying: str, exchange: str | None = None) -> Row:
    """What Dhan's option APIs need for an underlying (index, NSE/BSE F&O stock, MCX commodity).

    Returns {name, scrip, seg, exchange, contract_segment, contract_instrument, kind
    index|stock|commodity, nearest_expiry}; exchange NSE|BSE|MCX picks the listing (default
    NSE, else BSE, else MCX). Raises InstrumentNotFound when no options are listed.
    """
    name = canonical(underlying)
    if not name:
        raise InstrumentNotFound("Empty underlying symbol.")
    ex = (exchange or "").strip().upper() or None
    if ex and ex not in _EXCHANGE_TO_SEGMENT:
        raise ValueError(f"exchange must be one of {sorted(_EXCHANGE_TO_SEGMENT)}.")
    db()
    key = (name, ex, _today())
    cached = _underlying_cache.get(key)
    if cached is not None:
        return dict(cached)
    by_seg = {str(r["exchange_segment"]): r for r in _option_listings(name)}
    if not by_seg:
        hint = (
            " It exists in the master but has no live option contracts."
            if resolve_symbol(name)
            else ""
        )
        raise InstrumentNotFound(
            f"{underlying!r} is not an option underlying on NSE, BSE or MCX.{hint} Use the "
            "exchange ticker, e.g. NIFTY, BANKNIFTY, SENSEX, RELIANCE, BAJAJ-AUTO, GOLD, CRUDEOIL."
        )
    if ex:
        seg = _EXCHANGE_TO_SEGMENT[ex]
        if seg not in by_seg:
            have = [k for k, v in _EXCHANGE_TO_SEGMENT.items() if v in by_seg]
            raise InstrumentNotFound(f"{name} has no options on {ex}; listed on {', '.join(have)}.")
    else:
        seg = next(s for s in _SEGMENT_PREFERENCE if s in by_seg)
    row = by_seg[seg]
    inst = str(row["instrument"])
    exch = next(k for k, v in _EXCHANGE_TO_SEGMENT.items() if v == seg)
    scrip: str | None
    if seg == "MCX_COMM":
        scrip, useg = row["underlying_security_id"], "MCX_COMM"
        kind = "index" if inst == "OPTIDX" else "commodity"
    elif inst == "OPTIDX":
        scrip, useg, kind = _index_id(name, exch), "IDX_I", "index"
        if not scrip:
            raise InstrumentNotFound(
                f"{name} options are listed on {exch} but the master has no IDX_I index row for "
                "it, so Dhan's option-chain APIs cannot address it. Use find_option_contracts "
                "and ltp on the contracts instead."
            )
    else:
        scrip, useg, kind = row["underlying_security_id"], f"{exch}_EQ", "stock"
    if not scrip:
        raise InstrumentNotFound(
            f"{name}: the master carries no underlying id for its {inst} contracts on {seg}."
        )
    out: Row = {
        "name": name,
        "scrip": int(scrip),
        "seg": useg,
        "exchange": exch,
        "contract_segment": seg,
        "contract_instrument": inst,
        "kind": kind,
        "nearest_expiry": row["nearest"] or None,
    }
    if len(_underlying_cache) > 500:
        _underlying_cache.clear()
    _underlying_cache[key] = out
    return dict(out)


def find_option_contracts(
    underlying: str,
    expiry_date: str | None = None,
    option_type: str | None = None,
    strike_price: float | None = None,
    limit: int = 50,
    exchange: str | None = None,
    include_expired: bool = False,
) -> list[Row]:
    """Option contracts of an underlying from the master (no Dhan call).

    Filter by expiry_date YYYY-MM-DD, option_type CE|PE, strike_price and exchange NSE|BSE|MCX.
    Rows carry security_id, exchange_segment, expiry_date, strike_price, option_type, lot_size.
    """
    u: Row | None
    try:
        u = resolve_underlying(underlying, exchange)
    except InstrumentNotFound:
        if not include_expired:
            return []
        u = None
    names = _names(canonical(underlying))
    sql = (
        "SELECT * FROM instruments WHERE instrument IN ('OPTIDX','OPTSTK','OPTFUT') AND "
        f"upper(underlying_symbol) IN ({','.join('?' * len(names))})"
    )
    args: list[Any] = [*names]
    if u:
        sql += " AND exchange_segment = ?"
        args.append(u["contract_segment"])
    if expiry_date:
        sql += " AND expiry_date = ?"
        args.append(expiry_date)
    if option_type:
        sql += " AND upper(option_type) = ?"
        args.append(option_type.upper())
    if strike_price is not None:
        sql += " AND abs(CAST(strike_price AS REAL) - ?) < 0.001"
        args.append(float(strike_price))
    live, largs = _live_sql(include_expired)
    sql += live
    args += largs
    sql += " ORDER BY expiry_date, CAST(strike_price AS REAL), option_type LIMIT ?"
    args.append(max(1, min(int(limit), 2000)))
    return _rows(db().execute(sql, args))


def futures_contracts(underlying: str, exchange: str | None = None) -> list[Row]:
    """Live futures of an underlying (index, stock or MCX commodity), nearest expiry first."""
    names = _names(canonical(underlying))
    ex = (exchange or "").strip().upper() or None
    if ex and ex not in _EXCHANGE_TO_SEGMENT:
        raise ValueError(f"exchange must be one of {sorted(_EXCHANGE_TO_SEGMENT)}.")
    sql = (
        "SELECT * FROM instruments WHERE instrument IN ('FUTIDX','FUTSTK','FUTCOM') AND "
        f"upper(underlying_symbol) IN ({','.join('?' * len(names))})"
    )
    args: list[Any] = [*names]
    if ex:
        sql += " AND exchange_segment = ?"
        args.append(_EXCHANGE_TO_SEGMENT[ex])
    live, largs = _live_sql(False)
    sql += live
    args += largs
    sql += (
        " ORDER BY CASE exchange_segment WHEN 'NSE_FNO' THEN 0 WHEN 'BSE_FNO' THEN 1 ELSE 2 END,"
        " expiry_date"
    )
    return _rows(db().execute(sql, args))


def instrument_for(security_id: int | str, exchange_segment: str) -> str:
    """Chart `instrument` enum (EQUITY, INDEX, FUTIDX, OPTSTK, FUTCOM, OPTFUT) from the master."""
    seg = check_segment(exchange_segment)
    r = (
        db()
        .execute(
            "SELECT instrument FROM instruments WHERE security_id = ? AND exchange_segment = ? "
            "LIMIT 1",
            [str(security_id), seg],
        )
        .fetchone()
    )
    if r and r["instrument"]:
        return str(r["instrument"])
    if seg.endswith("_EQ"):
        return "EQUITY"
    if seg == "IDX_I":
        return "INDEX"
    raise InstrumentNotFound(
        f"Cannot derive the chart instrument for security_id={security_id} on {seg}: not in the "
        "scrip master. Pass chart_instrument= explicitly (OPTIDX/OPTSTK/FUTIDX/FUTSTK/FUTCOM/"
        "OPTFUT)."
    )


def contract_info(security_id: int | str, exchange_segment: str | None = None) -> Row | None:
    """Master row of a derivative contract id, or None; raises if the id is on several segments."""
    sql = (
        "SELECT * FROM instruments WHERE security_id = ? AND instrument IN "
        "('OPTIDX','OPTSTK','OPTFUT','FUTIDX','FUTSTK','FUTCOM')"
    )
    args: list[Any] = [str(security_id)]
    if exchange_segment:
        sql += " AND exchange_segment = ?"
        args.append(check_segment(exchange_segment))
    rows = db().execute(sql, args).fetchall()
    if not rows:
        return None
    segs = {str(r["exchange_segment"]) for r in rows}
    if len(segs) > 1:
        raise ValueError(
            f"security_id {security_id} exists on {sorted(segs)}: pass exchange_segment (or "
            "underlying) to disambiguate."
        )
    return _row(rows[0])


def list_underlyings(kind: str = "all") -> list[str]:
    """Underlying names with live options: kind = all | index | stock | commodity."""
    filters = {
        "all": "instrument IN ('OPTIDX','OPTSTK','OPTFUT')",
        "index": "instrument = 'OPTIDX' AND exchange_segment != 'MCX_COMM'",
        "stock": "instrument = 'OPTSTK'",
        "commodity": "exchange_segment = 'MCX_COMM' AND instrument IN ('OPTFUT','OPTIDX')",
    }
    if kind not in filters:
        raise ValueError(f"kind must be one of {sorted(filters)}")
    live, largs = _live_sql(False)
    cur = db().execute(
        f"SELECT DISTINCT underlying_symbol FROM instruments WHERE {filters[kind]}{live} "
        "ORDER BY underlying_symbol",
        largs,
    )
    return [str(r[0]) for r in cur.fetchall() if r[0]]


def by_id(security_id: int | str, exchange_segment: str) -> Row | None:
    """Master row for a security id on a segment (any instrument type), or None."""
    r = (
        db()
        .execute(
            "SELECT * FROM instruments WHERE security_id = ? AND exchange_segment = ? LIMIT 1",
            [str(security_id), check_segment(exchange_segment)],
        )
        .fetchone()
    )
    return _row(r)
