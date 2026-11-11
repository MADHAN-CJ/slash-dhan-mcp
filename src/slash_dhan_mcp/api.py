"""Plain HTTP interface to the four MCP tools, for chat clients that have no MCP support.

Mounted at `/api/v1` by the server. It calls the same services the MCP tools use (runner,
feed, watchers) and shares their concurrency gate; the MCP tool code is not involved.
Unauthenticated, like the MCP itself (internal host). Browser clients are allowed by CORS.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import anyio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .analytics import SCRIPT_PROPERTY_LIMIT, clip, library_calls
from .feed import Creds

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .server import App

API_VERSION = "1"
MAX_BODY_BYTES = 512 * 1024  # the same cap the MCP transport applies
CALLER = {"host": "api", "user_agent": None, "client_name": None}


def build_api(app: App, mcp_server: MCPServer[Any]) -> Starlette:
    from .server import feed_message

    s = app.settings

    async def read_json(request: Request) -> dict[str, Any] | JSONResponse:
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse({"error": "body too large (max 512 KB)"}, status_code=413)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        return body

    async def run(request: Request) -> JSONResponse:
        body = await read_json(request)
        if isinstance(body, JSONResponse):
            return body
        code = str(body.get("code") or "")
        if not code.strip():
            return JSONResponse(
                {"error": "Empty script. Pass the complete Python source in `code`."},
                status_code=400,
            )
        try:
            timeout = int(body.get("timeout_seconds") or 0)
        except (TypeError, ValueError):
            return JSONResponse({"error": "timeout_seconds must be an integer"}, status_code=400)
        creds = await anyio.to_thread.run_sync(app.feed.creds)
        if not isinstance(creds, Creds):
            app.analytics.capture(
                "run_python", **CALLER, ok=False, feed=creds, script_bytes=len(code.encode())
            )
            return JSONResponse(
                {"ok": False, "feed": creds, "message": feed_message(creds)}, status_code=503
            )
        timeout = timeout or s.script_timeout_s
        async with app.run_gate:
            result = await anyio.to_thread.run_sync(app.runner.run, code, timeout)
        after = await anyio.to_thread.run_sync(app.feed.creds)
        feed = "active" if isinstance(after, Creds) else after
        app.analytics.capture(
            "run_python",
            **CALLER,
            ok=result.exit_code == 0,
            feed=feed,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            truncated=result.truncated,
            timeout_s=timeout,
            script_bytes=len(code.encode()),
            script=clip(code, SCRIPT_PROPERTY_LIMIT),
            library_calls=library_calls(code),
            dhan_calls=result.dhan_calls,
            dhan_call_count=sum(result.dhan_calls.values()),
            error=None if result.exit_code == 0 else clip(result.stderr[-300:], 300),
        )
        return JSONResponse(
            {
                "ok": result.exit_code == 0,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
                "feed": feed,
                "message": None if feed == "active" else feed_message(feed),
            }
        )

    async def feed_status(_: Request) -> JSONResponse:
        status = await anyio.to_thread.run_sync(app.feed.status)
        state = str(status["status"])
        app.analytics.capture("feed_status", **CALLER, feed=state)
        return JSONResponse(
            {
                "feed": state,
                "dhanClientId": status["dhanClientId"],
                "expiresAt": status["expiresAt"],
                "expiresAtIst": status["expiresAtIst"],
                "renewsAtIst": status["renewAfterIst"] if state == "active" else None,
                "dataPlan": status["dataPlan"],
                "lastOkAt": status["lastOkAt"],
                "message": None if state == "active" else feed_message(state),
                "watchersPageUrl": s.watchers_url,
            }
        )

    async def create_watcher(request: Request) -> JSONResponse:
        body = await read_json(request)
        if isinstance(body, JSONResponse):
            return body
        missing = [k for k in ("symbol", "label", "script", "notify_email") if not body.get(k)]
        if missing:
            return JSONResponse({"error": f"missing: {', '.join(missing)}"}, status_code=400)
        try:
            every = int(body.get("check_every_minutes") or 1)
            days = int(body.get("expires_in_days") or 30)
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "check_every_minutes and expires_in_days must be integers"},
                status_code=400,
            )
        async with app.run_gate:
            result = await anyio.to_thread.run_sync(
                app.watchers.create,
                str(body["symbol"]),
                str(body["label"]),
                str(body["script"]),
                str(body["notify_email"]),
                every,
                days,
                str(body.get("market") or "auto"),
                CALLER,
            )
        if isinstance(result, str):
            return JSONResponse({"created": False, "error": result, "watcher": None})
        return JSONResponse({"created": True, "error": None, "watcher": result}, status_code=201)

    async def list_watchers(_: Request) -> JSONResponse:
        rows = await anyio.to_thread.run_sync(app.watchers.list_all)
        app.analytics.capture("list_watchers", **CALLER, count=len(rows))
        return JSONResponse({"watchers": rows, "pageUrl": s.watchers_url})

    async def tools(_: Request) -> JSONResponse:
        """The four tools as the MCP advertises them (name, description, schemas), plus the
        HTTP route that carries each, so a chat client can build its function definitions."""
        routes = {
            "run_python": ("POST", "/api/v1/run"),
            "feed_status": ("GET", "/api/v1/feed"),
            "create_watcher": ("POST", "/api/v1/watchers"),
            "list_watchers": ("GET", "/api/v1/watchers"),
        }
        listed = await mcp_server.list_tools()
        out = []
        for t in listed:
            method, path = routes.get(t.name, ("", ""))
            out.append(
                {
                    "name": t.name,
                    "title": t.title,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "outputSchema": t.output_schema,
                    "method": method,
                    "path": path,
                }
            )
        return JSONResponse({"version": API_VERSION, "tools": out})

    return Starlette(
        routes=[
            Route("/tools", tools, methods=["GET"]),
            Route("/run", run, methods=["POST"]),
            Route("/feed", feed_status, methods=["GET"]),
            Route("/watchers", create_watcher, methods=["POST"]),
            Route("/watchers", list_watchers, methods=["GET"]),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["content-type"],
                max_age=3600,
            )
        ],
    )
