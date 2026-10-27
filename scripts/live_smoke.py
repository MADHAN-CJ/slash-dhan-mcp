"""Live smoke against the real Dhan API with DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN from .env.

Prints compact summaries only, never the token. Run: uv run python scripts/live_smoke.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import slashdhan as z  # noqa: E402
from slashdhan.errors import DataPlanMissing, DhanError  # noqa: E402
from slashdhan.http import session  # noqa: E402

ok = fail = 0


def step(name: str, fn: Callable[[], Any]) -> None:
    global ok, fail
    try:
        out = fn()
        ok += 1
        print(f"ok   {name}: {json.dumps(out, default=str)[:300]}")
    except DataPlanMissing as e:
        fail += 1
        print(f"FAIL {name}: DATA-API 806: {str(e)[:120]}")
    except DhanError as e:
        fail += 1
        print(f"FAIL {name}: DhanError {e.status} {e.code}: {str(e)[:200]}")
    except Exception as e:
        fail += 1
        print(f"FAIL {name}: {e.__class__.__name__}: {str(e)[:200]}")


def chain_summary(underlying: str, **kw: Any) -> dict[str, Any]:
    c = z.option_chain(underlying, strikes_around=2, **kw)
    mid = c["strikes"][len(c["strikes"]) // 2]
    return {
        "spot": c["underlyingLastPrice"],
        "expiry": c["expiry"],
        "seg": c["contractSegment"],
        "lot": c["lot_size"],
        "pcr": c["pcr"],
        "atm_ce": {k: mid["ce"][k] for k in ("ltp", "iv", "oi", "securityId", "delta")},
    }


step(
    "profile",
    lambda: {
        k: z.profile().get(k) for k in ("dhanClientId", "dataPlan", "dataValidity", "tokenValidity")
    },
)
step("market_status", lambda: {m: v["open"] for m, v in z.market_status()["markets"].items()})
step("funds", lambda: {k: z.funds().get(k) for k in ("availabelBalance", "utilizedAmount")})
step("positions", lambda: [(p.get("tradingSymbol"), p.get("netQty")) for p in z.positions()])
step(
    "holdings",
    lambda: [
        (h.get("tradingSymbol"), h.get("totalQty"), h.get("lastTradedPrice")) for h in z.holdings()
    ][:5],
)
step("orders/trades", lambda: (len(z.orders()), len(z.trades())))
step(
    "master_status",
    lambda: {k: z.master_status()[k] for k in ("schema_version", "rows", "skipped")},
)
step(
    "resolve TCS/NIFTY/SENSEX/Bank Nifty",
    lambda: {
        n: (r["security_id"], r["exchange_segment"])
        for n in ("TCS", "NIFTY", "SENSEX", "Bank Nifty")
        if (r := z.resolve_symbol(n))
    },
)
step(
    "resolve GOLD/SILVER/CRUDEOIL -> nearest MCX future",
    lambda: {
        n: (r["security_id"], r["expiry_date"])
        for n in ("GOLD", "SILVER", "CRUDEOIL")
        if (r := z.resolve_symbol(n))
    },
)
step("resolve USDINR -> None", lambda: z.resolve_symbol("USDINR"))
step(
    "search FUTIDX NIFTY",
    lambda: [
        (r["display_name"], r["security_id"])
        for r in z.search_instruments("NIFTY", instrument="FUTIDX", limit=2)
    ],
)
step("ltp symbols", lambda: z.ltp(["RELIANCE", "NIFTY", "GOLD"]))
step("ltp_of TCS", lambda: z.ltp_of("TCS"))
step(
    "quote RELIANCE depth",
    lambda: {
        k: z.quote(["RELIANCE"])["NSE_EQ"]["2885"].get(k) for k in ("last_price", "volume", "oi")
    },
)
step("daily RELIANCE 10d", lambda: z.daily_candles("RELIANCE", days_back=10)[-2:])
step("intraday NIFTY 15m 1d", lambda: z.intraday_candles("NIFTY", interval=15, days_back=1)[-2:])
step(
    "intraday GOLD 60m 1d has oi",
    lambda: {
        "rows": len(c := z.intraday_candles("GOLD", interval=60, days_back=1)),
        "last": c[-1] if c else None,
    },
)
step("expiry_list NIFTY", lambda: z.expiry_list("NIFTY")[:3])
step("expiry_list GOLD (MCX)", lambda: z.expiry_list("GOLD")[:3])
step(
    "expiry_list MCXBULLDEX / FOCIT / SENSEX50",
    lambda: {n: z.expiry_list(n)[:2] for n in ("MCXBULLDEX", "FOCIT", "SENSEX50")},
)
step("option_chain NIFTY", lambda: chain_summary("NIFTY"))
step("option_chain CRUDEOIL", lambda: chain_summary("CRUDEOIL"))
step("option_chain RELIANCE on BSE", lambda: chain_summary("RELIANCE", exchange="BSE"))
step(
    "option_candles ATM CRUDEOIL CE (id from chain)",
    lambda: {
        "rows": len(
            x := z.option_candles(
                z.option_chain("CRUDEOIL", strikes_around=0)["strikes"][0]["ce"]["securityId"],
                interval=60,
                days_back=1,
            )
        ),
        "last": x[-1] if x else None,
    },
)
step(
    "find_option_contracts NIFTY nearest",
    lambda: len(z.find_option_contracts("NIFTY", expiry_date=z.nearest_expiry("NIFTY"), limit=500)),
)
step(
    "futures_list GOLD",
    lambda: [(f["security_id"], f["expiry_date"]) for f in z.futures_list("GOLD")][:3],
)
step(
    "expired_options GOLD 7d",
    lambda: {
        "bars": len(
            (
                r := z.expired_options(
                    "GOLD", "CALL", z.days_ago(9), z.days_ago(2), fields=["close", "iv"]
                )
            )["bars"]
        ),
        "flag": r["expiryFlag"],
    },
)


def _err(fn: Callable[[], Any]) -> str:
    try:
        fn()
    except ValueError as e:
        return str(e)[:80]
    raise AssertionError("expected ValueError")


step("currency segment -> ValueError", lambda: _err(lambda: z.ltp({"NSE_CURRENCY": [1229]})))


print(f"\n{ok} ok, {fail} failed; Dhan calls made: {session().calls}")
sys.exit(1 if fail else 0)
