"""The OpenAI Responses adapter: explicit input items, captured bodies, reasoning replay."""

import json

import httpx
import pytest
from test_api import start, wait_state
from test_providers import mock_client

from glassbox.domain import (
    AppError,
    Attachment,
    GlassboxConfig,
    Message,
    Preset,
    ProviderConfig,
    ToolCall,
)
from glassbox.domain.prompts import compile_prompt
from glassbox.infrastructure.providers.chat import generate, prepare

PROVIDER = ProviderConfig(
    adapter="responses",
    model="gpt-x",
    base_url="http://127.0.0.1:12345/v1",
    credential_ref="",
    reasoning_effort="high",
)
TOOL = {"name": "read_text", "description": "Read", "input_schema": {"type": "object"}}


def events(*items) -> str:
    return "".join(f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in items)


def completed(output, status="completed", **extra):
    return {
        "type": f"response.{status}" if status != "failed" else "response.failed",
        "response": {
            "id": "resp_1",
            "model": "gpt-x",
            "status": status,
            "output": output,
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 80},
                "output_tokens": 20,
            },
            **extra,
        },
    }


def reply(*items):
    return lambda _request: httpx.Response(
        200, text=events(*items), headers={"content-type": "text/event-stream"}
    )


async def run(compiled, prepared, key="sk-test"):
    recorded = []

    async def on_wire(body):
        recorded.append(body)

    result = [event async for event in generate(PROVIDER, compiled, [TOOL], prepared, on_wire, key)]
    return result, recorded


def test_messages_become_input_items_with_unchanged_roles_and_ids():
    image = Attachment(hash="a" * 64, media_type="image/png", size=3)
    history = [
        Message(role="user", content="Earlier", source="history"),
        Message(role="assistant", content="Earlier answer", source="history"),
    ]
    compiled = compile_prompt(
        Preset(name="Empty"),
        GlassboxConfig(provider=PROVIDER),
        history,
        "Look at a.py",
        {},
        [TOOL],
        [
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="call_1", name="read_text", args={"path": "a.py"})],
            ),
            Message(role="tool", content="print(1)", tool_call_id="call_1"),
        ],
        attachments=[image],
    )
    body = prepare(PROVIDER, compiled, [TOOL])
    assert body["store"] is False and body["stream"] is True
    assert body["reasoning"] == {"effort": "high"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["tools"][0] == {
        "type": "function",
        "name": "read_text",
        "description": "Read",
        "parameters": {"type": "object"},
        "strict": False,
    }
    assert body["input"][-5:] == [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Earlier answer"},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Look at a.py"},
                {
                    "type": "input_image",
                    "image_url": f"attachment-data-url:{'a' * 64}",
                    "detail": "auto",
                },
            ],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "read_text",
            "arguments": '{"path": "a.py"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "print(1)"},
    ]
    unpaired = compile_prompt(
        Preset(name="Empty"),
        GlassboxConfig(provider=PROVIDER),
        [],
        "x",
        {},
        [],
        [Message(role="assistant", tool_calls=[ToolCall(id="c", name="read_text", args={})])],
    )
    with pytest.raises(AppError) as failure:
        prepare(PROVIDER, unpaired, [])
    assert failure.value.code == "tool_protocol"


async def test_streams_text_and_replays_reasoning_within_the_run(monkeypatch):
    compiled = compile_prompt(
        Preset(name="Empty"), GlassboxConfig(provider=PROVIDER), [], "Read a.py", {}, [TOOL]
    )
    prepared = prepare(PROVIDER, compiled, [TOOL])
    reasoning = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "enc"}
    call = {
        "id": "fc_1",
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_text",
        "arguments": '{"path": "a.py"}',
        "status": "completed",
    }
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Reading."}],
    }
    mock_client(
        monkeypatch,
        "responses",
        reply(
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.output_text.delta", "delta": "Read"},
            {"type": "response.output_text.delta", "delta": "ing."},
            completed([reasoning, message, call]),
        ),
    )
    result, recorded = await run(compiled, prepared)
    assert recorded == [prepared]
    assert [event["text"] for event in result if event["kind"] == "delta"] == ["Read", "ing."]
    final = result[-1]
    assert final["message"].content == "Reading."
    assert final["message"].tool_calls == [
        ToolCall(id="call_1", name="read_text", args={"path": "a.py"})
    ]
    assert final["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 80,
    }
    # Unstored responses cannot resolve item IDs, so the echoed items carry none.
    blocks = final["message"].blocks
    assert blocks[0] == {"type": "reasoning", "summary": [], "encrypted_content": "enc"}
    assert all("id" not in block for block in blocks)

    follow_up = compile_prompt(
        Preset(name="Empty"),
        GlassboxConfig(provider=PROVIDER),
        [],
        "Read a.py",
        {},
        [TOOL],
        [final["message"], Message(role="tool", content="print(1)", tool_call_id="call_1")],
    )
    items = prepare(PROVIDER, follow_up, [TOOL])["input"]
    assert items[-4:] == [
        *blocks,
        {"type": "function_call_output", "call_id": "call_1", "output": "print(1)"},
    ]


@pytest.mark.parametrize(
    "handler,code",
    [
        (
            reply(
                completed(
                    [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": "I can't help."}],
                        }
                    ]
                )
            ),
            "provider_refusal",
        ),
        (
            reply(
                completed(
                    [{"type": "function_call", "call_id": "c", "name": "x", "arguments": '{"a'}],
                    status="incomplete",
                    incomplete_details={"reason": "max_output_tokens"},
                )
            ),
            "provider_output_limit",
        ),
        (
            reply(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "server_error", "message": "Overloaded"}},
                }
            ),
            "provider_error",
        ),
        (
            lambda _request: httpx.Response(
                401, json={"error": {"message": "Incorrect API key sk-test", "type": "auth"}}
            ),
            "provider_error",
        ),
        (reply({"type": "response.created", "response": {}}), "provider_protocol"),
    ],
)
async def test_failures_are_explained_without_the_key(monkeypatch, handler, code):
    compiled = compile_prompt(
        Preset(name="Empty"), GlassboxConfig(provider=PROVIDER), [], "Hi", {}, [TOOL]
    )
    mock_client(monkeypatch, "responses", handler)
    with pytest.raises(AppError) as failure:
        await run(compiled, prepare(PROVIDER, compiled, [TOOL]))
    assert failure.value.code == code
    assert "sk-test" not in failure.value.message


async def test_a_run_uses_a_responses_provider_end_to_end(glassbox_client, monkeypatch):
    """Preview, execution and the recorded wire body agree; usage and cost come through."""
    client, app = glassbox_client
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    profile = {
        "id": "responses",
        "name": "Responses",
        "adapter": "responses",
        "base_url": "http://127.0.0.1:12345/v1",
        "credential_ref": "",
        "models": ["gpt-x"],
        "prices": {"gpt-x": {"input": 2, "output": 8, "cache_read": 0.5}},
    }
    config["providers"].append(profile)
    config["provider"] = {
        **config["provider"],
        "profile_id": "responses",
        "adapter": "responses",
        "model": "gpt-x",
        "base_url": profile["base_url"],
        "credential_ref": "",
    }
    saved = await client.put("/api/v1/config", json=config)
    assert saved.status_code == 200, saved.text
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        message = {"type": "message", "content": [{"type": "output_text", "text": "Done."}]}
        return reply(completed([message]))(request)

    mock_client(monkeypatch, "responses", handler)
    run_view, *_ = await start(client, "Say done")
    finished = await wait_state(client, run_view["id"], {"succeeded"})
    assert finished["messages"][-1]["content"] == "Done."
    assert finished["provider"] == "responses" and finished["model"] == "gpt-x"
    # 20 uncached x $2 + 80 cached x $0.5 + 20 output x $8, per million tokens.
    assert finished["usage"]["cost"] == 0.00024
    call = await app.state.glassbox.repository.call(f"{run_view['id']}:model:0000")
    assert call["data"]["wire_request_body"] == seen[0] == call["data"]["prepared_request"]
    assert seen[0]["input"][-1] == {"role": "user", "content": "Say done"}
