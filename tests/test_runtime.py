"""Exercise the durable loop at approval, side-effect, and checkpoint boundaries."""

import asyncio

import pytest
from test_api import start, wait_state

from glassbox.application.context import context_usage, session_usage
from glassbox.application.runner import Runner
from glassbox.domain import Message, ToolCall


def test_context_usage_counts_cached_prompt_tokens_per_adapter():
    """Messages reports uncached input, so the full prompt is its sum with the cache tokens."""
    anthropic = context_usage(
        {
            "adapter": "anthropic",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read": 900,
            "cache_write": 50,
            "model": "claude-x",
        },
        "default-model",
    )
    assert anthropic.prompt_tokens == 1050 and anthropic.cache_read_tokens == 900
    assert anthropic.model == "claude-x"
    # Chat Completions already counts cached tokens inside input_tokens; do not double-count.
    openai = context_usage(
        {"adapter": "openai", "input_tokens": 1000, "output_tokens": 20, "cache_read": 900},
        "gpt-x",
    )
    assert openai.prompt_tokens == 1000 and openai.model == "gpt-x"
    # No call, or a provider that omits usage: null prompt rather than a misleading zero.
    assert context_usage(None, "m") is None
    assert context_usage({"adapter": "fake", "input_tokens": None}, "sim").prompt_tokens is None


async def test_context_reports_the_latest_request_usage(glassbox_client, monkeypatch):
    """The context view surfaces the provider's accounting for the most recent model request."""
    client, _app = glassbox_client

    async def scripted_model(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        yield {
            "kind": "result",
            "message": Message(role="assistant", content="Done."),
            "usage": {"input_tokens": 321, "output_tokens": 45, "cache_read_input_tokens": 200},
        }

    monkeypatch.setattr("glassbox.application.runner.generate", scripted_model)
    run, _body, session_id, _preview = await start(client)
    await wait_state(client, run["id"], {"succeeded"})
    usage = (await client.get(f"/api/v1/sessions/{session_id}/context")).json()["live_usage"]
    assert usage["input_tokens"] == 321 and usage["output_tokens"] == 45
    assert usage["cache_read_tokens"] == 200
    # The default test provider is the fake adapter, where input already counts any cache.
    assert usage["prompt_tokens"] == 321


def test_session_usage_totals_every_reported_request():
    """Totals cover the whole conversation; the cache rate uses requests that report caching."""

    def row(run, adapter, inputs, read=None, write=None, subagent=0):
        return {
            "run_id": run,
            "adapter": adapter,
            "input_tokens": inputs,
            "output_tokens": 10 if inputs is not None else None,
            "cache_read": read,
            "cache_write": write,
            "model": "m",
            "subagent": subagent,
            "context_window": 200_000,
        }

    totals, requests = session_usage(
        [
            row("r1", "anthropic", 100, read=0, write=900),
            row("r1", "anthropic", 50, read=950, write=0),
            row("r2", "openai", 4000, subagent=1),
            row("r3", "fake", None),
        ]
    )
    assert (totals.requests, totals.reported_requests, totals.subagent_requests) == (4, 3, 1)
    assert totals.prompt_tokens == 6000 and totals.output_tokens == 30
    assert (totals.cache_read_tokens, totals.cache_write_tokens) == (950, 900)
    # The sub-agent's provider reported no cache figures, so its prompt is not in the rate.
    assert totals.cache_hit_rate == round(950 / 2000, 4)
    # The sub-agent's larger prompt filled its own window, not this conversation's.
    assert totals.peak_prompt_tokens == 1000
    # Only the conversation's own requests are listed for the window chart.
    assert [item.run_id for item in requests] == ["r1", "r1", "r3"]
    assert requests[1].prompt_tokens == 1000 and requests[-1].prompt_tokens is None
    assert session_usage([])[0].cache_hit_rate is None


async def test_context_totals_cover_every_request(glassbox_client, monkeypatch):
    """Each model request of a Run adds to the conversation totals, not just the latest one."""
    client, _app = glassbox_client
    replies = iter(
        [
            (
                Message(
                    role="assistant",
                    tool_calls=[ToolCall(id="c1", name="list_files", args={"path": "."})],
                ),
                {"input_tokens": 1000, "output_tokens": 30, "cache_read_input_tokens": 0},
            ),
            (
                Message(role="assistant", content="Listed."),
                {"input_tokens": 1500, "output_tokens": 20, "cache_read_input_tokens": 1000},
            ),
        ]
    )

    async def scripted_model(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        message, usage = next(replies)
        yield {"kind": "result", "message": message, "usage": usage}

    monkeypatch.setattr("glassbox.application.runner.generate", scripted_model)
    run, _body, session_id, _preview = await start(client)
    await wait_state(client, run["id"], {"succeeded"})
    context = (await client.get(f"/api/v1/sessions/{session_id}/context")).json()
    totals = context["session_usage"]
    assert (totals["requests"], totals["prompt_tokens"], totals["output_tokens"]) == (2, 2500, 50)
    assert totals["cache_read_tokens"] == 1000 and totals["cache_hit_rate"] == 0.4
    assert totals["peak_prompt_tokens"] == 1500
    assert [item["prompt_tokens"] for item in context["requests"]] == [1000, 1500]
    assert context["live_usage"]["prompt_tokens"] == 1500


async def allow_echo(client, **limits):
    """Explicitly permit the harmless fixture tool and adjust bounded test limits."""
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    config["tools"]["echo"] = "allow"
    config.update(limits)
    response = await client.put("/api/v1/config", json=config)
    assert response.status_code == 200


async def replace_runner(app):
    """Reopen the same durable stores with a fresh graph and task owner."""
    old = app.state.glassbox.runner
    await old.close()
    replacement = Runner(old.repository, old.registry, old.checkpoint_path)
    await replacement.open()
    app.state.glassbox.runner = replacement
    return replacement


async def test_multiple_calls_keep_ids_and_complete_before_next_model(glassbox_client, monkeypatch):
    """A batch must return one ordered result for every original model call ID."""
    client, _app = glassbox_client
    await allow_echo(client)

    async def scripted_model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        results = [message for message in compiled.messages if message.role == "tool"]
        if not results:
            message = Message(
                role="assistant",
                tool_calls=[
                    ToolCall(id="read-id", name="read_text", args={"path": "README.md"}),
                    ToolCall(id="echo-id", name="echo", args={"text": "second result"}),
                ],
            )
        else:
            assert [message.tool_call_id for message in results] == ["read-id", "echo-id"]
            assert results[1].content == "second result"
            message = Message(role="assistant", content="Both results received.")
        yield {
            "kind": "result",
            "message": message,
            "usage": {"input_tokens": 7, "output_tokens": 2},
        }

    monkeypatch.setattr("glassbox.application.runner.generate", scripted_model)
    run, *_ = await start(client, "Use both tools")
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert [message["role"] for message in completed["messages"]] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert completed["usage"]["input_tokens"] == 14


async def test_unknown_effect_requires_explicit_result_before_resume(glassbox_client, monkeypatch):
    """A shutdown after dispatch cannot silently execute the same effect again."""
    client, app = glassbox_client
    await allow_echo(client)
    entered = asyncio.Event()
    executions = 0

    async def uncertain_execute(*_args):
        nonlocal executions
        executions += 1
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.glassbox.registry, "execute", uncertain_execute)
    run, *_ = await start(client, "/echo external effect")
    await asyncio.wait_for(entered.wait(), 3)
    replacement = await replace_runner(app)
    try:
        interrupted = await wait_state(client, run["id"], {"interrupted"})
        assert len(interrupted["unknown_calls"]) == 1
        assert (await client.post(f"/api/v1/runs/{run['id']}/resume", json={})).status_code == 409
        call_id = interrupted["unknown_calls"][0]["id"]
        response = await client.post(
            f"/api/v1/runs/{run['id']}/resume",
            json={
                "resolutions": {
                    call_id: {"action": "record", "result": "Externally verified result"}
                }
            },
        )
        assert response.status_code == 200
        completed = await wait_state(client, run["id"], {"succeeded"})
        assert executions == 1
        assert any(
            message["content"] == "Externally verified result" for message in completed["messages"]
        )
        call = await app.state.glassbox.repository.call(call_id)
        assert call["data"]["attempt_history"][0]["status"] == "outcome_unknown"
    finally:
        await replacement.close()


async def test_receipt_survives_interruption_before_checkpoint(glassbox_client, monkeypatch):
    """Replaying a node after its receipt is durable must not repeat the tool."""
    client, app = glassbox_client
    await allow_echo(client)
    repository = app.state.glassbox.repository
    original_emit = repository.emit
    original_execute = app.state.glassbox.registry.execute
    executions = 0
    interrupted_once = False
    at_receipt = asyncio.Event()

    async def counted_execute(*args):
        nonlocal executions
        executions += 1
        return await original_execute(*args)

    async def interrupted_emit(run_id, event_type, payload, **changes):
        nonlocal interrupted_once
        if event_type == "tool.completed" and not interrupted_once:
            interrupted_once = True
            at_receipt.set()
            await asyncio.Event().wait()
        return await original_emit(run_id, event_type, payload, **changes)

    monkeypatch.setattr(repository, "emit", interrupted_emit)
    monkeypatch.setattr(app.state.glassbox.registry, "execute", counted_execute)
    run, *_ = await start(client, "/echo durable receipt")
    await asyncio.wait_for(at_receipt.wait(), 3)
    replacement = await replace_runner(app)
    try:
        response = await client.post(f"/api/v1/runs/{run['id']}/resume", json={})
        assert response.status_code == 200
        completed = await wait_state(client, run["id"], {"succeeded"})
        assert executions == 1
        assert sum(message["role"] == "tool" for message in completed["messages"]) == 1
    finally:
        await replacement.close()


async def test_tool_timeout_is_uncertain_and_can_be_cancelled(glassbox_client, monkeypatch):
    """A timeout stops execution without pretending an external call was rolled back."""
    client, app = glassbox_client
    await allow_echo(client, tool_timeout=0.1)

    async def slow_execute(*_args):
        await asyncio.sleep(10)
        return "late"

    monkeypatch.setattr(app.state.glassbox.registry, "execute", slow_execute)
    run, *_ = await start(client, "/echo timeout")
    interrupted = await wait_state(client, run["id"], {"interrupted"})
    assert len(interrupted["unknown_calls"]) == 1
    assert (await client.post(f"/api/v1/runs/{run['id']}/cancel")).json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "limits,reason", [({"max_steps": 1}, "max_steps"), ({"max_tool_calls": 1}, "max_tool_calls")]
)
async def test_attempt_limits_stop_the_loop(glassbox_client, monkeypatch, limits, reason):
    """A provider requesting tools forever cannot escape either attempt budget."""
    client, _app = glassbox_client
    await allow_echo(client, **limits)

    async def looping_model(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        yield {
            "kind": "result",
            "message": Message(
                role="assistant",
                tool_calls=[ToolCall(id="call", name="echo", args={"text": "again"})],
            ),
            "usage": {"input_tokens": None, "output_tokens": None},
        }

    monkeypatch.setattr("glassbox.application.runner.generate", looping_model)
    run, *_ = await start(client)
    stopped = await wait_state(client, run["id"], {"failed"})
    assert stopped["stop_reason"] == reason


async def test_runs_are_priced_from_the_frozen_model_price(glassbox_client, monkeypatch):
    """A Run's cost uses the price frozen in its snapshot; the context view sums the conversation."""
    client, _app = glassbox_client
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    config["providers"][0]["prices"] = {"gpt-x": {"input": 2, "output": 8, "cache_read": 0.5}}
    config["provider"] = {**config["provider"], "profile_id": "openai", "adapter": "openai"}
    config["provider"]["model"] = "gpt-x"
    assert (await client.put("/api/v1/config", json=config)).status_code == 200

    async def priced_model(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        yield {
            "kind": "result",
            "message": Message(role="assistant", content="Done."),
            # Chat Completions counts the 800 cached tokens inside the 1000 input tokens.
            "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 800},
        }

    monkeypatch.setattr("glassbox.application.runner.generate", priced_model)
    run, _body, session_id, _preview = await start(client)
    completed = await wait_state(client, run["id"], {"succeeded"})
    # 200 uncached x $2 + 800 cached x $0.5 + 500 output x $8, per million tokens.
    assert completed["usage"]["cost"] == 0.0048
    context = (await client.get(f"/api/v1/sessions/{session_id}/context")).json()
    assert (context["spent"], context["unpriced_runs"]) == (0.0048, 0)
