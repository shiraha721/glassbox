"""Application use cases; keep HTTP and framework orchestration out of these methods."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

from rtagent.domain import (
    AppError,
    MemoryDocument,
    MemorySave,
    Message,
    Preview,
    PreviewRequest,
    ResumeRequest,
    RunView,
    SkillDocument,
    SkillSave,
    StartRun,
    StudioConfig,
    WorkspaceView,
    uid,
)
from rtagent.prompts import compile_prompt
from rtagent.providers import prepare
from rtagent.runtime import Runner
from rtagent.storage import Repository
from rtagent.tools import ToolRegistry
from rtagent.tracing import redact
from rtagent.workspace import MEMORY_PATH


class Studio:
    """Coordinate versioned previews, Run commands, and explicit recovery decisions."""

    def __init__(self, repository: Repository, registry: ToolRegistry, runner: Runner) -> None:
        """Share the process-owned repository, registry, and runner between use cases."""
        self.repository, self.registry, self.runner = repository, registry, runner
        self.commands = asyncio.Lock()

    async def workspace(self, refresh: bool = False) -> WorkspaceView:
        """Read editable workspace context and probe the configured sandbox."""
        skills, errors = await asyncio.to_thread(self.registry.files.skills)
        return WorkspaceView(
            root=str(self.registry.workspace),
            sandbox=await self.registry.sandbox.status(refresh),
            memory=await asyncio.to_thread(self.registry.files.memory),
            skills=[
                {key: value for key, value in skill.items() if key != "content"} for skill in skills
            ],
            skill_errors=errors,
        )

    async def save_memory(self, request: MemorySave) -> MemoryDocument:
        """Persist a user-authored note without silently overwriting a concurrent edit."""
        await asyncio.to_thread(self.registry.files.write, MEMORY_PATH, **request.model_dump())
        return MemoryDocument.model_validate(await asyncio.to_thread(self.registry.files.memory))

    async def skill(self, name: str) -> SkillDocument:
        """Load the current source for the workspace editor."""
        return SkillDocument.model_validate(
            await asyncio.to_thread(self.registry.files.load_skill, name)
        )

    async def save_skill(self, request: SkillSave) -> SkillDocument:
        """Persist a skill through the same validation used by the native tool."""
        await asyncio.to_thread(self.registry.files.save_skill, **request.model_dump())
        return await self.skill(request.name)

    async def preview(self, request: PreviewRequest) -> Preview:
        """Validate revisions and persist the immutable input that execution will consume."""
        session = await self.repository.session(request.session_id)
        preset = await self.repository.preset(request.preset_id)
        config = await self.repository.config()
        if session["active_run_id"]:
            raise AppError("session_busy", "Finish or cancel the current Run first.", 409)
        if session["archived"]:
            raise AppError(
                "session_archived", "Restore this conversation before running a task.", 409
            )
        if (
            session["revision"] != request.expected_session_revision
            or preset.revision != request.preset_revision
            or config.revision != request.config_revision
        ):
            raise AppError(
                "revision_conflict", "Reload the latest preset, settings, and session.", 409
            )
        tools = self.registry.selected(config)
        encoded_tools = [tool.model_dump() for tool in tools]
        # Shell variables also start with $. Only catalogued names are implicit skill mentions;
        # explicit UI selections still fail clearly when a skill is unavailable.
        catalog, _ = await asyncio.to_thread(self.registry.files.skills)
        known_skills = {skill["name"] for skill in catalog}
        mentions = re.findall(r"(?<!\S)\$([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$|[.,!?])", request.input)
        selected_skills = request.skills + [name for name in mentions if name in known_skills]
        workspace_context = await asyncio.to_thread(self.registry.files.context, selected_skills)
        compiled = compile_prompt(
            preset,
            config,
            [Message.model_validate(item) for item in session["messages"]],
            request.input,
            request.variables,
            encoded_tools,
            workspace_context=workspace_context,
        )
        prepared = prepare(config.provider, compiled, encoded_tools)
        preview = Preview(
            id=uid(),
            input_hash=compiled.input_hash,
            compiled=compiled,
            prepared_request=prepared,
            tools=tools,
            expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        )
        snapshot = {
            "schema_version": 1,
            "preset": preset.model_dump(),
            "config": config.model_dump(),
            "history": session["messages"],
            "input": request.input,
            "variables": request.variables,
            "tools": encoded_tools,
            "session_id": session["id"],
            "session_revision": session["revision"],
            "workspace_root": str(self.registry.workspace),
            "workspace_context": [message.model_dump() for message in workspace_context],
            "prepared_request": prepared,
        }
        await self.repository.put_preview(
            preview.id, {**preview.model_dump(), "snapshot": snapshot}
        )
        return preview

    async def start(self, request: StartRun | PreviewRequest, key: str) -> RunView:
        """Compile direct input or consume an optional preview, preserving retry identity."""
        if not key or len(key) > 200:
            raise AppError(
                "idempotency_key", "Provide an Idempotency-Key of at most 200 characters."
            )
        async with self.commands:
            submission = request.model_dump()
            run = await self.repository.submitted_run(key, submission)
            if run is None:
                if isinstance(request, PreviewRequest):
                    prepared = await self.preview(request)
                    request = StartRun(
                        preview_id=prepared.id,
                        input_hash=prepared.input_hash,
                        expected_session_revision=request.expected_session_revision,
                    )
                run = await self.repository.create_run(
                    request.preview_id, request.model_dump(), key, submission
                )
            if run["status"] == "queued":
                self.runner.start(run["id"])
            return await self.view(run["id"])

    async def view(self, run_id: str) -> RunView:
        """Project persisted state without exposing editable configuration or secret values."""
        run = await self.repository.run(run_id)
        values = {
            key: run[key]
            for key in (
                "id",
                "session_id",
                "status",
                "stop_reason",
                "messages",
                "partial_output",
                "latest_event_seq",
                "approval",
                "error",
                "created_at",
                "updated_at",
                "usage",
                "source_run_id",
            )
        }
        values.update(
            input=run["snapshot"]["input"],
            preset_name=run["snapshot"]["preset"]["name"],
            provider=run["snapshot"]["config"]["provider"]["adapter"],
            unknown_calls=[
                {"id": call["id"], "kind": call["kind"], "data": redact(call["data"])}
                for call in await self.repository.trace(run_id)
                if call["status"] == "outcome_unknown"
            ],
        )
        return RunView.model_validate(values)

    async def approve(self, run_id: str, approval_id: str, decision: str) -> RunView:
        """Record one idempotent decision before resuming the interrupted checkpoint."""
        async with self.commands:
            run = await self.repository.run(run_id)
            approval = run["approval"]
            if not approval or approval["id"] != approval_id:
                raise AppError("approval_conflict", "This approval is no longer pending.", 409)
            if approval["decision"]:
                if approval["decision"] != decision:
                    raise AppError(
                        "approval_conflict", "A different decision has already been recorded.", 409
                    )
                return await self.view(run_id)
            if run["status"] != "waiting_approval":
                raise AppError("approval_conflict", "The Run is not awaiting this approval.", 409)
            approval = {**approval, "decision": decision}
            await self.repository.emit(
                run_id, "approval.resolved", approval, approval=approval, status="queued"
            )
            await self._resume_after_checkpoint(run_id)
            return await self.view(run_id)

    async def _resume_after_checkpoint(self, run_id: str) -> None:
        """Wait for checkpoint persistence before starting the next graph invocation."""
        task = self.runner.tasks.get(run_id)
        if task and not task.done():
            await asyncio.shield(task)
        self.runner.start(run_id, resume=True)

    async def resume(self, run_id: str, request: ResumeRequest) -> RunView:
        """Resolve every uncertain call explicitly before scheduling checkpoint recovery."""
        async with self.commands:
            run = await self.repository.run(run_id)
            if run["status"] != "interrupted":
                raise AppError("state_conflict", "Only interrupted Runs can be resumed.", 409)
            uncertain = [
                call
                for call in await self.repository.trace(run_id)
                if call["status"] == "outcome_unknown"
            ]
            if set(request.resolutions) != {call["id"] for call in uncertain}:
                raise AppError(
                    "recovery_required",
                    "Resolve every uncertain call explicitly before resuming.",
                    409,
                )
            for call in uncertain:
                decision = request.resolutions[call["id"]]
                if decision.action == "record" and call["kind"] != "tool":
                    raise AppError(
                        "recovery_invalid", "Model calls can only be explicitly retried."
                    )
            for call in uncertain:
                decision = request.resolutions[call["id"]]
                status = "pending" if decision.action == "retry" else "succeeded"
                data = {**call["data"], "recovery_decision": decision.action}
                # Retrying must not overwrite evidence from the uncertain attempt.
                data["attempt_history"] = call["data"].get("attempt_history", []) + [
                    {
                        "status": call["status"],
                        "result": call["result"],
                        "resolution": decision.action,
                        "data": {
                            key: value
                            for key, value in call["data"].items()
                            if key != "attempt_history"
                        },
                    }
                ]
                await self.repository.save_call(
                    call["id"],
                    run_id,
                    call["kind"],
                    status,
                    data,
                    decision.result if decision.action == "record" else None,
                )
            await self.repository.emit(run_id, "run.queued", {"resumed": True}, status="queued")
            await self._resume_after_checkpoint(run_id)
            return await self.view(run_id)

    async def cancel(self, run_id: str) -> RunView:
        """Serialize cancellation with approval and resume commands."""
        async with self.commands:
            await self.runner.cancel(run_id)
            return await self.view(run_id)

    async def save_config(self, value: StudioConfig) -> StudioConfig:
        """Apply optimistic version checks and invalidate stale MCP discovery."""
        previous = await self.repository.config()
        saved = await self.repository.save_config(value)
        if saved.mcp_servers != previous.mcp_servers:
            self.registry.reset()
        return saved

    async def delete_session(self, session_id: str) -> None:
        """Remove checkpoints and owned data only after the session becomes idle."""
        async with self.commands:
            session = await self.repository.session(session_id)
            if session["active_run_id"]:
                raise AppError(
                    "session_busy", "Cancel the active Run before deleting this session.", 409
                )
            run_ids = [run["id"] for run in await self.repository.list_runs(session_id)]
            # Checkpoint cleanup happens before removing its application references.
            for run_id in run_ids:
                task = self.runner.tasks.get(run_id)
                if task:
                    await asyncio.shield(task)
                await self.runner.checkpointer.adelete_thread(run_id)
            await self.repository.delete_session(session_id)
