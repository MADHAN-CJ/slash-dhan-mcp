from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from slash_dhan_mcp import server as srv
from slash_dhan_mcp.config import load_settings

from .fake_dhan import CLIENT_ID, TODAY, FakeDhan

ADMIN_SECRET = "a" * 48
SWEEP_SECRET = "sweep-secret-for-tests"
CHALLENGE_TOKEN = "openai-apps-challenge-token"
OPERATORS = ["ops1@example.com", "ops2@example.com"]
USER_EMAIL = "trader@example.com"


def jwt_like(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.signature-signature"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Harness:
    def __init__(self, tmp: Path) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.fake = FakeDhan()
        env = {
            "SLASH_DHAN_MCP_PUBLIC_URL": self.base,
            "SLASH_DHAN_MCP_HOST": "127.0.0.1",
            "SLASH_DHAN_MCP_PORT": str(self.port),
            "SLASH_DHAN_MCP_ADMIN_SECRET": ADMIN_SECRET,
            "CREDS_ENCRYPTION_KEY": secrets.token_hex(32),
            "SLASH_DHAN_MCP_STATE_DIR": str(tmp / "state"),
            "SLASH_DHAN_MCP_OPERATOR_EMAILS": ",".join(OPERATORS),
            "DHAN_BASE_URL": "https://fake.dhan.test",
            "SLASHDHAN_SCRIP_MASTER_URL": "https://fake.dhan.test/scrip-master.csv",
            "SLASHDHAN_TODAY": TODAY.isoformat(),
            "SWEEP_SECRET": SWEEP_SECRET,
            "WATCHERS_MIN_GAP_MINUTES": "0",
            "RESEND_API_KEY": "",
            "SCRIPT_TIMEOUT_OPENAI_SECONDS": "1",
            "OPENAI_APPS_CHALLENGE_TOKEN": CHALLENGE_TOKEN,
        }
        self._old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.settings = load_settings()
        self.app = srv.build_app(
            self.settings,
            dhan_transport=self.fake.transport(),
            dhan_async_transport=self.fake.transport(),
        )
        mcp = srv.build_server(self.app)
        asgi = srv.build_asgi(mcp, self.app)
        for name in ("mcp", "uvicorn", "slash_dhan_mcp"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._server = uvicorn.Server(
            uvicorn.Config(asgi, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not boot")

    def reset(self) -> None:
        self.app.db.execute("DELETE FROM watcher_fires")
        self.app.db.execute("DELETE FROM watchers")
        self.app.feed.clear()
        self.app.db.execute(
            "UPDATE feed SET dhan_client_id = NULL, expiry_mail_sent_for = NULL, "
            "warning_mail_sent_for = NULL, token_validity = NULL, data_plan = NULL WHERE id = 1"
        )
        self.app.mailer.sent.clear()
        self.fake.calls.clear()
        self.fake.mode = "normal"
        self.fake.token = None
        self.fake.prices.clear()

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
        self.app.close()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Harness]:
    h = Harness(tmp_path_factory.mktemp("sdm"))
    yield h
    h.stop()


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.reset()
    return harness


class Mcp:
    def __init__(self, url: str) -> None:
        self.url = url
        self.h = httpx.Client(timeout=120)
        self.n = 0
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        )

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.n += 1
        r = self.h.post(
            self.url,
            json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200, f"{method}: {r.status_code} {r.text[:300]}"
        body: dict[str, Any] = r.json()
        return body

    def tool(self, name: str, **args: Any) -> tuple[str, bool]:
        res = self.call(name, args)
        text = "".join(c.get("text", "") for c in res.get("content", []))
        return text, bool(res.get("isError"))

    def call(
        self, name: str, args: dict[str, Any], meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name, "arguments": args}
        if meta is not None:
            params["_meta"] = meta
        result: dict[str, Any] = self.rpc("tools/call", params)["result"]
        return result

    def tools(self) -> list[str]:
        return [t["name"] for t in self.tool_specs()]

    def tool_specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = self.rpc("tools/list", {})["result"]["tools"]
        return specs


def mcp(h: Harness) -> Mcp:
    return Mcp(f"{h.base}/")


def admin(h: Harness, method: str, path: str, **kw: Any) -> httpx.Response:
    return httpx.request(
        method,
        f"{h.base}{path}",
        headers={"Authorization": f"Bearer {ADMIN_SECRET}"},
        timeout=30,
        **kw,
    )


def push_token(
    h: Harness, token: str = "live-token-0001-abcdefgh", client_id: str = CLIENT_ID
) -> dict[str, Any]:
    r = admin(
        h, "PUT", "/admin/feed/token", json={"dhan_client_id": client_id, "access_token": token}
    )
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def sweep(h: Harness, **body: Any) -> dict[str, Any]:
    r = httpx.post(
        f"{h.base}/internal/sweep",
        headers={"x-sweep-secret": SWEEP_SECRET},
        json={"force": True, **body},
        timeout=120,
    )
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def stdout_of(text: str) -> str:
    return text.split("--- stdout ---\n")[1].split("\n--- stderr ---")[0].strip()


def row(h: Harness, wid: str) -> dict[str, Any]:
    found = h.app.watchers.row(wid)
    assert found is not None
    return found


def age_watchers(h: Harness) -> None:
    h.app.db.execute("UPDATE watchers SET last_checked_at = '2020-01-01T00:00:00+00:00'")


# ---------------------------------------------------------------- tests


def test_health_root_mcp_and_tool_specs(h: Harness) -> None:
    assert httpx.get(f"{h.base}/health").json() == {
        "ok": True,
        "service": "slash-dhan-mcp",
        "feed": "unset",
    }
    assert httpx.post(f"{h.base}/mcp", json={}).status_code == 404
    r = httpx.get(f"{h.base}/.well-known/openai-apps-challenge")
    assert r.status_code == 200 and r.text == CHALLENGE_TOKEN
    m = mcp(h)
    assert m.tools() == ["run_python", "feed_status", "create_watcher", "list_watchers"]
    specs = {t["name"]: t for t in m.tool_specs()}
    for spec in specs.values():
        hints = spec["annotations"]
        assert spec["title"] and len(spec["name"]) <= 64
        for key in ("readOnlyHint", "destructiveHint", "openWorldHint"):
            assert isinstance(hints.get(key), bool), (spec["name"], key)
        assert spec["_meta"]["securitySchemes"] == [{"type": "noauth"}]
    read_only = {n for n, t in specs.items() if t["annotations"]["readOnlyHint"]}
    assert read_only == {"run_python", "feed_status", "list_watchers"}
    for name in ("feed_status", "create_watcher", "list_watchers"):
        assert specs[name]["outputSchema"]["type"] == "object", name
    assert "outputSchema" not in specs["run_python"]
    assert "notify_email" in specs["create_watcher"]["inputSchema"]["required"]
    assert "import slashdhan as z" in specs["run_python"]["description"]
    assert "- option_chain(" in specs["run_python"]["description"]


def test_skill_zip_download_and_page_link(h: Harness) -> None:
    import io
    import zipfile
    from pathlib import Path

    r = httpx.get(f"{h.base}/slash-markets-dhan.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="slash-markets-dhan.zip"'
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["slash-markets-dhan/SKILL.md"]
        body = zf.read("slash-markets-dhan/SKILL.md")
    assert body == (Path(__file__).resolve().parents[1] / "SKILL.md").read_bytes()
    assert body.startswith(b"---\nname: slash-markets-dhan\n")
    page = httpx.get(f"{h.base}/watchers").text
    assert f'href="{h.base}/slash-markets-dhan.zip"' in page
    assert f'href="{h.base}/SKILL.md"' in page
    r = httpx.get(f"{h.base}/SKILL.md")
    assert r.status_code == 200 and r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.headers["content-disposition"] == 'inline; filename="SKILL.md"'
    assert r.content == body


def test_tools_before_a_token_is_pushed(h: Harness) -> None:
    m = mcp(h)
    res = m.call("feed_status", {})
    assert res["structuredContent"]["feed"] == "unset"
    assert "no token yet" in res["structuredContent"]["message"]
    assert res["structuredContent"]["watchersPageUrl"] == f"{h.base}/watchers"
    text, is_error = m.tool("run_python", code="print(1)")
    assert not is_error and "no token yet" in text
    text, _ = m.tool(
        "create_watcher",
        symbol="TCS",
        label="x",
        script="print('{\"met\": false}')",
        notify_email=USER_EMAIL,
    )
    assert json.loads(text)["created"] is False and "no token yet" in json.loads(text)["error"]


def test_admin_token_api(h: Harness) -> None:
    assert httpx.put(f"{h.base}/admin/feed/token", json={}).status_code == 401
    assert httpx.get(f"{h.base}/admin/feed/status").status_code == 401
    r = admin(h, "PUT", "/admin/feed/token", json={"dhan_client_id": CLIENT_ID})
    assert r.status_code == 400 and "required" in r.json()["error"]
    r = admin(
        h, "PUT", "/admin/feed/token", json={"dhan_client_id": "abc", "access_token": "x" * 30}
    )
    assert r.status_code == 400 and "numeric" in r.json()["error"]
    h.fake.token = "the-real-token-000000"
    r = admin(
        h,
        "PUT",
        "/admin/feed/token",
        json={"dhan_client_id": CLIENT_ID, "access_token": "wrong-token-00000000"},
    )
    assert r.status_code == 400 and "Dhan rejected the token" in r.json()["error"]
    assert r.json()["error"].endswith("Invalid Token")
    assert admin(h, "GET", "/admin/feed/status").json()["status"] == "unset"

    exp = int(time.time()) + 20 * 3600
    h.fake.token = jwt_like(exp)
    status = push_token(h, h.fake.token)
    assert status["status"] == "active" and status["dhanClientId"] == CLIENT_ID
    assert status["dataPlan"] == "Active" and status["tokenValidity"] == "11/09/2026 09:00"
    assert status["expiresAt"].startswith(time.strftime("%Y-%m-%dT", time.gmtime(exp))[:10])
    assert status["resumedWatchers"] == 0
    text = json.dumps(status)
    assert h.fake.token not in text and "access_token" not in text
    assert httpx.get(f"{h.base}/health").json()["feed"] == "active"
    probe_paths = [c.path for c in h.fake.calls]
    assert probe_paths[-2:] == ["/fundlimit", "/profile"]

    h.fake.token = None
    status = push_token(h, "plain-opaque-token-00000")
    assert status["expiresAt"] > status["issuedAt"]
    r = admin(h, "DELETE", "/admin/feed/token")
    assert r.status_code == 200 and r.json()["status"] == "unset"
    assert mcp(h).call("feed_status", {})["structuredContent"]["feed"] == "unset"


def test_run_python_through_the_gateway(h: Harness) -> None:
    push_token(h)
    m = mcp(h)
    assert m.call("feed_status", {})["structuredContent"]["feed"] == "active"
    code = (
        "import os, json, slashdhan as z\n"
        "print(json.dumps({'client': z.profile()['dhanClientId'], 'tcs': z.ltp_of('TCS'),"
        " 'leak': sorted(k for k in os.environ if k.startswith('DHAN') or 'SECRET' in k or 'KEY' in k),"
        " 'gw': 'SLASHDHAN_GATEWAY_URL' in os.environ}))"
    )
    text, is_error = m.tool("run_python", code=code)
    assert not is_error, text
    assert json.loads(stdout_of(text)) == {
        "client": CLIENT_ID,
        "tcs": 636.0,
        "leak": [],
        "gw": True,
    }
    upstream = [c for c in h.fake.calls if c.path == "/marketfeed/ltp"]
    assert upstream and upstream[-1].headers["access-token"] == "live-token-0001-abcdefgh"
    assert upstream[-1].headers["client-id"] == CLIENT_ID and upstream[-1].body == {
        "NSE_EQ": [11536]
    }

    text, is_error = m.tool("run_python", code="import slashdhan as z\nz.nope()")
    assert is_error and "AttributeError" in text
    text, is_error = m.tool("run_python", code="")
    assert is_error and "Empty script" in text

    text, is_error = m.tool(
        "run_python",
        code="import slashdhan as z\nc=z.option_chain('NIFTY', strikes_around=2)\nprint(len(c['strikes']), c['strikes'][2]['ce']['delta'], c['lot_size'])",
    )
    assert not is_error and stdout_of(text) == "5 0.5 75.0"

    text, _ = m.tool("run_python", code="open('shared-note.txt','w').write('kept')\nprint('w')")
    text, _ = m.tool("run_python", code="print(open('shared-note.txt').read())")
    assert stdout_of(text) == "kept"


def test_gateway_rejects_bad_tokens_and_writes(h: Harness) -> None:
    push_token(h)
    r = httpx.get(f"{h.base}/dhan/positions")
    assert r.status_code == 401 and r.json()["errorCode"] == "DH-901"
    token = h.app.tokens.issue()
    hdr = {"Authorization": f"Bearer {token}"}
    assert httpx.get(f"{h.base}/dhan/positions", headers=hdr).status_code == 200
    assert httpx.get(f"{h.base}/dhan/orders/123", headers=hdr).status_code in (200, 404)
    for method, path in (
        ("POST", "orders"),
        ("POST", "orders/slicing"),
        ("POST", "margincalculator"),
        ("POST", "forever/orders"),
        ("POST", "ip/setIP"),
        ("GET", "edis/tpin"),
        ("POST", "positions/convert"),
    ):
        r = httpx.request(method, f"{h.base}/dhan/{path}", headers=hdr, json={})
        assert r.status_code == 403 and "not a read endpoint" in r.text, (method, path)
    assert not [c for c in h.fake.calls if c.path.startswith("/orders") and c.method == "POST"]
    r = httpx.request("DELETE", f"{h.base}/dhan/orders/1", headers=hdr)
    assert r.status_code == 403 and "not a read endpoint" in r.text
    r = httpx.request("PUT", f"{h.base}/dhan/orders/1", headers=hdr, json={})
    assert r.status_code == 403
    r = httpx.post(f"{h.base}/dhan/marketfeed/ltp", headers=hdr, content=b"not json")
    assert r.status_code == 400
    h.app.tokens.revoke(token)
    assert httpx.get(f"{h.base}/dhan/positions", headers=hdr).status_code == 401


def test_feed_expiry_pauses_watchers_and_mails_operators(h: Harness) -> None:
    push_token(h)
    m = mcp(h)
    text, _ = m.tool(
        "create_watcher",
        symbol="TCS",
        label="TCS above 600",
        script="import slashdhan as z, json\nltp=z.ltp_of('TCS')\nprint(json.dumps({'met': ltp > 600, 'value': ltp}))",
        notify_email=USER_EMAIL,
    )
    created = json.loads(text)["watcher"]
    assert created["status"] == "ARMED" and created["alreadyMet"] is True

    h.fake.mode = "token_dead"
    text, is_error = m.tool("run_python", code="import slashdhan as z\nprint(z.funds())")
    assert is_error and "TokenExpired" in text and "being refreshed" in text
    assert m.call("feed_status", {})["structuredContent"]["feed"] == "expired"
    assert httpx.get(f"{h.base}/health").json()["feed"] == "expired"
    listed = m.call("list_watchers", {})["structuredContent"]["watchers"]
    assert listed[0]["status"] == "PAUSED" and listed[0]["pausedBy"] == "feed"
    text, is_error = m.tool("run_python", code="print(1)")
    assert not is_error and "being refreshed" in text

    report = sweep(h)
    assert report["operatorMails"] == 1
    mails = [x for x in h.app.mailer.sent if "feed DOWN" in x["subject"]]
    assert sorted(x["to"] for x in mails) == OPERATORS
    assert "Invalid Token" in mails[0]["text"]
    assert sweep(h)["operatorMails"] == 0

    h.fake.mode = "normal"
    status = push_token(h, "live-token-0002-abcdefgh")
    assert status["status"] == "active" and status["resumedWatchers"] == 1
    listed = m.call("list_watchers", {})["structuredContent"]["watchers"]
    assert listed[0]["status"] == "ARMED" and listed[0]["pausedBy"] is None

    h.fake.mode = "data_unsubscribed"
    text, is_error = m.tool(
        "run_python", code="import slashdhan as z\nprint(z.ltp({'IDX_I': [13]}))"
    )
    assert is_error and "DataPlanMissing" in text
    assert m.call("feed_status", {})["structuredContent"]["feed"] == "active"
    h.fake.mode = "endpoint_401"
    text, is_error = m.tool(
        "run_python", code="import slashdhan as z\nprint(z.expiry_list('NIFTY'))"
    )
    assert is_error and "endpoint-specific" in text
    assert m.call("feed_status", {})["structuredContent"]["feed"] == "active"


def test_pre_expiry_warning_is_mailed_once(h: Harness) -> None:
    push_token(h, jwt_like(int(time.time()) + 30 * 60))
    assert sweep(h)["operatorMails"] == 1
    warn = [x for x in h.app.mailer.sent if "expires" in x["subject"]]
    assert sorted(x["to"] for x in warn) == OPERATORS
    assert sweep(h)["operatorMails"] == 0
    push_token(h, jwt_like(int(time.time()) + 10 * 3600))
    assert sweep(h)["operatorMails"] == 0


def test_watcher_lifecycle_and_page(h: Harness) -> None:
    push_token(h)
    m = mcp(h)
    script = (
        "import slashdhan as z, json\n"
        "ltp = z.ltp_of('RELIANCE')\n"
        "print(json.dumps({'met': ltp > 1000, 'value': ltp, 'detail': 'rel'}))"
    )
    bad, _ = m.tool(
        "create_watcher",
        symbol="RELIANCE",
        label="bad",
        script="print('hello')",
        notify_email=USER_EMAIL,
    )
    assert json.loads(bad)["error"].startswith("Watcher NOT created")
    bad, _ = m.tool(
        "create_watcher", symbol="RELIANCE", label="x", script=script, notify_email="nope"
    )
    assert "notify_email" in json.loads(bad)["error"]
    text, _ = m.tool(
        "create_watcher",
        symbol="RELIANCE",
        label="RELIANCE above 1000",
        script=script,
        notify_email=USER_EMAIL.upper(),
        check_every_minutes=1,
    )
    w = json.loads(text)["watcher"]
    assert w["status"] == "ARMED" and w["alreadyMet"] is False and w["market"] == "NSE"
    assert w["notifyEmail"] == USER_EMAIL and w["pageUrl"] == f"{h.base}/watchers"
    wid = w["id"]

    gold, _ = m.tool(
        "create_watcher",
        symbol="GOLD",
        label="gold",
        script="import json, slashdhan as z\nprint(json.dumps({'met': z.ltp_of('GOLD') > 1}))",
        notify_email="other@example.com",
    )
    gold_w = json.loads(gold)["watcher"]
    assert gold_w["market"] == "MCX" and gold_w["alreadyMet"] is True

    age_watchers(h)
    report = sweep(h)
    assert report["checked"] == 2 and report["fired"] == 0

    h.fake.prices["NSE_EQ:2885"] = 1650.0
    age_watchers(h)
    assert sweep(h)["fired"] == 1
    mail = h.app.mailer.sent[-1]
    assert (
        mail["to"] == USER_EMAIL
        and "RELIANCE above 1000" in mail["subject"]
        and "1650" in mail["text"]
    )
    assert f"{h.base}/watchers" in mail["text"]

    age_watchers(h)
    assert sweep(h)["fired"] == 0
    h.fake.prices["NSE_EQ:2885"] = 900.0
    age_watchers(h)
    sweep(h)
    h.fake.prices["NSE_EQ:2885"] = 1700.0
    age_watchers(h)
    assert sweep(h)["fired"] == 1
    listed = {x["id"]: x for x in m.call("list_watchers", {})["structuredContent"]["watchers"]}
    assert listed[wid]["alertCount"] == 2 and len(listed[wid]["recentAlerts"]) == 2
    assert listed[wid]["notifyEmail"] == USER_EMAIL

    web = httpx.Client(base_url=h.base, follow_redirects=False, timeout=10)
    page = web.get("/watchers").text
    assert "RELIANCE above 1000" in page and "gold" in page and "in MCX hours" in page
    assert "tr…@example.com" in page and USER_EMAIL not in page
    assert "Libre Baskerville" in page and 'class="slash"' in page and "Dhan feed active" in page
    assert f'value="{h.base}/"' in page and "Connect your AI assistant" in page
    assert "Zap" not in page and "Slash Markets" not in page
    r = web.post(f"/watchers/{wid}/pause")
    assert r.status_code == 303 and r.headers["location"] == f"{h.base}/watchers"
    assert row(h, wid)["status"] == "PAUSED"
    assert web.post(f"/watchers/{wid}/pause").status_code == 303
    assert "cannot pause" in web.get(web.post(f"/watchers/{wid}/pause").headers["location"]).text
    assert web.post(f"/watchers/{wid}/resume").status_code == 303
    assert row(h, wid)["status"] == "ARMED"
    assert web.post("/watchers/nope/cancel").status_code == 404
    assert web.post(f"/watchers/{wid}/explode").status_code == 404
    assert web.post(f"/watchers/{wid}/cancel").status_code == 303
    assert row(h, wid)["status"] == "CANCELLED"
    assert "Finished" in web.get("/watchers").text

    h.app.db.execute("UPDATE watchers SET error_count = 0")
    h.app.db.execute("UPDATE watchers SET script = 'raise SystemExit(3)' WHERE status = 'ARMED'")
    for _ in range(5):
        age_watchers(h)
        sweep(h)
    errored = row(h, gold_w["id"])
    assert errored["status"] == "ERROR" and "5 times" in errored["status_reason"]
    assert "5 times" in web.get("/watchers").text


def test_openai_host_gets_a_shorter_script_timeout(h: Harness) -> None:
    push_token(h)
    m = mcp(h)
    code = "import time\ntime.sleep(1.5)\nprint('done')"
    res = m.call("run_python", {"code": code}, meta={"openai/userAgent": "ChatGPT/1.0"})
    assert res.get("isError") and "timed out after 1s" in res["content"][0]["text"]
    res = m.call("run_python", {"code": code}, meta={"progressToken": "p1"})
    assert not res.get("isError") and "done" in res["content"][0]["text"]
