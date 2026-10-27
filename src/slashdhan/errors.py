from __future__ import annotations

from collections.abc import Callable
from typing import Any

FEED_EXPIRED_MSG = (
    "The /markets Dhan data feed token has expired (Dhan tokens last 24 hours) and is being "
    "refreshed by the operator. Tell the user to try again in a few minutes; nothing in this "
    "script can refresh it. Do not retry now."
)
DATA_PLAN_MSG = (
    "The feed account has no Dhan Data-API subscription (Dhan error 806): live quotes, candles "
    "and option chains are unavailable, while positions, holdings, funds, orders and trades "
    "still work. Holdings rows carry a lastTradedPrice you can use instead."
)
ENDPOINT_DENIED_MSG = (
    "Dhan rejected this call although the feed token is valid (verified with a funds probe); "
    "the failure is endpoint-specific, not a login problem. Dhan said: {detail}"
)


class SlashDhanError(Exception):
    """Base class for every error raised by slashdhan."""


class DhanError(SlashDhanError):
    """Dhan answered with an error (status 502 = network failure, 200 = status:failure body)."""

    def __init__(self, message: str, status: int, code: str = "", body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body


class TokenExpired(DhanError):
    """The feed's Dhan access token is dead; the operator refreshes it, scripts cannot."""


class DataPlanMissing(DhanError):
    """Dhan error 806: the feed account has no Data-API subscription. The token is fine."""


class RateLimited(DhanError):
    """Dhan throttled the feed (HTTP 429 / DH-904); every caller pauses for a few seconds."""


class CallBudgetExceeded(SlashDhanError):
    """A single script made more Dhan requests than its budget allows."""


class InstrumentNotFound(SlashDhanError, ValueError):
    """No instrument matched the given symbol, id or underlying."""


class GatewayError(SlashDhanError):
    """The local gateway rejected the request before it reached Dhan."""


def error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("errorMessage", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    remarks = payload.get("remarks")
    if isinstance(remarks, dict):
        value = remarks.get("message") or remarks.get("error_message")
        if isinstance(value, str) and value:
            return value
    data = payload.get("data")
    if isinstance(data, dict) and data:
        return "; ".join(f"{k}: {v}" for k, v in data.items())
    code = payload.get("errorCode")
    return str(code) if code else None


def error_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    code = payload.get("errorCode")
    if isinstance(code, str) and code:
        return code
    remarks = payload.get("remarks")
    if isinstance(remarks, dict) and remarks.get("error_code"):
        return str(remarks["error_code"])
    data = payload.get("data")
    if isinstance(data, dict) and len(data) == 1:
        return str(next(iter(data)))
    return ""


def is_data_plan_error(payload: Any, message: str) -> bool:
    raw = f"{message} {payload!r}".lower()
    return "806" in raw or "not subscribed" in raw


def classify(
    status: int,
    payload: Any,
    fallback: str,
    token_alive: Callable[[], bool] | None = None,
) -> DhanError:
    message = error_message(payload) or fallback
    code = error_code(payload)
    if is_data_plan_error(payload, message):
        return DataPlanMissing(DATA_PLAN_MSG, status, code, payload)
    if status == 429 or code == "DH-904" or "rate limit" in message.lower():
        return RateLimited(f"Dhan rate limit hit: {message}", status, code, payload)
    if status in (401, 403):
        if token_alive is not None and token_alive():
            return DhanError(ENDPOINT_DENIED_MSG.format(detail=message), status, code, payload)
        return TokenExpired(FEED_EXPIRED_MSG, status, code, payload)
    return DhanError(
        f"Dhan error {status} {code}: {message}".replace("  ", " "), status, code, payload
    )
