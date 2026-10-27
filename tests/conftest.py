from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import slashdhan
from slashdhan import http, instruments
from slashdhan.config import settings
from slashdhan.ratelimit import RateLimiter

from .fake_dhan import ACCESS_TOKEN, BASE_URL, CLIENT_ID, CSV_URL, TODAY, FakeDhan


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake() -> FakeDhan:
    return FakeDhan()


@pytest.fixture
def dhan_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DHAN_BASE_URL", BASE_URL)
    monkeypatch.setenv("DHAN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", ACCESS_TOKEN)
    monkeypatch.setenv("SLASHDHAN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("SLASHDHAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SLASHDHAN_SCRIP_MASTER_URL", CSV_URL)
    monkeypatch.setenv("SLASHDHAN_CALL_BUDGET", "40")
    monkeypatch.delenv("SLASHDHAN_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SLASHDHAN_RUN_TOKEN", raising=False)


@pytest.fixture
def z(dhan_env: None, fake: FakeDhan, clock: FakeClock) -> Iterator[ModuleType]:
    limiter = RateLimiter(now=clock.now, sleep=clock.sleep)
    transport = http.DirectTransport(
        CLIENT_ID, ACCESS_TOKEN, settings().base_url, limiter, fake.client()
    )
    http.configure(transport)
    instruments.configure(fake.client())
    instruments.reset()
    yield slashdhan
    instruments.reset()
    instruments.configure(None)
    http.reset()
