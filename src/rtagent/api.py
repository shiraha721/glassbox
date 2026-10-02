"""HTTP adapters for the local studio."""

import asyncio
import json
import secrets
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field

from rtagent.domain import (
    ApprovalDecision,
    Contract,
    Preset,
    Preview,
    PreviewRequest,
    ResumeRequest,
    RunEvent,
    RunView,
    SessionView,
    StartRun,
    StudioConfig,
    ToolDefinition,
)
from rtagent.storage import TERMINAL
from rtagent.tracing import redact

router = APIRouter(prefix="/api/v1")


class Unlock(Contract):
    """Local access-token exchange payload; never persisted as configuration."""

    token: str = Field(min_length=1, max_length=500)


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
    """Expose process availability without requiring workspace access."""
    return {"status": "ok", "version": "0.1.0"}


@router.post("/auth/session")
async def unlock(body: Unlock, request: Request, response: Response) -> dict[str, str]:
    """Exchange the workspace token for an HttpOnly browser cookie and CSRF token."""
    from rtagent.domain import AppError

    if not secrets.compare_digest(body.token, request.app.state.settings.access_token):
        await asyncio.sleep(0.2)
        raise AppError("unauthenticated", "The local access token is incorrect.", 401)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    await request.app.state.studio.repository.create_browser_session(token, csrf)
    response.set_cookie(
        "studio_session",
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=43200,
    )
    return {"csrf_token": csrf}


@router.get("/auth/session")
async def auth_session(request: Request) -> dict[str, str]:
    """Restore the CSRF token for an already authenticated browser."""
    return {
        "csrf_token": request.state.browser_session["csrf"],
        "expires_at": request.state.browser_session["expires_at"],
    }


@router.delete("/auth/session", status_code=204)
async def logout(request: Request, response: Response) -> None:
    """Revoke server-side authentication and clear the browser cookie."""
    await request.app.state.studio.repository.logout(request.cookies.get("studio_session", ""))
    response.delete_cookie("studio_session")


@router.get("/bootstrap", response_model=Bootstrap)
async def bootstrap(request: Request):
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
async def save_config(body: StudioConfig, request: Request):
    """Update settings with optimistic revision checks."""
    return await request.app.state.studio.save_config(body)


@router.put("/presets/{preset_id}", response_model=Preset)
async def save_preset(preset_id: str, body: Preset, request: Request):
    """Persist an edited preset whose identifier matches the requested resource."""
    from rtagent.domain import AppError

    if preset_id != body.id:
        raise AppError("preset_id", "The route and preset ID must match.")
    return await request.app.state.studio.repository.save_preset(body)


@router.post("/tools/refresh", response_model=list[ToolDefinition])
async def refresh_tools(request: Request):
    """Explicitly discover tools from the saved MCP configuration."""
    studio = request.app.state.studio
    return await studio.registry.discover(await studio.repository.config())


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(body: SessionCreate, request: Request):
    """Create a conversation without starting model execution."""
    return await request.app.state.studio.repository.new_session(body.title)


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str, request: Request):
    """Read committed history and the active Run identifier."""
    return await request.app.state.studio.repository.session(session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request) -> None:
    """Delete an idle conversation and its owned execution evidence."""
    await request.app.state.studio.delete_session(session_id)


@router.get("/sessions/{session_id}/runs", response_model=list[RunView])
async def session_runs(session_id: str, request: Request):
    """List saved Run projections for a conversation."""
    studio = request.app.state.studio
    return [await studio.view(run["id"]) for run in await studio.repository.list_runs(session_id)]


@router.post("/prompts/preview", response_model=Preview)
async def preview(body: PreviewRequest, request: Request):
    """Compile and save a version-bound preview without calling a provider."""
    return await request.app.state.studio.preview(body)


@router.post("/runs", response_model=RunView, status_code=202)
async def start_run(body: StartRun, request: Request, idempotency_key: str = Header()):
    """Schedule a saved preview with an idempotency key."""
    return await request.app.state.studio.start(body, idempotency_key)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, request: Request):
    """Read execution state without creating or resuming work."""
    return await request.app.state.studio.view(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(run_id: str, request: Request):
    """Cancel the local task and preserve any uncertain external effects."""
    return await request.app.state.studio.cancel(run_id)


@router.post("/runs/{run_id}/approvals/{approval_id}", response_model=RunView)
async def approve_run(run_id: str, approval_id: str, body: ApprovalDecision, request: Request):
    """Resolve exactly the pending approval identified in the URL."""
    return await request.app.state.studio.approve(run_id, approval_id, body.decision)


@router.post("/runs/{run_id}/resume", response_model=RunView)
async def resume_run(run_id: str, body: ResumeRequest, request: Request):
    """Resume a checkpoint after explicit reconciliation of uncertain calls."""
    return await request.app.state.studio.resume(run_id, body)


@router.get("/runs/{run_id}/trace")
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
            page = await studio.repository.read_events(run_id, cursor)
            for item in page:
                cursor = item["seq"]
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(redact(item))}\n\n"
            current = await studio.repository.run(run_id)
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
