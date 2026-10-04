"""Application use cases; keep HTTP and framework orchestration out of these methods."""

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rtagent.context import compaction_view, estimated_tokens, reduce_context
from rtagent.domain import (
    AppError,
    CompactionView,
    CompactRequest,
    ContextView,
    DraftPreviewRequest,
    MemoryDocument,
    MemorySave,
    Message,
    Preset,
    Preview,
    PreviewRequest,
    ProjectCreate,
    ProjectView,
    PromptInspection,
    ResumeRequest,
    RunView,
    SkillDocument,
    SkillSave,
    StartRun,
    StudioConfig,
    WorkspaceView,
    digest,
    latest_plan,
    uid,
)
from rtagent.prompts import compile_prompt
from rtagent.providers import prepare, transformations
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
        self.compaction_tasks: dict[str, asyncio.Task[None]] = {}

    async def close(self) -> None:
        """Stop explicit reduction jobs before their repository resources are closed."""
        tasks = list(self.compaction_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def context(self, session_id: str) -> ContextView:
        """Expose estimated history accounting and the recorded active summary boundary."""
        session = await self.repository.session(session_id)
        effective = await self.repository.effective_history(session)
        config = await self.repository.config()
        return ContextView(
            session_id=session_id,
            raw_messages=len(session["messages"]),
            effective_messages=len(effective),
            raw_estimated_tokens=estimated_tokens(session["messages"]),
            effective_estimated_tokens=estimated_tokens(effective),
            context_window=config.provider.context_window,
            reserved_output_tokens=config.provider.max_output_tokens,
            active_compaction_id=session.get("compaction_id"),
            compacting_id=session.get("compacting_id"),
            compactions=[
                compaction_view(item) for item in await self.repository.list_compactions(session_id)
            ],
        )

    async def compact(self, session_id: str, request: CompactRequest) -> CompactionView:
        """Start an explicit source-bound summary job while retaining every original message."""
        async with self.commands:
            session = await self.repository.session(session_id)
            config = await self.repository.config()
            if config.revision != request.config_revision:
                raise AppError(
                    "revision_conflict", "Settings changed. Reload before compacting.", 409
                )
            turns = [
                item
                for item in await self.repository.list_runs(session_id)
                if item["status"] == "succeeded"
            ]
            if len(turns) <= request.keep_recent_turns:
                raise AppError(
                    "compaction_scope",
                    "Keep fewer recent turns, or complete more turns before compacting.",
                )
            older = turns[: -request.keep_recent_turns]
            count = sum(1 + len(item["messages"]) for item in older)
            previous = (
                await self.repository.compaction(session["compaction_id"])
                if session.get("compaction_id")
                else None
            )
            previous_data = previous["data"] if previous else {}
            previous_count = previous_data.get("covered_messages", 0)
            if count <= previous_count:
                raise AppError("compaction_scope", "No additional older turns need reduction.")
            data = {
                "source_revision": session["revision"],
                "covered_messages": count,
                "source_hash": digest(session["messages"][:count]),
                "source_messages": session["messages"][previous_count:count],
                "previous_covered": previous_count,
                "previous_summary": previous_data.get("summary", ""),
                "previous_compaction_id": session.get("compaction_id"),
                "config": config.model_dump(),
                "calls": [],
                "summary": "",
                "through_run_id": older[-1]["id"],
            }
            record = await self.repository.begin_compaction(
                session_id, request.expected_revision, data
            )
            task = asyncio.create_task(reduce_context(self.repository, record))
            self.compaction_tasks[record["id"]] = task
            task.add_done_callback(lambda completed: self.compaction_tasks.pop(record["id"], None))
            return compaction_view(record)

    async def stop_compaction(self, session_id: str, identifier: str) -> CompactionView:
        """Stop a selected reduction without activating partial summary text."""
        record = await self.repository.compaction(identifier)
        if record["session_id"] != session_id:
            raise AppError("not_found", "Compaction does not belong to this conversation.", 404)
        task = self.compaction_tasks.get(identifier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return compaction_view(await self.repository.compaction(identifier))

    async def workspace_registry(self, project_id: str | None = None) -> ToolRegistry:
        """Resolve native files/tools from the selected project, never from model arguments."""
        root = (
            Path((await self.repository.project(project_id))["workspace_root"])
            if project_id
            else self.registry.workspace
        )
        return self.registry.for_workspace(root)

    async def create_project(self, request: ProjectCreate) -> ProjectView:
        """Register an existing absolute workspace folder without creating or copying files."""
        root = Path(request.workspace_root).expanduser()
        if not root.is_absolute():
            raise AppError(
                "workspace_path", "Use an absolute path to an existing workspace folder."
            )
        try:
            root = await asyncio.to_thread(root.resolve, strict=True)
            is_directory = await asyncio.to_thread(root.is_dir)
        except OSError as exc:
            raise AppError(
                "workspace_path", "The workspace folder does not exist or is inaccessible."
            ) from exc
        if not is_directory:
            raise AppError("workspace_path", "The workspace path must be a directory.")
        if root.is_relative_to(self.registry.files.data_dir):
            raise AppError("workspace_path", "Application runtime data cannot be a workspace.")
        return ProjectView.model_validate(
            await self.repository.create_project(request.name, str(root))
        )

    async def workspace(
        self, refresh: bool = False, project_id: str | None = None
    ) -> WorkspaceView:
        """Read editable workspace context and probe the configured sandbox."""
        registry = await self.workspace_registry(project_id)
        skills, errors = await asyncio.to_thread(registry.files.skills)
        return WorkspaceView(
            root=str(registry.workspace),
            sandbox=await registry.sandbox.status(refresh),
            instructions=await asyncio.to_thread(registry.files.instructions),
            memory=await asyncio.to_thread(registry.files.memory),
            skills=[
                {key: value for key, value in skill.items() if key != "content"} for skill in skills
            ],
            skill_errors=errors,
        )

    async def save_memory(
        self, request: MemorySave, project_id: str | None = None
    ) -> MemoryDocument:
        """Persist a user-authored note without silently overwriting a concurrent edit."""
        registry = await self.workspace_registry(project_id)
        await asyncio.to_thread(registry.files.write, MEMORY_PATH, **request.model_dump())
        return MemoryDocument.model_validate(await asyncio.to_thread(registry.files.memory))

    async def skill(self, name: str, project_id: str | None = None) -> SkillDocument:
        """Load the current source for the workspace editor."""
        registry = await self.workspace_registry(project_id)
        return SkillDocument.model_validate(
            await asyncio.to_thread(registry.files.load_skill, name)
        )

    async def save_skill(self, request: SkillSave, project_id: str | None = None) -> SkillDocument:
        """Persist a skill through the same validation used by the native tool."""
        registry = await self.workspace_registry(project_id)
        await asyncio.to_thread(registry.files.save_skill, **request.model_dump())
        return await self.skill(request.name, project_id)

    async def _prepare(
        self,
        preset: Preset,
        config: StudioConfig,
        history: list[dict[str, Any]],
        user_input: str,
        variables: dict[str, Any],
        skills: list[str],
        registry: ToolRegistry,
    ) -> tuple[PromptInspection, list[Message]]:
        """Share compilation and file capture between live inspection and immutable execution."""
        tools = registry.selected(config)
        encoded_tools = [tool.model_dump() for tool in tools]
        # Shell variables also start with $. Only catalogued names are implicit skill mentions;
        # explicit UI selections still fail clearly when a skill is unavailable.
        catalog, _ = await asyncio.to_thread(registry.files.skills)
        known_skills = {skill["name"] for skill in catalog}
        mentions = re.findall(r"(?<!\S)\$([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$|[.,!?])", user_input)
        selected_skills = skills + [name for name in mentions if name in known_skills]
        workspace_context = await asyncio.to_thread(registry.files.context, selected_skills)
        workspace_context.insert(
            0,
            Message(
                role="system",
                source="runtime:environment",
                content=(
                    f"Workspace: {registry.workspace}\n"
                    f"Native command language: {'Windows PowerShell' if os.name == 'nt' else 'Bash on Linux'}.\n"
                    f"Workspace policy: {config.sandbox_mode}; native command network is disabled. "
                    "Command availability is not pre-probed; failures are reported without a host fallback. "
                    "Host credentials, Git metadata, runtime data, and dependencies are excluded. "
                    "MCP tools use their explicitly configured external connections.\n"
                    f"Exposed tools: {', '.join(tool.name for tool in tools) or '(none)'}.\n"
                    f"Trusted runtime directories: {config.toolchain_roots or '(none)'}. "
                    "Copies are read-only; use RTAGENT_TOOLCHAIN_1 etc. for their sandbox paths. "
                    "Python workspace packages: .rtagent/packages (PYTHONPATH); pip is offline.\n"
                    f"HTTPS download hosts: {', '.join(config.network_hosts) or '(none)'}. "
                    "fetch_url downloads public resources separately; commands still have no network.\n"
                    "Background commands use start_process/poll_process/send_process_input/stop_process. "
                    "Use pipe stdin, not terminal interactions. Windows copies back after exit; "
                    "background commands see their initial private workspace copy.\n"
                    f"Run limits: {config.max_steps} model steps, {config.max_tool_calls} tool calls, "
                    f"{config.tool_timeout:g}s per tool, {config.run_timeout:g}s active runtime."
                ),
            ),
        )
        compiled = compile_prompt(
            preset,
            config,
            [Message.model_validate(item) for item in history],
            user_input,
            variables,
            encoded_tools,
            workspace_context=workspace_context,
        )
        prepared = prepare(config.provider, compiled, encoded_tools)
        return (
            PromptInspection(
                input_hash=compiled.input_hash,
                compiled=compiled,
                prepared_request=prepared,
                tools=tools,
                transformations=transformations(config.provider.adapter),
            ),
            workspace_context,
        )

    async def inspect(self, request: DraftPreviewRequest) -> PromptInspection:
        """Compile current editor values without saving presets, sessions, or preview rows."""
        config = await self.repository.config()
        if config.revision != request.config_revision:
            raise AppError("revision_conflict", "Reload the latest settings.", 409)
        history = []
        project_id = request.project_id
        if request.session_id:
            session = await self.repository.session(request.session_id)
            if session["revision"] != request.expected_session_revision:
                raise AppError("revision_conflict", "Reload the latest conversation.", 409)
            history = await self.repository.effective_history(session)
            project_id = session["project_id"]
        inspection, _ = await self._prepare(
            request.preset,
            config,
            history,
            request.input,
            request.variables,
            request.skills,
            await self.workspace_registry(project_id),
        )
        return inspection

    async def preview(self, request: PreviewRequest) -> Preview:
        """Validate revisions and persist the immutable input that execution will consume."""
        session = await self.repository.session(request.session_id)
        preset = await self.repository.preset(request.preset_id)
        config = await self.repository.config()
        if session["active_run_id"] or session.get("compacting_id"):
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
        registry = await self.workspace_registry(session["project_id"])
        history = await self.repository.effective_history(session)
        inspection, workspace_context = await self._prepare(
            preset,
            config,
            history,
            request.input,
            request.variables,
            request.skills,
            registry,
        )
        preview = Preview(
            **inspection.model_dump(),
            id=uid(),
            expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        )
        snapshot = {
            "schema_version": 1,
            "preset": preset.model_dump(),
            "config": config.model_dump(),
            "history": history,
            "context_compaction_id": session.get("compaction_id"),
            "input": request.input,
            "variables": request.variables,
            "tools": [tool.model_dump() for tool in inspection.tools],
            "session_id": session["id"],
            "session_revision": session["revision"],
            "workspace_root": str(registry.workspace),
            "project_id": session["project_id"],
            "workspace_context": [message.model_dump() for message in workspace_context],
            "prepared_request": inspection.prepared_request,
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
            workspace_root=run["snapshot"]["workspace_root"],
            plan=latest_plan([Message.model_validate(item) for item in run["messages"]]),
            preset_name=run["snapshot"]["preset"]["name"],
            provider=run["snapshot"]["config"]["provider"]["adapter"],
            unknown_calls=[
                {"id": call["id"], "kind": call["kind"], "data": redact(call["data"])}
                for call in await self.repository.trace(run_id)
                if call["status"] == "outcome_unknown"
            ],
        )
        active = next(
            (
                call
                for call in reversed(await self.repository.trace(run_id))
                if call["kind"] == "tool" and call["status"] == "executing"
            ),
            None,
        )
        values["active_tool"] = (
            {"id": active["id"], "name": active["data"]["name"], **active["data"].get("output", {})}
            if active
            else None
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
        from rtagent.toolchains import validate_toolchains

        await asyncio.to_thread(validate_toolchains, value.toolchain_roots, self.registry.workspace)
        saved = await self.repository.save_config(value)
        if saved.mcp_servers != previous.mcp_servers:
            self.registry.reset()
        return saved

    async def delete_session(self, session_id: str) -> None:
        """Remove checkpoints and owned data only after the session becomes idle."""
        async with self.commands:
            session = await self.repository.session(session_id)
            if (
                session["active_run_id"]
                or session["compacting_id"]
                or any(
                    item["status"] in {"starting", "running", "stopping"}
                    for item in await self.repository.list_processes(session_id)
                )
            ):
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
