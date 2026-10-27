from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import Settings, settings
from .errors import CallBudgetExceeded, DhanError, GatewayError, classify, error_message
from .ratelimit import RateLimiter, chain_key, kind_for

CHARTS_TIMEOUT_S = 30.0
ROLLING_OPTION_TIMEOUT_S = 60.0
PROBE_PATH = "/fundlimit"


@dataclass(frozen=True)
class Response:
    status: int
    content: bytes
    content_type: str

    def json(self) -> Any:
        try:
            return json.loads(self.content.decode("utf-8"))
        except ValueError:
            return None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Transport(Protocol):
    def request(self, method: str, path: str, body: Any, timeout_s: float) -> Response: ...

    def token_alive(self) -> bool: ...


def _timeout_for(path: str, default: float) -> float:
    p = path.lstrip("/")
    if p.startswith("charts/rollingoption"):
        return ROLLING_OPTION_TIMEOUT_S
    if p.startswith("charts"):
        return CHARTS_TIMEOUT_S
    return default


def _wrap(r: httpx.Response) -> Response:
    return Response(r.status_code, r.content, r.headers.get("content-type", ""))


class DirectTransport:
    """Talks to api.dhan.co with the feed's client id and access token, rate limited."""

    def __init__(
        self,
        client_id: str,
        access_token: str,
        base_url: str,
        limiter: RateLimiter | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not client_id or not access_token:
            raise GatewayError(
                "DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are required for direct access"
            )
        self.base_url = base_url.rstrip("/")
        self.limiter = limiter or RateLimiter()
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": access_token,
            "client-id": client_id,
        }
        self._client = client or httpx.Client()

    def request(self, method: str, path: str, body: Any, timeout_s: float) -> Response:
        self.limiter.acquire(kind_for(path), chain_key(body))
        r = self._client.request(
            method,
            f"{self.base_url}/{path.lstrip('/')}",
            json=body,
            headers=self._headers,
            timeout=timeout_s,
        )
        if r.status_code == 429:
            self.limiter.hold()
        return _wrap(r)

    def token_alive(self) -> bool:
        try:
            return self.request("GET", PROBE_PATH, None, 10.0).status < 400
        except httpx.HTTPError:
            return False


class GatewayTransport:
    """Talks to the local slash-dhan-mcp gateway, which holds the Dhan token and the limiter."""

    def __init__(
        self, gateway_url: str, run_token: str, client: httpx.Client | None = None
    ) -> None:
        if not gateway_url or not run_token:
            raise GatewayError("SLASHDHAN_GATEWAY_URL and SLASHDHAN_RUN_TOKEN are required")
        self.base_url = gateway_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {run_token}"}
        self._client = client or httpx.Client()

    def request(self, method: str, path: str, body: Any, timeout_s: float) -> Response:
        r = self._client.request(
            method,
            f"{self.base_url}/dhan/{path.lstrip('/')}",
            json=body,
            headers=self._headers,
            timeout=timeout_s + 5.0,
        )
        return _wrap(r)

    def token_alive(self) -> bool:
        try:
            return self.request("GET", PROBE_PATH, None, 10.0).status < 400
        except httpx.HTTPError:
            return False


class Session:
    """One script's view of Dhan: a transport plus the per-script call budget."""

    def __init__(self, transport: Transport, cfg: Settings) -> None:
        self.transport = transport
        self.cfg = cfg
        self.calls = 0
        self._lock = threading.Lock()

    def _spend(self) -> None:
        with self._lock:
            if self.calls >= self.cfg.call_budget:
                raise CallBudgetExceeded(
                    f"This script already made {self.calls} Dhan requests (budget "
                    f"{self.cfg.call_budget}). Batch ids into one ltp/quote call, fetch fewer "
                    "strikes or wider candle intervals, or split the work across scripts."
                )
            self.calls += 1

    def request(self, method: str, path: str, body: Any = None) -> Response:
        self._spend()
        timeout = _timeout_for(path, self.cfg.timeout_s)
        try:
            return self.transport.request(method, path, body, timeout)
        except httpx.HTTPError as e:
            raise DhanError(f"Dhan request failed: {e.__class__.__name__}: {e}", 502) from e

    def call(self, method: str, path: str, body: Any = None) -> Any:
        """One Dhan call: errors become typed exceptions, the {data,status} envelope is removed."""
        r = self.request(method, path, body)
        payload = r.json()
        if r.status >= 400:
            probe = None if path.rstrip("/").endswith(PROBE_PATH) else self.transport.token_alive
            raise classify(r.status, payload, f"HTTP {r.status} on {path}", probe)
        if isinstance(payload, dict) and str(payload.get("status", "")).lower() in (
            "failure",
            "failed",
        ):
            raise DhanError(f"Dhan {path} failed: {error_message(payload) or payload}", 200)
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self.call("POST", path, body)


_session: Session | None = None
_session_lock = threading.Lock()


def _build_transport(cfg: Settings) -> Transport:
    if cfg.uses_gateway:
        return GatewayTransport(cfg.gateway_url, cfg.run_token)
    return DirectTransport(cfg.client_id, cfg.access_token, cfg.base_url)


def session() -> Session:
    global _session
    with _session_lock:
        if _session is None:
            cfg = settings()
            _session = Session(_build_transport(cfg), cfg)
        return _session


def configure(transport: Transport | None = None, cfg: Settings | None = None) -> Session:
    """Install an explicit transport (tests, the gateway process) and reset the call budget."""
    global _session
    with _session_lock:
        c = cfg or settings()
        _session = Session(transport or _build_transport(c), c)
        return _session


def reset() -> None:
    global _session
    with _session_lock:
        _session = None


__all__ = [
    "DirectTransport",
    "GatewayTransport",
    "Response",
    "Session",
    "Transport",
    "configure",
    "reset",
    "session",
]
