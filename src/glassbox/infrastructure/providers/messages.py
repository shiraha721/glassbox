"""Explicit text/tool adaptation and captured streaming for the Anthropic Messages protocol."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from glassbox.domain import AppError, CompiledPrompt, Message, ProviderConfig, ToolCall
from glassbox.infrastructure.providers.attachments import BASE64, fill_attachments
from glassbox.infrastructure.providers.errors import (
    error_message,
    provider_failure,
    transport_failure,
    truncated_output,
)

# Content blocks the adapter understands. Thinking is on by default for current Claude models.
BLOCK_TYPES = {"text", "tool_use", "thinking", "redacted_thinking"}


def prepare_anthropic(
    config: ProviderConfig, compiled: CompiledPrompt, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Extract only leading system blocks and preserve ordered text/tool-use/result identities."""
    if not config.model.strip():
        raise AppError(
            "provider_not_configured",
            "No model is selected. Choose one below the message box or in Settings → Models.",
        )
    if config.temperature is not None and config.temperature > 1:
        raise AppError("provider_temperature", "Anthropic Messages temperature must not exceed 1.")
    systems, messages, pending = [], [], set()
    for item in compiled.messages:
        if item.role == "system":
            if messages:
                raise AppError(
                    "unsupported_role_mapping",
                    "Messages cannot move a system block from inside conversation history. Put system blocks before history.",
                )
            systems.append({"type": "text", "text": item.content})
            continue
        if item.role != "tool" and pending:
            raise AppError(
                "tool_protocol", "Pair every pending tool_use before another conversation message."
            )
        if item.role == "tool":
            if item.tool_call_id not in pending:
                raise AppError(
                    "tool_protocol", "Tool result must match a pending assistant tool_use."
                )
            pending.remove(item.tool_call_id)
            block = {
                "type": "tool_result",
                "tool_use_id": item.tool_call_id,
                "content": item.content,
            }
            if (
                messages
                and messages[-1]["role"] == "user"
                and all(value["type"] == "tool_result" for value in messages[-1]["content"])
            ):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
            continue
        blocks = (
            [{"type": "text", "text": item.content}] if item.content or not item.tool_calls else []
        )
        # Images go before the text that refers to them, as the Messages API recommends.
        blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": attachment.media_type,
                    "data": BASE64 + attachment.hash,
                },
            }
            for attachment in item.attachments
        ] + blocks
        for call in item.tool_calls:
            if not call.id or call.id in pending:
                raise AppError(
                    "tool_protocol", "Tool-use IDs must be present and unique within their batch."
                )
            pending.add(call.id)
            blocks.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
            )
        # Within a Run, thinking blocks go back exactly as received (the API requires them
        # before tool results). Earlier turns are sent without them: their signatures bind
        # the prompt they were produced under, which a new Run rebuilds.
        if item.blocks and item.source != "history":
            blocks = item.blocks
        messages.append({"role": item.role, "content": blocks})
    if pending:
        raise AppError("tool_protocol", "Unpaired tool uses cannot be sent to a model.")
    # Prompt-cache breakpoints. Each request resends the whole prefix, so marking the end of
    # the stable system prompt (which also covers the tools rendered before it) and the newest
    # message lets the next step of the Run read everything before it from the cache. Thinking
    # blocks cannot carry a marker, but the final message is always the task or tool results.
    if systems:
        systems[-1] = {**systems[-1], "cache_control": {"type": "ephemeral"}}
    if messages and messages[-1]["content"]:
        last = messages[-1]
        last["content"] = [
            *last["content"][:-1],
            {**last["content"][-1], "cache_control": {"type": "ephemeral"}},
        ]
    body: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_output_tokens,
        "stream": True,
        "messages": messages,
    }
    if config.temperature is not None:
        body["temperature"] = config.temperature
    if config.reasoning_effort:
        body["output_config"] = {"effort": config.reasoning_effort}
    if config.show_reasoning:
        # Current models omit thinking text unless asked; summarized text is for display.
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
    if systems:
        body["system"] = systems
    if tools:
        body["tools"] = [
            {
                "name": item["name"],
                "description": item["description"],
                "input_schema": item["input_schema"],
            }
            for item in tools
        ]
    return body


async def sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parse bounded event data and reject broken streams instead of returning partial success."""
    data: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        size += len(line.encode())
        if size > 1024 * 1024:
            raise AppError("provider_protocol", "A stream event exceeds 1 MiB.", 502)
        if not line:
            if data:
                yield json.loads("\n".join(data))
            data, size = [], 0
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        raise AppError("provider_protocol", "The stream ended inside an event.", 502)


async def stream_anthropic(
    config: ProviderConfig,
    prepared: dict[str, Any],
    on_wire: Callable[[dict[str, Any] | None], Awaitable[None]],
    key: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Capture body before transmission and reconstruct only supported text/tool blocks and usage."""

    async def capture(request: httpx.Request) -> None:
        """Compare actual JSON with the preview body without recording key-bearing headers."""
        body = json.loads(request.content)
        if body != prepared:
            raise AppError("request_mismatch", "Messages request differs from its preview.", 409)
        await on_wire(body)
        fill_attachments(request)

    headers = {"anthropic-version": "2023-06-01"}
    if key:
        headers["x-api-key"] = key
    blocks, stopped = {}, set()
    metadata: dict[str, Any] = {}
    usage: dict[str, Any] = {"input_tokens": None, "output_tokens": None}
    started, complete, output_size = False, False, 0
    try:
        async with httpx.AsyncClient(
            event_hooks={"request": [capture]}, timeout=90, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "POST", config.base_url.rstrip("/") + "/messages", json=prepared, headers=headers
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
                    if kind == "ping":
                        continue
                    if kind == "error":
                        raise provider_failure(error_message(event) or "streaming error", key)
                    if kind == "message_start":
                        if started:
                            raise AppError(
                                "provider_protocol", "Duplicate Messages stream start.", 502
                            )
                        started = True
                        message = event["message"]
                        metadata.update(id=message.get("id"), model=message.get("model"))
                        usage.update(message.get("usage", {}))
                    elif kind == "content_block_start":
                        index, block = event["index"], event["content_block"]
                        if not started or index in blocks or block["type"] not in BLOCK_TYPES:
                            raise AppError(
                                "provider_protocol",
                                "Unsupported or duplicate Messages content block; server tools "
                                "are not enabled.",
                                502,
                            )
                        blocks[index] = {**block, "json": ""}
                        if block["type"] == "text" and block.get("text"):
                            output_size += len(block["text"].encode())
                            yield {"kind": "delta", "text": block["text"]}
                    elif kind == "content_block_delta":
                        index, delta = event["index"], event["delta"]
                        block = blocks[index]
                        if index in stopped:
                            raise AppError(
                                "provider_protocol", "Delta follows a closed Messages block.", 502
                            )
                        if delta["type"] == "text_delta" and block["type"] == "text":
                            block["text"] += delta["text"]
                            output_size += len(delta["text"].encode())
                            yield {"kind": "delta", "text": delta["text"]}
                        elif delta["type"] == "input_json_delta" and block["type"] == "tool_use":
                            block["json"] += delta["partial_json"]
                            output_size += len(delta["partial_json"].encode())
                        elif delta["type"] == "thinking_delta" and block["type"] == "thinking":
                            block["thinking"] = block.get("thinking", "") + delta["thinking"]
                            output_size += len(delta["thinking"].encode())
                        elif delta["type"] == "signature_delta" and block["type"] == "thinking":
                            block["signature"] = block.get("signature", "") + delta["signature"]
                        else:
                            raise AppError(
                                "provider_protocol", "Unsupported Messages delta type.", 502
                            )
                    elif kind == "content_block_stop":
                        index = event["index"]
                        if index not in blocks or index in stopped:
                            raise AppError(
                                "provider_protocol",
                                "Messages closed an unknown/duplicate block.",
                                502,
                            )
                        stopped.add(index)
                    elif kind == "message_delta":
                        metadata.update(event.get("delta", {}))
                        usage.update(event.get("usage", {}))
                    elif kind == "message_stop":
                        if not started or set(blocks) != stopped:
                            raise AppError(
                                "provider_protocol",
                                "Messages ended with unfinished content blocks.",
                                502,
                            )
                        complete = True
                        break
                    else:
                        raise AppError(
                            "provider_protocol", "Unsupported Messages stream event.", 502
                        )
                    if output_size > 1024 * 1024:
                        raise AppError("provider_protocol", "Messages output exceeds 1 MiB.", 502)
    except AppError:
        raise
    except httpx.TransportError as exc:
        raise transport_failure(exc) from None
    except Exception as exc:
        raise provider_failure(f"{type(exc).__name__}; no automatic retry.", key) from None
    if not complete:
        raise AppError("provider_protocol", "Messages stream ended without message_stop.", 502)
    if metadata.get("stop_reason") == "refusal":
        # A safety classifier declined the request: an HTTP 200, not a protocol failure.
        details = metadata.get("stop_details")
        category = details.get("category") if isinstance(details, dict) else None
        raise AppError(
            "provider_refusal",
            f"The model declined this request{f' ({category})' if category else ''}. Rephrase "
            "the task with its authorization context, or choose another model.",
            502,
        )
    ordered = [blocks[index] for index in sorted(blocks)]
    try:
        calls = [
            ToolCall(
                id=block["id"],
                name=block["name"],
                args=json.loads(block["json"]) if block["json"] else block.get("input", {}),
            )
            for block in ordered
            if block["type"] == "tool_use"
        ]
    except (ValueError, TypeError):
        if metadata.get("stop_reason") == "max_tokens":
            raise truncated_output(config.max_output_tokens) from None
        raise AppError("provider_protocol", "Invalid Messages tool arguments.", 502) from None
    if len({call.id for call in calls}) != len(calls) or any(not call.id for call in calls):
        raise AppError(
            "provider_protocol", "Messages tool-use IDs must be present and unique.", 502
        )
    text = "".join(block["text"] for block in ordered if block["type"] == "text")
    if not text and not calls:
        raise AppError("provider_protocol", "Messages returned no supported content.", 502)
    # Keep the exact content array when it holds reasoning, so tool continuations can echo it.
    replay = (
        [replay_block(block, calls) for block in ordered]
        if any(block["type"] in {"thinking", "redacted_thinking"} for block in ordered)
        else []
    )
    yield {
        "kind": "result",
        "message": Message(role="assistant", content=text, tool_calls=calls, blocks=replay),
        "usage": usage,
        "metadata": metadata,
    }


def replay_block(block: dict[str, Any], calls: list[ToolCall]) -> dict[str, Any]:
    """Rebuild one received content block in the shape the API accepts back."""
    if block["type"] == "thinking":
        return {
            "type": "thinking",
            "thinking": block.get("thinking", ""),
            "signature": block.get("signature", ""),
        }
    if block["type"] == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block["data"]}
    if block["type"] == "tool_use":
        call = next(call for call in calls if call.id == block["id"])
        return {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
    return {"type": "text", "text": block["text"]}
