"""HTTP adapters for the local studio."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from anyio import CancelScope
from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import StreamingResponse

from rtagent.domain import (
    ApprovalDecision,
    CompactionView,
    CompactRequest,
    ContextRestore,
    ContextView,
    Contract,
    DraftPreviewRequest,
    ErrorEnvelope,
    FolderCreate,
    FolderPickerRequest,
    FolderSelection,
    FolderUpdate,
    FolderView,
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
    ResumeRequest,
    RunEvent,
    RunView,
    SessionCopy,
    SessionCreate,
    SessionUpdate,
    SessionView,
    SkillDocument,
    SkillPackage,
    SkillSave,
    StartRun,
    StudioConfig,
    ToolDefinition,
    TraceView,
    WorkspaceView,
)
from rtagent.processes import process_view
from rtagent.storage import TERMINAL
from rtagent.tracing import redact

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
    return await request.app.state.studio.export_preset(preset_id)


@router.post("/presets/import", response_model=Preset, status_code=201)
async def preset_import(body: PresetPackage, request: Request) -> Preset:
    """Save a reusable prompt as a new independent aggregate with fresh block IDs."""
    return await request.app.state.studio.import_preset(body)


@router.get("/workspace/skills/{name}/export", response_model=SkillPackage)
async def skill_export(name: str, request: Request, project_id: str | None = None) -> SkillPackage:
    """Export one selected workspace's Skill and bounded supporting files."""
    return await request.app.state.studio.export_skill(name, project_id)


@router.post("/workspace/skills/import", response_model=SkillDocument, status_code=201)
async def skill_import(
    body: SkillPackage, request: Request, project_id: str | None = None
) -> SkillDocument:
    """Publish a new Skill only after all source paths and its entry document are validated."""
    return await request.app.state.studio.import_skill(body, project_id)


@router.get("/runs/{run_id}/event-log", response_model=list[RunEvent])
async def event_log(run_id: str, request: Request, after: int = 0) -> list[dict]:
    """Read a finite event page for CLI/RPC clients without subscribing to SSE."""
    await request.app.state.studio.repository.run(run_id)
    return await request.app.state.studio.repository.read_events(run_id, max(0, after))


@router.get("/sessions/{session_id}/processes", response_model=list[ProcessView])
async def list_processes(session_id: str, request: Request) -> list[ProcessView]:
    """Read lifecycle records without reconnecting to or recreating a sandbox process."""
    return [
        process_view(item)
        for item in await request.app.state.studio.repository.list_processes(session_id)
    ]


@router.post("/sessions/{session_id}/processes/{identifier}/input", response_model=ProcessView)
async def process_input(
    session_id: str, identifier: str, body: ProcessInput, request: Request
) -> ProcessView:
    """Send the user's explicit bounded pipe input to this conversation's live process."""
    return await request.app.state.studio.registry.processes.input(
        identifier, session_id, body.text, body.eof
    )


@router.post("/sessions/{session_id}/processes/{identifier}/stop", response_model=ProcessView)
async def stop_process(session_id: str, identifier: str, request: Request) -> ProcessView:
    """Wait for native cleanup before returning the user's stop action."""
    return await request.app.state.studio.registry.processes.stop(identifier, session_id)


class Bootstrap(Contract):
    """Typed initial workspace state for the browser."""

    config: StudioConfig
    presets: list[Preset]
    sessions: list[SessionView]
    folders: list[FolderView]
    projects: list[ProjectView]
    tools: list[ToolDefinition]
    tool_errors: dict[str, str]
    workspace_root: str


@router.get("/health")
async def health() -> dict[str, str]:
    """Expose local process availability."""
    return {"status": "ok", "version": "0.1.0"}


@router.get("/bootstrap", response_model=Bootstrap)
async def bootstrap(request: Request) -> Bootstrap:
    """Load settings, presets, sessions, and the currently discovered tool catalog."""
    studio = request.app.state.studio
    return Bootstrap(
        config=await studio.repository.config(),
        presets=await studio.repository.list_presets(),
        sessions=await studio.repository.list_sessions(),
        folders=await studio.repository.list_folders(),
        projects=await studio.repository.list_projects(),
        tools=list(studio.registry.catalog.values()),
        tool_errors=studio.registry.errors,
        workspace_root=str(studio.registry.workspace),
    )


@router.put("/config", response_model=StudioConfig)
async def save_config(body: StudioConfig, request: Request) -> StudioConfig:
    """Update settings with optimistic revision checks."""
    return await request.app.state.studio.save_config(body)


@router.get("/workspace", response_model=WorkspaceView)
async def workspace(
    request: Request, refresh: bool = False, project_id: str | None = None
) -> WorkspaceView:
    """Inspect project memory, skills, and the native OS sandbox status."""
    return await request.app.state.studio.workspace(refresh, project_id)


@router.put("/workspace/memory", response_model=MemoryDocument)
async def save_memory(
    body: MemorySave, request: Request, project_id: str | None = None
) -> MemoryDocument:
    """Save explicitly edited project notes."""
    return await request.app.state.studio.save_memory(body, project_id)


@router.get("/workspace/skills/{name}", response_model=SkillDocument)
async def skill(name: str, request: Request, project_id: str | None = None) -> SkillDocument:
    """Load a skill body on demand."""
    return await request.app.state.studio.skill(name, project_id)


@router.put("/workspace/skills", response_model=SkillDocument)
async def save_skill(
    body: SkillSave, request: Request, project_id: str | None = None
) -> SkillDocument:
    """Create or update a workspace skill with conflict detection."""
    return await request.app.state.studio.save_skill(body, project_id)


@router.put("/presets/{preset_id}", response_model=Preset)
async def save_preset(preset_id: str, body: Preset, request: Request) -> Preset:
    """Persist an edited preset whose identifier matches the requested resource."""
    from rtagent.domain import AppError

    if preset_id != body.id:
        raise AppError("preset_id", "The route and preset ID must match.")
    return await request.app.state.studio.repository.save_preset(body)


@router.post("/tools/refresh", response_model=list[ToolDefinition])
async def refresh_tools(request: Request) -> list[ToolDefinition]:
    """Explicitly discover tools from the saved MCP configuration."""
    studio = request.app.state.studio
    return await studio.registry.discover(await studio.repository.config())


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(body: SessionCreate, request: Request) -> dict[str, Any]:
    """Create a conversation without starting model execution."""
    return await request.app.state.studio.repository.new_session(body.title, body.project_id)


@router.post("/projects", response_model=ProjectView, status_code=201)
async def create_project(body: ProjectCreate, request: Request) -> ProjectView:
    """Register a named workspace folder for new and idle conversations."""
    return await request.app.state.studio.create_project(body)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """Read committed history and the active Run identifier."""
    return await request.app.state.studio.repository.session(session_id)


@router.get("/sessions/{session_id}/context", response_model=ContextView)
async def session_context(session_id: str, request: Request) -> ContextView:
    """Inspect estimated history accounting and explicit summary provenance."""
    return await request.app.state.studio.context(session_id)


@router.post("/sessions/{session_id}/compact", response_model=CompactionView, status_code=202)
async def compact_session(
    session_id: str, body: CompactRequest, request: Request
) -> CompactionView:
    """Explicitly reduce older turns without modifying their saved evidence."""
    return await request.app.state.studio.compact(session_id, body)


@router.put("/sessions/{session_id}/context", status_code=204)
async def restore_context(session_id: str, body: ContextRestore, request: Request) -> Response:
    """Deactivate the summary and restore original history for subsequent Runs."""
    await request.app.state.studio.repository.reset_compaction(session_id, body.expected_revision)
    return Response(status_code=204)


@router.get("/sessions/{session_id}/compactions/{identifier}")
async def compaction_evidence(
    session_id: str, identifier: str, request: Request, redacted: bool = True
) -> dict[str, Any]:
    """Read original or labelled redacted model evidence for one owned reduction."""
    from rtagent.domain import AppError

    record = await request.app.state.studio.repository.compaction(identifier)
    if record["session_id"] != session_id:
        raise AppError("not_found", "Compaction does not belong to this conversation.", 404)
    return {"redacted": redacted, **(redact(record) if redacted else record)}


@router.post(
    "/sessions/{session_id}/compactions/{identifier}/cancel", response_model=CompactionView
)
async def cancel_compaction(session_id: str, identifier: str, request: Request) -> CompactionView:
    """Cancel a current reduction and keep the previous effective context."""
    return await request.app.state.studio.stop_compaction(session_id, identifier)


@router.patch("/sessions/{session_id}", response_model=SessionView)
async def update_session(session_id: str, body: SessionUpdate, request: Request) -> dict[str, Any]:
    """Rename, archive, restore, or move a conversation using its observed revision."""
    return await request.app.state.studio.repository.update_session(session_id, body)


@router.post("/sessions/{session_id}/copy", response_model=SessionView, status_code=201)
async def copy_session(session_id: str, body: SessionCopy, request: Request) -> dict[str, Any]:
    """Create an independent duplicate or turn-boundary fork without model execution."""
    return await request.app.state.studio.repository.copy_session(session_id, body)


@router.post("/folders", response_model=FolderView, status_code=201)
async def create_folder(body: FolderCreate, request: Request) -> dict[str, Any]:
    """Create an empty conversation folder."""
    return await request.app.state.studio.repository.save_folder(body.name)


@router.patch("/folders/{folder_id}", response_model=FolderView)
async def rename_folder(folder_id: str, body: FolderUpdate, request: Request) -> dict[str, Any]:
    """Rename a folder without changing conversation membership."""
    return await request.app.state.studio.repository.save_folder(
        body.name, folder_id, body.expected_revision
    )


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(folder_id: str, request: Request) -> None:
    """Delete a folder while keeping its conversations in the unfiled group."""
    await request.app.state.studio.repository.delete_folder(folder_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request) -> None:
    """Delete an idle conversation and its owned execution evidence."""
    await request.app.state.studio.delete_session(session_id)


@router.get("/sessions/{session_id}/runs", response_model=list[RunView])
async def session_runs(session_id: str, request: Request) -> list[RunView]:
    """List saved Run projections for a conversation."""
    studio = request.app.state.studio
    return [await studio.view(run["id"]) for run in await studio.repository.list_runs(session_id)]


@router.post("/prompts/inspect", response_model=PromptInspection)
async def inspect_prompt(body: DraftPreviewRequest, request: Request) -> PromptInspection:
    """Inspect a live draft without persistence or provider execution."""
    return await request.app.state.studio.inspect(body)


@router.post("/prompts/preview", response_model=Preview)
async def preview(body: PreviewRequest, request: Request) -> Preview:
    """Compile and save a version-bound preview without calling a provider."""
    return await request.app.state.studio.preview(body)


@router.post("/runs", response_model=RunView, status_code=202)
async def start_run(
    body: StartRun | PreviewRequest, request: Request, idempotency_key: str = Header()
) -> RunView:
    """Compile a direct submission or run an optional preview with retry protection."""
    return await request.app.state.studio.start(body, idempotency_key)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, request: Request) -> RunView:
    """Read execution state without creating or resuming work."""
    return await request.app.state.studio.view(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(run_id: str, request: Request) -> RunView:
    """Cancel the local task and preserve any uncertain external effects."""
    return await request.app.state.studio.cancel(run_id)


@router.post("/runs/{run_id}/approvals/{approval_id}", response_model=RunView)
async def approve_run(
    run_id: str, approval_id: str, body: ApprovalDecision, request: Request
) -> RunView:
    """Resolve exactly the pending approval identified in the URL."""
    return await request.app.state.studio.approve(run_id, approval_id, body.decision)


@router.post("/runs/{run_id}/resume", response_model=RunView)
async def resume_run(run_id: str, body: ResumeRequest, request: Request) -> RunView:
    """Resume a checkpoint after explicit reconciliation of uncertain calls."""
    return await request.app.state.studio.resume(run_id, body)


@router.get("/runs/{run_id}/trace", response_model=TraceView)
async def trace(run_id: str, request: Request, raw: bool = False) -> dict[str, Any]:
    """Return redacted evidence by default; raw access is an explicit local choice."""
    studio = request.app.state.studio
    await studio.repository.run(run_id)
    calls = await studio.repository.trace(run_id)
    return {"schema_version": 1, "redacted": not raw, "calls": calls if raw else redact(calls)}


@router.get("/runs/{run_id}/export")
async def export(run_id: str, request: Request) -> Response:
    """Download a labeled, redacted record that is not a replayable request bundle."""
    studio = request.app.state.studio
    run = await studio.repository.run(run_id)
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
        "calls": await studio.repository.trace(run_id),
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
    from rtagent.domain import AppError

    studio = request.app.state.studio
    run = await studio.repository.run(run_id)
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
            with CancelScope(shield=True):
                page = await studio.repository.read_events(run_id, cursor)
                current = await studio.repository.run(run_id)
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
