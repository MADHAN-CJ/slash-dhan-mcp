from __future__ import annotations

from typing import Any

import pytest

from slash_dhan_mcp.analytics import Analytics, clip, host_label, library_calls
from slash_dhan_mcp.config import load_settings


class FakeClient:
    def __init__(self) -> None:
        self.captured: list[tuple[str, dict[str, Any]]] = []
        self.shut = False

    def capture(self, event: str, **kwargs: Any) -> str | None:
        self.captured.append((event, kwargs))
        return "uuid"

    def shutdown(self) -> None:
        self.shut = True


class BrokenClient(FakeClient):
    def capture(self, event: str, **kwargs: Any) -> str | None:
        raise RuntimeError("network down")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    monkeypatch.setenv("SLASH_DHAN_MCP_ADMIN_SECRET", "a" * 40)
    monkeypatch.setenv("CREDS_ENCRYPTION_KEY", "b" * 40)
    monkeypatch.setenv("SLASH_DHAN_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SLASH_DHAN_MCP_PUBLIC_URL", "https://dhan.example.test/")
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    return load_settings()


def test_library_calls_finds_aliases_and_from_imports() -> None:
    code = (
        "import slashdhan as z, json\n"
        "from slashdhan import option_chain, resolve_symbol as rs\n"
        "c = z.ltp('TCS'); z.ltp('INFY')\n"
        "chain = option_chain('NIFTY')\n"
        "ids = slashdhan.search('GOLD')\n"
        "x = z.not_a_call + 1\n"
    )
    assert library_calls(code) == ["ltp", "option_chain", "search"]
    assert library_calls("print('hi')") == []


def test_host_label_prefers_openai_meta_then_user_agent() -> None:
    assert host_label({"openai/locale": "en"}, {"user-agent": "x"}, None)[0] == "chatgpt"
    assert host_label(None, {"user-agent": "Claude-User/1.0"}, None) == (
        "claude",
        "Claude-User/1.0",
        None,
    )
    assert host_label(None, None, "claude-ai 1.2") == ("claude", None, "claude-ai 1.2")
    assert host_label(None, {"user-agent": "python-httpx"}, "t 0")[0] == "other"
    assert host_label(None, {"user-agent": "ChatGPT-User"}, None)[0] == "chatgpt"


def test_clip_marks_truncation() -> None:
    assert clip(None, 5) is None
    assert clip("abc", 5) == "abc"
    assert clip("abcdefgh", 5) == "abcde…[+3 chars]"


def test_analytics_records_locally_and_forwards_personless(settings: Any) -> None:
    client = FakeClient()
    a = Analytics(settings, client)
    assert a.enabled
    a.capture("run_python", host="claude", ok=True, error=None, dhan_calls={"marketfeed/ltp": 1})
    assert a.events("run_python")[0]["host"] == "claude"
    assert "error" not in a.events()[0]
    event, kwargs = client.captured[0]
    assert event == "run_python" and kwargs["distinct_id"] == "dhan.example.test"
    assert kwargs["properties"] == {
        "host": "claude",
        "ok": True,
        "dhan_calls": {"marketfeed/ltp": 1},
        "$process_person_profile": False,
    }
    a.close()
    assert client.shut


def test_analytics_never_raises_and_is_quiet_without_a_client(settings: Any) -> None:
    broken = Analytics(settings, BrokenClient())
    broken.capture("feed_status", feed="active")
    assert broken.events("feed_status")
    quiet = Analytics(settings)
    assert not quiet.enabled
    quiet.capture("x")
    quiet.close()
    assert quiet.events() == [{"event": "x", "at": quiet.events()[0]["at"]}]
