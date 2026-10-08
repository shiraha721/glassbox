"""Sub-agents: a delegated task runs as a child Run inside the parent's tool call."""

import asyncio
import json

from test_api import start, wait_state

from rtagent.application.runner import SUBAGENT_NOTE
from rtagent.domain import Message, ToolCall


def is_child(compiled) -> bool:
    current = next(m for m in compiled.messages if m.source == "current_input")
    return current.content.endswith(SUBAGENT_NOTE)


async def test_a_delegated_task_runs_as_a_hidden_child_run(studio_client, monkeypatch):
    client, app = studio_client
    seen_tools = {}

    async def model(_config, compiled, tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        names = {tool["name"] for tool in tools}
        if is_child(compiled):
            seen_tools["child"] = names
            message = Message(role="assistant", content="There are 2 files.")
        elif any(m.role == "tool" for m in compiled.messages):
            message = Message(role="assistant", content="The sub-agent counted 2.")
        else:
            seen_tools["parent"] = names
            message = Message(
                role="assistant",
                tool_calls=[ToolCall(id="d1", name="delegate", args={"task": "Count the files."})],
            )
        yield {"kind": "result", "message": message, "usage": {}}

    monkeypatch.setattr("rtagent.application.runner.generate", model)
    run, _body, session_id, _preview = await start(client, "Delegate the count")
    finished = await wait_state(client, run["id"], {"succeeded"})
    assert finished["messages"][-1]["content"] == "The sub-agent counted 2."
    result = json.loads(next(m for m in finished["messages"] if m["role"] == "tool")["content"])
    assert (result["status"], result["answer"]) == ("succeeded", "There are 2 files.")

    # The child cannot delegate again or use tools that would wait for approval.
    assert "delegate" in seen_tools["parent"] and "echo" in seen_tools["parent"]
    assert "delegate" not in seen_tools["child"] and "echo" not in seen_tools["child"]
    assert "read_text" in seen_tools["child"]

    repository = app.state.studio.repository
    receipt = await repository.call(f"{run['id']}:tool:0000:0000")
    assert receipt["data"]["child_run_id"] == result["run_id"]
    child_id = result["run_id"]
    child = await repository.run(child_id)
    assert child["status"] == "succeeded"
    assert child["snapshot"]["parent_run_id"] == run["id"]
    assert child["snapshot"]["workspace_root"] == finished["workspace_root"]
    assert (await client.get(f"/api/v1/runs/{child_id}")).json()["status"] == "succeeded"
    # Hidden from the conversation list, counted with the conversation, deleted with it.
    # The conversation's request totals include the sub-agent's request; its chart does not.
    context = (await client.get(f"/api/v1/sessions/{session_id}/context")).json()
    assert context["session_usage"]["requests"] == 3
    assert context["session_usage"]["subagent_requests"] == 1
    assert len(context["requests"]) == 2
    listed = [item["id"] for item in (await client.get("/api/v1/bootstrap")).json()["sessions"]]
    assert child["session_id"] not in listed
    assert [item["id"] for item in await repository.child_runs(session_id)] == [child_id]
    assert (await client.delete(f"/api/v1/sessions/{session_id}")).status_code in {200, 204}
    assert (await client.get(f"/api/v1/runs/{child_id}")).status_code == 404


async def test_cancelling_the_parent_cancels_the_working_sub_agent(studio_client, monkeypatch):
    client, app = studio_client
    child_started = asyncio.Event()

    async def model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        if is_child(compiled):
            child_started.set()
            await asyncio.Event().wait()
        message = Message(
            role="assistant",
            tool_calls=[ToolCall(id="d1", name="delegate", args={"task": "Wait forever."})],
        )
        yield {"kind": "result", "message": message, "usage": {}}

    monkeypatch.setattr("rtagent.application.runner.generate", model)
    run, *_ = await start(client, "Delegate and stop")
    await asyncio.wait_for(child_started.wait(), 5)
    running = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    child_id = running["active_tool"]["child_run_id"]
    cancelled = await client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    child = await app.state.studio.repository.run(child_id)
    assert child["status"] == "cancelled"


async def test_a_queued_child_is_not_restarted_without_its_parent(studio_client):
    """After a restart only the parent can continue the work, so the child is interrupted."""
    client, app = studio_client
    repository = app.state.studio.repository
    session = (await client.post("/api/v1/sessions", json={})).json()
    snapshot = {"input": "Orphaned task", "project_id": None, "workspace_root": "/tmp"}
    child = await repository.create_child_run(session["id"], "subagent:x", snapshot)
    again = await repository.create_child_run(session["id"], "subagent:x", snapshot)
    assert again["id"] == child["id"]
    assert child["id"] not in await repository.recover()
    assert (await repository.run(child["id"]))["status"] == "interrupted"
