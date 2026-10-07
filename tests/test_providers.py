import json

import httpx
import pytest

from rtagent.domain import AppError, Message, Preset, ProviderConfig, ProviderProfile, StudioConfig
from rtagent.domain.prompts import compile_prompt
from rtagent.infrastructure.providers.catalog import list_models
from rtagent.infrastructure.providers.chat import generate, prepare

# Adapters share the httpx module, so every mock must subclass the real client class.
ORIGINAL_CLIENT = httpx.AsyncClient


async def test_openai_preview_matches_captured_outbound_request(monkeypatch):
    provider = ProviderConfig(
        adapter="openai", model="contract-model", base_url="http://127.0.0.1:12345/v1"
    )
    compiled = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=provider), [], "Hello", {}, []
    )
    prepared = prepare(provider, compiled, [])
    captured = []
    original_client = httpx.AsyncClient

    async def transport(request):
        assert json.loads(request.content) == prepared
        chunks = [
            {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "contract-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Hello"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "contract-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        ]
        body = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    class TestClient(original_client):
        """Keep the SDK's client type checks intact while replacing the transport."""

        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr("rtagent.infrastructure.providers.chat.httpx.AsyncClient", TestClient)

    async def on_wire(body):
        captured.append(body)

    events = [event async for event in generate(provider, compiled, [], prepared, on_wire)]
    assert captured == [prepared]
    assert events[-1]["message"].content == "Hello"
    assert events[-1]["usage"]["input_tokens"] == 5


def mock_client(monkeypatch, module: str, handler) -> None:
    """Route a provider adapter's httpx client through an in-process transport."""

    class TestClient(ORIGINAL_CLIENT):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(f"rtagent.infrastructure.providers.{module}.httpx.AsyncClient", TestClient)


async def test_provider_errors_keep_the_reason_but_not_the_credential(monkeypatch):
    secret = "sk-test-secret-0123456789abcdef"
    provider = ProviderConfig(
        adapter="openai",
        model="contract-model",
        base_url="http://127.0.0.1:12345/v1",
        credential_ref="TEST_PROVIDER_KEY",
    )
    compiled = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=provider), [], "Hello", {}, []
    )
    prepared = prepare(provider, compiled, [])
    message = f"Incorrect API key provided: {secret}"
    mock_client(
        monkeypatch,
        "chat",
        lambda _request: httpx.Response(
            401, json={"error": {"message": message, "type": "invalid_request_error"}}
        ),
    )

    async def on_wire(_body):
        return None

    with pytest.raises(AppError) as failure:
        async for _event in generate(provider, compiled, [], prepared, on_wire, secret):
            pass
    assert failure.value.code == "provider_error"
    assert "HTTP 401" in failure.value.message and "Incorrect API key" in failure.value.message
    assert secret not in failure.value.message

    anthropic = provider.model_copy(update={"adapter": "anthropic"})
    body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    mock_client(monkeypatch, "messages", lambda _request: httpx.Response(529, json=body))
    anthropic_prepared = prepare(anthropic, compiled, [])
    with pytest.raises(AppError) as failure:
        async for _event in generate(anthropic, compiled, [], anthropic_prepared, on_wire, secret):
            pass
    assert failure.value.message == "Model request failed (HTTP 529): overloaded_error: Overloaded"


def sse(*events) -> str:
    """Encode Messages API stream events."""
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


async def test_messages_thinking_blocks_are_kept_and_replayed_within_the_run(monkeypatch):
    """Current Claude models think by default; tool continuations must echo those blocks."""
    provider = ProviderConfig(
        adapter="anthropic",
        model="claude-opus-5-5",
        base_url="http://127.0.0.1:12345/v1",
        reasoning_effort="high",
    )
    compiled = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=provider), [], "Read a.py", {}, []
    )
    prepared = prepare(provider, compiled, [])
    assert prepared["output_config"] == {"effort": "high"} and "temperature" not in prepared
    stream = sse(
        {"type": "message_start", "message": {"id": "m1", "model": "x", "usage": {}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig-1"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "read_text",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.py"}'},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    )
    mock_client(
        monkeypatch,
        "messages",
        lambda _request: httpx.Response(
            200, text=stream, headers={"content-type": "text/event-stream"}
        ),
    )

    async def on_wire(_body):
        return None

    events = [event async for event in generate(provider, compiled, [], prepared, on_wire, "k")]
    message = events[-1]["message"]
    assert message.tool_calls[0].args == {"path": "a.py"}
    assert message.blocks == [
        {"type": "thinking", "thinking": "", "signature": "sig-1"},
        {"type": "tool_use", "id": "toolu_1", "name": "read_text", "input": {"path": "a.py"}},
    ]
    result = Message(role="tool", content="print(1)", tool_call_id="toolu_1")
    following = compile_prompt(
        Preset(name="Empty"),
        StudioConfig(provider=provider),
        [],
        "Read a.py",
        {},
        [],
        [message, result],
    )
    assert prepare(provider, following, [])["messages"][1]["content"] == message.blocks
    # In a later Run the turn is history: its thinking is bound to the earlier prompt.
    history = [
        Message(role="user", content="Read a.py", source="history"),
        message.model_copy(update={"source": "history"}),
        result.model_copy(update={"source": "history"}),
    ]
    later = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=provider), history, "Next", {}, []
    )
    assert prepare(provider, later, [])["messages"][1]["content"] == [message.blocks[1]]


async def test_messages_refusals_are_reported_as_declined(monkeypatch):
    """A classifier refusal arrives as a normal stream and must not look like a protocol bug."""
    provider = ProviderConfig(
        adapter="anthropic", model="claude-opus-5-5", base_url="http://127.0.0.1:12345/v1"
    )
    compiled = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=provider), [], "Analyze", {}, []
    )
    stream = sse(
        {"type": "message_start", "message": {"id": "m1", "model": "x", "usage": {}}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "refusal", "stop_details": {"category": "cyber"}},
            "usage": {"output_tokens": 0},
        },
        {"type": "message_stop"},
    )
    mock_client(
        monkeypatch,
        "messages",
        lambda _request: httpx.Response(
            200, text=stream, headers={"content-type": "text/event-stream"}
        ),
    )

    async def on_wire(_body):
        return None

    with pytest.raises(AppError) as failure:
        async for _event in generate(
            provider, compiled, [], prepare(provider, compiled, []), on_wire, "k"
        ):
            pass
    assert failure.value.code == "provider_refusal" and "(cyber)" in failure.value.message


async def test_model_lists_follow_each_provider_format(monkeypatch):
    """OpenAI-compatible servers return one page; the Messages API paginates."""
    requests = []

    def openai(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]})

    mock_client(monkeypatch, "catalog", openai)
    profile = ProviderProfile(id="openai", name="OpenAI", base_url="http://127.0.0.1:1/v1")
    assert await list_models(profile, "sk-key") == ["gpt-a", "gpt-b"]
    assert requests[0].headers["authorization"] == "Bearer sk-key"

    pages = iter(
        [
            {"data": [{"id": "claude-b"}], "has_more": True, "last_id": "claude-b"},
            {"data": [{"id": "claude-a"}], "has_more": False, "last_id": "claude-a"},
        ]
    )

    def anthropic(request):
        requests.append(request)
        return httpx.Response(200, json=next(pages))

    mock_client(monkeypatch, "catalog", anthropic)
    claude = profile.model_copy(update={"adapter": "anthropic"})
    assert await list_models(claude, "key") == ["claude-a", "claude-b"]
    assert requests[-1].url.params["after_id"] == "claude-b"
    assert requests[-1].headers["x-api-key"] == "key"

    mock_client(
        monkeypatch,
        "catalog",
        lambda _r: httpx.Response(401, json={"error": {"message": "bad key"}}),
    )
    with pytest.raises(AppError, match="HTTP 401"):
        await list_models(profile, "sk-key")
