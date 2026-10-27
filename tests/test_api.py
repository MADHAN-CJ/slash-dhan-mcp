from __future__ import annotations

from types import ModuleType

import pytest

from slashdhan.errors import InstrumentNotFound
from slashdhan.schema import reference

from .fake_dhan import BASE_TS, FakeDhan


def _csv_downloads(fake: FakeDhan) -> int:
    return sum(1 for c in fake.calls if c.path == "/scrip-master.csv")


def test_account_reads(z: ModuleType) -> None:
    assert z.positions()[0]["tradingSymbol"] == "RELIANCE"
    assert z.holdings()[0]["lastTradedPrice"] == 3902.0
    assert z.funds()["availabelBalance"] == 125000.75
    assert z.orders()[0]["orderStatus"] == "TRADED"
    assert z.trades()[0]["tradedQuantity"] == 5
    assert z.profile()["dataPlan"] == "Active"
    open_pos = z.position_for(45623)
    assert open_pos["_source"] == "positions" and open_pos["closed"] is False
    assert z.position_for("1594")["closed"] is True
    assert z.position_for(2885, "NSE_EQ", "CNC") is None
    assert z.position_for(11536)["_source"] == "holdings"
    assert z.position_for(99999) is None


def test_ltp_forms_and_batching(z: ModuleType, fake: FakeDhan) -> None:
    assert z.ltp({"NSE_EQ": [2885, "11536"], "IDX_I": [13]}) == {
        "NSE_EQ": {"2885": 985.0, "11536": 636.0},
        "IDX_I": {"13": 113.0},
    }
    assert fake.bodies("/marketfeed/ltp")[-1] == {"NSE_EQ": [2885, 11536], "IDX_I": [13]}
    assert z.ltp(["TCS", "nifty 50", "GOLD", "NSE_EQ:2885"]) == {
        "NSE_EQ": {"11536": 636.0, "2885": 985.0},
        "IDX_I": {"13": 113.0},
        "MCX_COMM": {"483079": 179.0},
    }
    assert z.ltp(security_id=13, exchange_segment="IDX_I") == {"IDX_I": {"13": 113.0}}
    assert z.ltp_of("TCS") == 636.0 and z.ltp_of(13, "IDX_I") == 113.0
    with pytest.raises(ValueError):
        z.ltp(security_id=13)
    with pytest.raises(ValueError):
        z.ltp({"NSE_CURRENCY": [1229]})
    with pytest.raises(InstrumentNotFound):
        z.ltp(["NOPE"])
    fake.calls.clear()
    z.ltp({"NSE_EQ": list(range(1, 1501))})
    assert [sum(len(v) for v in b.values()) for b in fake.bodies("/marketfeed/ltp")] == [1000, 500]
    assert _csv_downloads(fake) == 0


def test_quote_and_ohlc(z: ModuleType) -> None:
    q = z.quote({"NSE_EQ": [2885]})["NSE_EQ"]["2885"]
    assert q["ohlc"]["high"] == 101 and q["depth"]["buy"][0]["quantity"] == 100
    assert q["upper_circuit_limit"] == pytest.approx(985.0 * 1.1)
    o = z.ohlc(["TCS"])["NSE_EQ"]["11536"]
    assert o["last_price"] == 636.0 and o["ohlc"]["close"] == 100


def test_candles_normalise_and_chunk(z: ModuleType, fake: FakeDhan) -> None:
    c = z.intraday_candles("RELIANCE", interval=15, days_back=2)
    assert len(c) == 5 and c[0]["time"] == "2026-09-10T09:15:00+05:30"
    assert c[0]["timestamp"] == BASE_TS and "oi" not in c[0]
    body = fake.bodies("/charts/intraday")[-1]
    assert body["instrument"] == "EQUITY" and body["interval"] == "15" and body["oi"] is False
    assert body["fromDate"] == "2026-09-08" and body["toDate"] == "2026-09-11"
    assert body["securityId"] == "2885" and body["exchangeSegment"] == "NSE_EQ"

    d = z.daily_candles(13, "IDX_I", days_back=30)
    body = fake.bodies("/charts/historical")[-1]
    assert body["instrument"] == "INDEX" and d[-1]["close"] == 104.5
    assert d[0]["time"] == "2026-09-10T09:15:00+05:30"
    z.daily_candles("NIFTY", expiry_code=1, chart_instrument="FUTIDX")
    body = fake.bodies("/charts/historical")[-1]
    assert body["expiryCode"] == 1 and body["instrument"] == "FUTIDX" and body["oi"] is True

    o = z.intraday_candles(45623, "NSE_FNO", "5m")
    body = fake.bodies("/charts/intraday")[-1]
    assert body["instrument"] == "OPTIDX" and body["oi"] is True and body["interval"] == "5"
    assert o[0]["oi"] == 5000
    assert z.intraday_candles("GOLD", interval="1h")[0]["oi"] == 5000
    assert fake.bodies("/charts/intraday")[-1]["exchangeSegment"] == "MCX_COMM"

    fake.calls.clear()
    bars = z.intraday_candles("TCS", interval=1, days_back=200)
    chunks = fake.bodies("/charts/intraday")
    assert len(chunks) == 3
    assert chunks[0]["fromDate"] == "2026-02-22" and chunks[0]["toDate"] == "2026-05-23"
    assert chunks[-1]["toDate"] == "2026-09-11"
    assert len(bars) == 5

    with pytest.raises(ValueError):
        z.intraday_candles("TCS", interval=3)
    with pytest.raises(ValueError):
        z.intraday_candles("TCS", interval="day")
    with pytest.raises(ValueError):
        z.daily_candles("TCS", from_date="2026-09-10", to_date="2026-09-01")
    with pytest.raises(ValueError):
        z.intraday_candles(2885)
    with pytest.raises(InstrumentNotFound):
        z.intraday_candles("NOPE")


def test_option_lookups(z: ModuleType, fake: FakeDhan) -> None:
    assert z.expiry_list("nifty 50") == ["2026-09-11", "2026-09-18", "2026-09-25", "2026-10-30"]
    assert fake.bodies("/optionchain/expirylist")[-1] == {
        "UnderlyingScrip": 13,
        "UnderlyingSeg": "IDX_I",
    }
    assert z.nearest_expiry("NIFTY") == "2026-09-11"
    assert z.expiry_list("RELIANCE", exchange="BSE")
    assert fake.bodies("/optionchain/expirylist")[-1] == {
        "UnderlyingScrip": 500325,
        "UnderlyingSeg": "BSE_EQ",
    }
    z.expiry_list("GOLD")
    assert fake.bodies("/optionchain/expirylist")[-1] == {
        "UnderlyingScrip": 114,
        "UnderlyingSeg": "MCX_COMM",
    }
    assert z.resolve_underlying("MCXBULLDEX")["kind"] == "index"
    assert (
        z.resolve_underlying("SENSEX")["seg"] == "IDX_I"
        and z.resolve_underlying("SENSEX")["scrip"] == 51
    )
    with pytest.raises(InstrumentNotFound, match="IDX_I"):
        z.resolve_underlying("NIFTYFPI")
    with pytest.raises(InstrumentNotFound):
        z.resolve_underlying("TCS")
    with pytest.raises(InstrumentNotFound, match="no options on MCX"):
        z.resolve_underlying("RELIANCE", exchange="MCX")
    rows = z.find_option_contracts(
        "NIFTY", expiry_date="2026-09-25", option_type="CE", strike_price=24700
    )
    assert [r["security_id"] for r in rows] == ["45623"] and rows[0]["lot_size"] == 75
    assert z.find_option_contracts("TCS") == []
    assert [f["security_id"] for f in z.futures_list("GOLD")] == ["483079", "495213"]
    assert z.futures_list("NIFTY")[0]["security_id"] == "45700"
    assert z.list_underlyings("index") == ["BANKNIFTY", "NIFTY", "NIFTYFPI", "SENSEX", "SENSEX50"]
    assert z.list_underlyings("stock") == ["RELIANCE"]
    assert z.list_underlyings("commodity") == ["CRUDEOIL", "GOLD", "MCXBULLDEX"]


def test_option_chain_trims_around_atm(z: ModuleType, fake: FakeDhan) -> None:
    ch = z.option_chain("NIFTY", expiry="2026-09-25", strikes_around=3)
    assert ch["expiry"] == "2026-09-25" and ch["underlyingLastPrice"] == 24712.35
    assert ch["contractSegment"] == "NSE_FNO" and ch["lot_size"] == 75
    assert [s["strike"] for s in ch["strikes"]] == [
        24400.0,
        24500.0,
        24600.0,
        24700.0,
        24800.0,
        24900.0,
        25000.0,
    ]
    ce = ch["strikes"][3]["ce"]
    assert ce["securityId"] == 45247 and ce["delta"] == 0.5 and ce["bidQty"] == 75
    assert ch["pcr"] is not None and ch["max_pain"] in [s["strike"] for s in ch["strikes"]]
    assert fake.bodies("/optionchain")[-1] == {
        "UnderlyingScrip": 13,
        "UnderlyingSeg": "IDX_I",
        "Expiry": "2026-09-25",
    }
    ch2 = z.option_chain("RELIANCE")
    assert (
        fake.bodies("/optionchain")[-1]["UnderlyingSeg"] == "NSE_EQ"
        and ch2["expiry"] == "2026-09-11"
    )
    assert len(z.option_chain("GOLD", strikes_around=None)["strikes"]) == 15
    assert fake.bodies("/optionchain")[-1]["UnderlyingScrip"] == 114


def test_option_candles_and_expired(z: ModuleType, fake: FakeDhan) -> None:
    c = z.option_candles(45623, interval=5, days_back=1)
    b = fake.bodies("/charts/intraday")[-1]
    assert b["exchangeSegment"] == "NSE_FNO" and b["instrument"] == "OPTIDX" and b["oi"] is True
    assert c[0]["oi"] == 5000
    z.option_candles(99, "GOLD")
    assert fake.bodies("/charts/intraday")[-1]["instrument"] == "OPTFUT"
    with pytest.raises(InstrumentNotFound):
        z.option_candles(99)
    r = z.expired_options("SENSEX", "PUT", "2026-09-01", "2026-09-05")
    b = fake.bodies("/charts/rollingoption")[-1]
    assert b["exchangeSegment"] == "BSE_FNO" and b["securityId"] == "51"
    assert b["drvOptionType"] == "PUT" and b["expiryCode"] == 1 and b["expiryFlag"] == "WEEK"
    assert r["bars"][0] == {
        "time": "2026-09-10T09:15:00+05:30",
        "timestamp": BASE_TS,
        "close": 10.0,
        "iv": 12.0,
        "oi": 100.0,
        "strike": 24700.0,
        "spot": 24710.0,
    }
    assert z.expired_options("GOLD", "CALL", "2026-09-01", "2026-09-05")["expiryFlag"] == "MONTH"
    with pytest.raises(ValueError, match="7 days"):
        z.expired_options("NIFTY", "CALL", "2026-09-01", "2026-09-20")
    with pytest.raises(ValueError, match="expiry_code"):
        z.expired_options("NIFTY", "CALL", "2026-09-01", "2026-09-03", expiry_code=0)


def test_symbol_resolution(z: ModuleType, fake: FakeDhan) -> None:
    r = z.resolve_symbol("tcs")
    assert (
        r["security_id"] == "11536"
        and r["exchange_segment"] == "NSE_EQ"
        and r["isin"] == "INE467B01029"
    )
    assert z.resolve_symbol("TCS", "BSE_EQ")["security_id"] == "532540"
    assert (
        z.resolve_symbol("NIFTY")["security_id"] == "13"
        and z.resolve_symbol("NIFTY")["kind"] == "index"
    )
    assert z.resolve_symbol("Bank Nifty")["security_id"] == "25"
    assert z.resolve_symbol("SENSEX")["exchange_segment"] == "IDX_I"
    gold = z.resolve_symbol("GOLD")
    assert gold["security_id"] == "483079" and gold["expiry_date"] == "2026-10-05"
    assert z.resolve_symbol("SILVER")["exchange_segment"] == "MCX_COMM"
    assert z.resolve_symbol("SILVER", kind="equity")["security_id"] == "8003"
    assert z.resolve_symbol("NIFTY", kind="future")["security_id"] == "45700"
    assert z.resolve_symbol("USDINR") is None and z.resolve_symbol("NOPE") is None
    with pytest.raises(ValueError):
        z.resolve_symbol("TCS", kind="option")
    assert z.instrument("NSE_EQ:2885")["symbol"] == "RELIANCE"
    assert z.instrument(45623, "NSE_FNO")["strike_price"] == 24700.0
    assert z.instrument_for(13, "IDX_I") == "INDEX" and z.instrument_for(1, "BSE_EQ") == "EQUITY"
    with pytest.raises(InstrumentNotFound):
        z.instrument_for(1, "MCX_COMM")
    assert z.contract_info(580006)["instrument"] == "OPTFUT"
    assert z.contract_info(1) is None
    assert _csv_downloads(fake) == 1


def test_search_hides_expired_by_default(z: ModuleType) -> None:
    assert z.search_instruments("GOLD SEP") == []
    assert z.search_instruments("GOLD SEP", include_expired=True)[0]["expiry_date"] == "2026-09-05"
    s = z.search_instruments("NIFTY", instrument="FUTIDX")
    assert s[0]["security_id"] == "45700" and s[0]["expiry_date"] == "2026-09-25"
    assert z.search_instruments("RELIANCE", exchange_segment="BSE_EQ")[0]["security_id"] == "500325"
    status = z.master_status()
    assert status["rows"] == 32 and status["skipped"] == {"BSE:C": 1, "NSE:C": 1, "NSE:M": 1}
    assert status["segments"] == ["BSE_EQ", "BSE_FNO", "IDX_I", "MCX_COMM", "NSE_EQ", "NSE_FNO"]
    with pytest.raises(ValueError, match="not available on Dhan"):
        z.check_segment("NSE_CURRENCY")
    with pytest.raises(ValueError, match="Unknown exchange_segment"):
        z.check_segment("NASDAQ")


def test_market_status_and_time(z: ModuleType) -> None:
    s = z.market_status("2026-09-10 10:00")
    assert s["markets"]["NSE"]["open"] and s["markets"]["MCX"]["open"] and s["open"]
    s = z.market_status("2026-09-10 16:00:00")
    assert not s["markets"]["NSE"]["open"] and s["markets"]["MCX"]["open"]
    s = z.market_status("2026-09-12 10:00")
    assert not s["open"]
    with pytest.raises(ValueError):
        z.market_status("tomorrow")
    assert z.today() == "2026-09-10" and z.days_ago(5) == "2026-09-05"
    assert z.tomorrow() == "2026-09-11"
    assert z.now().startswith("2026-09-10 12:00")
    assert z.to_ist(BASE_TS).hour == 9 and z.ymd(z.to_ist(BASE_TS)) == "2026-09-10"


def test_indicators(z: ModuleType) -> None:
    assert z.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    e = z.ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0
    assert z.rsi(list(range(1, 20)), 14)[-1] == 100.0
    m = z.macd([float(i) for i in range(60)])
    assert set(m) == {"macd", "signal", "histogram"} and m["histogram"][-1] is not None
    candles = [{"high": 10 + i, "low": 9 + i, "close": 9.5 + i, "volume": 100} for i in range(20)]
    assert z.atr(candles, 14)[-1] is not None and z.vwap(candles)[0] == pytest.approx(9.5)
    assert z.pct_change([100.0, 110.0]) == [None, pytest.approx(10.0)]
    assert z.closes(candles)[0] == 9.5 and z.is_nan(None)


def test_schema_covers_every_function(z: ModuleType) -> None:
    text = reference(brief=True)
    for name in z.__all__:
        obj = getattr(z, name)
        if callable(obj) and not isinstance(obj, type):
            assert f"- {name}(" in text, name
    assert "Row" not in text and "Literal" not in text
    assert len(text) < 11000, len(text)
    assert "option_chain(" in z.help_api()


def test_pp_truncates(z: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    z.pp({"a": "x" * 50}, limit=20)
    out = capsys.readouterr().out
    assert "more chars" in out and len(out) < 80
