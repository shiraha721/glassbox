"""HTTP adapters for the local studio."""

import asyncio
import json
from typing import Any

from anyio import CancelScope
from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field

from rtagent.domain import (
    ApprovalDecision,
    Contract,
    MemoryDocument,
    MemorySave,
    Preset,
    Preview,
    PreviewRequest,
    ResumeRequest,
    RunEvent,
    RunView,
    SessionView,
    SkillDocument,
    SkillSave,
    StartRun,
    StudioConfig,
    ToolDefinition,
    TraceView,
    WorkspaceView,
)
from rtagent.storage import TERMINAL
from rtagent.tracing import redact

router = APIRouter(prefix="/api/v1")


class SessionCreate(Contract):
    """Optional display title for a new empty conversation."""

    title: str = Field(default="New investigation", min_length=1, max_length=120)


class Bootstrap(Contract):
    """Typed initial workspace state for the browser."""

    config: StudioConfig
    presets: list[Preset]
    sessions: list[SessionView]
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
        tools=list(studio.registry.catalog.values()),
        tool_errors=studio.registry.errors,
        workspace_root=str(studio.registry.workspace),
    )


@router.put("/config", response_model=StudioConfig)
async def save_config(body: StudioConfig, request: Request) -> StudioConfig:
    """Update settings with optimistic revision checks."""
    return await request.app.state.studio.save_config(body)


@router.get("/workspace", response_model=WorkspaceView)
async def workspace(request: Request, refresh: bool = False) -> WorkspaceView:
    """Inspect project memory, skills, and the actual Bash sandbox status."""
    return await request.app.state.studio.workspace(refresh)


@router.put("/workspace/memory", response_model=MemoryDocument)
async def save_memory(body: MemorySave, request: Request) -> MemoryDocument:
    """Save explicitly edited project notes."""
    return await request.app.state.studio.save_memory(body)


@router.get("/workspace/skills/{name}", response_model=SkillDocument)
async def skill(name: str, request: Request) -> SkillDocument:
    """Load a skill body on demand."""
    return await request.app.state.studio.skill(name)


@router.put("/workspace/skills", response_model=SkillDocument)
async def save_skill(body: SkillSave, request: Request) -> SkillDocument:
    """Create or update a workspace skill with conflict detection."""
    return await request.app.state.studio.save_skill(body)


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
    return await request.app.state.studio.repository.new_session(body.title)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """Read committed history and the active Run identifier."""
    return await request.app.state.studio.repository.session(session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request) -> None:
    """Delete an idle conversation and its owned execution evidence."""
    await request.app.state.studio.delete_session(session_id)


@router.get("/sessions/{session_id}/runs", response_model=list[RunView])
async def session_runs(session_id: str, request: Request) -> list[RunView]:
    """List saved Run projections for a conversation."""
    studio = request.app.state.studio
    return [await studio.view(run["id"]) for run in await studio.repository.list_runs(session_id)]


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
):
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

    async def stream():
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
