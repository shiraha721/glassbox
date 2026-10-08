"""Explicit request building and captured streaming for the OpenAI Responses API.

Like the Messages adapter, the prepared body is what is sent: the request hook compares the
outgoing JSON with it, records it, and only then fills in attached images. Requests use
`store: false`, so nothing is kept on the provider between calls; reasoning carries across a
Run's tool calls as encrypted reasoning items echoed back in the next request."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from rtagent.domain import AppError, CompiledPrompt, Message, ProviderConfig, ToolCall
from rtagent.infrastructure.providers.attachments import DATA_URL, fill_attachments
from rtagent.infrastructure.providers.errors import (
    error_message,
    provider_failure,
    transport_failure,
    truncated_output,
)
from rtagent.infrastructure.providers.messages import sse_events

OUTPUT_LIMIT = 1024 * 1024


def _content(message: Message) -> str | list[dict[str, Any]]:
    """Plain text, or input parts when the message carries images."""
    if not message.attachments:
        return message.content
    return [
        {"type": "input_text", "text": message.content},
        *(
            {"type": "input_image", "image_url": DATA_URL + item.hash, "detail": "auto"}
            for item in message.attachments
        ),
    ]


def prepare_responses(
    config: ProviderConfig, compiled: CompiledPrompt, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Serialize canonical messages as Responses input items with unchanged roles and call IDs."""
    if not config.model.strip():
        raise AppError(
            "provider_not_configured",
            "No model is selected. Choose one below the message box or in Settings → Models.",
        )
    items: list[dict[str, Any]] = []
    pending: set[str] = set()
    for message in compiled.messages:
        if message.role != "tool" and pending:
            raise AppError(
                "tool_protocol", "Pair every pending function call before another message."
            )
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise AppError("tool_protocol", "Tool result must match a pending function call.")
            pending.remove(message.tool_call_id)
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
            continue
        for call in message.tool_calls:
            if not call.id or call.id in pending:
                raise AppError(
                    "tool_protocol", "Function call IDs must be present and unique in a batch."
                )
            pending.add(call.id)
        # Within a Run, a response holding reasoning goes back exactly as received (minus
        # provider IDs, which unstored responses cannot resolve). Earlier turns are rebuilt.
        if message.blocks and message.source != "history":
            # Reasoning without encrypted content (no effort set) cannot be resolved by an
            # unstored request; it stays in the blocks only for its readable summary.
            items.extend(
                block
                for block in message.blocks
                if block.get("type") != "reasoning" or block.get("encrypted_content")
            )
            continue
        if message.content or not message.tool_calls or message.attachments:
            items.append({"role": message.role, "content": _content(message)})
        items.extend(
            {
                "type": "function_call",
                "call_id": call.id,
                "name": call.name,
                "arguments": json.dumps(call.args, ensure_ascii=False),
            }
            for call in message.tool_calls
        )
    if pending:
        raise AppError("tool_protocol", "Unpaired function calls cannot be sent to a model.")
    body: dict[str, Any] = {
        "model": config.model,
        "input": items,
        "max_output_tokens": config.max_output_tokens,
        "stream": True,
        "store": False,
    }
    if config.temperature is not None:
        body["temperature"] = config.temperature
    if config.reasoning_effort:
        body["reasoning"] = {"effort": config.reasoning_effort}
        # Unstored responses keep reasoning only as encrypted items the next request echoes.
        body["include"] = ["reasoning.encrypted_content"]
    if config.show_reasoning:
        body.setdefault("reasoning", {})["summary"] = "auto"
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "name": item["name"],
                "description": item["description"],
                "parameters": item["input_schema"],
                "strict": False,
            }
            for item in tools
        ]
    return body


def _without_id(item: dict[str, Any]) -> dict[str, Any]:
    """An output item in the shape an unstored follow-up request accepts as input."""
    return {key: value for key, value in item.items() if key != "id"}


async def stream_responses(
    config: ProviderConfig,
    prepared: dict[str, Any],
    on_wire: Callable[[dict[str, Any] | None], Awaitable[None]],
    key: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Stream text deltas, then build the result from the completed response's output items."""

    async def capture(request: httpx.Request) -> None:
        """Compare the actual JSON with the preview body without recording the key header."""
        body = json.loads(request.content)
        if body != prepared:
            raise AppError("request_mismatch", "Responses request differs from its preview.", 409)
        await on_wire(body)
        fill_attachments(request)

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    final: dict[str, Any] | None = None
    size = 0
    try:
        async with httpx.AsyncClient(
            event_hooks={"request": [capture]}, timeout=90, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "POST", config.base_url.rstrip("/") + "/responses", json=prepared, headers=headers
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread())[:4096]
                    try:
                        detail = error_message(json.loads(body))
                    except ValueError:
                        detail = body.decode("utf-8", errors="replace")
                    raise provider_failure(detail, key, response.status_code)
                async for event in sse_events(response):
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        text = str(event.get("delta", ""))
                        size += len(text.encode())
                        if size > OUTPUT_LIMIT:
                            raise AppError(
                                "provider_protocol", "Responses output exceeds 1 MiB.", 502
                            )
                        yield {"kind": "delta", "text": text}
                    elif kind in {"response.completed", "response.incomplete"}:
                        final = event.get("response")
                        break
                    elif kind == "response.failed":
                        failed = event.get("response") or {}
                        raise provider_failure(
                            error_message({"error": failed.get("error")}) or "response failed", key
                        )
                    elif kind == "error":
                        raise provider_failure(error_message(event) or "streaming error", key)
    except AppError:
        raise
    except httpx.TransportError as exc:
        raise transport_failure(exc) from None
    except Exception as exc:
        raise provider_failure(f"{type(exc).__name__}; no automatic retry.", key) from None
    if not isinstance(final, dict):
        raise AppError("provider_protocol", "Responses stream ended without a response.", 502)
    output = [item for item in final.get("output") or [] if isinstance(item, dict)]
    text, calls, refusal = "", [], None
    try:
        for item in output:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        text += part.get("text", "")
                    elif part.get("type") == "refusal":
                        refusal = part.get("refusal") or ""
            elif item.get("type") == "function_call":
                arguments = item.get("arguments") or "{}"
                calls.append(
                    ToolCall(id=item["call_id"], name=item["name"], args=json.loads(arguments))
                )
    except (ValueError, TypeError, KeyError):
        details = final.get("incomplete_details") or {}
        if details.get("reason") == "max_output_tokens":
            raise truncated_output(config.max_output_tokens) from None
        raise AppError("provider_protocol", "Invalid Responses function call.", 502) from None
    if refusal is not None:
        raise AppError(
            "provider_refusal",
            (
                f"The model declined this request: {refusal[:300]}"
                if refusal
                else "The model declined this request. Rephrase the task or choose another model."
            ),
            502,
        )
    if final.get("status") == "incomplete":
        reason = (final.get("incomplete_details") or {}).get("reason")
        if reason == "max_output_tokens" and not text and not calls:
            raise truncated_output(config.max_output_tokens)
        if reason == "content_filter":
            raise AppError("provider_refusal", "The provider filtered this response.", 502)
    if len({call.id for call in calls}) != len(calls):
        raise AppError("provider_protocol", "Function call IDs must be unique.", 502)
    if not text and not calls:
        raise AppError("provider_protocol", "Responses returned no supported content.", 502)
    replay = (
        [_without_id(item) for item in output]
        if any(item.get("type") == "reasoning" for item in output)
        else []
    )
    raw = final.get("usage") or {}
    usage: dict[str, Any] = {
        "input_tokens": raw.get("input_tokens"),
        "output_tokens": raw.get("output_tokens"),
    }
    cached = (raw.get("input_tokens_details") or {}).get("cached_tokens")
    if cached is not None:
        usage["cache_read_input_tokens"] = cached
    yield {
        "kind": "result",
        "message": Message(role="assistant", content=text, tool_calls=calls, blocks=replay),
        "usage": usage,
        "metadata": {
            "id": final.get("id"),
            "model": final.get("model"),
            "status": final.get("status"),
        },
    }
