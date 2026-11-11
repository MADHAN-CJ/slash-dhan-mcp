"""The plain HTTP interface at /api/v1 behaves like the four MCP tools."""

from __future__ import annotations

import json

import httpx

from .fake_dhan import CLIENT_ID
from .test_server import USER_EMAIL, Harness, mcp, push_token
from .test_server import h as h
from .test_server import harness as harness


def test_api_tools_match_the_mcp_catalogue(h: Harness) -> None:
    r = httpx.get(f"{h.base}/api/v1/tools")
    assert r.status_code == 200
    listed = r.json()
    assert listed["version"] == "1"
    by_name = {t["name"]: t for t in listed["tools"]}
    assert list(by_name) == ["run_python", "feed_status", "create_watcher", "list_watchers"]
    specs = {t["name"]: t for t in mcp(h).tool_specs()}
    for name, tool in by_name.items():
        assert tool["description"] == specs[name]["description"]
        assert tool["inputSchema"] == specs[name]["inputSchema"]
    assert by_name["run_python"]["method"] == "POST" and by_name["run_python"]["path"].endswith(
        "/run"
    )
    assert by_name["list_watchers"]["method"] == "GET"
    assert "code" in by_name["run_python"]["inputSchema"]["properties"]
    assert "notify_email" in by_name["create_watcher"]["inputSchema"]["required"]


def test_api_run_feed_and_cors(h: Harness) -> None:
    # Feed not set: a clear 503 with the same message the tool relays.
    r = httpx.post(f"{h.base}/api/v1/run", json={"code": "print(1)"})
    assert r.status_code == 503 and r.json()["ok"] is False
    assert "no token yet" in r.json()["message"]
    assert httpx.get(f"{h.base}/api/v1/feed").json()["feed"] == "unset"
    assert httpx.post(f"{h.base}/api/v1/run", json={"code": "  "}).status_code == 400
    assert httpx.post(f"{h.base}/api/v1/run", content=b"nope").status_code == 400
    big = {"code": "x = '" + "a" * (600 * 1024) + "'"}
    assert httpx.post(f"{h.base}/api/v1/run", json=big).status_code == 413

    push_token(h)
    feed = httpx.get(f"{h.base}/api/v1/feed").json()
    assert feed["feed"] == "active" and feed["dhanClientId"] == CLIENT_ID
    assert feed["renewsAtIst"] and feed["message"] is None
    assert feed["watchersPageUrl"] == f"{h.base}/watchers"

    code = "import slashdhan as z, json\nprint(json.dumps({'ltp': z.ltp_of('TCS')}))"
    r = httpx.post(f"{h.base}/api/v1/run", json={"code": code}, timeout=120)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["exit_code"] == 0 and body["feed"] == "active"
    assert json.loads(body["stdout"])["ltp"] > 0 and body["stderr"] == ""
    assert body["duration_ms"] > 0 and body["timed_out"] is False

    r = httpx.post(f"{h.base}/api/v1/run", json={"code": "raise ValueError('boom')"}, timeout=120)
    assert r.status_code == 200  # a failing script is a normal outcome for a chat client
    body = r.json()
    assert body["ok"] is False and body["exit_code"] == 1 and "boom" in body["stderr"]

    r = httpx.post(f"{h.base}/api/v1/run", json={"code": "print(1)", "timeout_seconds": "x"})
    assert r.status_code == 400

    pre = httpx.options(
        f"{h.base}/api/v1/run",
        headers={
            "Origin": "https://chat.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "*"
    assert "POST" in pre.headers["access-control-allow-methods"]
    got = httpx.get(f"{h.base}/api/v1/feed", headers={"Origin": "https://chat.example"})
    assert got.headers["access-control-allow-origin"] == "*"

    events = h.app.analytics.events("run_python")
    assert events and all(e["host"] == "api" for e in events[-2:])


def test_api_watchers(h: Harness) -> None:
    push_token(h)
    r = httpx.post(f"{h.base}/api/v1/watchers", json={"symbol": "TCS"})
    assert r.status_code == 400 and "missing" in r.json()["error"]
    payload = {
        "symbol": "TCS",
        "label": "TCS above 600",
        "script": (
            "import slashdhan as z, json\nltp=z.ltp_of('TCS')\n"
            "print(json.dumps({'met': ltp > 600, 'value': ltp}))"
        ),
        "notify_email": USER_EMAIL,
    }
    r = httpx.post(
        f"{h.base}/api/v1/watchers", json={**payload, "notify_email": "nope"}, timeout=120
    )
    assert r.status_code == 200 and r.json()["created"] is False and r.json()["watcher"] is None
    r = httpx.post(f"{h.base}/api/v1/watchers", json=payload, timeout=120)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["created"] is True and created["watcher"]["status"] == "ARMED"
    listed = httpx.get(f"{h.base}/api/v1/watchers").json()
    assert [w["id"] for w in listed["watchers"]] == [created["watcher"]["id"]]
    assert listed["watchers"][0]["notifyEmail"] == USER_EMAIL
    assert listed["pageUrl"] == f"{h.base}/watchers"
    # Same rows the MCP tool returns.
    via_mcp = mcp(h).call("list_watchers", {})["structuredContent"]["watchers"]
    assert [w["id"] for w in via_mcp] == [created["watcher"]["id"]]
