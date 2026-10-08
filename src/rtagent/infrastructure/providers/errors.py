"""Actionable provider failures that never echo credentials or request bodies."""

from typing import Any

import httpx
import openai

from rtagent.domain import AppError
from rtagent.infrastructure.tracing import redact

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


def sdk_failure(exc: Exception, secret: str) -> AppError:
    """Describe an OpenAI-compatible SDK exception without its URL, headers or request body."""
    if isinstance(exc, openai.APIStatusError):
        return provider_failure(error_message(exc.body) or exc.message, secret, exc.status_code)
    if isinstance(exc, openai.APITimeoutError):
        return transport_failure(exc.__cause__ or httpx.ReadTimeout("timed out"))
    if isinstance(exc, openai.APIConnectionError):
        return transport_failure(exc.__cause__)
    return provider_failure(f"{type(exc).__name__}; check endpoint, model and credentials.")


def truncated_output(limit: int) -> AppError:
    """Explain tool arguments cut off by the output token limit instead of a protocol error."""
    return AppError(
        "provider_output_limit",
        f"The model reached its {limit}-token output limit before finishing a tool call. "
        "Increase Max output tokens in Settings, then retry.",
        502,
    )
