"""Typed local HTTP client used by Python callers, CLI and newline JSON/RPC."""

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from rtagent.domain import AppError, ModelChoice, PreviewRequest, RunView, WorkspaceAccess, uid
from rtagent.domain.lifecycle import TERMINAL


class AgentClient:
    """Attach to the existing service instead of opening another worker on its databases."""

    def __init__(self, base_url: str = "http://127.0.0.1:8001") -> None:
        """Keep transport lifetime explicit and avoid inheriting proxy credentials."""
        self.base_url = base_url.rstrip("/") + "/api/v1"
        self.http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AgentClient":
        """Open one reusable connection pool without contacting a model."""
        if self.http is not None:
            raise AppError("client_open", "Use one active context per AgentClient instance.")
        self.http = httpx.AsyncClient(base_url=self.base_url, trust_env=False, timeout=90)
        return self

    async def __aexit__(self, *_exception: Any) -> None:
        """Detach the client; closing a connection never cancels server-side work."""
        if self.http:
            await self.http.aclose()
            self.http = None

    async def request(
        self, method: str, path: str, body: Any = None, key: str | None = None
    ) -> Any:
        """Exchange relative API JSON and raise the service's safe structured error."""
        if self.http is None:
            raise AppError("client_closed", "Enter the AgentClient context first.")
        # Colons are valid inside local record identifiers (including approval IDs).
        if not path.startswith("/") or path.startswith("//"):
            raise AppError("client_path", "Use a relative API path.")
        try:
            response = await self.http.request(
                method,
                self.base_url + path,
                json=body,
                headers={"Idempotency-Key": key} if key else None,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                "service_connection", f"Local service request failed ({type(exc).__name__}).", 503
            ) from None
        if response.is_error:
            try:
                data = response.json()
                if not isinstance(data, dict):
                    data = {}
            except ValueError:
                data = {}
            error = data.get("error", {})
            if not isinstance(error, dict):
                error = {}
            raise AppError(
                error.get("code", "request_invalid"),
                error.get(
                    "message", f"Local service request failed (HTTP {response.status_code})."
                ),
                response.status_code,
                error.get("details", data.get("detail")),
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise AppError(
                "service_response", "Local service returned an invalid JSON response.", 502
            ) from None

    async def start(self, request: PreviewRequest, key: str) -> RunView:
        """Submit an explicit immutable request; retry this same request/key for idempotency."""
        return RunView.model_validate(
            await self.request("POST", "/runs", request.model_dump(), key)
        )

    async def run(
        self,
        task: str,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
        preset_id: str = "general-agent",
        variables: dict[str, Any] | None = None,
        skills: list[str] | None = None,
        key: str | None = None,
        model: ModelChoice | None = None,
        access: WorkspaceAccess | None = None,
    ) -> RunView:
        """Read current revisions and submit direct Run through the same compiler as Web.

        `model` and `access` choose the model and workspace access for this Run only; by
        default the saved default model and workspace policy apply."""
        boot = await self.request("GET", "/bootstrap")
        if session_id:
            if project_id:
                raise AppError(
                    "session_project", "Move an existing conversation explicitly before running."
                )
            session = await self.request("GET", "/sessions/" + quote(session_id, safe=""))
        else:
            session = await self.request("POST", "/sessions", {"project_id": project_id})
        preset = next((value for value in boot["presets"] if value["id"] == preset_id), None)
        if preset is None:
            raise AppError("preset_not_found", "Preset not found.", 404)
        return await self.start(
            PreviewRequest(
                session_id=session["id"],
                expected_session_revision=session["revision"],
                preset_id=preset["id"],
                preset_revision=preset["revision"],
                config_revision=boot["config"]["revision"],
                input=task,
                variables=variables or {},
                skills=skills or [],
                model=model,
                access=access,
            ),
            key or uid(),
        )

    async def get(self, run_id: str) -> RunView:
        """Read the saved projection without resubmitting execution."""
        return RunView.model_validate(await self.request("GET", "/runs/" + quote(run_id, safe="")))

    async def wait(self, run_id: str, timeout: float = 600) -> RunView:
        """Wait for terminal or attention state; timeout detaches without changing the Run."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    view = await self.get(run_id)
                    if view.status in TERMINAL | {"waiting_approval", "interrupted"}:
                        return view
                    await asyncio.sleep(0.2)
        except TimeoutError:
            raise AppError(
                "wait_timeout", "Run is still active; inspect or cancel it explicitly.", 408
            ) from None

    async def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        """Read at most 200 persisted events after a cursor; no SSE connection is required."""
        return await self.request("GET", f"/runs/{quote(run_id, safe='')}/event-log?after={after}")
