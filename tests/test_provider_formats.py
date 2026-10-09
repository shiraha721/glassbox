"""Services that accept several request formats, and conversations that switch between them."""

import httpx
import pytest
from pydantic import ValidationError
from test_providers import mock_client

from glassbox.domain import (
    AppError,
    GlassboxConfig,
    Message,
    ModelChoice,
    Preset,
    ProviderConfig,
    ProviderProfile,
    ToolCall,
)
from glassbox.domain.models import resolve_model
from glassbox.domain.prompts import compile_prompt
from glassbox.infrastructure.providers import USER_AGENT
from glassbox.infrastructure.providers.catalog import list_models
from glassbox.infrastructure.providers.chat import prepare

GATEWAY = ProviderProfile(
    id="gateway",
    name="Gateway",
    adapter="openai",
    base_url="https://gateway.example/v1",
    endpoints={"anthropic": "https://gateway.example/anthropic/v1"},
    model_formats={"claude-x": "anthropic"},
    credential_ref="GATEWAY_API_KEY",
    models=["claude-x", "open-model"],
)


def test_each_model_is_resolved_with_its_own_format_and_endpoint():
    config = GlassboxConfig(providers=[GATEWAY])
    claude = resolve_model(
        config, ModelChoice(provider_id="gateway", model="claude-x", reasoning_effort="max")
    )
    assert (claude.adapter, claude.base_url) == (
        "anthropic",
        "https://gateway.example/anthropic/v1",
    )
    assert claude.credential_ref == "GATEWAY_API_KEY" and claude.auth == "key"
    other = resolve_model(config, ModelChoice(provider_id="gateway", model="open-model"))
    assert (other.adapter, other.base_url) == ("openai", "https://gateway.example/v1")
    # Effort levels follow the model's format: Chat Completions stops at high.
    with pytest.raises(AppError) as failure:
        resolve_model(
            config, ModelChoice(provider_id="gateway", model="open-model", reasoning_effort="max")
        )
    assert failure.value.code == "reasoning_effort"


def test_models_can_use_only_formats_the_service_offers():
    with pytest.raises(ValidationError, match="does not offer"):
        ProviderProfile.model_validate(GATEWAY.model_dump() | {"model_formats": {"x": "responses"}})
    with pytest.raises(ValidationError, match="endpoints lists the others"):
        ProviderProfile.model_validate(
            GATEWAY.model_dump() | {"endpoints": {"openai": "https://gateway.example/v1"}}
        )
    with pytest.raises(ValidationError, match="does not offer"):
        ProviderProfile.model_validate(GATEWAY.model_dump() | {"endpoints": {"anthropic": ""}})
    with pytest.raises(ValidationError, match="without credentials"):
        ProviderProfile.model_validate(
            GATEWAY.model_dump() | {"endpoints": {"anthropic": "https://user:pw@gateway.example"}}
        )
    # Settings saved before formats existed still load, with one format per provider.
    legacy = {
        key: value
        for key, value in GATEWAY.model_dump().items()
        if key not in {"endpoints", "model_formats", "auth", "service"}
    }
    assert ProviderProfile.model_validate(legacy).format_of("claude-x") == "openai"


def test_a_chatgpt_sign_in_is_bound_to_the_openai_api():
    with pytest.raises(ValidationError, match="ChatGPT sign-in"):
        ProviderProfile(id="plan", name="Plan", auth="chatgpt", base_url="https://other.example")
    with pytest.raises(ValidationError, match="ChatGPT sign-in"):
        ProviderConfig(
            profile_id="plan",
            adapter="responses",
            auth="chatgpt",
            base_url="https://other.example/v1",
        )
    plan = ProviderProfile(
        id="plan",
        name="Plan",
        auth="chatgpt",
        adapter="responses",
        base_url="https://api.openai.com/v1",
    )
    resolved = resolve_model(
        GlassboxConfig(providers=[plan]), ModelChoice(provider_id="plan", model="gpt-x")
    )
    assert (resolved.auth, resolved.adapter) == ("chatgpt", "responses")


async def test_a_service_with_several_formats_lists_models_at_its_openai_endpoint(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "claude-x"}]})

    mock_client(monkeypatch, "catalog", handler)
    messages_first = ProviderProfile.model_validate(
        GATEWAY.model_dump()
        | {
            "adapter": "anthropic",
            "base_url": "https://gateway.example/anthropic/v1",
            "endpoints": {"openai": "https://gateway.example/v1"},
        }
    )
    assert await list_models(messages_first, "sk-key") == ["claude-x"]
    assert str(requests[0].url) == "https://gateway.example/v1/models"
    assert requests[0].headers["authorization"] == "Bearer sk-key"
    assert requests[0].headers["user-agent"] == USER_AGENT


@pytest.mark.parametrize("adapter", ["openai", "responses", "anthropic"])
def test_history_tool_ids_from_another_provider_are_made_portable(adapter):
    """IDs such as Kimi's `functions.read_text:0` are rejected by the Messages API."""
    foreign = "functions.read_text:0"
    history = [
        Message(role="user", content="Read a.py", source="history"),
        Message(
            role="assistant",
            source="history",
            tool_calls=[ToolCall(id=foreign, name="read_text", args={"path": "a.py"})],
        ),
        Message(role="tool", content="print(1)", tool_call_id=foreign, source="history"),
        Message(role="assistant", content="It prints 1.", source="history"),
    ]
    provider = ProviderConfig(adapter=adapter, model="m", base_url="http://127.0.0.1:1/v1")
    current = [
        Message(role="assistant", tool_calls=[ToolCall(id="call:now", name="read_text", args={})]),
        Message(role="tool", content="x", tool_call_id="call:now"),
    ]
    compiled = compile_prompt(
        Preset(name="Empty"), GlassboxConfig(provider=provider), history, "Again", {}, [], current
    )
    body = prepare(provider, compiled, [])
    text = str(body)
    assert foreign not in text
    # The current Run's IDs come from its own provider and go back exactly as received.
    assert "call:now" in text
    if adapter == "anthropic":
        use, result = body["messages"][1]["content"][0], body["messages"][2]["content"][0]
        assert use["id"] == result["tool_use_id"] and use["id"].startswith("call_")
    elif adapter == "responses":
        calls = [item for item in body["input"] if item.get("type", "").startswith("function")]
        assert calls[0]["call_id"] == calls[1]["call_id"] != foreign
    else:
        roles = [message["role"] for message in body["messages"]]
        call = body["messages"][roles.index("assistant")]["tool_calls"][0]["id"]
        assert body["messages"][roles.index("tool")]["tool_call_id"] == call != foreign
