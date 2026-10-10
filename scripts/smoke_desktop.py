"""Accept folder selection through actual HTTP, optionally driving only its own Windows dialog."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn
from fastapi.responses import Response

from glassbox.domain import AppError
from glassbox.infrastructure.config import Settings
from glassbox.interfaces.client import AgentClient
from glassbox.interfaces.http.app import create_app


def dialog_window(pid: int) -> int | None:
    """Locate this helper's native window by PID and title, never a window from another application."""
    if sys.platform != "win32":
        raise RuntimeError("The native dialog check runs only on Windows.")
    import ctypes
    from ctypes import wintypes

    user = ctypes.WinDLL("user32")
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found: list[int] = []

    @callback
    def visit(hwnd: int, _parameter: int) -> bool:
        """Collect only the known acceptance dialog so a user's other windows remain untouched."""
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd, title, 256)
        if owner.value == pid and title.value == "Select workspace folder":
            found.append(hwnd)
        return True

    user.EnumWindows(visit, 0)
    return found[0] if found else None


def answer_dialog(window: int, cancel: bool) -> None:
    """Activate Select/Cancel through the known dialog's own command, without keyboard injection."""
    if sys.platform != "win32":
        raise RuntimeError("The native dialog check runs only on Windows.")
    import ctypes
    from ctypes import wintypes

    user = ctypes.WinDLL("user32")
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    assert user.PostMessageW(window, 0x111, 2 if cancel else 1, 0)


async def exercise(client: httpx.AsyncClient, app, root: Path, native: bool) -> None:
    """Verify distinct outcomes, UI-helper cleanup, registration and concurrent service availability."""
    picker = app.state.folder_picker
    invalid = await client.post("/sessions", json={"title": "   "})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "request_validation"
    assert all("input" not in item for item in invalid.json()["error"]["details"])
    fault = await client.get("/acceptance/error")
    assert fault.status_code == 500 and fault.json()["error"]["code"] == "internal_error"
    assert fault.json()["error"]["request_id"]
    assert "fixture-diagnostic" not in fault.text
    assert (await client.get("/sessions/missing/runs")).status_code == 404
    async with AgentClient("http://127.0.0.1:8776") as attached:
        try:
            await attached.request("GET", "/acceptance/plain-error")
        except AppError as error:
            assert error.status == 502 and "HTTP 502" in error.message
        else:
            raise AssertionError("Expected a structured client failure.")
        assert (await attached.request("GET", "/health"))["status"] == "ok"
    print(
        "PASS real HTTP validation, internal errors, plain-error client handling and continued requests",
        flush=True,
    )

    config = (await client.get("/bootstrap")).json()["config"]
    config["sandbox_mode"] = "read-only"
    assert (await client.put("/config", json=config)).status_code == 200
    imported = await client.post(
        "/workspace/skills/import",
        json={
            "format": "glassbox.skill.v1",
            "name": "manual-workspace-notes",
            "files": {
                "SKILL.md": "---\nname: manual-workspace-notes\ndescription: Review project notes.\n---\nRead the existing project memory.\n"
            },
        },
    )
    assert imported.status_code == 201, imported.text
    assert (root / ".agents/skills/manual-workspace-notes/SKILL.md").is_file()
    print(
        "PASS explicit user Skill import remains usable with read-only agent tool settings",
        flush=True,
    )
    command = picker._command
    try:
        picker._command = lambda _initial: None
        response = await client.post("/desktop/folder-picker", json={"initial_path": str(root)})
        assert response.status_code == 200 and response.json()["status"] == "unavailable"
        assert response.json()["path"] is None
        print("PASS HTTP manual fallback when no native desktop is available", flush=True)
    finally:
        picker._command = command

    if not native:
        return
    assert os.name == "nt", "--native acceptance currently drives Windows dialogs only."
    for action in ("select", "cancel", "disconnect", "shutdown"):
        pending = asyncio.create_task(
            client.post("/desktop/folder-picker", json={"initial_path": str(root)})
        )
        async with asyncio.timeout(15):
            while not picker.process or not (window := dialog_window(picker.process.pid)):
                if pending.done():
                    raise AssertionError((await pending).text)
                await asyncio.sleep(0.05)
            process = picker.process
            assert (await client.get("/health")).status_code == 200
            assert (await client.post("/desktop/folder-picker", json={})).status_code == 409
            if action == "disconnect":
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                while picker.process:
                    await asyncio.sleep(0.05)
            elif action == "shutdown":
                await picker.close()
                await asyncio.gather(pending, return_exceptions=True)
            else:
                answer_dialog(window, action == "cancel")
                result = (await pending).json()
                assert result["status"] == ("selected" if action == "select" else "cancelled")
                if action == "select":
                    assert Path(result["path"]) == root
                    project = await client.post(
                        "/projects",
                        json={"name": "Selected workspace", "workspace_root": result["path"]},
                    )
                    assert project.status_code == 201 and project.json()["workspace_root"] == str(
                        root
                    )
            assert process.returncode is not None and not dialog_window(process.pid)
            print(
                f"PASS actual Windows dialog {action}, ownership and service responsiveness",
                flush=True,
            )


async def main(native: bool) -> None:
    """Start an isolated listener and keep acceptance data outside the user's application database."""
    with TemporaryDirectory(prefix="glassbox-desktop-") as temporary:
        root = Path(temporary).resolve()
        workspace = root / "Workspace with spaces and Unicode λ"
        workspace.mkdir()
        app = create_app(
            Settings.without_env_file(
                data_dir=root / "data", workspace_root=workspace, provider="fake"
            )
        )

        @app.get("/api/v1/acceptance/error")
        async def fault() -> None:
            """Inject a service failure through real HTTP without returning internal diagnostics."""
            raise RuntimeError("fixture-diagnostic")

        @app.get("/api/v1/acceptance/plain-error")
        async def plain_error() -> Response:
            """Represent an older or intermediary service that sends a non-JSON error body."""
            return Response("plain failure", status_code=502)

        # Acceptance-only routes precede the application SPA catch-all, as normal API routes do.
        acceptance_routes = app.router.routes[-2:]
        app.router.routes[-2:] = []
        app.router.routes[0:0] = acceptance_routes

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8776, log_level="critical")
        )
        serving = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(20):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8776/api/v1", trust_env=False, timeout=20
            ) as client:
                await exercise(client, app, workspace, native)
        finally:
            server.should_exit = True
            await serving


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native", action="store_true", help="Open and accept/cancel owned Windows dialogs."
    )
    asyncio.run(main(parser.parse_args().native))
