"""Protect lifecycle and protocol failures identified by service and browser review."""

import asyncio
import io
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.pool import QueuePool

from glassbox.domain import AppError, GlassboxConfig
from glassbox.harness import Harness
from glassbox.infrastructure.config import Settings
from glassbox.infrastructure.sandbox.command_io import CommandControl
from glassbox.interfaces.client import AgentClient
from glassbox.interfaces.rpc import serve_rpc


async def test_process_exit_during_settings_read_returns_conflict(tmp_path, monkeypatch):
    """An owned command finishing across an await cannot turn an input action into a KeyError/500."""
    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
    )
    async with Harness(settings) as harness:
        run = await harness.run("Create an owner for the process receipt.")
        await harness.wait(run.id, 10)
        manager = harness.registry.processes
        assert manager is not None
        gate = asyncio.Event()
        control = CommandControl()
        data = {
            "session_id": run.session_id,
            "workspace_root": str(tmp_path),
            "mode": "workspace-write",
        }
        await harness.repository.save_process("exiting", run.id, "running", data)

        async def finish() -> None:
            """Finish a real persisted lifecycle receipt exactly while input reads settings."""
            await gate.wait()
            await harness.repository.save_process("exiting", run.id, "exited", data)

        task = asyncio.create_task(finish())
        manager.live["exiting"] = (control, task, data)
        task.add_done_callback(lambda _completed: manager.live.pop("exiting", None))

        async def settings_after_exit() -> GlassboxConfig:
            """Force the otherwise timing-dependent completion between ownership and input checks."""
            gate.set()
            await task
            return GlassboxConfig()

        monkeypatch.setattr(harness.repository, "config", settings_after_exit)
        with pytest.raises(AppError) as caught:
            await manager.input("exiting", run.session_id, "Do not queue this input.")
        assert caught.value.status == 409 and caught.value.code == "process_finished"
        assert control.inputs.empty()
        assert (await harness.repository.process("exiting"))["status"] == "exited"


async def test_failed_summary_cleanup_still_closes_checkpoints(tmp_path, monkeypatch):
    """A failure in the first shutdown stage still closes actual checkpoint and DB resources."""
    harness = Harness(
        Settings.without_env_file(
            data_dir=tmp_path / "data", workspace_root=tmp_path, provider="fake"
        )
    )
    await harness.__aenter__()
    connection = harness.runner.checkpointer.conn
    monkeypatch.setattr(
        harness.glassbox, "close", AsyncMock(side_effect=RuntimeError("cleanup fixture"))
    )
    with pytest.raises(RuntimeError, match="cleanup fixture"):
        await harness.__aexit__()
    with pytest.raises(ValueError):
        await connection.execute("SELECT 1")
    pool = harness.repository.engine.pool
    assert not harness.entered and isinstance(pool, QueuePool) and pool.checkedout() == 0


async def test_rpc_internal_failure_keeps_next_frame_readable(monkeypatch):
    """One internal failure returns JSON-RPC -32603 and does not end the client's next request."""
    lines = b'{"jsonrpc":"2.0","id":1,"method":"fixture"}\n{"jsonrpc":"2.0","id":2,"method":"fixture"}\n'
    input_stream = io.TextIOWrapper(io.BytesIO(lines), encoding="utf-8")
    output = io.StringIO()
    monkeypatch.setattr("glassbox.interfaces.rpc.sys.stdin", input_stream)
    monkeypatch.setattr("glassbox.interfaces.rpc.sys.stdout", output)
    monkeypatch.setattr(
        "glassbox.interfaces.rpc.dispatch",
        AsyncMock(side_effect=[RuntimeError("fixture"), {"status": "ok"}]),
    )
    # The dispatcher is replaced above, so the client is never used.
    await serve_rpc(AgentClient())
    first, second = [json.loads(line) for line in output.getvalue().splitlines()]
    assert first["id"] == 1 and first["error"]["code"] == -32603
    assert second["id"] == 2 and second["result"] == {"status": "ok"}
