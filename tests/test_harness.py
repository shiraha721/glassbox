"""Protect context provenance and extension boundaries after live HTTP acceptance."""

import asyncio

import pytest

from rtagent.domain import (
    AppError,
    Block,
    Message,
    Preset,
    ProviderConfig,
    StudioConfig,
    ToolCall,
    ToolDefinition,
    digest,
)
from rtagent.domain.hooks import Hooks
from rtagent.domain.prompts import compile_prompt
from rtagent.harness import Harness
from rtagent.infrastructure.config import Settings
from rtagent.infrastructure.providers.messages import prepare_anthropic
from rtagent.infrastructure.storage import Repository


async def test_late_summary_does_not_activate_after_metadata_revision(tmp_path, monkeypatch):
    """A completed provider result cannot replace context after its captured revision changes."""
    settings = Settings(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake", _env_file=None
    )
    async with Harness(settings) as harness:
        first = await harness.run("Original observed evidence.")
        assert (await harness.wait(first.id, 10)).status == "succeeded"
        second = await harness.run("Keep the recent turn intact.", session_id=first.session_id)
        assert (await harness.wait(second.id, 10)).status == "succeeded"
        session = await harness.repository.session(first.session_id)
        gate, release = asyncio.Event(), asyncio.Event()

        async def summarizer(_config, _compiled, _tools, _prepared, on_wire, _key=""):
            """Hold a derived result until a real versioned conversation edit commits."""
            await on_wire(None)
            gate.set()
            await release.wait()
            yield {
                "kind": "result",
                "message": Message(role="assistant", content="Derived evidence."),
                "usage": {"input_tokens": 1, "output_tokens": None},
            }

        monkeypatch.setattr("rtagent.application.context.generate", summarizer)
        from rtagent.domain import CompactRequest, SessionUpdate

        job = await harness.studio.compact(
            first.session_id,
            CompactRequest(
                expected_revision=session["revision"],
                config_revision=(await harness.repository.config()).revision,
                keep_recent_turns=1,
            ),
        )
        await asyncio.wait_for(gate.wait(), 3)
        await harness.repository.update_session(
            first.session_id,
            SessionUpdate(
                expected_revision=session["revision"],
                title="User renamed this conversation",
            ),
        )
        release.set()
        await harness.studio.compaction_tasks[job.id]
        final = await harness.repository.session(first.session_id)
        assert final["compaction_id"] is None and final["messages"] == session["messages"]
        failed = await harness.repository.compaction(job.id)
        assert failed["status"] == "failed" and failed["data"]["calls"][0]["status"] == "succeeded"
        assert failed["data"]["usage"]["output_tokens"] is None


async def test_restart_marks_reduction_and_process_intents_uncertain(tmp_path):
    """Reopening receipts preserves partial evidence without starting any abandoned effects."""
    settings = Settings(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake", _env_file=None
    )
    async with Harness(settings) as harness:
        view = await harness.run("A completed source turn.")
        assert (await harness.wait(view.id, 10)).status == "succeeded"
        session = await harness.repository.session(view.session_id)
        job = await harness.repository.begin_compaction(
            view.session_id,
            session["revision"],
            {
                "source_revision": session["revision"],
                "covered_messages": 2,
                "source_hash": digest(session["messages"]),
                "calls": [{"status": "executing"}],
            },
        )
        await harness.repository.save_process(
            "abandoned-process",
            view.id,
            "running",
            {
                "session_id": view.session_id,
                "stdout": "Partial progress",
                "stdin_closed": False,
            },
        )
        url = harness.repository.url
    repository = Repository(url)
    try:
        await repository.initialize()
        assert await repository.recover() == []
        recovered = await repository.compaction(job["id"])
        assert recovered["status"] == "interrupted"
        assert recovered["data"]["calls"][0]["status"] == "outcome_unknown"
        process = await repository.process("abandoned-process")
        assert process["status"] == "interrupted" and process["data"]["stdin_closed"]
        assert process["data"]["stdout"] == "Partial progress" and process["data"]["error"]
        assert (await repository.session(view.session_id))["messages"] == session["messages"]
    finally:
        await repository.close()


def test_messages_rejects_lossy_roles_and_unpaired_tool_ids():
    """Unsupported canonical ordering must fail before transmitting a rewritten request."""
    config = StudioConfig(provider=ProviderConfig(adapter="anthropic", model="fixture"))
    preset = Preset(
        id="fixture",
        name="Fixture",
        blocks=[
            Block(id="system", name="System", template="Rules", order=10),
        ],
    )
    compiled = compile_prompt(
        preset, config, [Message(role="user", content="Earlier")], "Task", {}, []
    )
    compiled.messages.insert(2, Message(role="system", content="Late instructions"))
    with pytest.raises(AppError, match="cannot move"):
        prepare_anthropic(config.provider, compiled, [])
    compiled.messages.pop(2)
    compiled.messages.append(Message(role="tool", content="Orphan", tool_call_id="missing"))
    with pytest.raises(AppError, match="pending assistant"):
        prepare_anthropic(config.provider, compiled, [])


async def test_trusted_guard_is_never_called_before_core_denial(tmp_path, monkeypatch):
    """An embedding callback cannot grant an effect forbidden by the captured permission map."""
    guards, executions = [], []

    async def guard(call, _config):
        """Record attempts while returning no extra restriction."""
        guards.append(call.id)
        return None

    async def extension(_args, _workspace):
        """Fail evidence if a denied trusted extension reaches dispatch."""
        executions.append(True)
        return "Unexpected execution"

    async def model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        """Request a deliberately unexposed tool to exercise backend policy independently."""
        await on_wire(None)
        message = (
            Message(role="assistant", content="Denial observed.")
            if any(item.role == "tool" for item in compiled.messages)
            else Message(
                role="assistant",
                tool_calls=[ToolCall(id="forbidden", name="local_effect", args={})],
            )
        )
        yield {
            "kind": "result",
            "message": message,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr("rtagent.application.runner.generate", model)
    async with Harness(
        Settings(
            data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake", _env_file=None
        ),
        Hooks(before_tool=guard),
    ) as harness:
        harness.registry.register(
            ToolDefinition(
                name="local_effect",
                description="Fixture effect",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            extension,
        )
        view = await harness.run("The model requests a denied tool.")
        finished = await harness.wait(view.id, 10)
        assert finished.status == "succeeded"
        assert "tool_denied" in finished.messages[1].content and not guards and not executions


async def test_mcp_tools_are_rediscovered_after_restart(tmp_path):
    """Saved MCP permissions keep working after a restart without a manual refresh."""
    import sys
    from pathlib import Path

    from rtagent.domain import MCPServer

    fixture = Path(__file__).parent / "fixtures" / "mcp_server.py"
    settings = Settings(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake", _env_file=None
    )
    async with Harness(settings) as harness:
        config = await harness.repository.config()
        config.mcp_servers = [
            MCPServer(name="Fixture", command=sys.executable, args=[str(fixture.resolve())])
        ]
        config = await harness.studio.save_config(config)
        tools = await harness.registry.discover(config)
        name = next(tool.name for tool in tools if tool.original_name == "fixture_echo")
        config.tools[name] = "allow"
        await harness.studio.save_config(config)
    async with Harness(settings) as harness:
        assert name not in harness.registry.catalog
        run = await harness.wait((await harness.run(f'/tool {name} {{"text": "again"}}')).id, 30)
        assert run.status == "succeeded", run.error
        assert "MCP evidence: again" in run.messages[1].content
