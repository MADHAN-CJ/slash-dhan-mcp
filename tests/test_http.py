from __future__ import annotations

from types import ModuleType

import httpx
import pytest

from slashdhan import http
from slashdhan.config import settings
from slashdhan.errors import (
    CallBudgetExceeded,
    DataPlanMissing,
    DhanError,
    RateLimited,
    TokenExpired,
)
from slashdhan.ratelimit import RateLimiter

from .conftest import FakeClock
from .fake_dhan import ACCESS_TOKEN, CLIENT_ID, FakeDhan


def test_direct_transport_sends_dhan_headers(z: ModuleType, fake: FakeDhan) -> None:
    assert z.profile()["dhanClientId"] == CLIENT_ID
    assert z.ltp({"NSE_EQ": [2885]}) == {"NSE_EQ": {"2885": 985.0}}
    for call in fake.calls:
        assert call.headers["access-token"] == ACCESS_TOKEN
        assert call.headers["client-id"] == CLIENT_ID
        assert call.headers["content-type"] == "application/json"
    assert fake.calls[1].method == "POST" and fake.calls[1].body == {"NSE_EQ": [2885]}


def test_dead_token_becomes_token_expired_after_a_probe(z: ModuleType, fake: FakeDhan) -> None:
    fake.mode = "token_dead"
    with pytest.raises(TokenExpired) as e:
        z.positions()
    assert e.value.status == 401 and e.value.code == "DH-901"
    assert "feed token has expired" in str(e.value)
    assert fake.paths() == ["/positions", "/fundlimit"]


def test_endpoint_only_401_is_not_a_dead_token(z: ModuleType, fake: FakeDhan) -> None:
    fake.mode = "endpoint_401"
    with pytest.raises(DhanError) as e:
        z.expiry_list("NIFTY")
    assert not isinstance(e.value, TokenExpired)
    assert "endpoint-specific" in str(e.value) and "endpoint denied" in str(e.value)
    assert fake.paths()[-2:] == ["/optionchain/expirylist", "/fundlimit"]


def test_806_becomes_data_plan_missing_without_a_probe(z: ModuleType, fake: FakeDhan) -> None:
    fake.mode = "data_unsubscribed"
    with pytest.raises(DataPlanMissing):
        z.ltp({"IDX_I": [13]})
    assert "/fundlimit" not in fake.paths()
    assert z.holdings()[0]["tradingSymbol"] == "TCS"
    assert z.ltp_of("TCS") == 3902.0
    with pytest.raises(DataPlanMissing):
        z.ltp_of("NIFTY")


def test_429_raises_and_parks_the_limiter(z: ModuleType, fake: FakeDhan) -> None:
    fake.rate_limit_next = True
    with pytest.raises(RateLimited) as e:
        z.funds()
    assert e.value.code == "DH-904"
    limiter = http.session().transport.limiter  # type: ignore[attr-defined]
    assert limiter.holding_for() >= 4.9


def test_other_errors_keep_status_and_code(z: ModuleType) -> None:
    with pytest.raises(DhanError) as e:
        z.dhan_get("/nope")
    assert e.value.status == 404 and e.value.code == "DH-907"
    with pytest.raises(DhanError) as bad:
        z.dhan_post("/optionchain/expirylist", {"UnderlyingScrip": 1, "UnderlyingSeg": "IDX_I"})
    assert bad.value.status == 400 and bad.value.code == "813"


def test_call_budget_is_enforced(
    dhan_env: None, fake: FakeDhan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLASHDHAN_CALL_BUDGET", "2")
    transport = http.DirectTransport(
        CLIENT_ID, ACCESS_TOKEN, settings().base_url, RateLimiter(), fake.client()
    )
    http.configure(transport)
    try:
        import slashdhan

        slashdhan.funds()
        slashdhan.funds()
        with pytest.raises(CallBudgetExceeded):
            slashdhan.funds()
    finally:
        http.reset()


def test_rate_limiter_buckets_chain_gap_and_hold(clock: FakeClock) -> None:
    limiter = RateLimiter(now=clock.now, sleep=clock.sleep)
    assert limiter.acquire("quote") == 0.0
    limiter.acquire("quote")
    limiter.acquire("quote")
    assert [round(s, 3) for s in clock.slept] == [1.2, 1.2]
    for _ in range(4):
        assert limiter.acquire("data") == 0.0
    assert round(limiter.acquire("data"), 3) == 0.25
    limiter = RateLimiter(now=clock.now, sleep=clock.sleep)
    clock.slept.clear()
    assert limiter.acquire("chain", "IDX_I:13:2026-09-25") == 0.0
    assert limiter.acquire("chain", "IDX_I:13:2026-10-30") == 0.0
    waited = limiter.acquire("chain", "IDX_I:13:2026-09-25")
    assert 2.9 <= waited <= 3.0
    limiter.hold(5)
    assert limiter.acquire("general") >= 5.0
    assert limiter.holding_for() == 0.0


def test_chain_calls_are_throttled_per_underlying_and_expiry(
    z: ModuleType, fake: FakeDhan, clock: FakeClock
) -> None:
    z.option_chain("NIFTY", expiry="2026-09-25", strikes_around=1)
    z.option_chain("NIFTY", expiry="2026-10-30", strikes_around=1)
    assert clock.slept == []
    z.option_chain("NIFTY", expiry="2026-09-25", strikes_around=1)
    assert len(clock.slept) == 1 and 2.9 <= clock.slept[0] <= 3.0


def test_gateway_transport_forwards_through_the_local_gateway() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/dhan/fundlimit":
            return httpx.Response(200, json={"availabelBalance": 1})
        if request.url.path == "/dhan/marketfeed/ltp":
            return httpx.Response(
                200, json={"status": "success", "data": {"NSE_EQ": {"2885": {"last_price": 9}}}}
            )
        return httpx.Response(
            401,
            json={
                "errorType": "Invalid_Authentication",
                "errorCode": "DH-901",
                "errorMessage": "x",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = http.GatewayTransport("http://127.0.0.1:9", "run-token", client)
    session = http.Session(transport, settings())
    assert session.post("/marketfeed/ltp", {"NSE_EQ": [2885]}) == {
        "NSE_EQ": {"2885": {"last_price": 9}}
    }
    assert seen[0].headers["Authorization"] == "Bearer run-token"
    assert seen[0].url.path == "/dhan/marketfeed/ltp" and seen[0].method == "POST"
    assert b"2885" in seen[0].read()
    assert transport.token_alive()
    with pytest.raises(DhanError) as e:
        session.get("/positions")
    assert not isinstance(e.value, TokenExpired)
    assert "endpoint-specific" in str(e.value)
