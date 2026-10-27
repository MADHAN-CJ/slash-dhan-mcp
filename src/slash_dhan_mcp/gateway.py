from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

import anyio
import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from slashdhan.errors import is_data_plan_error
from slashdhan.ratelimit import RateLimiter, chain_key, kind_for

from .config import Settings
from .feed import Creds, Feed

log = logging.getLogger(__name__)

READ_GET_PREFIXES = ("profile", "positions", "holdings", "fundlimit", "orders", "trades", "ledger")
READ_POST_PATHS = frozenset(
    {
        "marketfeed/ltp",
        "marketfeed/ohlc",
        "marketfeed/quote",
        "charts/intraday",
        "charts/historical",
        "charts/rollingoption",
        "optionchain",
        "optionchain/expirylist",
    }
)
RUN_TOKEN_TTL_S = 300
UPSTREAM_TIMEOUT_S = 75.0
PROBE_CACHE_S = 30.0


class RunTokens:
    """Short-lived bearer tokens handed to one script run; the gateway accepts nothing else."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}

    def issue(self, ttl_s: int = RUN_TOKEN_TTL_S) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic()
            self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
            self._tokens[token] = now + ttl_s
        return token

    def valid(self, token: str) -> bool:
        with self._lock:
            exp = self._tokens.get(token)
            return exp is not None and exp > time.monotonic()

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)


def allowed(method: str, path: str) -> bool:
    p = path.strip("/")
    if method == "GET":
        return any(p == base or p.startswith(base + "/") for base in READ_GET_PREFIXES)
    if method == "POST":
        return p in READ_POST_PATHS
    return False


class Gateway:
    """Forwards read-only Dhan calls for a run token, adding the feed's token and the app-wide

    rate limiter. Scripts never see the Dhan token; writes never pass.
    """

    def __init__(
        self,
        settings: Settings,
        feed: Feed,
        tokens: RunTokens,
        on_feed_expired: Callable[[str], None],
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._feed = feed
        self._tokens = tokens
        self._on_feed_expired = on_feed_expired
        self._limiter = limiter or RateLimiter()
        self._client = httpx.AsyncClient(transport=transport, timeout=UPSTREAM_TIMEOUT_S)
        self._probe_lock = threading.Lock()
        self._probe: tuple[str, float, bool] | None = None
        self.calls = 0

    @property
    def limiter(self) -> RateLimiter:
        return self._limiter

    async def handle(self, request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not token or not self._tokens.valid(token):
            return _dhan_error(401, "DH-901", "Run token missing or expired")
        path = str(request.path_params["path"])
        if not allowed(request.method, path):
            return _dhan_error(
                403, "DH-902", f"{request.method} /{path} is not a read endpoint of this gateway"
            )
        creds = await anyio.to_thread.run_sync(self._feed.creds)
        if not isinstance(creds, Creds):
            return _dhan_error(401, "DH-901", f"The /markets Dhan feed token is {creds}")
        raw = await request.body()
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                return _dhan_error(400, "DH-905", "Request body must be JSON")
        kind = kind_for(path)
        await anyio.to_thread.run_sync(self._limiter.acquire, kind, chain_key(body))
        self.calls += 1
        try:
            upstream = await self._client.request(
                request.method,
                f"{self._settings.dhan_base_url}/{path.lstrip('/')}",
                json=body if request.method == "POST" else None,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "access-token": creds.access_token,
                    "client-id": creds.client_id,
                },
            )
        except httpx.HTTPError as e:
            return _dhan_error(502, "DH-909", f"Dhan unreachable: {e.__class__.__name__}")
        if upstream.status_code == 429:
            self._limiter.hold()
        elif upstream.status_code in (401, 403):
            await self._check_feed(creds, upstream)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    async def _check_feed(self, creds: Creds, upstream: httpx.Response) -> None:
        try:
            payload: Any = upstream.json()
        except ValueError:
            payload = None
        if is_data_plan_error(payload, upstream.text[:300]):
            return
        if not await self._token_alive(creds):
            await anyio.to_thread.run_sync(
                self._on_feed_expired, f"Dhan {upstream.status_code}: {upstream.text[:200]}"
            )

    async def _token_alive(self, creds: Creds) -> bool:
        with self._probe_lock:
            cached = self._probe
            if cached and cached[0] == creds.access_token and cached[1] > time.monotonic():
                return cached[2]
        await anyio.to_thread.run_sync(self._limiter.acquire, "general", "")
        try:
            r = await self._client.get(
                f"{self._settings.dhan_base_url}/fundlimit",
                headers={"access-token": creds.access_token, "client-id": creds.client_id},
            )
            alive = r.status_code < 400
        except httpx.HTTPError:
            alive = True
        with self._probe_lock:
            self._probe = (creds.access_token, time.monotonic() + PROBE_CACHE_S, alive)
        return alive

    async def aclose(self) -> None:
        await self._client.aclose()


def _dhan_error(status: int, code: str, message: str) -> JSONResponse:
    error_type = {
        "DH-901": "Invalid_Authentication",
        "DH-902": "Invalid_Access",
        "DH-905": "Input_Exception",
        "DH-909": "Network_Error",
    }.get(code, "Internal_Server_Error")
    return JSONResponse(
        {"errorType": error_type, "errorCode": code, "errorMessage": message}, status_code=status
    )
