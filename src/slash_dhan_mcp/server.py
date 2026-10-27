from __future__ import annotations

import argparse
import hmac
import io
import json
import logging
import sys
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

import anyio
import httpx
import uvicorn
from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route

from slashdhan.config import today_ist
from slashdhan.schema import reference

from . import pages
from .config import SKILL_NAME, SKILL_ZIP_NAME, ConfigError, Settings, load_settings
from .db import Database
from .feed import Creds, Feed, FeedError
from .gateway import Gateway, RunTokens
from .mail import Mailer
from .master import MasterCache
from .runner import Runner
from .watchers import FEED_REASONS, Watchers

log = logging.getLogger("slash_dhan_mcp")

RULES = """Run a Python 3 script in the /markets Dhan sandbox and return what it prints. Use it for ANY question about Indian market data (prices, depth, candles, option chains with greeks, expiries, expired-option history, instrument lookup) across NSE/BSE stocks and indices, NSE/BSE index and stock F&O and MCX commodities, and for the feed account's positions, holdings, funds, orders and trades.

HOW TO USE
- Write a COMPLETE script that does `import slashdhan as z`. It is pre-installed and read-only; the shared Dhan session is wired in through a local gateway, so never ask for, print or hard-code credentials. Standard library only (json, statistics, math, datetime, csv, sqlite3, collections); pandas/numpy are NOT installed.
- Do the computation IN the script and print ONLY the compact result (a small dict via json.dumps, or a few lines). NEVER print raw candle lists, full chains or whole holdings; every printed byte lands in your context. Aggregate, filter, round.
- Files persist in a SHARED working directory between calls: store big fetched data in a file in one script and read it back in the next; use distinctive file names.
- One question may need several scripts; call this tool once per script and compose the answer yourself.
- A failing script returns its traceback as an error. Fix the code and retry; if it keeps failing, tell the user plainly what could not be done.
- ACCOUNT DATA IS SHARED: positions(), holdings(), funds(), orders() and trades() describe the /markets feed account (the operator's Dhan account), not the user's own broker account. Say so when you report them.
- Errors explain themselves: TokenExpired means the shared feed token is being refreshed by the operator (tell the user to try again in a few minutes; call feed_status for details). DataPlanMissing means the feed account lacks Dhan's Data-API plan. CallBudgetExceeded means fetch less (batch ids into one ltp/quote call, fewer strikes, wider intervals). RateLimited means Dhan throttled the shared feed; wait a few seconds.
- IDS: Dhan addresses instruments by (exchange_segment, security_id): NSE_EQ, BSE_EQ, IDX_I (indices), NSE_FNO, BSE_FNO, MCX_COMM. z.resolve_symbol("TCS" | "NIFTY" | "GOLD") picks the instrument to quote or chart (stock → NSE share, index → IDX_I, commodity → nearest MCX future); most functions also accept a plain symbol or a "SEGMENT:ID" key directly. Option underlyings are plain names (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, RELIANCE, GOLD, CRUDEOIL). Candle intervals: 1, 5, 15, 25, 60 minutes or day. Currency derivatives are not available.
- Option chains come from Dhan with IV and greeks; Dhan allows ONE chain call per (underlying, expiry) every 3 s, so keep it to one or two per script. expired_options gives rolling history of expired contracts (slow, ≤7 days per call).
- Hours (Mon–Fri): NSE/BSE/F&O 09:15–15:30, MCX 09:00–23:30 IST; check z.market_status(). Off-hours, quotes are the last traded price. Money is ₹.
- The library cannot place, modify or cancel orders. If asked to trade, say so; the user trades in their own broker app.

slashdhan REFERENCE (exact signatures; z.<name>):
"""

FEED_STATUS_DESCRIPTION = """\
Whether the shared Dhan data feed behind this connector is usable right now.

Call this when run_python reports TokenExpired or DataPlanMissing, or when the user asks why
data is unavailable. The feed is one operator-managed Dhan session that every user shares; it
is refreshed daily by the operator, never by the user. Relay the returned `message`.\
"""

CREATE_WATCHER_DESCRIPTION = """\
Create a market watcher: a background check that runs a Python script on a schedule during
that market's hours (NSE/BSE/F&O 09:15–15:30, MCX 09:00–23:30 IST, Mon–Fri) with the shared
Dhan feed, and EMAILS `notify_email` when its condition BECOMES true. Use it when the user asks
to be alerted, notified or watched about a market condition (price level, indicator cross, OI
change). `market` is auto-detected from the script (MCX for commodities, else NSE); pass NSE,
BSE or MCX to override.

ALWAYS ASK the user which email address should receive the alerts if they have not told you;
never guess or reuse an address from elsewhere. `notify_email` is required.

`script` runs in the same sandbox as run_python (`import slashdhan as z`) once every
`check_every_minutes`, so KEEP IT CHEAP: one or two API calls, no loops over option chains.
Resolve ids ONCE now and hard-code them in the script. Its LAST printed line MUST be exactly
one JSON object: {"met": <bool>, "value": <current number, optional>, "detail": "<short note, optional>"}
Print nothing after that line. The script is validated with one immediate dry run; if the
output violates the contract, creation is rejected and the output is returned so you can fix
the script and retry.

Alert semantics (explain to the user): the first check is a BASELINE and never alerts; a
condition already true at creation must become false and then true again. One email per
not-met→met transition with a minimum gap between alerts; the watcher expires after
`expires_in_days`. When the shared feed token expires, watchers pause and resume by themselves
after the operator refreshes it.

You canNOT modify, pause, resume or cancel watchers; that happens on the watchers page, which
lists every watcher on this host. ALWAYS show the user the returned pageUrl after creating one.\
"""

LIST_WATCHERS_DESCRIPTION = """\
List every watcher on this host (there are no per-user accounts): status (ARMED / PAUSED /
ERROR / EXPIRED / CANCELLED), condition, alert email, last checked value and detail, alert
history. Read-only. Pause, resume and cancel exist only on the watchers page; send the user
the pageUrl when they ask for a change.\
"""

WARM_SCRIPT = "import slashdhan as z, json\nprint(json.dumps(z.master_status()))\n"


@dataclass
class App:
    settings: Settings
    db: Database
    mailer: Mailer
    feed: Feed
    tokens: RunTokens
    gateway: Gateway
    runner: Runner
    watchers: Watchers
    master: MasterCache

    def close(self) -> None:
        self.db.close()


def build_app(
    settings: Settings,
    *,
    dhan_transport: httpx.BaseTransport | None = None,
    dhan_async_transport: httpx.AsyncBaseTransport | None = None,
    mail_client: httpx.Client | None = None,
) -> App:
    db = Database(settings.db_path)
    db.migrate()
    mailer = Mailer(settings, mail_client)
    dhan_http = httpx.Client(transport=dhan_transport, timeout=180.0)
    feed = Feed(settings, db, dhan_http)
    tokens = RunTokens()
    gateway_url = f"http://{settings.host}:{settings.port}"
    runner = Runner(settings, tokens, gateway_url)
    watchers = Watchers(settings, db, feed, runner, mailer)

    def on_feed_expired(error: str) -> None:
        if feed.mark_expired(error):
            paused = watchers.pause_for_feed(FEED_REASONS["expired"])
            log.warning("feed expired; paused %d watchers", paused)

    gateway = Gateway(settings, feed, tokens, on_feed_expired, transport=dhan_async_transport)
    master = MasterCache(settings, dhan_http)
    return App(settings, db, mailer, feed, tokens, gateway, runner, watchers, master)


def feed_message(reason: str) -> str:
    return FEED_REASONS[reason]


class Sweeper(threading.Thread):
    """Runs the watcher sweep every interval and warms the instrument master once per IST day."""

    def __init__(self, app: App) -> None:
        super().__init__(name="sweeper", daemon=True)
        self._app = app
        self._interval = max(5, app.settings.sweep_interval_s)
        self._stop = threading.Event()
        self._warmed_for = ""

    def run(self) -> None:
        self._warm()
        while not self._stop.wait(self._interval):
            try:
                self._app.watchers.sweep()
            except Exception:
                log.exception("sweep crashed")
            self._warm()

    def _warm(self) -> None:
        day = today_ist().isoformat()
        if self._warmed_for == day:
            return
        try:
            result = self._app.runner.run(WARM_SCRIPT, timeout_s=300)
            if result.exit_code == 0:
                self._warmed_for = day
                log.info("instrument master ready: %s", result.stdout.strip()[:200])
            else:
                log.warning("instrument master warm-up failed: %s", result.stderr.strip()[-300:])
        except Exception:
            log.exception("instrument master warm-up crashed")

    def stop(self) -> None:
        self._stop.set()


class FeedStatus(TypedDict):
    feed: str
    dhanClientId: str | None
    expiresAt: str | None
    expiresAtIst: str | None
    dataPlan: str | None
    lastOkAt: str | None
    message: str | None
    watchersPageUrl: str


class WatcherCreated(TypedDict):
    created: bool
    error: str | None
    watcher: dict[str, Any] | None


class WatcherList(TypedDict):
    watchers: list[dict[str, Any]]
    pageUrl: str
    managing: str


NOAUTH_SCHEME = {"securitySchemes": [{"type": "noauth"}]}


def _is_openai_host(ctx: Context[Any, Any]) -> bool:
    meta = ctx.request_context.meta or {}
    return any(str(key).startswith("openai/") for key in meta)


def build_server(app: App) -> MCPServer[Any]:
    s = app.settings
    description = RULES + reference(brief=True)
    server: MCPServer[Any] = MCPServer(
        "slash-markets-dhan",
        instructions=(
            "/markets Dhan sandbox: answer Indian market-data questions by writing Python "
            "against `slashdhan` and running it with run_python. Print only compact results."
        ),
    )
    run_gate = anyio.Semaphore(s.max_concurrent_runs)

    @server.tool(
        name="run_python",
        title="Run Python against Dhan market data",
        description=description,
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, open_world_hint=True
        ),
        meta=NOAUTH_SCHEME,
        structured_output=False,
    )
    async def run_python(code: str, ctx: Context[Any, Any], timeout_seconds: int = 0) -> str:
        """code: the complete Python script. timeout_seconds: wall-clock limit (default 60, max 180; hosts with a shorter tool timeout, such as ChatGPT, are capped at about 50 s, so keep scripts cheap)."""
        if not code.strip():
            raise ToolError("Empty script. Pass the complete Python source in `code`.")
        creds = await anyio.to_thread.run_sync(app.feed.creds)
        if not isinstance(creds, Creds):
            return feed_message(creds)
        timeout = timeout_seconds or s.script_timeout_s
        if _is_openai_host(ctx):
            timeout = min(timeout, s.script_timeout_openai_s)
        async with run_gate:
            result = await anyio.to_thread.run_sync(app.runner.run, code, timeout)
        text = result.text()
        after = await anyio.to_thread.run_sync(app.feed.creds)
        if not isinstance(after, Creds):
            text += f"\n\n[{feed_message(after)}]"
        if result.exit_code != 0:
            raise ToolError(text)
        return text

    @server.tool(
        name="feed_status",
        title="Dhan feed status",
        description=FEED_STATUS_DESCRIPTION,
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, open_world_hint=False
        ),
        meta=NOAUTH_SCHEME,
    )
    async def feed_status() -> FeedStatus:
        status = await anyio.to_thread.run_sync(app.feed.status)
        state = str(status["status"])
        return FeedStatus(
            feed=state,
            dhanClientId=status["dhanClientId"],
            expiresAt=status["expiresAt"],
            expiresAtIst=status["expiresAtIst"],
            dataPlan=status["dataPlan"],
            lastOkAt=status["lastOkAt"],
            message=None if state == "active" else feed_message(state),
            watchersPageUrl=s.watchers_url,
        )

    @server.tool(
        name="create_watcher",
        title="Create a market watcher",
        description=CREATE_WATCHER_DESCRIPTION,
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
        meta=NOAUTH_SCHEME,
    )
    async def create_watcher(
        symbol: str,
        label: str,
        script: str,
        notify_email: str,
        check_every_minutes: int = 1,
        expires_in_days: int = 30,
        market: str = "auto",
    ) -> WatcherCreated:
        async with run_gate:
            result = await anyio.to_thread.run_sync(
                app.watchers.create,
                symbol,
                label,
                script,
                notify_email,
                check_every_minutes,
                expires_in_days,
                market,
            )
        if isinstance(result, str):
            return WatcherCreated(created=False, error=result, watcher=None)
        return WatcherCreated(created=True, error=None, watcher=result)

    @server.tool(
        name="list_watchers",
        title="List market watchers",
        description=LIST_WATCHERS_DESCRIPTION,
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, open_world_hint=False
        ),
        meta=NOAUTH_SCHEME,
    )
    async def list_watchers() -> WatcherList:
        rows = await anyio.to_thread.run_sync(app.watchers.list_all)
        return WatcherList(
            watchers=rows, pageUrl=s.watchers_url, managing="page only; you cannot modify watchers"
        )

    @server.custom_route("/watchers", methods=["GET"])
    async def watchers_page(request: Request) -> HTMLResponse:
        notice = ""
        if request.query_params.get("error"):
            notice = pages.error_box(request.query_params["error"])
        cards = await anyio.to_thread.run_sync(_cards, app)
        feed = await anyio.to_thread.run_sync(app.feed.status)
        return HTMLResponse(
            pages.watchers_page(
                cards, s.watchers_url, feed, notice, s.skill_url, s.skill_md_url, s.mcp_url
            )
        )

    @server.custom_route("/watchers/{wid}/{action}", methods=["POST"])
    async def watchers_action(request: Request) -> Response:
        wid, action = str(request.path_params["wid"]), str(request.path_params["action"])
        if action not in ("pause", "resume", "cancel"):
            return JSONResponse({"error": "unknown action"}, status_code=404)
        error = await anyio.to_thread.run_sync(app.watchers.page_action, wid, action)
        if error == "not found":
            return JSONResponse({"error": "not found"}, status_code=404)
        target = s.watchers_url + (
            f"?error={httpx.URL(f'/?e={error}').params['e']}" if error else ""
        )
        return RedirectResponse(target, status_code=303)

    return server


def _cards(app: App) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    return [(r, app.watchers.fires_for(str(r["id"]))) for r in app.watchers.rows()]


def transport_security(settings: Settings) -> TransportSecuritySettings:
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*"]
    netloc = urlparse(settings.public_url).netloc
    if netloc and not netloc.startswith(("127.0.0.1", "localhost")):
        hosts += [netloc, f"{netloc}:*"]
        origins += [f"https://{netloc}", f"http://{netloc}"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def _admin_ok(request: Request, settings: Settings) -> bool:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    return bool(token) and hmac.compare_digest(token, settings.admin_secret)


def build_asgi(server: MCPServer[Any], app: App) -> Starlette:
    """One Starlette app: admin, gateway and health routes first, then the MCP app at the root."""
    s = app.settings
    mcp_app = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=512 * 1024,
        transport_security=transport_security(s),
        host=s.host,
    )

    async def health(_: Request) -> JSONResponse:
        feed = await anyio.to_thread.run_sync(app.feed.status)
        return JSONResponse({"ok": True, "service": "slash-dhan-mcp", "feed": feed["status"]})

    async def admin_status(request: Request) -> JSONResponse:
        if not _admin_ok(request, s):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        status = await anyio.to_thread.run_sync(app.feed.status)
        armed = await anyio.to_thread.run_sync(
            app.db.one, "SELECT count(*) AS n FROM watchers WHERE status = 'ARMED'"
        )
        status["armedWatchers"] = int(armed["n"]) if armed else 0
        return JSONResponse(status)

    async def admin_token(request: Request) -> JSONResponse:
        if not _admin_ok(request, s):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.method == "DELETE":
            status = await anyio.to_thread.run_sync(app.feed.clear)
            paused = await anyio.to_thread.run_sync(
                app.watchers.pause_for_feed, FEED_REASONS["unset"]
            )
            status["pausedWatchers"] = paused
            return JSONResponse(status)
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        client_id = str(body.get("dhan_client_id") or "")
        token = str(body.get("access_token") or "")
        if not client_id or not token:
            return JSONResponse(
                {"error": "dhan_client_id and access_token are required"}, status_code=400
            )
        try:
            status = await anyio.to_thread.run_sync(app.feed.set_token, client_id, token)
        except FeedError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        resumed = await anyio.to_thread.run_sync(app.watchers.resume_after_feed)
        status["resumedWatchers"] = resumed
        return JSONResponse(status)

    async def gateway(request: Request) -> Response:
        return await app.gateway.handle(request)

    async def scrip_master(_: Request) -> Response:
        try:
            path = await anyio.to_thread.run_sync(app.master.path)
        except httpx.HTTPError as e:
            return PlainTextResponse(f"scrip master unavailable: {e}", status_code=502)
        return FileResponse(path, media_type="text/csv")

    async def skill_md(_: Request) -> Response:
        try:
            text = await anyio.to_thread.run_sync(s.skill_file.read_bytes)
        except OSError:
            return PlainTextResponse("skill file not available", status_code=404)
        return Response(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="SKILL.md"'},
        )

    async def skill_zip(_: Request) -> Response:
        try:
            text = await anyio.to_thread.run_sync(s.skill_file.read_bytes)
        except OSError:
            return PlainTextResponse("skill file not available", status_code=404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{SKILL_NAME}/SKILL.md", text)
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{SKILL_ZIP_NAME}"'},
        )

    async def openai_apps_challenge(_: Request) -> PlainTextResponse:
        if not s.openai_apps_challenge:
            return PlainTextResponse("not configured", status_code=404)
        return PlainTextResponse(s.openai_apps_challenge)

    async def sweep(request: Request) -> JSONResponse:
        secret = s.sweep_secret
        if not secret or not hmac.compare_digest(request.headers.get("x-sweep-secret", ""), secret):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        force = bool(body.get("force"))
        gap = body.get("min_gap_minutes")
        report = await anyio.to_thread.run_sync(
            app.watchers.sweep, force, int(gap) if force and gap is not None else None
        )
        return JSONResponse(report)

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/admin/feed/status", admin_status, methods=["GET"]),
            Route("/admin/feed/token", admin_token, methods=["PUT", "POST", "DELETE"]),
            Route("/dhan/{path:path}", gateway, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
            Route("/scrip-master.csv", scrip_master, methods=["GET"]),
            Route(f"/{SKILL_ZIP_NAME}", skill_zip, methods=["GET"]),
            Route("/SKILL.md", skill_md, methods=["GET"]),
            Route("/.well-known/openai-apps-challenge", openai_apps_challenge, methods=["GET"]),
            Route("/internal/sweep", sweep, methods=["POST"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.router.lifespan_context,
    )


def serve(app: App) -> None:
    server = build_server(app)
    s = app.settings
    sweeper = Sweeper(app)
    sweeper.start()
    log.info("slash-dhan-mcp on http://%s:%s  public %s", s.host, s.port, s.mcp_url)
    try:
        uvicorn.run(build_asgi(server, app), host=s.host, port=s.port, log_level="info")
    finally:
        sweeper.stop()


def _env_file(explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in (Path(".env"), Path("..") / ".env"):
        if candidate.is_file():
            return str(candidate)
    return ".env"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slash-dhan-mcp")
    parser.add_argument(
        "--env-file", default=None, help="KEY=VALUE file to load first (default: .env)"
    )
    parser.add_argument("--migrate", action="store_true", help="apply migrations and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(_env_file(args.env_file), override=False)
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"slash-dhan-mcp: {e}", file=sys.stderr)
        return 2
    if args.migrate:
        db = Database(settings.db_path)
        applied = db.migrate()
        db.close()
        print("applied: " + (", ".join(applied) or "nothing"))
        return 0
    app = build_app(settings)
    try:
        serve(app)
    finally:
        app.close()
    return 0
