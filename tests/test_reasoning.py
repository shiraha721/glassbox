"""Readable model reasoning is shown when a provider returns it, never its signatures."""

import json

from test_api import start, wait_state

from rtagent.domain import (
    Message,
    ModelChoice,
    Preset,
    ProviderConfig,
    ProviderProfile,
    StudioConfig,
    ToolCall,
    reasoning_text,
    resolve_model,
)
from rtagent.domain.prompts import compile_prompt
from rtagent.infrastructure.providers.chat import prepare


def test_reasoning_text_reads_thinking_and_summaries_only():
    blocks = [
        {"type": "thinking", "thinking": "First, read a.py.", "signature": "sig-1"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "Answer"},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Checked the tests."}],
            "encrypted_content": "enc",
        },
        {"type": "reasoning", "summary": [], "content": [{"type": "reasoning_text", "text": "x"}]},
    ]
    assert reasoning_text(blocks) == "First, read a.py.\n\nChecked the tests.\n\nx"
    assert reasoning_text([{"type": "thinking", "thinking": "", "signature": "s"}]) == ""


def test_the_option_asks_each_api_for_readable_reasoning():
    profile = ProviderProfile(
        id="claude",
        name="Claude",
        adapter="anthropic",
        base_url="https://api.anthropic.com/v1",
        show_reasoning=True,
    )
    config = StudioConfig(providers=[profile])
    resolved = resolve_model(config, ModelChoice(provider_id="claude", model="claude-opus-5-5"))
    assert resolved.show_reasoning is True
    compiled = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=resolved), [], "Hi", {}, []
    )
    assert prepare(resolved, compiled, [])["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert "thinking" not in prepare(
        resolved.model_copy(update={"show_reasoning": False}), compiled, []
    )

    responses = ProviderConfig(
        adapter="responses", model="gpt-x", base_url="http://127.0.0.1:1/v1", show_reasoning=True
    )
    compiled = compile_prompt(
        Preset(name="Empty"), StudioConfig(provider=responses), [], "Hi", {}, []
    )
    assert prepare(responses, compiled, [])["reasoning"] == {"summary": "auto"}


def test_responses_replay_skips_reasoning_it_cannot_resolve():
    """Without an effort there is no encrypted content; the summary is display-only."""
    provider = ProviderConfig(adapter="responses", model="gpt-x", base_url="http://x/v1")
    reasoning = {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Plan"}]}
    call = {"type": "function_call", "call_id": "c1", "name": "read_text", "arguments": "{}"}
    assistant = Message(
        role="assistant",
        tool_calls=[ToolCall(id="c1", name="read_text", args={})],
        blocks=[reasoning, call],
    )
    compiled = compile_prompt(
        Preset(name="Empty"),
        StudioConfig(provider=provider),
        [],
        "Read",
        {},
        [],
        [assistant, Message(role="tool", content="ok", tool_call_id="c1")],
    )
    items = prepare(provider, compiled, [])["input"]
    assert reasoning not in items and call in items


async def test_run_views_show_reasoning_without_signatures(studio_client, monkeypatch):
    client, _app = studio_client

    async def thinking_model(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        blocks = [
            {"type": "thinking", "thinking": "The task asks for a greeting.", "signature": "s-9"},
            {"type": "text", "text": "Hello."},
        ]
        message = Message(role="assistant", content="Hello.", blocks=blocks)
        yield {"kind": "result", "message": message, "usage": {}}

    monkeypatch.setattr("rtagent.application.runner.generate", thinking_model)
    run, *_ = await start(client, "Greet me")
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert completed["messages"][-1]["reasoning"] == "The task asks for a greeting."
    assert completed["messages"][-1]["blocks"] == []
    assert "s-9" not in json.dumps(completed)
