"""Two provider implementations sharing the exact same request preparation path."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from rtagent.domain import AppError, CompiledPrompt, Message, ProviderConfig, ToolCall, uid
from rtagent.infrastructure.providers.errors import sdk_failure, truncated_output
from rtagent.infrastructure.providers.messages import prepare_anthropic, stream_anthropic


def langchain_messages(messages: list[Message]) -> list:
    """Map canonical roles and tool IDs to SDK messages without adding instructions."""
    result = []
    for message in messages:
        if message.role == "system":
            result.append(SystemMessage(content=message.content))
        elif message.role == "user":
            result.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            result.append(
                AIMessage(
                    content=message.content,
                    tool_calls=[
                        {"id": call.id, "name": call.name, "args": call.args, "type": "tool_call"}
                        for call in message.tool_calls
                    ],
                )
            )
        else:
            if not message.tool_call_id:
                raise AppError("tool_protocol", "Tool results require a tool_call_id.")
            result.append(ToolMessage(content=message.content, tool_call_id=message.tool_call_id))
    return result


def model(config: ProviderConfig, key: str, client: httpx.AsyncClient | None = None) -> ChatOpenAI:
    """Create a Chat Completions adapter with retries and role fallback disabled."""
    if not config.model.strip():
        raise AppError(
            "provider_not_configured",
            "No model is selected. Choose one below the message box or in Settings → Models.",
        )
    url = urlparse(config.base_url)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise AppError(
            "provider_url", "Use an HTTP(S) base URL without credentials or query parameters."
        )
    return ChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=key or "local-no-key",
        temperature=config.temperature,
        reasoning_effort=config.reasoning_effort,
        max_tokens=config.max_output_tokens,
        max_retries=0,
        timeout=90,
        use_responses_api=False,
        stream_usage=True,
        http_async_client=client,
    )


def call_options(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialize the frozen tool catalog and shared streaming options."""
    options: dict[str, Any] = {"stream": True, "stream_options": {"include_usage": True}}
    if tools:
        options["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]
    return options


def prepare(
    config: ProviderConfig, compiled: CompiledPrompt, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Prepare the payload used by both preview and execution, rejecting role changes."""
    if config.adapter == "fake":
        return {
            "adapter": "fake",
            "model": config.model,
            "messages": [message.model_dump() for message in compiled.messages],
            "tools": tools,
        }
    if config.adapter == "anthropic":
        return prepare_anthropic(config, compiled, tools)
    # Pinned LangChain serialization is contract-tested against the HTTP boundary.
    instance = model(config, "preview-only")
    payload = instance._get_request_payload(
        langchain_messages(compiled.messages), **call_options(tools)
    )
    roles = [message["role"] for message in payload["messages"]]
    if roles != [message.role for message in compiled.messages]:
        raise AppError(
            "unsupported_role_mapping",
            "This model adapter changes message roles. Choose a compatible model or adjust its adapter explicitly.",
        )
    return payload


async def fake_stream(
    messages: list[Message], tools: list[dict[str, Any]]
) -> AsyncIterator[dict[str, Any]]:
    """Simulate a model offline while routing explicit demo commands to real tools."""
    current_index = next(
        i for i, message in enumerate(messages) if message.source == "current_input"
    )
    current = messages[current_index].content
    # Prior turns must not satisfy a tool request in the current turn.
    results = [message for message in messages[current_index + 1 :] if message.role == "tool"]
    names = {tool["name"] for tool in tools}
    selected = None
    if not results and current.lower().startswith("/read ") and "read_text" in names:
        selected = ToolCall(
            id="call_" + uid(), name="read_text", args={"path": current[6:].strip()}
        )
    elif not results and current.lower().startswith("/echo ") and "echo" in names:
        selected = ToolCall(id="call_" + uid(), name="echo", args={"text": current[6:]})
    elif not results and current.startswith("/tool "):
        parts = current.split(" ", 2)
        if len(parts) == 3 and parts[1] in names:
            try:
                selected = ToolCall(id="call_" + uid(), name=parts[1], args=json.loads(parts[2]))
            except (ValueError, TypeError) as exc:
                raise AppError("demo_input", "Use /tool NAME followed by a JSON object.") from exc
    if selected:
        yield {
            "kind": "result",
            "message": Message(role="assistant", tool_calls=[selected]),
            "usage": {"input_tokens": None, "output_tokens": None},
        }
        return
    if current == "/error":
        raise AppError("provider_error", "Simulated provider failure.", 502)
    if current.startswith("Earlier derived summary:\n") and not tools:
        text = "Simulated conversation summary. Original evidence is retained; no model service was called."
    elif results:
        text = (
            "The tool result below is real; this reply is simulated and no model service was "
            "called. Inspect the turn to see how the result entered the next model request.\n\n"
            f"````text\n{results[-1].content}\n````"
        )
    else:
        text = (
            "## Workspace ready\n\nYour prompt was compiled and recorded successfully.\n\n"
            + f"- **Messages:** {len(messages)}\n- **Available tools:** {len(tools)}\n- **Task:** {current}\n\n"
            + "This is the **simulated model**. Try `/read README.md` to inspect a local file or `/echo hello` to exercise tool approval. Configure an OpenAI-compatible endpoint in Settings for model-generated analysis."
        )
    for chunk in re.findall(r"[\s\S]{1,40}", text):
        await asyncio.sleep(0.2 if current == "/slow" else 0.015)
        yield {"kind": "delta", "text": chunk}
    yield {
        "kind": "result",
        "message": Message(role="assistant", content=text),
        "usage": {"input_tokens": None, "output_tokens": None},
    }


def transformations(adapter: str) -> list[str]:
    """Name adapter serialization changes beside the preserved canonical source mapping."""
    if adapter == "openai":
        return ["LangChain Chat Completions serialization"]
    if adapter == "anthropic":
        return [
            "Leading system blocks extracted into Messages system array in original order",
            "Assistant tool calls serialized as tool_use blocks with unchanged IDs/arguments",
            "Tool results serialized as user tool_result blocks; consecutive results grouped in order",
            "Consecutive user/assistant messages may be combined by the provider",
        ]
    return []


async def generate(
    config: ProviderConfig,
    compiled: CompiledPrompt,
    tools: list[dict[str, Any]],
    prepared: dict[str, Any],
    on_wire: Callable[[dict[str, Any] | None], Awaitable[None]],
    key: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Stream model output and capture the checked request before network transmission.

    `key` is the resolved credential, supplied by the caller that owns credential storage.
    Simulated calls record no wire body. Real calls fail closed if SDK serialization
    differs from the prepared payload, and never retry automatically."""
    if config.adapter == "fake":
        await on_wire(None)
        async for event in fake_stream(compiled.messages, tools):
            yield event
        return
    if config.adapter == "anthropic":
        async for event in stream_anthropic(config, prepared, on_wire, key):
            yield event
        return

    async def capture(request: httpx.Request) -> None:
        body = json.loads(request.content)
        if body != prepared:
            raise AppError(
                "request_mismatch",
                "SDK request differs from the preview. No model request was sent.",
                409,
            )
        await on_wire(body)

    async with httpx.AsyncClient(
        event_hooks={"request": [capture]}, follow_redirects=False, timeout=90
    ) as client:
        instance = model(config, key, client)
        total = None
        try:
            async for chunk in instance.astream(
                langchain_messages(compiled.messages), **call_options(tools)
            ):
                total = chunk if total is None else total + chunk
                if isinstance(chunk.content, str) and chunk.content:
                    yield {"kind": "delta", "text": chunk.content}
        except AppError:
            raise
        except Exception as exc:
            # Keep the provider's explanation but never its URL, headers or request body.
            raise sdk_failure(exc, key) from None
        if total is None:
            raise AppError("provider_protocol", "The model returned an empty stream.", 502)
        if total.invalid_tool_calls:
            if total.response_metadata.get("finish_reason") == "length":
                raise truncated_output(config.max_output_tokens)
            raise AppError(
                "provider_protocol", "The model returned invalid tool-call arguments.", 502
            )
        message = Message(
            role="assistant",
            content=total.content if isinstance(total.content, str) else total.text,
            tool_calls=[
                ToolCall(id=call["id"], name=call["name"], args=call["args"])
                for call in total.tool_calls
            ],
        )
        ids = [call.id for call in message.tool_calls]
        if len(set(ids)) != len(ids) or any(not identifier for identifier in ids):
            raise AppError("provider_protocol", "Tool call IDs must be present and unique.", 502)
        usage = dict(total.usage_metadata or {"input_tokens": None, "output_tokens": None})
        # Surface cached-prompt tokens at the top level so context accounting does not have to
        # know each provider's nesting. Chat Completions reports them under input_token_details,
        # where input_tokens already includes the cached part (unlike the Messages API).
        details = usage.get("input_token_details") or {}
        if "cache_read" in details:
            usage["cache_read_input_tokens"] = details["cache_read"]
        if "cache_creation" in details:
            usage["cache_creation_input_tokens"] = details["cache_creation"]
        yield {
            "kind": "result",
            "message": message,
            "usage": usage,
            "metadata": total.response_metadata,
        }
