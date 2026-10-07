"""Application use cases; keep HTTP and framework orchestration out of these methods."""

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rtagent.application.context import (
    compaction_view,
    context_usage,
    estimated_tokens,
    reduce_context,
)
from rtagent.application.evaluation import batch_view, eval_record, eval_summary, graded_item
from rtagent.application.runner import Runner
from rtagent.domain import (
    AppError,
    CompactionView,
    CompactRequest,
    ContextView,
    CredentialStatus,
    DraftPreviewRequest,
    Eval,
    EvalBatchView,
    EvalCheck,
    EvalDraft,
    EvalItemResult,
    EvalSummary,
    EvalUpdate,
    MemoryDocument,
    MemorySave,
    Message,
    ModelChoice,
    Preset,
    PresetPackage,
    Preview,
    PreviewRequest,
    ProjectCreate,
    ProjectView,
    PromptInspection,
    ProviderProfile,
    ResumeRequest,
    RunView,
    SkillDocument,
    SkillPackage,
    SkillSave,
    StartRun,
    StudioConfig,
    ToolCall,
    ToolDefinition,
    WorkspaceAccess,
    WorkspaceSkills,
    WorkspaceView,
    digest,
    latest_plan,
    resolve_model,
    uid,
)
from rtagent.domain.lifecycle import ACTIVE_PROCESSES, TERMINAL
from rtagent.domain.policies import grant_for
from rtagent.domain.prompts import compile_prompt
from rtagent.infrastructure.packages import export_skill, import_preset, import_skill
from rtagent.infrastructure.providers.catalog import list_models
from rtagent.infrastructure.providers.chat import prepare, transformations
from rtagent.infrastructure.storage import Repository
from rtagent.infrastructure.tools import ToolRegistry
from rtagent.infrastructure.tracing import redact
from rtagent.infrastructure.workspace.files import MEMORY_PATH
from rtagent.infrastructure.workspace.toolchains import validate_toolchains


def environment_message(root: Path, config: StudioConfig, tools: list[ToolDefinition]) -> Message:
    """Describe the actual execution boundary the model works in, recorded with each Run."""
    if os.name == "nt":
        shell = "Windows PowerShell in an AppContainer, on a private copy of the workspace"
        hidden = "credentials, Git metadata, runtime data and dependency folders are not copied"
        changes = "Workspace edits are copied back after each command or background process exits"
        runtimes = "Trusted runtimes are read-only copies; RTAGENT_TOOLCHAIN_1 etc. name them"
    else:
        shell = "Bash in a bubblewrap sandbox with the workspace mounted at /workspace"
        hidden = "credentials, Git metadata and runtime data are masked; dependency folders remain"
        changes = "Workspace edits apply directly"
        runtimes = (
            "Trusted runtimes are mounted read-only at their host paths and at "
            "RTAGENT_TOOLCHAIN_1 etc."
        )
    lines = [
        f"Workspace: {root}",
        f"Native commands: {shell}; network is disabled; {hidden}. {changes}.",
        f"Workspace policy: {config.sandbox_mode}. Command availability is not pre-probed; "
        "failures are reported without a host fallback. MCP tools use their explicitly "
        "configured external connections.",
        f"Exposed tools: {', '.join(tool.name for tool in tools) or '(none)'}.",
        f"Trusted runtime directories: {', '.join(config.toolchain_roots) or '(none)'}. "
        f"{runtimes}. Python workspace packages: .rtagent/packages (PYTHONPATH); pip is offline.",
        f"HTTPS download hosts: {', '.join(config.network_hosts) or '(none)'}. fetch_url "
        "downloads public resources separately; commands still have no network.",
        "Background commands use start_process/poll_process/send_process_input/stop_process "
        "with pipe stdin, not a terminal.",
        f"Run limits: {config.max_steps} model steps, {config.max_tool_calls} tool calls, "
        f"{config.tool_timeout:g}s per tool, {config.run_timeout:g}s active runtime.",
    ]
    return Message(role="system", source="runtime:environment", content="\n".join(lines))


class Studio:
    """Coordinate versioned previews, Run commands, and explicit recovery decisions."""

    def __init__(self, repository: Repository, registry: ToolRegistry, runner: Runner) -> None:
        """Share the process-owned repository, registry, and runner between use cases."""
        self.repository, self.registry, self.runner = repository, registry, runner
        self.commands = asyncio.Lock()
        self.compaction_tasks: dict[str, asyncio.Task[None]] = {}
        self.eval_tasks: dict[str, asyncio.Task[None]] = {}

    async def close(self) -> None:
        """Stop explicit reduction and evaluation jobs before repository resources are closed."""
        tasks = list(self.compaction_tasks.values()) + list(self.eval_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def context(self, session_id: str) -> ContextView:
        """Expose estimated history accounting and the recorded active summary boundary."""
        session = await self.repository.session(session_id)
        effective = await self.repository.effective_history(session)
        config = await self.repository.config()
        usage = await self.repository.latest_model_usage(session_id)
        costs = [
            run["usage"].get("cost")
            for run in await self.repository.list_runs(session_id)
            if run["status"] in TERMINAL
        ]
        return ContextView(
            session_id=session_id,
            raw_messages=len(session["messages"]),
            effective_messages=len(effective),
            raw_estimated_tokens=estimated_tokens(session["messages"]),
            effective_estimated_tokens=estimated_tokens(effective),
            context_window=config.provider.context_window,
            reserved_output_tokens=config.provider.max_output_tokens,
            live_usage=context_usage(usage, config.provider.model),
            spent=round(sum(cost for cost in costs if cost is not None), 6),
            unpriced_runs=sum(1 for cost in costs if cost is None),
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
                for item in await self.repository.run_projections(session_id=session_id)
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
            config = config.model_copy(update={"provider": resolve_model(config, request.model)})
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
            task = asyncio.create_task(
                reduce_context(self.repository, record, self.registry.credentials)
            )
            self.compaction_tasks[record["id"]] = task
            task.add_done_callback(lambda _completed: self.compaction_tasks.pop(record["id"], None))
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

    # --- Prompt evaluation ---------------------------------------------------------------
    # An evaluation runs each case under each variant prompt as an ordinary Run in a hidden
    # eval session, then grades the final text, so prompts can be compared on the same tasks.

    async def list_evals(self) -> list[EvalSummary]:
        """List saved evaluations with the status of their most recent comparison."""
        return [eval_summary(row) for row in await self.repository.list_evals()]

    async def get_eval(self, eval_id: str) -> Eval:
        """Return one evaluation definition for editing."""
        return eval_record(await self.repository.eval(eval_id))

    async def create_eval(self, draft: EvalDraft) -> Eval:
        """Store a new evaluation definition."""
        row = await self.repository.create_eval(
            draft.name,
            [case.model_dump() for case in draft.cases],
            [variant.model_dump() for variant in draft.variants],
        )
        return eval_record(row)

    async def update_eval(self, eval_id: str, draft: EvalUpdate) -> Eval:
        """Replace an evaluation under its observed revision."""
        row = await self.repository.update_eval(
            eval_id,
            draft.expected_revision,
            draft.name,
            [case.model_dump() for case in draft.cases],
            [variant.model_dump() for variant in draft.variants],
        )
        return eval_record(row)

    async def delete_eval(self, eval_id: str) -> None:
        """Delete an evaluation, its batches and the hidden Run sessions they created."""
        await self.repository.eval(eval_id)
        if eval_id in self.eval_tasks and not self.eval_tasks[eval_id].done():
            raise AppError("eval_busy", "Stop the running comparison before deleting.", 409)
        session_ids = {
            item.get("run_session_id")
            for batch in await self.repository.list_batches(eval_id)
            for item in batch["data"]["items"]
            if item.get("run_session_id")
        }
        for session_id in session_ids:
            try:
                await self.delete_session(session_id)
            except AppError:
                continue
        await self.repository.delete_eval(eval_id)

    async def start_batch(self, eval_id: str, project_id: str | None) -> EvalBatchView:
        """Freeze the definition and start a background comparison over every case and variant."""
        if eval_id in self.eval_tasks and not self.eval_tasks[eval_id].done():
            raise AppError("eval_busy", "A comparison for this evaluation is already running.", 409)
        record = eval_record(await self.repository.eval(eval_id))
        if project_id:
            await self.repository.project(project_id)
        items = [
            {"case_id": case.id, "variant_id": variant.id, "status": "pending"}
            for variant in record.variants
            for case in record.cases
        ]
        data = {
            "eval_name": record.name,
            "eval_revision": record.revision,
            "project_id": project_id,
            "cases": [{"id": case.id, "name": case.name} for case in record.cases],
            "variants": [{"id": variant.id, "name": variant.name} for variant in record.variants],
            "definition": {
                "cases": [case.model_dump() for case in record.cases],
                "variants": [variant.model_dump() for variant in record.variants],
            },
            "items": items,
            "error": None,
        }
        batch = await self.repository.create_batch(eval_id, data)
        task = asyncio.create_task(self._execute_batch(batch["id"], eval_id))
        self.eval_tasks[eval_id] = task
        return batch_view(await self.repository.batch(batch["id"]))

    async def batch(self, batch_id: str) -> EvalBatchView:
        """Return one comparison batch's progress and results."""
        return batch_view(await self.repository.batch(batch_id))

    async def list_batches(self, eval_id: str) -> list[EvalBatchView]:
        """Return an evaluation's comparison batches, newest first."""
        await self.repository.eval(eval_id)
        return [batch_view(row) for row in await self.repository.list_batches(eval_id)]

    async def cancel_batch(self, batch_id: str) -> EvalBatchView:
        """Stop a running comparison; completed items and their evidence are kept."""
        record = await self.repository.batch(batch_id)
        task = self.eval_tasks.get(record["eval_id"])
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return batch_view(await self.repository.batch(batch_id))

    async def _execute_batch(self, batch_id: str, eval_id: str) -> None:
        """Run each case under each variant sequentially, grading and saving after each item."""
        record = await self.repository.batch(batch_id)
        data = record["data"]
        definition = data["definition"]
        cases = {case["id"]: case for case in definition["cases"]}
        variants = {variant["id"]: variant for variant in definition["variants"]}
        results = {(item["case_id"], item["variant_id"]): dict(item) for item in data["items"]}
        try:
            for item in data["items"]:
                key = (item["case_id"], item["variant_id"])
                case, variant = cases[item["case_id"]], variants[item["variant_id"]]
                results[key] = (
                    await self._run_item(case, variant, data["project_id"])
                ).model_dump()
                data["items"] = list(results.values())
                await self.repository.save_batch(batch_id, "running", data)
            await self.repository.save_batch(batch_id, "succeeded", data)
        except asyncio.CancelledError:
            data["items"] = list(results.values())
            await self.repository.save_batch(batch_id, "cancelled", data)
            raise
        except Exception as error:  # noqa: BLE001 - record the failure instead of losing the batch
            data["error"] = str(error)
            data["items"] = list(results.values())
            await self.repository.save_batch(batch_id, "failed", data)

    async def _run_item(
        self, case: dict[str, Any], variant: dict[str, Any], project_id: str | None
    ) -> EvalItemResult:
        """Run one case under one variant in a fresh hidden session and grade the result.

        A submission error (a missing variable, an unconfigured model, settings saved meanwhile)
        fails this item with its reason; the remaining items still run."""
        check = EvalCheck.model_validate(case["check"])
        # Read settings per item: a batch can outlast a settings edit.
        config = await self.repository.config()
        try:
            preset = await self.repository.preset(variant["preset_id"])
        except AppError:
            return EvalItemResult(
                case_id=case["id"],
                variant_id=variant["id"],
                status="failed",
                passed=False,
                detail="The variant's preset no longer exists.",
            )
        session = await self.repository.new_session(
            title=f"{variant['name']} · {case['name']}", project_id=project_id, kind="eval"
        )
        request = PreviewRequest(
            session_id=session["id"],
            expected_session_revision=0,
            preset_id=preset.id,
            preset_revision=preset.revision,
            config_revision=config.revision,
            input=case["input"],
            variables=case.get("variables", {}),
            skills=[],
            model=ModelChoice.model_validate(variant["model"]) if variant.get("model") else None,
            access=variant.get("access"),
        )
        try:
            view = await self.start(request, key=uid())
        except AppError as error:
            return EvalItemResult(
                case_id=case["id"],
                variant_id=variant["id"],
                run_session_id=session["id"],
                status="failed",
                passed=False,
                detail=f"The Run could not start: {error.message}",
            )
        run = await self._await_run(view.id, config.run_timeout)
        item = graded_item(case["id"], variant["id"], view.id, run, check)
        return item.model_copy(update={"run_session_id": session["id"]})

    async def _await_run(self, run_id: str, run_timeout: float) -> dict[str, Any]:
        """Poll until the Run reaches a terminal state, cancelling if it pauses or overruns.

        Eval Runs are unattended: a Run that waits for approval or exceeds its time budget is
        cancelled so the batch can continue, and is graded as not passed."""

        deadline = asyncio.get_event_loop().time() + run_timeout + 30
        while True:
            run = await self.repository.run(run_id)
            if run["status"] in TERMINAL:
                return run
            if run["status"] == "waiting_approval" or asyncio.get_event_loop().time() > deadline:
                await self.cancel(run_id)
                return await self.repository.run(run_id)
            await asyncio.sleep(0.1)

    async def workspace_registry(self, project_id: str | None = None) -> ToolRegistry:
        """Resolve native files/tools from the selected project, never from model arguments."""
        # Project registries share the root catalog when they are resolved, so discover first.
        await self.registry.ensure_discovered(await self.repository.config())
        root = (
            Path((await self.repository.project(project_id))["workspace_root"])
            if project_id
            else self.registry.workspace
        )
        return self.registry.for_workspace(root)

    @staticmethod
    def _effective_config(
        config: StudioConfig,
        registry: ToolRegistry,
        model: ModelChoice | None = None,
        access: WorkspaceAccess | None = None,
    ) -> StudioConfig:
        """Apply a Run's model and workspace access choices to the saved settings.

        Read only removes file-changing tools; ask turns every allowed tool that changes files,
        runs code or calls MCP into an approval; workspace write keeps the configured policy."""
        update: dict[str, Any] = {"provider": resolve_model(config, model)}
        if access is not None:
            update["sandbox_mode"] = "read-only" if access == "read-only" else "workspace-write"
        if access == "ask":
            update["tools"] = {
                name: (
                    "ask"
                    if permission == "allow" and registry.changes_workspace(name)
                    else permission
                )
                for name, permission in config.tools.items()
            }
            # Commands change files, so under "ask" a command rule can tighten but not allow.
            update["command_rules"] = [
                (
                    rule.model_copy(update={"permission": "ask"})
                    if rule.permission == "allow"
                    else rule
                )
                for rule in config.command_rules
            ]
        return config.model_copy(update=update)

    async def create_project(self, request: ProjectCreate) -> ProjectView:
        """Register an absolute workspace folder without copying files.

        A missing folder is created only when the request asks for it, so a mistyped path is
        reported instead of silently becoming an empty workspace."""
        root = Path(request.workspace_root).expanduser()
        if not root.is_absolute():
            raise AppError("workspace_path", "Use an absolute path to a workspace folder.")
        data_dir = self.registry.files.data_dir
        if request.create:
            # Check the target before creating anything; links resolve to their destination.
            target = await asyncio.to_thread(root.resolve)
            if target.is_relative_to(data_dir):
                raise AppError("workspace_path", "Application runtime data cannot be a workspace.")
            try:
                await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
            except FileExistsError as exc:
                raise AppError("workspace_path", "The workspace path must be a directory.") from exc
            except OSError as exc:
                raise AppError(
                    "workspace_path",
                    f"The folder could not be created: {exc.strerror or type(exc).__name__}.",
                ) from exc
        try:
            root = await asyncio.to_thread(root.resolve, strict=True)
            is_directory = await asyncio.to_thread(root.is_dir)
        except FileNotFoundError as exc:
            raise AppError(
                "workspace_missing",
                f"The folder {root} does not exist. Choose another folder, or create it.",
            ) from exc
        except OSError as exc:
            raise AppError("workspace_path", "The workspace folder is inaccessible.") from exc
        if not is_directory:
            raise AppError("workspace_path", "The workspace path must be a directory.")
        if root.is_relative_to(data_dir):
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

    async def global_skills(self) -> WorkspaceSkills:
        """List bundled and global skills, which every project shares."""
        skills, errors = await asyncio.to_thread(self.registry.files.skills, False)
        return WorkspaceSkills(
            skills=[
                {key: value for key, value in skill.items() if key != "content"} for skill in skills
            ],
            errors=errors,
        )

    async def global_skill(self, name: str) -> SkillDocument:
        """Load a bundled or global skill without reference to any project."""
        skills, errors = await asyncio.to_thread(self.registry.files.skills, False)
        found = next((skill for skill in skills if skill["name"] == name), None)
        if found is None:
            raise AppError("skill_unavailable", errors.get(name, f"Skill {name} was not found."))
        return SkillDocument.model_validate(found)

    async def save_global_skill(self, request: SkillSave) -> SkillDocument:
        """Create or update a global skill with the same validation as project skills."""
        await asyncio.to_thread(
            self.registry.files.save_skill, **request.model_dump(), scope="global"
        )
        return await self.global_skill(request.name)

    async def delete_global_skill(self, name: str, expected_hash: str) -> None:
        """Delete a global skill the user has seen; bundled skills cannot be deleted."""
        await asyncio.to_thread(self.registry.files.delete_global_skill, name, expected_hash)

    async def list_models(self, profile: ProviderProfile) -> list[str]:
        """Ask a provider which models it serves, using its server-side credential."""
        key = await asyncio.to_thread(self.registry.credentials.resolve, profile.credential_ref)
        return await list_models(profile, key)

    async def credential_status(self, names: list[str]) -> list[CredentialStatus]:
        """Report where each named key comes from, never its value."""
        return [
            await asyncio.to_thread(self.registry.credentials.status, name)
            for name in dict.fromkeys(names)
        ]

    async def save_credential(self, name: str, value: str) -> CredentialStatus:
        """Store a key entered in Settings under its reference name."""
        await asyncio.to_thread(self.registry.credentials.save, name, value)
        return await asyncio.to_thread(self.registry.credentials.status, name)

    async def delete_credential(self, name: str) -> CredentialStatus:
        """Forget a stored key; an environment variable of the same name still applies."""
        await asyncio.to_thread(self.registry.credentials.delete, name)
        return await asyncio.to_thread(self.registry.credentials.status, name)

    async def export_preset(self, identifier: str) -> PresetPackage:
        """Package saved prompt data without settings, credentials or execution evidence."""
        return PresetPackage(preset=await self.repository.preset(identifier))

    async def import_preset(self, package: PresetPackage) -> Preset:
        """Create independent prompt and block identities through the ordinary preset writer."""
        return await self.repository.save_preset(import_preset(package))

    async def export_skill(self, name: str, project_id: str | None = None) -> SkillPackage:
        """Read a bounded Skill package from the selected workspace, off the event loop."""
        registry = await self.workspace_registry(project_id)
        return await asyncio.to_thread(export_skill, registry.files, name)

    async def import_skill(
        self, package: SkillPackage, project_id: str | None = None
    ) -> SkillDocument:
        """Publish an explicitly user-authored package under the same rules as the Skill editor."""
        registry = await self.workspace_registry(project_id)
        return SkillDocument.model_validate(
            await asyncio.to_thread(import_skill, registry.files, package)
        )

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
        workspace_context.insert(0, environment_message(registry.workspace, config, tools))
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
        registry = await self.workspace_registry(project_id)
        inspection, _ = await self._prepare(
            request.preset,
            self._effective_config(config, registry, request.model, request.access),
            history,
            request.input,
            request.variables,
            request.skills,
            registry,
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
        config = self._effective_config(config, registry, request.model, request.access)
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
            "access": request.access
            or ("read-only" if config.sandbox_mode == "read-only" else "workspace-write"),
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
        views = await self.run_views(run_id=run_id)
        if not views:
            raise AppError("not_found", "The requested record does not exist.", 404)
        return views[0]

    async def run_views(
        self, *, run_id: str | None = None, session_id: str | None = None
    ) -> list[RunView]:
        """Project one Run or a conversation's Runs with a constant number of queries."""
        if session_id is not None:
            await self.repository.session(session_id)
        views = []
        for value in await self.repository.run_projections(run_id=run_id, session_id=session_id):
            pending = value.pop("open_calls")
            active = next(
                (
                    call
                    for call in reversed(pending)
                    if call["kind"] == "tool" and call["status"] == "executing"
                ),
                None,
            )
            # Replay blocks (provider reasoning signatures) are evidence, not display data.
            value["messages"] = [{**item, "blocks": []} for item in value["messages"]]
            value.update(
                plan=latest_plan([Message.model_validate(item) for item in value["messages"]]),
                unknown_calls=[
                    {"id": call["id"], "kind": call["kind"], "data": redact(call["data"])}
                    for call in pending
                    if call["status"] == "outcome_unknown"
                ],
                active_tool=(
                    {
                        "id": active["id"],
                        "name": active["data"]["name"],
                        **active["data"].get("output", {}),
                    }
                    if active
                    else None
                ),
            )
            views.append(RunView.model_validate(value))
        return views

    async def approve(self, run_id: str, approval_id: str, decision: str) -> RunView:
        """Record one idempotent decision before resuming the interrupted checkpoint."""
        async with self.commands:
            run = await self.repository.run(run_id)
            approval = run["approval"]
            if not approval or approval["id"] != approval_id:
                raise AppError("approval_conflict", "This approval is no longer pending.", 409)
            if approval["decision"]:
                recorded = "approve" if decision == "approve_conversation" else decision
                if approval["decision"] != recorded:
                    raise AppError(
                        "approval_conflict", "A different decision has already been recorded.", 409
                    )
                return await self.view(run_id)
            if run["status"] != "waiting_approval":
                raise AppError("approval_conflict", "The Run is not awaiting this approval.", 409)
            if decision == "approve_conversation":
                # Record the standing approval first; this call is then approved like any other.
                grant = grant_for(
                    ToolCall(
                        id=approval["tool_call_id"], name=approval["name"], args=approval["args"]
                    )
                )
                await self.repository.add_grant(run["session_id"], grant.model_dump())
                approval = {**approval, "decision": "approve", "grant": grant.model_dump()}
            else:
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
            uncertain = await self.repository.calls_with_status(run_id, ["outcome_unknown"])
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
        await asyncio.to_thread(validate_toolchains, value.toolchain_roots, self.registry.workspace)
        # The default model's connection comes from its provider. Reject an edit that changes
        # the request format under a provider ID instead of silently discarding it.
        resolved = resolve_model(value)
        if value.provider.profile_id is not None and value.provider.adapter != resolved.adapter:
            raise AppError(
                "provider_mismatch",
                f"provider.profile_id {value.provider.profile_id} is a {resolved.adapter} "
                f"provider, not {value.provider.adapter}. Select a configured provider, or set "
                "profile_id to null for an explicit endpoint.",
            )
        value = value.model_copy(update={"provider": resolved})
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
                    item["status"] in ACTIVE_PROCESSES
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
