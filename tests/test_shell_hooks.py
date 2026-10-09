"""User-configured shell hooks: bounded host commands that observe Runs or block tool calls."""

import asyncio
import json
import os
import time

import pytest
from test_api import start, wait_state
from test_runtime import allow_echo

from glassbox.domain import Message, ShellHook, ToolCall
from glassbox.domain.hooks import matching_hooks
from glassbox.infrastructure.shell_hooks import run_hook

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the hook commands use a POSIX shell")


def test_hooks_match_by_event_and_tool_glob():
    hooks = [
        ShellHook(event="before_tool", tools="mcp_*", command="a"),
        ShellHook(event="before_tool", command="b"),
        ShellHook(event="run_end", command="c"),
    ]
    assert [index for index, _ in matching_hooks(hooks, "before_tool", "bash")] == [1]
    assert [index for index, _ in matching_hooks(hooks, "before_tool", "mcp_x")] == [0, 1]
    assert [index for index, _ in matching_hooks(hooks, "run_end")] == [2]


async def test_a_hook_reads_stdin_with_a_minimal_environment(tmp_path, monkeypatch):
    """Service secrets in the environment never reach a hook; event data arrives on stdin."""
    monkeypatch.setenv("GLASSBOX_TEST_SECRET", "do-not-pass")
    hook = ShellHook(event="after_tool", command='cat; echo; env; echo "to stderr" >&2')
    outcome = await run_hook(hook, {"event": "after_tool", "value": "λ"}, tmp_path)
    assert outcome.succeeded and outcome.error is None
    first, environment = outcome.stdout.split("\n", 1)
    assert json.loads(first) == {"event": "after_tool", "value": "λ"}
    assert "GLASSBOX_HOOK_EVENT=after_tool" in environment
    assert "GLASSBOX_TEST_SECRET" not in environment
    assert outcome.stderr == "to stderr\n"


async def test_hooks_are_bounded_in_time_and_do_not_wait_for_background_children(tmp_path):
    slow = await run_hook(ShellHook(event="run_end", command="sleep 30", timeout=1), {}, tmp_path)
    assert slow.exit_code is None and slow.error == "Timed out after 1 s."
    started = time.monotonic()
    detached = await run_hook(
        ShellHook(event="run_end", command="sleep 30 & echo started", timeout=20), {}, tmp_path
    )
    assert detached.succeeded and detached.stdout == "started\n"
    assert time.monotonic() - started < 5
    missing = await run_hook(ShellHook(event="run_end", command="exit 3"), {}, tmp_path)
    assert missing.exit_code == 3 and not missing.succeeded


def echo_then_finish():
    """A scripted model that calls echo once, then answers."""

    async def model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        if any(message.role == "tool" for message in compiled.messages):
            message = Message(role="assistant", content="Finished.")
        else:
            message = Message(
                role="assistant",
                tool_calls=[ToolCall(id="echo-1", name="echo", args={"text": "hooked"})],
            )
        yield {"kind": "result", "message": message, "usage": {}}

    return model


async def test_before_tool_hooks_can_block_and_other_hooks_observe(
    glassbox_client, monkeypatch, tmp_path
):
    """Exit 2 blocks with the hook's reason; after_tool and run_end hooks see the outcome."""
    client, app = glassbox_client
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("glassbox.application.runner.generate", echo_then_finish())
    blocking = {
        "event": "before_tool",
        "tools": "echo",
        "command": 'cat > before.json; echo "echo is paused" >&2; exit 2',
    }
    await allow_echo(client, hooks=[blocking])
    run, *_ = await start(client, "Use echo")
    completed = await wait_state(client, run["id"], {"succeeded"})
    result = json.loads(next(m for m in completed["messages"] if m["role"] == "tool")["content"])
    assert result["error"] == {"code": "tool_denied", "message": "echo is paused"}
    seen = json.loads((workspace / "before.json").read_text())
    assert seen["tool"] == {"name": "echo", "args": {"text": "hooked"}}
    assert seen["run_id"] == run["id"] and seen["workspace_root"] == str(workspace)
    receipt = await app.state.glassbox.repository.call(f"{run['id']}:tool:0000:0000")
    assert receipt["data"]["hooks"][0]["exit_code"] == 2
    assert "after_tool" not in json.dumps(receipt["data"]["hooks"])

    hooks = [
        {"event": "before_tool", "command": "exit 0"},
        {"event": "after_tool", "tools": "echo", "command": "cat > after.json"},
        {"event": "run_end", "command": "cat > end.json"},
    ]
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    assert (await client.put("/api/v1/config", json={**config, "hooks": hooks})).status_code == 200
    run, *_ = await start(client, "Use echo again")
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert any(m["content"] == "hooked" for m in completed["messages"] if m["role"] == "tool")
    after = json.loads((workspace / "after.json").read_text())
    assert (after["status"], after["result"]) == ("succeeded", "hooked")
    async with asyncio.timeout(5):
        while not (workspace / "end.json").exists():
            await asyncio.sleep(0.05)
        while (events := await app.state.glassbox.repository.read_events(run["id"], 0))[-1][
            "type"
        ] != "hook.ran":
            await asyncio.sleep(0.05)
    end = json.loads((workspace / "end.json").read_text())
    assert (end["status"], end["input"]) == ("succeeded", "Use echo again")
    assert events[-1]["payload"]["exit_code"] == 0


async def test_a_failing_before_tool_hook_blocks_the_call(glassbox_client, monkeypatch):
    """A hook that crashes or times out fails closed: the tool is not dispatched."""
    client, app = glassbox_client
    monkeypatch.setattr("glassbox.application.runner.generate", echo_then_finish())
    executed = []
    original = app.state.glassbox.registry.execute

    async def counted(*args):
        executed.append(args)
        return await original(*args)

    monkeypatch.setattr(app.state.glassbox.registry, "execute", counted)
    await allow_echo(client, hooks=[{"event": "before_tool", "command": "exit 1"}])
    run, *_ = await start(client, "Use echo")
    completed = await wait_state(client, run["id"], {"succeeded"})
    result = json.loads(next(m for m in completed["messages"] if m["role"] == "tool")["content"])
    assert result["error"]["code"] == "hook_failed" and "exit code 1" in result["error"]["message"]
    assert executed == []
