"""Actionable provider failures that never echo credentials or request bodies."""

from typing import Any

import httpx
import openai

from glassbox.domain import AppError
from glassbox.infrastructure.providers.chatgpt import USAGE_URL
from glassbox.infrastructure.tracing import redact

DETAIL_LIMIT = 500


def error_message(body: Any) -> str:
    """Extract the human-readable message from common provider error bodies."""
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            kind = error.get("type") or error.get("code")
            return f"{kind}: {error['message']}" if isinstance(kind, str) else error["message"]
        if isinstance(error, str):
            return error
    return body if isinstance(body, str) else ""


def provider_failure(
    detail: str, secret: str = "", status: int | None = None, details: Any = None
) -> AppError:
    """Build a bounded, redacted provider error that keeps the provider's own explanation."""
    text = redact(" ".join(detail.split()), [secret] if secret else None)[:DETAIL_LIMIT]
    prefix = f"Model request failed (HTTP {status})" if status else "Model request failed"
    return AppError("provider_error", f"{prefix}: {text}" if text else prefix + ".", 502, details)


def transport_failure(exc: BaseException | None) -> AppError:
    """Say what happened to the connection, marked transient so a pure request may be retried.

    A connection that never opened differs from one the provider or network closed while the
    response was streaming; the SDK reports both as a connection error. Runs still never
    retry a model call by themselves."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)):
        detail = "could not connect to the configured base URL"
    elif isinstance(exc, httpx.TimeoutException):
        detail = "the provider did not respond in time"
    else:
        detail = "the connection to the provider closed before the response was complete"
    name = type(exc).__name__ if exc is not None else "connection error"
    return provider_failure(f"{detail} ({name}).", details={"transient": True})


def transport_cause(exc: BaseException) -> BaseException | None:
    """The transport's exception beneath an SDK connection error.

    LangChain re-raises the SDK's error as its own subclass, so the immediate cause can be
    the SDK's error rather than the transport's."""
    cause = exc.__cause__
    while isinstance(cause, openai.APIConnectionError):
        cause = cause.__cause__
    return cause


def sdk_failure(exc: Exception, secret: str) -> AppError:
    """Describe an OpenAI-compatible SDK exception without its URL, headers or request body."""
    if isinstance(exc, openai.APIStatusError):
        return provider_failure(error_message(exc.body) or exc.message, secret, exc.status_code)
    if isinstance(exc, openai.APITimeoutError):
        return transport_failure(transport_cause(exc) or httpx.ReadTimeout("timed out"))
    if isinstance(exc, openai.APIConnectionError):
        return transport_failure(transport_cause(exc))
    return provider_failure(f"{type(exc).__name__}; check endpoint, model and credentials.")


# OpenAI's error codes for requests on a ChatGPT plan, with what the user can do about them.
PLAN_ERRORS = {
    "subscription_sharing_usage_limit_exceeded": (
        "chatgpt_usage_limit",
        f"The ChatGPT plan's usage limit for Glassbox is reached. Review it under Manage usage "
        f"({USAGE_URL}), or choose another model.",
    ),
    "subscription_sharing_user_not_eligible": (
        "chatgpt_not_eligible",
        "ChatGPT plan usage is not available for this account or workspace. Choose another model.",
    ),
    "subscription_sharing_invalid_user": (
        "chatgpt_sign_in",
        "ChatGPT did not accept this sign-in. Continue with ChatGPT again in Settings → Providers.",
    ),
    "chatpass_v2_scope_not_authorized": (
        "chatgpt_sign_in",
        "The ChatGPT sign-in does not allow this request. Continue with ChatGPT again in "
        "Settings → Providers.",
    ),
    "chatpass_v2_invalid_authorization_context": (
        "chatgpt_sign_in",
        "The ChatGPT sign-in does not allow this request. Continue with ChatGPT again in "
        "Settings → Providers.",
    ),
    "subscription_sharing_unsupported_capability": (
        "chatgpt_unsupported",
        "The ChatGPT plan does not support part of this request",
    ),
    "subscription_sharing_route_not_supported": (
        "chatgpt_unsupported",
        "The ChatGPT plan does not support this kind of request",
    ),
}
PLAN_UNAVAILABLE = {
    "subscription_sharing_user_unavailable",
    "subscription_sharing_usage_unavailable",
}


def plan_failure(body: Any, status: int | None) -> AppError | None:
    """The actionable error for a failed request on a ChatGPT plan, or None if not one of them.

    These failures carry a machine-readable code; their messages are not shown, since
    validation errors on this route may echo the request."""
    detail = body if isinstance(body, dict) else {}
    for _ in range(4):
        inner = detail.get("error", detail.get("detail"))
        if not isinstance(inner, dict):
            break
        detail = inner
    code = detail.get("code") or detail.get("error")
    code = code if isinstance(code, str) else ""
    if code in PLAN_UNAVAILABLE:
        return provider_failure(
            "ChatGPT plan usage is temporarily unavailable; try again shortly.",
            status=status,
            details={"transient": True},
        )
    if code in PLAN_ERRORS:
        kind, message = PLAN_ERRORS[code]
        param = detail.get("param")
        if kind == "chatgpt_unsupported":
            message += f" ({param})." if isinstance(param, str) and param.isidentifier() else "."
        return AppError(kind, message, 502)
    if status == 401:
        return AppError("chatgpt_sign_in", PLAN_ERRORS["subscription_sharing_invalid_user"][1], 502)
    return None


def truncated_output(limit: int) -> AppError:
    """Explain tool arguments cut off by the output token limit instead of a protocol error."""
    return AppError(
        "provider_output_limit",
        f"The model reached its {limit}-token output limit before finishing a tool call. "
        "Increase Max output tokens in Settings, then retry.",
        502,
    )
