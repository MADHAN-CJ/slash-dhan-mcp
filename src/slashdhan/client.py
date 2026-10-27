"""Thin wrappers over the Dhan v2 read endpoints. Returns Dhan's payload (envelope removed)."""

from __future__ import annotations

from typing import Any

from .http import session

LTP_BATCH = 1000

Body = dict[str, Any]


def _list(data: Any) -> list[dict[str, Any]]:
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _dict(data: Any) -> dict[str, Any]:
    return data if isinstance(data, dict) else {}


def profile() -> dict[str, Any]:
    return _dict(session().get("/profile"))


def positions() -> list[dict[str, Any]]:
    return _list(session().get("/positions"))


def holdings() -> list[dict[str, Any]]:
    return _list(session().get("/holdings"))


def fundlimit() -> dict[str, Any]:
    return _dict(session().get("/fundlimit"))


def orders() -> list[dict[str, Any]]:
    return _list(session().get("/orders"))


def trades() -> list[dict[str, Any]]:
    return _list(session().get("/trades"))


def _batched(path: str, targets: dict[str, list[int]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    flat = [(seg, sid) for seg, ids in targets.items() for sid in ids]
    for i in range(0, max(len(flat), 1), LTP_BATCH):
        chunk = flat[i : i + LTP_BATCH]
        body: dict[str, list[int]] = {}
        for seg, sid in chunk:
            body.setdefault(seg, []).append(sid)
        if not body:
            break
        data = _dict(session().post(path, body))
        for seg, rows in data.items():
            if isinstance(rows, dict):
                out.setdefault(seg, {}).update(rows)
    return out


def ltp(targets: dict[str, list[int]]) -> dict[str, Any]:
    return _batched("/marketfeed/ltp", targets)


def ohlc(targets: dict[str, list[int]]) -> dict[str, Any]:
    return _batched("/marketfeed/ohlc", targets)


def quote(targets: dict[str, list[int]]) -> dict[str, Any]:
    return _batched("/marketfeed/quote", targets)


def intraday(body: Body) -> dict[str, Any]:
    return _dict(session().post("/charts/intraday", body))


def historical(body: Body) -> dict[str, Any]:
    return _dict(session().post("/charts/historical", body))


def expiry_list(body: Body) -> list[str]:
    data = session().post("/optionchain/expirylist", body)
    return [str(e) for e in data] if isinstance(data, list) else []


def option_chain(body: Body) -> dict[str, Any]:
    return _dict(session().post("/optionchain", body))


def rolling_option(body: Body) -> dict[str, Any]:
    return _dict(session().post("/charts/rollingoption", body))
