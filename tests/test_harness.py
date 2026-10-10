"""Protect context provenance and extension boundaries after live HTTP acceptance."""

import asyncio
import socket

import httpx
import httpx2
import openai
import pytest
from langchain_core.messages import HumanMessage

from glassbox.domain import (
    AppError,
    AutoCompact,
    Block,
    GlassboxConfig,
    Message,
    Preset,
    ProviderConfig,
    ToolCall,
    ToolDefinition,
    digest,
)
from glassbox.domain.hooks import Hooks
from glassbox.domain.prompts import compile_prompt
from glassbox.harness import Harness
from glassbox.infrastructure.config import Settings
from glassbox.infrastructure.providers.messages import prepare_anthropic
from glassbox.infrastructure.storage import Repository


async def test_late_summary_does_not_activate_after_metadata_revision(tmp_path, monkeypatch):
    """A completed provider result cannot replace context after its captured revision changes."""
    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
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

        monkeypatch.setattr("glassbox.application.context.generate", summarizer)
        from glassbox.domain import CompactRequest, SessionUpdate

        job = await harness.glassbox.compact(
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
        await harness.glassbox.compaction_tasks[job.id]
        final = await harness.repository.session(first.session_id)
        assert final["compaction_id"] is None and final["messages"] == session["messages"]
        failed = await harness.repository.compaction(job.id)
        assert failed["status"] == "failed" and failed["data"]["calls"][0]["status"] == "succeeded"
        assert failed["data"]["usage"]["output_tokens"] is None


async def test_auto_compaction_is_opt_in_recorded_and_reversible(tmp_path, monkeypatch):
    """Once enabled, a Run that leaves the history past the threshold starts a recorded summary
    of older turns; the original messages stay and Restore full context undoes it."""
    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
    )

    async def summarizer(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        yield {
            "kind": "result",
            "message": Message(role="assistant", content="Derived summary."),
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr("glassbox.application.context.generate", summarizer)
    async with Harness(settings) as harness:
        first = await harness.run("Large earlier evidence. " * 400)
        assert (await harness.wait(first.id, 10)).status == "succeeded"
        second = await harness.run("Keep the recent turn.", session_id=first.session_id)
        assert (await harness.wait(second.id, 10)).status == "succeeded"
        # Off by default, however large the history is.
        assert await harness.glassbox.auto_compact(second.id) is None
        config = await harness.repository.config()
        config.auto_compact = AutoCompact(enabled=True, threshold_tokens=100_000)
        config = await harness.glassbox.save_config(config)
        # Below the threshold nothing happens.
        assert await harness.glassbox.auto_compact(second.id) is None
        config.auto_compact = AutoCompact(enabled=True, threshold_tokens=1_000, keep_recent_turns=1)
        await harness.glassbox.save_config(config)
        before = await harness.repository.session(first.session_id)

        third = await harness.run("Trigger the check.", session_id=first.session_id)
        assert (await harness.wait(third.id, 10)).status == "succeeded"
        async with asyncio.timeout(5):
            while not (records := await harness.repository.list_compactions(first.session_id)):
                await asyncio.sleep(0.05)
            await asyncio.gather(*harness.glassbox.compaction_tasks.values())
        job = await harness.repository.compaction(records[0]["id"])
        assert job["status"] == "succeeded" and job["data"]["trigger"] == "auto"
        # The two older turns are covered and the newest turn stays verbatim.
        assert job["data"]["through_run_id"] == second.id
        session = await harness.repository.session(first.session_id)
        assert session["compaction_id"] == job["id"]
        assert session["messages"][: len(before["messages"])] == before["messages"]
        context = await harness.glassbox.context(first.session_id)
        assert context.compactions[0].trigger == "auto"
        # The view carries the revision the activation produced, for the panel's next action.
        assert context.revision == session["revision"]
        assert context.effective_estimated_tokens < context.raw_estimated_tokens
        # Nothing new to cover: the next check is a no-op rather than another summary.
        assert await harness.glassbox.auto_compact(third.id) is None
        await harness.repository.reset_compaction(first.session_id, session["revision"])
        assert (await harness.repository.session(first.session_id))["compaction_id"] is None


async def test_restart_marks_reduction_and_process_intents_uncertain(tmp_path):
    """Reopening receipts preserves partial evidence without starting any abandoned effects."""
    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
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
    config = GlassboxConfig(provider=ProviderConfig(adapter="anthropic", model="fixture"))
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

    monkeypatch.setattr("glassbox.application.runner.generate", model)
    async with Harness(
        Settings.without_env_file(
            data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
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

    from glassbox.domain import MCPServer

    fixture = Path(__file__).parent / "fixtures" / "mcp_server.py"
    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
    )
    async with Harness(settings) as harness:
        config = await harness.repository.config()
        config.mcp_servers = [
            MCPServer(name="Fixture", command=sys.executable, args=[str(fixture.resolve())])
        ]
        config = await harness.glassbox.save_config(config)
        tools = await harness.registry.discover(config)
        name = next(tool.name for tool in tools if tool.original_name == "fixture_echo")
        config.tools[name] = "allow"
        await harness.glassbox.save_config(config)
    async with Harness(settings) as harness:
        assert name not in harness.registry.catalog
        run = await harness.wait((await harness.run(f'/tool {name} {{"text": "again"}}')).id, 30)
        assert run.status == "succeeded", run.error
        assert "MCP evidence: again" in run.messages[1].content


@pytest.mark.parametrize("failures,status", [(1, "succeeded"), (3, "failed")])
async def test_summary_requests_are_retried_after_a_broken_connection(
    tmp_path, monkeypatch, failures, status
):
    """A summary request has no effect but its cost, so a dropped connection is retried."""
    from glassbox.domain import CompactRequest
    from glassbox.infrastructure.providers.errors import transport_failure

    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
    )
    async with Harness(settings) as harness:
        first = await harness.run("Evidence to summarize.")
        assert (await harness.wait(first.id, 10)).status == "succeeded"
        second = await harness.run("Recent turn.", session_id=first.session_id)
        assert (await harness.wait(second.id, 10)).status == "succeeded"
        attempts = []

        async def flaky(_config, _compiled, _tools, _prepared, on_wire, _key=""):
            await on_wire(None)
            attempts.append(1)
            if len(attempts) <= failures:
                raise transport_failure(httpx.RemoteProtocolError("peer closed"))
            yield {
                "kind": "result",
                "message": Message(role="assistant", content="Summary."),
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        monkeypatch.setattr("glassbox.application.context.generate", flaky)
        monkeypatch.setattr("glassbox.application.context.RETRY_DELAYS", (0, 0))
        session = await harness.repository.session(first.session_id)
        job = await harness.glassbox.compact(
            first.session_id,
            CompactRequest(
                expected_revision=session["revision"],
                config_revision=(await harness.repository.config()).revision,
                keep_recent_turns=1,
            ),
        )
        await harness.glassbox.compaction_tasks[job.id]
        record = await harness.repository.compaction(job.id)
        assert record["status"] == status
        call = record["data"]["calls"][0]
        assert len(call["attempt_history"]) == min(failures, 2)
        assert "closed before the response was complete" in call["attempt_history"][0]["error"]
        if status == "failed":
            assert record["data"]["error"].endswith("Tried 3 times.")


def test_connection_failures_say_what_happened():
    from glassbox.infrastructure.providers.errors import sdk_failure, transport_failure

    refused = transport_failure(httpx.ConnectError("refused"))
    assert "could not connect" in refused.message and refused.details == {"transient": True}
    dropped = transport_failure(httpx.ReadError("reset"))
    assert "closed before the response was complete (ReadError)" in dropped.message
    # The SDK is built on httpx2; its errors carry httpx2 requests and responses.
    request = httpx2.Request("POST", "https://api.example/v1/chat/completions")
    wrapped = openai.APIConnectionError(request=request)
    wrapped.__cause__ = httpx.RemoteProtocolError("peer closed")
    assert "closed before" in sdk_failure(wrapped, "").message
    status = sdk_failure(
        openai.APIStatusError(
            "bad", response=httpx2.Response(400, request=request), body={"error": "x"}
        ),
        "",
    )
    assert status.details is None


async def test_a_refused_connection_is_named_through_the_sdk_wrappers():
    """LangChain re-raises the SDK's connection error, so the reason lies two causes down."""
    from glassbox.infrastructure.providers.chat import model
    from glassbox.infrastructure.providers.errors import sdk_failure

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Nothing listens on the port once the probe is closed, so connecting is refused.
    config = ProviderConfig(adapter="openai", model="m", base_url=f"http://127.0.0.1:{port}/v1")
    async with httpx.AsyncClient(timeout=5) as client:
        with pytest.raises(openai.APIConnectionError) as caught:
            async for _chunk in model(config, "key", client).astream([HumanMessage("Hi")]):
                pass
    failure = sdk_failure(caught.value, "key")
    assert "could not connect" in failure.message and failure.details == {"transient": True}


async def test_auto_compaction_starts_when_a_request_fills_the_context_window(
    tmp_path, monkeypatch
):
    """By default, automatic compaction waits until a request used 90% of the model's window."""
    settings = Settings.without_env_file(data_dir=tmp_path / "data", workspace_root=tmp_path)
    prompts = iter([1_000, 100_000, 185_000])

    async def model(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        usage = {"input_tokens": next(prompts), "output_tokens": 10}
        yield {
            "kind": "result",
            "message": Message(role="assistant", content="Ok."),
            "usage": usage,
        }

    async def summarizer(_config, _compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        yield {
            "kind": "result",
            "message": Message(role="assistant", content="Derived summary."),
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr("glassbox.application.runner.generate", model)
    monkeypatch.setattr("glassbox.application.context.generate", summarizer)
    async with Harness(settings) as harness:
        assert AutoCompact().window_percent == 90 and AutoCompact().threshold_tokens is None
        config = await harness.repository.config()
        profile = config.providers[0]
        profile.models, profile.context_window, profile.credential_ref = ["m"], 200_000, ""
        config.provider = ProviderConfig(profile_id=profile.id, model="m", credential_ref="")
        config.auto_compact = AutoCompact(enabled=True, keep_recent_turns=1)
        await harness.glassbox.save_config(config)
        session_id = None
        for task in ("First.", "Second, half the window.", "Third, nearly full."):
            run = await harness.run(task, session_id=session_id)
            session_id = run.session_id
            assert (await harness.wait(run.id, 10)).status == "succeeded"
            # A request at 50% of the window leaves the conversation alone.
            if task.startswith("Second"):
                await asyncio.sleep(0.2)
                assert not await harness.repository.list_compactions(session_id)
        assert await harness.glassbox.context_used(run.id) == 185_000
        async with asyncio.timeout(5):
            while not (records := await harness.repository.list_compactions(session_id)):
                await asyncio.sleep(0.05)
            await asyncio.gather(*harness.glassbox.compaction_tasks.values())
        job = await harness.repository.compaction(records[0]["id"])
        assert job["status"] == "succeeded" and job["data"]["trigger"] == "auto"
        with pytest.raises(ValueError, match="Choose when"):
            AutoCompact(enabled=True, window_percent=None, threshold_tokens=None)
