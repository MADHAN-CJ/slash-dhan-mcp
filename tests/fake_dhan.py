"""In-process stand-in for api.dhan.co (and the scrip-master CSV), served through httpx.MockTransport."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

from slashdhan.config import IST

BASE_URL = "https://fake.dhan.test"
CSV_URL = f"{BASE_URL}/scrip-master.csv"
CLIENT_ID = "1000000001"
ACCESS_TOKEN = "test-token-abc"
TODAY = date(2026, 9, 10)
BASE_TS = int(datetime(2026, 9, 10, 9, 15, tzinfo=IST).timestamp())

CSV = """EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,BRACKET_FLAG,COVER_FLAG,ASM_GSM_FLAG,ASM_GSM_CATEGORY,BUY_SELL_INDICATOR,BUY_CO_MIN_MARGIN_PER,BUY_CO_SL_RANGE_MAX_PERC,BUY_CO_SL_RANGE_MIN_PERC,BUY_BO_MIN_MARGIN_PER,BUY_BO_PROFIT_RANGE_MAX_PERC,BUY_BO_PROFIT_RANGE_MIN_PERC,MTF_LEVERAGE,SM_UPPER_LIMIT,SM_LOWER_LIMIT,SM_FREEZE_QTY,
NSE,E,2885,INE002A01018,EQUITY,2885,RELIANCE,RELIANCE INDUSTRIES LTD,RELIANCE,ES,EQ,1,NA,-0.01000,XX,0.05,NA,Y,Y,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,E,11536,INE467B01029,EQUITY,11536,TCS,TATA CONSULTANCY SERV LT,TCS,ES,EQ,1,NA,-0.01000,XX,0.05,NA,Y,Y,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,E,532540,INE467B01029,EQUITY,532540,TCS,TATA CONSULTANCY SERV LT,TCS,ES,A,1,NA,-0.01000,XX,0.05,NA,Y,Y,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,I,13,NA,INDEX,13,NIFTY,NIFTY 50,NIFTY 50,INDEX,NA,0,NA,-0.01000,XX,0.05,NA,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,45623,NA,OPTIDX,26000,NIFTY,NIFTY,NIFTY 25 SEP 24700 CALL,OP,NA,75,2026-09-25,24700.000000,CE,0.05,W,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,45624,NA,OPTIDX,26000,NIFTY,NIFTY,NIFTY 25 SEP 24700 PUT,OP,NA,75,2026-09-25,24700.000000,PE,0.05,W,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,45700,NA,FUTIDX,26000,NIFTY,NIFTY,NIFTY 25 SEP FUT,FUT,NA,75,2026-09-25,-0.01000,XX,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,I,25,NA,INDEX,25,BANKNIFTY,NIFTY BANK,Nifty Bank,INDEX,NA,0,0001-01-01,-0.01000,XX,0.05,N,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,I,51,NA,INDEX,1,SENSEX,SENSEX,Sensex,INDEX,NA,0,0001-01-01,-0.01000,XX,0.05,N,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,I,83,NA,INDEX,47,SNSX50,SNSX50,BSE Sensex 50,INDEX,NA,0,0001-01-01,-0.01000,XX,0.05,N,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,I,850,NA,INDEX,75,FOCIT,FOCIT,BSE Focused IT,INDEX,NA,0,0001-01-01,-0.01000,XX,0.05,N,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,46001,NA,OPTIDX,26009,BANKNIFTY,BANKNIFTY,BANKNIFTY 30 SEP 52000 CALL,OP,NA,30,2026-09-30,52000.000000,CE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,D,1130001,NA,OPTIDX,1,SENSEX,SENSEX,SENSEX 25 SEP 81000 CALL,OPTIDX,NA,20,2026-09-25,81000.000000,CE,0.05,W,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,D,1130002,NA,OPTIDX,47,SENSEX50,SENSEX50,SENSEX50 30 OCT 25000 CALL,OPTIDX,NA,60,2026-10-30,25000.000000,CE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,49001,NA,OPTSTK,2885,RELIANCE,RELIANCE,RELIANCE 27 OCT 1200 CALL,OP,NA,500,2026-10-27,1200.000000,CE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,49002,NA,OPTSTK,2885,RELIANCE,RELIANCE,RELIANCE 27 OCT 1200 PUT,OP,NA,500,2026-10-27,1200.000000,PE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,47001,NA,OPTIDX,26137,NIFTYFPI,NIFTYFPI,NIFTYFPI 30 OCT 1000 CALL,OP,NA,100,2026-10-30,1000.000000,CE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,E,500325,INE002A01018,EQUITY,500325,RELIANCE,RELIANCE INDUSTRIES LTD.,Reliance Industries,ES,A,1,,-0.01000,XX,5,NA,Y,Y,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,D,48987,NA,FUTSTK,2885,RELIANCE,RELIANCE,RELIANCE OCT FUT,FUT,NA,500,2026-10-27,-0.01000,XX,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,D,1141132,NA,OPTSTK,500325,RELIANCE,RELIANCE,RELIANCE 29 OCT 1150 CALL,OPTSTK,NA,500,2026-10-29,1150.000000,CE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,D,1141133,NA,OPTSTK,500325,RELIANCE,RELIANCE,RELIANCE 29 OCT 1150 PUT,OPTSTK,NA,500,2026-10-29,1150.000000,PE,0.05,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,E,8003,INE0IBA07019,EQUITY,8003,SILVER,ADITYA BIRLA SL SILVER ETF,Aditya Birla Silver ETF,ETF,EQ,1,,-0.01000,XX,0.01,NA,Y,Y,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,470001,NA,FUTCOM,114,GOLD,GOLD,GOLD SEP FUT,FUTCOM,NA,1,2026-09-05,-0.01000,XX,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,483079,NA,FUTCOM,114,GOLD,GOLD,GOLD OCT FUT,FUTCOM,NA,1,2026-10-05,-0.01000,XX,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,495213,NA,FUTCOM,114,GOLD,GOLD,GOLD DEC FUT,FUTCOM,NA,1,2026-12-04,-0.01000,XX,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,470100,NA,FUTCOM,115,SILVER,SILVER,SILVER DEC FUT,FUTCOM,NA,1,2026-12-04,-0.01000,XX,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,569900,NA,FUTCOM,294,CRUDEOIL,CRUDEOIL,CRUDEOIL OCT FUT,FUTCOM,NA,1,2026-10-19,-0.01000,XX,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,580006,NA,OPTFUT,114,GOLD,GOLD,GOLD 30 OCT 116500 CALL,OPTFUT,NA,1,2026-10-30,116500.00000,CE,0.5,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,580007,NA,OPTFUT,114,GOLD,GOLD,GOLD 30 OCT 116500 PUT,OPTFUT,NA,1,2026-10-30,116500.00000,PE,0.5,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,580476,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 15 OCT 8850 CALL,OPTFUT,NA,1,2026-10-15,8850.00000,CE,0.1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,580246,NA,FUTIDX,568,MCXBULLDEX,MCXBULLDEX,MCXBULLDEX OCT FUT,FUTIDX,NA,1,2026-10-28,-0.01000,XX,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
MCX,M,590001,NA,OPTIDX,568,MCXBULLDEX,MCXBULLDEX,MCXBULLDEX 28 OCT 20000 CALL,OPTIDX,NA,1,2026-10-28,20000.00000,CE,1,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,C,1229,NA,FUTCUR,1,USDINR,USDINR,USDINR JUL FUT,FUTCUR,NA,1,2025-07-29,-0.01000,XX,0.0025,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
BSE,C,1072211,NA,FUTCUR,1,USDINR,USDINR,USDINR JUL FUT,FUTCUR,NA,1,2025-07-29,-0.01000,XX,0.0025,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
NSE,M,121647,NA,OPTFUT,1,GOLD,GOLD,GOLD 29 OCT 125000 CALL,OPTFUT,NA,1,2026-10-29,125000.00000,CE,0.5,M,N,N,N,NA,A,0,0,0,0,0,0,0,0,0,0,
"""

CHAIN_UNDERLYINGS = {
    (13, "IDX_I"),
    (25, "IDX_I"),
    (51, "IDX_I"),
    (83, "IDX_I"),
    (850, "IDX_I"),
    (2885, "NSE_EQ"),
    (500325, "BSE_EQ"),
    (114, "MCX_COMM"),
    (294, "MCX_COMM"),
    (568, "MCX_COMM"),
}

PROFILE = {
    "dhanClientId": CLIENT_ID,
    "tokenValidity": "11/09/2026 09:00",
    "activeSegment": "Equity, Derivative, Commodity",
    "ddpi": "Active",
    "mtf": "Active",
    "dataPlan": "Active",
    "dataValidity": "30/09/2026",
}


@dataclass
class Call:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


def _chain() -> dict[str, Any]:
    oc: dict[str, Any] = {}
    for k in range(24000, 25500, 100):
        oc[f"{k}.000000"] = {
            "ce": {
                "greeks": {"delta": 0.5, "theta": -3.1, "gamma": 0.001, "vega": 12.0},
                "implied_volatility": 12.5,
                "last_price": max(1.0, 24700 - k + 80),
                "oi": 1000 + k,
                "previous_oi": 900 + k,
                "volume": 50,
                "top_bid_price": 1.0,
                "top_ask_price": 1.2,
                "top_bid_quantity": 75,
                "top_ask_quantity": 150,
                "security_id": 45000 + k // 100,
                "average_price": 1.1,
                "previous_close_price": 1.0,
            },
            "pe": {
                "greeks": {"delta": -0.5},
                "implied_volatility": 13.0,
                "last_price": max(1.0, k - 24700 + 80),
                "oi": 2000 + k,
                "previous_oi": 1900 + k,
                "volume": 60,
                "security_id": 46000 + k // 100,
            },
        }
    return {"last_price": 24712.35, "oc": oc}


def _quote(sid: int) -> dict[str, Any]:
    price = 100.0 + sid % 1000
    return {
        "last_price": price,
        "volume": 12345,
        "average_price": price - 0.5,
        "net_change": 1.25,
        "buy_quantity": 500,
        "sell_quantity": 700,
        "ohlc": {"open": 99, "high": 101, "low": 98, "close": 100},
        "last_trade_time": "2026-09-10 10:00:00",
        "oi": 0,
        "oi_day_high": 0,
        "oi_day_low": 0,
        "upper_circuit_limit": price * 1.1,
        "lower_circuit_limit": price * 0.9,
        "depth": {
            "buy": [{"price": price - 0.05, "quantity": 100, "orders": 3}],
            "sell": [{"price": price + 0.05, "quantity": 120, "orders": 2}],
        },
    }


@dataclass
class FakeDhan:
    mode: str = "normal"
    rate_limit_next: bool = False
    token: str | None = None
    prices: dict[str, float] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def price(self, seg: str, sid: Any) -> float:
        return self.prices.get(f"{seg}:{sid}", 100.0 + int(sid) % 1000)

    def bodies(self, path: str) -> list[Any]:
        return [c.body for c in self.calls if c.path == path]

    def paths(self) -> list[str]:
        return [c.path for c in self.calls]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append(Call(request.method, path, dict(request.headers), body))
        if path == "/scrip-master.csv":
            return httpx.Response(200, text=CSV, headers={"content-type": "text/csv"})
        if self.rate_limit_next:
            self.rate_limit_next = False
            return httpx.Response(
                429,
                json={
                    "errorType": "Rate_Limit",
                    "errorCode": "DH-904",
                    "errorMessage": "Too many requests",
                },
            )
        data_api = path.startswith(("/marketfeed", "/charts", "/optionchain"))
        bad_token = self.token is not None and request.headers.get("access-token") != self.token
        if self.mode == "token_dead" or bad_token:
            return httpx.Response(
                401,
                json={
                    "errorType": "Invalid_Authentication",
                    "errorCode": "DH-901",
                    "errorMessage": "Invalid Token",
                },
            )
        if data_api and not request.headers.get("client-id"):
            return httpx.Response(
                401,
                json={
                    "errorType": "Invalid_Authentication",
                    "errorCode": "DH-901",
                    "errorMessage": "client-id header missing",
                },
            )
        if data_api and self.mode == "data_unsubscribed":
            return httpx.Response(
                401, json={"data": {"806": "Data APIs not Subscribed"}, "status": "failure"}
            )
        if path == "/optionchain/expirylist" and self.mode == "endpoint_401":
            return httpx.Response(
                401,
                json={
                    "errorType": "Invalid_Access",
                    "errorCode": "DH-902",
                    "errorMessage": "endpoint denied",
                },
            )
        return self._route(path, body)

    def _route(self, path: str, body: Any) -> httpx.Response:
        if path == "/profile":
            return httpx.Response(200, json=PROFILE)
        if path == "/positions":
            return httpx.Response(
                200,
                json=[
                    {
                        "tradingSymbol": "RELIANCE",
                        "securityId": "2885",
                        "positionType": "LONG",
                        "exchangeSegment": "NSE_EQ",
                        "productType": "INTRADAY",
                        "netQty": 5,
                        "buyAvg": 2900,
                        "sellAvg": 0,
                        "realizedProfit": 0,
                        "unrealizedProfit": 215.5,
                    },
                    {
                        "tradingSymbol": "NIFTY-Sep2026-24700-CE",
                        "securityId": "45623",
                        "positionType": "LONG",
                        "exchangeSegment": "NSE_FNO",
                        "productType": "MARGIN",
                        "netQty": 75,
                        "buyAvg": 182.5,
                        "sellAvg": 0,
                        "realizedProfit": 0,
                        "unrealizedProfit": -412.5,
                    },
                    {
                        "tradingSymbol": "INFY",
                        "securityId": "1594",
                        "positionType": "CLOSED",
                        "exchangeSegment": "NSE_EQ",
                        "productType": "INTRADAY",
                        "netQty": 0,
                        "buyAvg": 1500,
                        "sellAvg": 1510,
                        "realizedProfit": 100,
                        "unrealizedProfit": 0,
                    },
                ],
            )
        if path == "/holdings":
            return httpx.Response(
                200,
                json=[
                    {
                        "exchange": "NSE",
                        "tradingSymbol": "TCS",
                        "securityId": "11536",
                        "isin": "INE467B01029",
                        "totalQty": 10,
                        "availableQty": 10,
                        "avgCostPrice": 3850.5,
                        "lastTradedPrice": 3902.0,
                    }
                ],
            )
        if path == "/fundlimit":
            return httpx.Response(
                200,
                json={
                    "dhanClientId": CLIENT_ID,
                    "availabelBalance": 125000.75,
                    "withdrawableBalance": 120000,
                    "utilizedAmount": 5000.75,
                    "collateralAmount": 0,
                },
            )
        if path == "/orders":
            return httpx.Response(
                200,
                json=[
                    {
                        "orderId": "1",
                        "tradingSymbol": "RELIANCE",
                        "transactionType": "BUY",
                        "quantity": 5,
                        "price": 2900,
                        "orderStatus": "TRADED",
                    }
                ],
            )
        if path == "/trades":
            return httpx.Response(
                200,
                json=[
                    {
                        "orderId": "1",
                        "tradingSymbol": "RELIANCE",
                        "transactionType": "BUY",
                        "tradedQuantity": 5,
                        "tradedPrice": 2900,
                    }
                ],
            )
        if path == "/marketfeed/ltp":
            out: dict[str, Any] = {
                seg: {str(i): {"last_price": self.price(seg, i)} for i in ids}
                for seg, ids in (body or {}).items()
            }
            return httpx.Response(200, json={"data": out, "status": "success"})
        if path == "/marketfeed/ohlc":
            out = {
                seg: {
                    str(i): {
                        "last_price": self.price(seg, i),
                        "ohlc": {"open": 99, "high": 101, "low": 98, "close": 100},
                    }
                    for i in ids
                }
                for seg, ids in (body or {}).items()
            }
            return httpx.Response(200, json={"data": out, "status": "success"})
        if path == "/marketfeed/quote":
            out = {seg: {str(i): _quote(int(i)) for i in ids} for seg, ids in (body or {}).items()}
            return httpx.Response(200, json={"data": out, "status": "success"})
        if path in ("/charts/intraday", "/charts/historical"):
            n = 5
            step = 900 if path.endswith("intraday") else 86400
            payload: dict[str, Any] = {
                "open": [100 + i for i in range(n)],
                "high": [101 + i for i in range(n)],
                "low": [99 + i for i in range(n)],
                "close": [100.5 + i for i in range(n)],
                "volume": [1000 * (i + 1) for i in range(n)],
                "timestamp": [BASE_TS + i * step for i in range(n)],
            }
            if body and body.get("oi"):
                payload["open_interest"] = [5000 + i for i in range(n)]
            return httpx.Response(200, json=payload)
        if path in ("/optionchain/expirylist", "/optionchain"):
            key = (int((body or {}).get("UnderlyingScrip", -1)), (body or {}).get("UnderlyingSeg"))
            if key not in CHAIN_UNDERLYINGS:
                return httpx.Response(
                    400, json={"data": {"813": "Invalid SecurityId"}, "status": "failure"}
                )
            if path.endswith("expirylist"):
                return httpx.Response(
                    200,
                    json={
                        "data": ["2026-09-18", "2026-09-25", "2026-09-11", "2026-10-30"],
                        "status": "success",
                    },
                )
            return httpx.Response(200, json={"data": _chain(), "status": "success"})
        if path == "/charts/rollingoption":
            n = 3
            side = {
                "timestamp": [BASE_TS + i * 3600 for i in range(n)],
                "close": [10 + i for i in range(n)],
                "iv": [12.0 + i for i in range(n)],
                "oi": [100 + i for i in range(n)],
                "strike": [24700] * n,
                "spot": [24710 + i for i in range(n)],
                "open": [],
                "high": [],
                "low": [],
                "volume": [],
            }
            return httpx.Response(200, json={"data": {"ce": side, "pe": side}, "status": "success"})
        return httpx.Response(
            404,
            json={
                "errorType": "Not_Found",
                "errorCode": "DH-907",
                "errorMessage": f"no route {path}",
            },
        )
