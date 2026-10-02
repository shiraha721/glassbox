"""Exercise the durable loop at approval, side-effect, and checkpoint boundaries."""

import asyncio

import pytest
from test_api import start, wait_state

from rtagent.domain import Message, ToolCall
from rtagent.runtime import Runner


async def allow_echo(client, **limits):
    """Explicitly permit the harmless fixture tool and adjust bounded test limits."""
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    config["tools"]["echo"] = "allow"
    config.update(limits)
    response = await client.put("/api/v1/config", json=config)
    assert response.status_code == 200


async def replace_runner(app):
    """Reopen the same durable stores with a fresh graph and task owner."""
    old = app.state.studio.runner
    await old.close()
    replacement = Runner(old.repository, old.registry, old.checkpoint_path)
    await replacement.open()
    app.state.studio.runner = replacement
    return replacement


async def test_multiple_calls_keep_ids_and_complete_before_next_model(studio_client, monkeypatch):
    """A batch must return one ordered result for every original model call ID."""
    client, _app = studio_client
    await allow_echo(client)

    async def scripted_model(config, compiled, tools, prepared, on_wire):
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

    monkeypatch.setattr("rtagent.runtime.generate", scripted_model)
    run, *_ = await start(client, "Use both tools")
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert [message["role"] for message in completed["messages"]] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert completed["usage"]["input_tokens"] == 14


async def test_unknown_effect_requires_explicit_result_before_resume(studio_client, monkeypatch):
    """A shutdown after dispatch cannot silently execute the same effect again."""
    client, app = studio_client
    await allow_echo(client)
    entered = asyncio.Event()
    executions = 0

    async def uncertain_execute(*_args):
        nonlocal executions
        executions += 1
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.studio.registry, "execute", uncertain_execute)
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
        call = await app.state.studio.repository.call(call_id)
        assert call["data"]["attempt_history"][0]["status"] == "outcome_unknown"
    finally:
        await replacement.close()


async def test_receipt_survives_interruption_before_checkpoint(studio_client, monkeypatch):
    """Replaying a node after its receipt is durable must not repeat the tool."""
    client, app = studio_client
    await allow_echo(client)
    repository = app.state.studio.repository
    original_emit = repository.emit
    original_execute = app.state.studio.registry.execute
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
    monkeypatch.setattr(app.state.studio.registry, "execute", counted_execute)
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


async def test_tool_timeout_is_uncertain_and_can_be_cancelled(studio_client, monkeypatch):
    """A timeout stops execution without pretending an external call was rolled back."""
    client, app = studio_client
    await allow_echo(client, tool_timeout=0.1)

    async def slow_execute(*_args):
        await asyncio.sleep(10)
        return "late"

    monkeypatch.setattr(app.state.studio.registry, "execute", slow_execute)
    run, *_ = await start(client, "/echo timeout")
    interrupted = await wait_state(client, run["id"], {"interrupted"})
    assert len(interrupted["unknown_calls"]) == 1
    assert (await client.post(f"/api/v1/runs/{run['id']}/cancel")).json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "limits,reason", [({"max_steps": 1}, "max_steps"), ({"max_tool_calls": 1}, "max_tool_calls")]
)
async def test_attempt_limits_stop_the_loop(studio_client, monkeypatch, limits, reason):
    """A provider requesting tools forever cannot escape either attempt budget."""
    client, _app = studio_client
    await allow_echo(client, **limits)

    async def looping_model(config, compiled, tools, prepared, on_wire):
        await on_wire(None)
        yield {
            "kind": "result",
            "message": Message(
                role="assistant",
                tool_calls=[ToolCall(id="call", name="echo", args={"text": "again"})],
            ),
            "usage": {"input_tokens": None, "output_tokens": None},
        }

    monkeypatch.setattr("rtagent.runtime.generate", looping_model)
    run, *_ = await start(client)
    stopped = await wait_state(client, run["id"], {"failed"})
    assert stopped["stop_reason"] == reason
