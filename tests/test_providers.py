import json

import httpx

from rtagent.domain import Preset, ProviderConfig, StudioConfig
from rtagent.domain.prompts import compile_prompt
from rtagent.infrastructure.providers.chat import generate, prepare


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
