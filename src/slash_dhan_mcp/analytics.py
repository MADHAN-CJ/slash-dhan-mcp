from __future__ import annotations

import logging
import re
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from .config import Settings

log = logging.getLogger(__name__)

SCRIPT_PROPERTY_LIMIT = 32 * 1024
TEXT_PROPERTY_LIMIT = 400
RECENT_EVENTS = 200
_IMPORT_ALIAS = re.compile(r"^\s*import\s+slashdhan\s+as\s+([A-Za-z_]\w*)", re.MULTILINE)
_FROM_IMPORT = re.compile(r"^\s*from\s+slashdhan\s+import\s+([^\n#]+)", re.MULTILINE)


class CaptureClient(Protocol):
    """The slice of `posthog.Posthog` this module uses."""

    def capture(self, event: str, **kwargs: Any) -> str | None: ...

    def shutdown(self) -> None: ...


def posthog_client(settings: Settings) -> CaptureClient | None:
    """A PostHog client when `POSTHOG_API_KEY` is set, else None (analytics stay local)."""
    if not settings.posthog_api_key:
        return None
    from posthog import Posthog

    host = urlparse(settings.public_url).netloc or "local"
    client: CaptureClient = Posthog(
        settings.posthog_api_key,
        host=settings.posthog_host,
        super_properties={"service": "slash-dhan-mcp", "public_host": host},
        disable_geoip=True,
    )
    return client


class Analytics:
    """Product analytics: every call becomes one event; nothing here may raise or block.

    Events are kept in `recent` (newest last, bounded) so tests and diagnostics can read them,
    and forwarded to PostHog when a client is configured. The MCP has no user accounts, so
    events are personless and share one distinct id (the public host); the `host` property
    (claude / chatgpt / other) is the only segmentation. Dhan credentials, run tokens and
    alert emails are never passed in.
    """

    def __init__(self, settings: Settings, client: CaptureClient | None = None) -> None:
        self._client = client
        self._distinct_id = urlparse(settings.public_url).netloc or "local"
        self._lock = threading.Lock()
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def capture(self, event: str, **properties: Any) -> None:
        props = {k: v for k, v in properties.items() if v is not None}
        with self._lock:
            self.recent.append(
                {"event": event, "at": datetime.now(UTC).isoformat(timespec="seconds"), **props}
            )
        if self._client is None:
            return
        try:
            self._client.capture(
                event,
                distinct_id=self._distinct_id,
                properties={**props, "$process_person_profile": False},
            )
        except Exception:
            log.warning("analytics capture failed for %s", event, exc_info=True)

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.recent if name is None or e["event"] == name]

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception:
            log.warning("analytics shutdown failed", exc_info=True)


def library_calls(code: str) -> list[str]:
    """Names of the slashdhan functions a script calls (`z.ltp(...)` -> `ltp`), sorted, unique."""
    aliases = set(_IMPORT_ALIAS.findall(code)) | {"slashdhan"}
    names: set[str] = set()
    for alias in aliases:
        names.update(re.findall(rf"\b{re.escape(alias)}\.([A-Za-z_]\w*)\s*\(", code))
    for group in _FROM_IMPORT.findall(code):
        for part in group.replace("(", "").replace(")", "").split(","):
            name = part.strip().split(" as ")[0].strip()
            if name and not name.startswith("_") and re.search(rf"\b{re.escape(name)}\s*\(", code):
                names.add(name)
    return sorted(names)


def host_label(
    meta: dict[str, Any] | None, headers: Any, client_name: str | None = None
) -> tuple[str, str | None, str | None]:
    """(host, user_agent, client_name): which AI host is calling, best effort from request data."""
    user_agent = None
    if headers is not None:
        try:
            raw = headers.get("user-agent")
        except Exception:
            raw = None
        user_agent = clip(raw, TEXT_PROPERTY_LIMIT) if raw else None
    haystack = f"{user_agent or ''} {client_name or ''}".lower()
    if meta and any(str(key).startswith("openai/") for key in meta):
        host = "chatgpt"
    elif "claude" in haystack or "anthropic" in haystack:
        host = "claude"
    elif "openai" in haystack or "chatgpt" in haystack:
        host = "chatgpt"
    else:
        host = "other"
    return host, user_agent, clip(client_name, TEXT_PROPERTY_LIMIT) if client_name else None


def clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit} chars]"
