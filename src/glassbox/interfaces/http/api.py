"""HTTP adapters for Glassbox's use cases."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from anyio import CancelScope
from fastapi import APIRouter, Header
from fastapi import Path as PathParameter
from fastapi import Query, Request, Response
from fastapi.responses import StreamingResponse

from glassbox.domain import (
    COMMON_DOWNLOAD_HOSTS,
    EFFORT_LEVELS,
    AppError,
    ApprovalDecision,
    Attachment,
    ChatGPTStatus,
    CommandInfo,
    CompactionView,
    CompactRequest,
    ContextRestore,
    ContextView,
    Contract,
    CredentialSave,
    CredentialStatus,
    DraftPreviewRequest,
    ErrorEnvelope,
    Eval,
    EvalBatchView,
    EvalDraft,
    EvalSummary,
    EvalUpdate,
    FileChanges,
    FolderCreate,
    FolderPickerRequest,
    FolderSelection,
    FolderUpdate,
    FolderView,
    GitDiff,
    GitStatus,
    GlassboxConfig,
    MemoryDocument,
    MemorySave,
    Preset,
    PresetPackage,
    Preview,
    PreviewRequest,
    ProcessInput,
    ProcessView,
    ProjectCreate,
    ProjectView,
    PromptInspection,
    ProviderProfile,
    ResumeRequest,
    RunEvent,
    RunView,
    SessionCopy,
    SessionCreate,
    SessionSummary,
    SessionUpdate,
    SessionView,
    SkillDocument,
    SkillPackage,
    SkillSave,
    StartRun,
    ToolDefinition,
    TraceView,
    WorkspaceSkills,
    WorkspaceView,
    credential_reference,
)
from glassbox.domain.lifecycle import TERMINAL
from glassbox.infrastructure.attachments import IMAGE_LIMIT
from glassbox.infrastructure.providers.chatgpt import CALLBACK_PATH
from glassbox.infrastructure.sandbox.processes import process_view
from glassbox.infrastructure.tracing import redact

router = APIRouter(
    prefix="/api/v1",
    responses={status: {"model": ErrorEnvelope} for status in (403, 404, 409, 422, 500)},
)


@router.post("/desktop/folder-picker", response_model=FolderSelection)
async def pick_folder(body: FolderPickerRequest, request: Request) -> FolderSelection:
    """Open the local OS dialog and dismiss it when its requesting browser disconnects."""
    task = asyncio.create_task(request.app.state.folder_picker.choose(body.initial_path))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.2)
            if await request.is_disconnected():
                task.cancel()
                return FolderSelection(status="cancelled")
        return await task
    except asyncio.CancelledError:
        if task.cancelled():
            return FolderSelection(status="cancelled")
        raise
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.get("/presets/{preset_id}/export", response_model=PresetPackage)
async def preset_export(preset_id: str, request: Request) -> PresetPackage:
    """Export the saved prompt aggregate, excluding all configuration and execution evidence."""
    return await request.app.state.glassbox.export_preset(preset_id)


@router.post("/presets/import", response_model=Preset, status_code=201)
async def preset_import(body: PresetPackage, request: Request) -> Preset:
    """Save a reusable prompt as a new independent aggregate with fresh block IDs."""
    return await request.app.state.glassbox.import_preset(body)


@router.get("/workspace/skills/{name}/export", response_model=SkillPackage)
async def skill_export(name: str, request: Request, project_id: str | None = None) -> SkillPackage:
    """Export one selected workspace's Skill and bounded supporting files."""
    return await request.app.state.glassbox.export_skill(name, project_id)


@router.post("/workspace/skills/import", response_model=SkillDocument, status_code=201)
async def skill_import(
    body: SkillPackage, request: Request, project_id: str | None = None
) -> SkillDocument:
    """Publish a new Skill only after all source paths and its entry document are validated."""
    return await request.app.state.glassbox.import_skill(body, project_id)


@router.get("/runs/{run_id}/event-log", response_model=list[RunEvent])
async def event_log(run_id: str, request: Request, after: int = 0) -> list[dict]:
    """Read a finite event page for CLI/RPC clients without subscribing to SSE."""
    await request.app.state.glassbox.repository.run_progress(run_id)
    return await request.app.state.glassbox.repository.read_events(run_id, max(0, after))


@router.get("/sessions/{session_id}/processes", response_model=list[ProcessView])
async def list_processes(session_id: str, request: Request) -> list[ProcessView]:
    """Read lifecycle records without reconnecting to or recreating a sandbox process."""
    return [
        process_view(item)
        for item in await request.app.state.glassbox.repository.list_processes(session_id)
    ]


@router.post("/sessions/{session_id}/processes/{identifier}/input", response_model=ProcessView)
async def process_input(
    session_id: str, identifier: str, body: ProcessInput, request: Request
) -> ProcessView:
    """Send the user's explicit bounded pipe input to this conversation's live process."""
    return await request.app.state.glassbox.registry.processes.input(
        identifier, session_id, body.text, body.eof
    )


@router.post("/sessions/{session_id}/processes/{identifier}/stop", response_model=ProcessView)
async def stop_process(session_id: str, identifier: str, request: Request) -> ProcessView:
    """Wait for native cleanup before returning the user's stop action."""
    return await request.app.state.glassbox.registry.processes.stop(identifier, session_id)


class Bootstrap(Contract):
    """Typed initial workspace state for the browser."""

    config: GlassboxConfig
    presets: list[Preset]
    sessions: list[SessionSummary]
    folders: list[FolderView]
    projects: list[ProjectView]
    evals: list[EvalSummary]
    tools: list[ToolDefinition]
    tool_errors: dict[str, str]
    workspace_root: str
    # Where each key referenced by providers and MCP servers comes from; never the value.
    credentials: list[CredentialStatus]
    effort_levels: dict[str, list[str]]
    # Download hosts new installations allow, offered to add to existing settings.
    common_download_hosts: list[str]


class ModelList(Contract):
    """Model IDs a provider reports it serves."""

    models: list[str]


class SignInStart(Contract):
    """The ChatGPT page that continues a sign-in in the browser."""

    url: str


class SignOutResult(Contract):
    """Whether OpenAI confirmed revoking a sign-in; its tokens are removed here either way."""

    revoked: bool


ProviderId = Annotated[str, PathParameter(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]


def credential_name(name: str) -> str:
    """Accept only environment-variable-style key names in credential routes."""
    try:
        return credential_reference(name)
    except ValueError as exc:
        raise AppError("credential_name", str(exc)) from None


def referenced_credentials(config: GlassboxConfig) -> list[str]:
    """Collect the key names the saved providers and MCP servers refer to."""
    names = [profile.credential_ref for profile in config.providers]
    for server in config.mcp_servers:
        names.extend([server.credential_ref, *server.env.values()])
    return [name for name in dict.fromkeys(names) if name]


@router.get("/health")
async def health() -> dict[str, str]:
    """Expose local process availability."""
    return {"status": "ok", "version": "0.1.0"}


@router.get("/bootstrap", response_model=Bootstrap)
async def bootstrap(request: Request) -> Bootstrap:
    """Load settings, presets, sessions, and the currently discovered tool catalog."""
    glassbox = request.app.state.glassbox
    config = await glassbox.repository.config()
    return Bootstrap(
        config=config,
        presets=await glassbox.repository.list_presets(),
        sessions=await glassbox.repository.list_sessions(),
        folders=await glassbox.repository.list_folders(),
        projects=await glassbox.repository.list_projects(),
        evals=await glassbox.list_evals(),
        tools=list(glassbox.registry.catalog.values()),
        tool_errors=glassbox.registry.errors,
        workspace_root=str(glassbox.registry.workspace),
        credentials=await glassbox.credential_status(referenced_credentials(config)),
        effort_levels={adapter: list(levels) for adapter, levels in EFFORT_LEVELS.items()},
        common_download_hosts=list(COMMON_DOWNLOAD_HOSTS),
    )


@router.get("/credentials", response_model=list[CredentialStatus])
async def credentials(
    request: Request, name: Annotated[list[str] | None, Query()] = None
) -> list[CredentialStatus]:
    """Report where named keys come from, for draft settings not saved yet."""
    return await request.app.state.glassbox.credential_status(
        [credential_name(item) for item in name or []]
    )


@router.put("/credentials/{name}", response_model=CredentialStatus)
async def save_credential(name: str, body: CredentialSave, request: Request) -> CredentialStatus:
    """Store a key entered in Settings; the response never contains the value."""
    return await request.app.state.glassbox.save_credential(credential_name(name), body.value)


@router.delete("/credentials/{name}", response_model=CredentialStatus)
async def delete_credential(name: str, request: Request) -> CredentialStatus:
    """Forget a stored key; an environment variable of the same name still applies."""
    return await request.app.state.glassbox.delete_credential(credential_name(name))


@router.post("/chatgpt/{provider_id}/sign-in", response_model=SignInStart)
async def chatgpt_sign_in(provider_id: ProviderId, request: Request) -> SignInStart:
    """Start Sign in with ChatGPT for a provider.

    ChatGPT sends the browser back to this server's callback on 127.0.0.1, at the port the
    browser reached it on, where the sign-in completes."""
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    redirect = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    return SignInStart(url=await request.app.state.glassbox.chatgpt.start(provider_id, redirect))


@router.get("/chatgpt/{provider_id}", response_model=ChatGPTStatus)
async def chatgpt_status(provider_id: ProviderId, request: Request) -> ChatGPTStatus:
    """Report whether a provider is signed in to ChatGPT, without any token."""
    return await request.app.state.glassbox.chatgpt.status(provider_id)


@router.delete("/chatgpt/{provider_id}", response_model=SignOutResult)
async def chatgpt_sign_out(provider_id: ProviderId, request: Request) -> SignOutResult:
    """Sign a provider out of ChatGPT: remove its tokens and revoke them at OpenAI."""
    return SignOutResult(revoked=await request.app.state.glassbox.chatgpt.sign_out(provider_id))


@router.post("/providers/models", response_model=ModelList)
async def provider_models(body: ProviderProfile, request: Request) -> ModelList:
    """List the models a provider serves, using the server-side key for its reference."""
    return ModelList(models=await request.app.state.glassbox.list_models(body))


@router.get("/skills", response_model=WorkspaceSkills)
async def global_skills(request: Request) -> WorkspaceSkills:
    """List bundled and global skills shared by every project."""
    return await request.app.state.glassbox.global_skills()


@router.get("/skills/{name}", response_model=SkillDocument)
async def global_skill(name: str, request: Request) -> SkillDocument:
    """Load a bundled or global skill."""
    return await request.app.state.glassbox.global_skill(name)


@router.put("/skills", response_model=SkillDocument)
async def save_global_skill(body: SkillSave, request: Request) -> SkillDocument:
    """Create or update a global skill with conflict detection."""
    return await request.app.state.glassbox.save_global_skill(body)


@router.delete("/skills/{name}", status_code=204)
async def delete_global_skill(name: str, expected_hash: str, request: Request) -> Response:
    """Delete a global skill whose current content the user has seen."""
    await request.app.state.glassbox.delete_global_skill(name, expected_hash)
    return Response(status_code=204)


@router.put("/config", response_model=GlassboxConfig)
async def save_config(body: GlassboxConfig, request: Request) -> GlassboxConfig:
    """Update settings with optimistic revision checks."""
    return await request.app.state.glassbox.save_config(body)


@router.get("/workspace", response_model=WorkspaceView)
async def workspace(
    request: Request, refresh: bool = False, project_id: str | None = None
) -> WorkspaceView:
    """Inspect project memory, skills, and the native OS sandbox status."""
    return await request.app.state.glassbox.workspace(refresh, project_id)


@router.put("/workspace/memory", response_model=MemoryDocument)
async def save_memory(
    body: MemorySave, request: Request, project_id: str | None = None
) -> MemoryDocument:
    """Save explicitly edited project notes."""
    return await request.app.state.glassbox.save_memory(body, project_id)


@router.post("/attachments", response_model=Attachment, status_code=201)
async def upload_attachment(request: Request) -> Attachment:
    """Store an image sent as the raw request body (PNG, JPEG, GIF or WebP, at most 5 MiB)."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > IMAGE_LIMIT:
            raise AppError("attachment_size", "Images can be at most 5 MiB.", 413)
    return await request.app.state.glassbox.save_attachment(bytes(body))


@router.get("/attachments/{identifier}", response_class=Response)
async def attachment(identifier: str, request: Request) -> Response:
    """Return a stored image with its verified type; it is never interpreted as a page."""
    data, media_type = await request.app.state.glassbox.attachment(identifier)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )


@router.get("/workspace/git", response_model=GitStatus)
async def git_status(request: Request, project_id: str | None = None) -> GitStatus:
    """Show the workspace's Git branch and changed files; Git runs read-only on the host."""
    return await request.app.state.glassbox.git_status(project_id)


@router.get("/workspace/git/diff", response_model=GitDiff)
async def git_diff(
    request: Request, project_id: str | None = None, path: str | None = None
) -> GitDiff:
    """Show uncommitted changes against HEAD; private files are listed, never shown."""
    return await request.app.state.glassbox.git_diff(project_id, path)


@router.get("/workspace/commands", response_model=list[CommandInfo])
async def commands(request: Request, project_id: str | None = None) -> list[CommandInfo]:
    """List custom slash commands from the workspace and the global commands folder."""
    return await request.app.state.glassbox.list_commands(project_id)


@router.get("/workspace/skills/{name}", response_model=SkillDocument)
async def skill(name: str, request: Request, project_id: str | None = None) -> SkillDocument:
    """Load a skill body on demand."""
    return await request.app.state.glassbox.skill(name, project_id)


@router.put("/workspace/skills", response_model=SkillDocument)
async def save_skill(
    body: SkillSave, request: Request, project_id: str | None = None
) -> SkillDocument:
    """Create or update a workspace skill with conflict detection."""
    return await request.app.state.glassbox.save_skill(body, project_id)


@router.put("/presets/{preset_id}", response_model=Preset)
async def save_preset(preset_id: str, body: Preset, request: Request) -> Preset:
    """Persist an edited preset whose identifier matches the requested resource."""
    if preset_id != body.id:
        raise AppError("preset_id", "The route and preset ID must match.")
    return await request.app.state.glassbox.repository.save_preset(body)


@router.post("/tools/refresh", response_model=list[ToolDefinition])
async def refresh_tools(request: Request) -> list[ToolDefinition]:
    """Explicitly discover tools from the saved MCP configuration."""
    glassbox = request.app.state.glassbox
    return await glassbox.registry.discover(await glassbox.repository.config())


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(body: SessionCreate, request: Request) -> dict[str, Any]:
    """Create a conversation without starting model execution."""
    return await request.app.state.glassbox.repository.new_session(body.title, body.project_id)


@router.post("/projects", response_model=ProjectView, status_code=201)
async def create_project(body: ProjectCreate, request: Request) -> ProjectView:
    """Register a named workspace folder for new and idle conversations."""
    return await request.app.state.glassbox.create_project(body)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """Read committed history and the active Run identifier."""
    return await request.app.state.glassbox.repository.session(session_id)


@router.get("/sessions/{session_id}/context", response_model=ContextView)
async def session_context(session_id: str, request: Request) -> ContextView:
    """Inspect estimated history accounting and explicit summary provenance."""
    return await request.app.state.glassbox.context(session_id)


@router.post("/sessions/{session_id}/compact", response_model=CompactionView, status_code=202)
async def compact_session(
    session_id: str, body: CompactRequest, request: Request
) -> CompactionView:
    """Explicitly reduce older turns without modifying their saved evidence."""
    return await request.app.state.glassbox.compact(session_id, body)


@router.put("/sessions/{session_id}/context", status_code=204)
async def restore_context(session_id: str, body: ContextRestore, request: Request) -> Response:
    """Deactivate the summary and restore original history for subsequent Runs."""
    await request.app.state.glassbox.repository.reset_compaction(session_id, body.expected_revision)
    return Response(status_code=204)


@router.get("/sessions/{session_id}/compactions/{identifier}")
async def compaction_evidence(
    session_id: str, identifier: str, request: Request, redacted: bool = True
) -> dict[str, Any]:
    """Read original or labelled redacted model evidence for one owned reduction."""
    record = await request.app.state.glassbox.repository.compaction(identifier)
    if record["session_id"] != session_id:
        raise AppError("not_found", "Compaction does not belong to this conversation.", 404)
    return {"redacted": redacted, **(redact(record) if redacted else record)}


@router.post(
    "/sessions/{session_id}/compactions/{identifier}/cancel", response_model=CompactionView
)
async def cancel_compaction(session_id: str, identifier: str, request: Request) -> CompactionView:
    """Cancel a current reduction and keep the previous effective context."""
    return await request.app.state.glassbox.stop_compaction(session_id, identifier)


@router.patch("/sessions/{session_id}", response_model=SessionView)
async def update_session(session_id: str, body: SessionUpdate, request: Request) -> dict[str, Any]:
    """Rename, archive, restore, or move a conversation using its observed revision."""
    return await request.app.state.glassbox.repository.update_session(session_id, body)


@router.post("/sessions/{session_id}/copy", response_model=SessionView, status_code=201)
async def copy_session(session_id: str, body: SessionCopy, request: Request) -> dict[str, Any]:
    """Create an independent duplicate or turn-boundary fork without model execution."""
    return await request.app.state.glassbox.repository.copy_session(session_id, body)


@router.post("/folders", response_model=FolderView, status_code=201)
async def create_folder(body: FolderCreate, request: Request) -> dict[str, Any]:
    """Create an empty conversation folder."""
    return await request.app.state.glassbox.repository.save_folder(body.name)


@router.patch("/folders/{folder_id}", response_model=FolderView)
async def rename_folder(folder_id: str, body: FolderUpdate, request: Request) -> dict[str, Any]:
    """Rename a folder without changing conversation membership."""
    return await request.app.state.glassbox.repository.save_folder(
        body.name, folder_id, body.expected_revision
    )


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(folder_id: str, request: Request) -> None:
    """Delete a folder while keeping its conversations in the unfiled group."""
    await request.app.state.glassbox.repository.delete_folder(folder_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request) -> None:
    """Delete an idle conversation and its owned execution evidence."""
    await request.app.state.glassbox.delete_session(session_id)


@router.get("/sessions/{session_id}/runs", response_model=list[RunView])
async def session_runs(session_id: str, request: Request) -> list[RunView]:
    """List saved Run projections for a conversation."""
    return await request.app.state.glassbox.run_views(session_id=session_id)


@router.post("/prompts/inspect", response_model=PromptInspection)
async def inspect_prompt(body: DraftPreviewRequest, request: Request) -> PromptInspection:
    """Inspect a live draft without persistence or provider execution."""
    return await request.app.state.glassbox.inspect(body)


@router.post("/prompts/preview", response_model=Preview)
async def preview(body: PreviewRequest, request: Request) -> Preview:
    """Compile and save a version-bound preview without calling a provider."""
    return await request.app.state.glassbox.preview(body)


@router.post("/runs", response_model=RunView, status_code=202)
async def start_run(
    body: StartRun | PreviewRequest, request: Request, idempotency_key: str = Header()
) -> RunView:
    """Compile a direct submission or run an optional preview with retry protection."""
    return await request.app.state.glassbox.start(body, idempotency_key)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, request: Request) -> RunView:
    """Read execution state without creating or resuming work."""
    return await request.app.state.glassbox.view(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(run_id: str, request: Request) -> RunView:
    """Cancel the local task and preserve any uncertain external effects."""
    return await request.app.state.glassbox.cancel(run_id)


@router.get("/runs/{run_id}/files", response_model=FileChanges)
async def run_file_changes(run_id: str, request: Request) -> FileChanges:
    """List files the Run changed with file tools and whether each can still be undone."""
    return await request.app.state.glassbox.file_changes(run_id)


@router.post("/runs/{run_id}/files/revert", response_model=FileChanges)
async def revert_run_files(run_id: str, request: Request) -> FileChanges:
    """Restore files still as the Run left them; files changed since are skipped."""
    return await request.app.state.glassbox.revert_files(run_id)


@router.post("/runs/{run_id}/approvals/{approval_id}", response_model=RunView)
async def approve_run(
    run_id: str, approval_id: str, body: ApprovalDecision, request: Request
) -> RunView:
    """Resolve exactly the pending approval identified in the URL."""
    return await request.app.state.glassbox.approve(run_id, approval_id, body.decision)


@router.post("/runs/{run_id}/resume", response_model=RunView)
async def resume_run(run_id: str, body: ResumeRequest, request: Request) -> RunView:
    """Resume a checkpoint after explicit reconciliation of uncertain calls."""
    return await request.app.state.glassbox.resume(run_id, body)


@router.get("/runs/{run_id}/trace", response_model=TraceView)
async def trace(run_id: str, request: Request, raw: bool = False) -> dict[str, Any]:
    """Return redacted evidence by default; raw access is an explicit local choice."""
    glassbox = request.app.state.glassbox
    await glassbox.repository.run_progress(run_id)
    calls = await glassbox.repository.trace(run_id)
    return {"schema_version": 1, "redacted": not raw, "calls": calls if raw else redact(calls)}


@router.get("/runs/{run_id}/export")
async def export(run_id: str, request: Request) -> Response:
    """Download a labeled, redacted record that is not a replayable request bundle."""
    glassbox = request.app.state.glassbox
    run = await glassbox.repository.run(run_id)
    payload = {
        "schema_version": 1,
        "redacted": True,
        "redaction_version": 1,
        "replayable": False,
        "run": {
            key: value
            for key, value in run.items()
            if key not in {"idempotency_key", "request_hash"}
        },
        "calls": await glassbox.repository.trace(run_id),
    }
    return Response(
        json.dumps(redact(payload), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}.json"'},
    )


@router.get(
    "/runs/{run_id}/events",
    responses={200: {"content": {"text/event-stream": {"schema": RunEvent.model_json_schema()}}}},
)
async def run_events(
    run_id: str, request: Request, after: int = 0, last_event_id: str | None = Header(default=None)
) -> StreamingResponse:
    """Replay persisted SSE events; browser reconnection never resumes execution."""
    glassbox = request.app.state.glassbox
    run = await glassbox.repository.run_progress(run_id)
    try:
        cursor = int(last_event_id) if last_event_id is not None else after
    except ValueError as exc:
        raise AppError("event_cursor", "Event cursor must be an integer.") from exc
    if cursor < 0 or cursor > run["latest_event_seq"]:
        raise AppError("event_cursor", "Reload the Run snapshot before reconnecting.", 410)

    async def stream() -> AsyncIterator[str]:
        """Replay bounded committed event pages until terminal state or browser disconnect."""
        nonlocal cursor
        ticks = 0
        while not await request.is_disconnected():
            # Browser disconnects cancel this generator. Finish short DB reads before
            # yielding so cancellation cannot return a half-closed SQLite connection.
            # Bound before the scope, which pyright assumes may swallow exceptions.
            page: list[dict[str, Any]] = []
            current = run
            with CancelScope(shield=True):
                page = await glassbox.repository.read_events(run_id, cursor)
                current = await glassbox.repository.run_progress(run_id)
            for item in page:
                cursor = item["seq"]
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(redact(item))}\n\n"
            if current["status"] in TERMINAL and cursor >= current["latest_event_seq"]:
                break
            ticks += 1
            if ticks % 40 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/evals", response_model=list[EvalSummary])
async def list_evals(request: Request) -> list[EvalSummary]:
    """List saved evaluations with the status of their most recent comparison."""
    return await request.app.state.glassbox.list_evals()


@router.post("/evals", response_model=Eval, status_code=201)
async def create_eval(body: EvalDraft, request: Request) -> Eval:
    """Store a new evaluation definition."""
    return await request.app.state.glassbox.create_eval(body)


@router.get("/evals/{eval_id}", response_model=Eval)
async def get_eval(eval_id: str, request: Request) -> Eval:
    """Return one evaluation definition for editing."""
    return await request.app.state.glassbox.get_eval(eval_id)


@router.put("/evals/{eval_id}", response_model=Eval)
async def update_eval(eval_id: str, body: EvalUpdate, request: Request) -> Eval:
    """Replace an evaluation definition under its observed revision."""
    return await request.app.state.glassbox.update_eval(eval_id, body)


@router.delete("/evals/{eval_id}", status_code=204)
async def delete_eval(eval_id: str, request: Request) -> Response:
    """Delete an evaluation, its batches and the hidden Run sessions they created."""
    await request.app.state.glassbox.delete_eval(eval_id)
    return Response(status_code=204)


@router.get("/evals/{eval_id}/batches", response_model=list[EvalBatchView])
async def list_batches(eval_id: str, request: Request) -> list[EvalBatchView]:
    """List an evaluation's comparison batches, newest first."""
    return await request.app.state.glassbox.list_batches(eval_id)


@router.post("/evals/{eval_id}/batches", response_model=EvalBatchView, status_code=202)
async def start_batch(
    eval_id: str, request: Request, project_id: str | None = None
) -> EvalBatchView:
    """Start a background comparison of every variant across every case."""
    return await request.app.state.glassbox.start_batch(eval_id, project_id)


@router.get("/batches/{batch_id}", response_model=EvalBatchView)
async def get_batch(batch_id: str, request: Request) -> EvalBatchView:
    """Return one comparison batch's progress and results."""
    return await request.app.state.glassbox.batch(batch_id)


@router.post("/batches/{batch_id}/cancel", response_model=EvalBatchView)
async def cancel_batch(batch_id: str, request: Request) -> EvalBatchView:
    """Stop a running comparison; completed items and their evidence are kept."""
    return await request.app.state.glassbox.cancel_batch(batch_id)
