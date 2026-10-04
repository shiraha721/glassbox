"""Own bounded live sandbox commands; persisted intents never restart an OS process."""

import asyncio
import json
from pathlib import Path
from typing import Any

from rtagent.domain import AppError, ProcessView, StudioConfig, digest, now, uid
from rtagent.domain.execution import ToolExecution
from rtagent.domain.lifecycle import ACTIVE_PROCESSES as ACTIVE
from rtagent.infrastructure.sandbox.command_io import CommandControl
from rtagent.infrastructure.storage import Repository


def process_view(record: dict[str, Any]) -> ProcessView:
    """Project durable bounded output and ownership without exposing private sandbox paths."""
    data = record["data"]
    return ProcessView(
        **{key: record[key] for key in ("id", "run_id", "status", "created_at")},
        **{key: data[key] for key in ("session_id", "command", "cwd", "workspace_root", "mode")},
        **{
            key: data[key]
            for key in ("pid", "stdin_closed", "stdout", "stderr", "truncated", "result", "error")
            if key in data
        },
    )


class ProcessManager:
    """Keep one service's live controls, bounded tasks, and durable lifecycle evidence."""

    def __init__(self, repository: Repository) -> None:
        """Bind persistence without spawning processes or creating another agent loop."""
        self.repository = repository
        self.live: dict[str, tuple[CommandControl, asyncio.Task[None], dict]] = {}
        self.lock = asyncio.Lock()

    async def owned(self, identifier: str, session_id: str, root: Path | None = None) -> dict:
        """Reject cross-session controls and model access after an idle workspace move."""
        record = await self.repository.process(identifier)
        data = record["data"]
        if data["session_id"] != session_id or (
            root is not None and Path(data["workspace_root"]) != root.resolve()
        ):
            raise AppError("not_found", "Process not found in this conversation workspace.", 404)
        return record

    async def start(
        self, sandbox: Any, args: dict, config: StudioConfig, owner: ToolExecution
    ) -> str:
        """Persist intent then start a bounded command in the same native OS sandbox."""
        async with self.lock:
            if sum(not task.done() for _, task, _ in self.live.values()) >= 4:
                raise AppError("process_limit", "Stop an active process before starting another.")
            identifier, control = uid(), CommandControl()
            data = {
                "session_id": owner.session_id,
                "command": args["command"],
                "cwd": args.get("cwd", "."),
                "mode": config.sandbox_mode,
                "workspace_root": str(sandbox.workspace.root),
                "timeout": min(args.get("timeout", config.run_timeout), config.run_timeout),
                "toolchain_roots": config.toolchain_roots,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "inputs": [],
            }
            await self.repository.save_process(identifier, owner.run_id, "starting", data)
            task = asyncio.create_task(
                self._drive(identifier, owner.run_id, sandbox, data, control)
            )
            self.live[identifier] = (control, task, data)
            task.add_done_callback(lambda completed: self.live.pop(identifier, None))
        # Return actual launch identity or setup failure; do not wait for the command to finish.
        ready = asyncio.create_task(control.ready.wait())
        try:
            await asyncio.wait(
                {ready, task},
                timeout=min(config.tool_timeout, 20),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if control.pid is not None and not task.done():
                data["pid"] = control.pid
                await self.repository.save_process(identifier, owner.run_id, "running", data)
        except asyncio.CancelledError:
            await self.stop(identifier, owner.session_id)
            raise
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        return process_view(await self.repository.process(identifier)).model_dump_json()

    async def _drive(
        self, identifier: str, run_id: str, sandbox: Any, data: dict, control: CommandControl
    ) -> None:
        """Capture bounded progress and wait for OS cleanup before persisting completion."""

        async def output(frame: dict) -> None:
            """Persist only output retained by the native drain, with its actual launch PID."""
            data[frame["stream"]] += frame["text"]
            data["truncated"] |= frame["truncated"]
            data["pid"], data["stdin_closed"] = control.pid, control.input_closed
            await self.repository.save_process(identifier, run_id, "running", data)

        status = "failed"
        try:
            result = json.loads(
                await sandbox.execute(
                    data["command"],
                    data["cwd"],
                    data["mode"],
                    data["timeout"],
                    control=control,
                    on_output=output,
                    toolchain_roots=data["toolchain_roots"],
                )
            )
            data.update(
                result=result,
                stdout=result["stdout"],
                stderr=result["stderr"],
                truncated=result["truncated"],
                pid=control.pid,
                stdin_closed=True,
            )
            status = (
                "stopped"
                if control.stop.is_set()
                else "timed_out" if result["timed_out"] else "exited"
            )
        except asyncio.CancelledError:
            data["error"] = "Service stopped; partial workspace effects require inspection."
            status = "interrupted"
        except Exception as exc:
            data["error"] = exc.message if isinstance(exc, AppError) else type(exc).__name__
        finally:
            data["ended_at"] = now()
            await self.repository.save_process(identifier, run_id, status, data)

    async def input(
        self,
        identifier: str,
        session_id: str,
        text: str,
        eof: bool = False,
        root: Path | None = None,
    ) -> ProcessView:
        """Send explicit pipe input; retain its hash/size instead of duplicating input text."""
        record = await self.owned(identifier, session_id, root)
        live = self.live.get(identifier)
        if not live or record["status"] not in ACTIVE:
            raise AppError("process_finished", "The process is no longer running.", 409)
        control, task, data = live
        if (
            record["data"]["mode"] == "workspace-write"
            and (await self.repository.config()).sandbox_mode == "read-only"
        ):
            raise AppError(
                "tool_denied", "Stop the earlier writable process before using read-only.", 403
            )
        # The command can finish during the settings read and remove itself from the live map.
        if task.done():
            raise AppError("process_finished", "The process is no longer running.", 409)
        try:
            control.feed(text, eof)
        except ValueError as exc:
            raise AppError("process_input", str(exc)) from exc
        data["stdin_closed"] = control.input_closed
        data["inputs"].append(
            {"hash": digest(text), "bytes": len(text.encode()), "eof": eof, "at": now()}
        )
        data["inputs"] = data["inputs"][-100:]
        await self.repository.save_process(identifier, record["run_id"], record["status"], data)
        return process_view(await self.repository.process(identifier))

    async def stop(self, identifier: str, session_id: str, root: Path | None = None) -> ProcessView:
        """Wait for the owned Job/process group to terminate before returning the receipt."""
        await self.owned(identifier, session_id, root)
        live = self.live.get(identifier)
        if live:
            control, task, _ = live
            control.stop.set()
            await asyncio.shield(task)
        return process_view(await self.repository.process(identifier))

    async def stop_run(self, run_id: str) -> None:
        """Stop background effects belonging to a cancelled or failed original Run."""
        for identifier in list(self.live):
            record = await self.repository.process(identifier)
            if record["run_id"] == run_id:
                await self.stop(identifier, record["data"]["session_id"])

    async def close(self) -> None:
        """Signal every live process and await sandbox cleanup before closing persistence."""
        pending = []
        for control, task, _ in list(self.live.values()):
            control.stop.set()
            pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
