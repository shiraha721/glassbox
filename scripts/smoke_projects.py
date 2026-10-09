"""Exercise project workspace binding through real HTTP and native tools without a model service."""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import uvicorn

from glassbox.infrastructure.config import Settings
from glassbox.interfaces.http.app import create_app


async def exercise(client: httpx.AsyncClient, roots: list[Path]) -> None:
    """Verify file/context separation, idle moves, frozen evidence, and busy-session rejection."""

    async def api(method: str, path: str, body=None):
        """Send an actual local request and reject unexpected application errors."""
        response = await client.request(
            method, path, json=body, headers={"Idempotency-Key": uuid4().hex}
        )
        assert response.is_success, f"{response.status_code}: {response.text}"
        return response.json()

    state = await api("GET", "/bootstrap")
    projects = [
        await api("POST", "/projects", {"name": f"Project {index}", "workspace_root": str(root)})
        for index, root in enumerate(roots)
    ]
    assert (
        await client.post("/projects", json={"name": "Duplicate", "workspace_root": str(roots[0])})
    ).status_code == 409
    assert (
        await client.post(
            "/projects", json={"name": "Missing", "workspace_root": str(roots[0] / "missing")}
        )
    ).status_code == 422
    sessions = [
        await api("POST", "/sessions", {"project_id": project["id"]}) for project in projects
    ]

    async def start(session_id: str, text: str):
        """Submit from the current revision, binding the task to its conversation's project."""
        session = await api("GET", f"/sessions/{session_id}")
        preset = state["presets"][0]
        return await api(
            "POST",
            "/runs",
            {
                "session_id": session_id,
                "expected_session_revision": session["revision"],
                "preset_id": preset["id"],
                "preset_revision": preset["revision"],
                "config_revision": state["config"]["revision"],
                "input": text,
            },
        )

    async def finish(run):
        """Wait for actual tool execution rather than treating Run creation as success."""
        async with asyncio.timeout(30):
            while run["status"] not in {"succeeded", "failed", "cancelled"}:
                await asyncio.sleep(0.08)
                run = await api("GET", f'/runs/{run["id"]}')
        assert run["status"] == "succeeded", run.get("error")
        return run

    original = await finish(await start(sessions[0]["id"], "/read README.md"))
    other = await finish(await start(sessions[1]["id"], "/read README.md"))
    assert "project-0" in original["messages"][-1]["content"]
    assert "project-1" in other["messages"][-1]["content"]
    assert original["workspace_root"] == str(roots[0]) and other["workspace_root"] == str(roots[1])
    for index, project in enumerate(projects):
        view = await api("GET", f'/workspace?project_id={project["id"]}')
        assert f"project-{index}" in view["instructions"]["content"]
        await api(
            "PUT",
            f'/workspace/memory?project_id={project["id"]}',
            {"content": f"memory-{index}", "expected_hash": view["memory"]["hash"]},
        )
        await api(
            "PUT",
            f'/workspace/skills?project_id={project["id"]}',
            {
                "name": "project-check",
                "description": f"Check project-{index}.",
                "instructions": f"Read README.md and report project-{index}.",
            },
        )
        skill = await api("GET", f'/workspace/skills/project-check?project_id={project["id"]}')
        assert f"project-{index}" in skill["content"]
        # Memory is Glassbox's data: it is kept per workspace outside the project folder.
        assert not (roots[index] / ".glassbox").exists()
        saved = await api("GET", f'/workspace?project_id={project["id"]}')
        assert saved["memory"]["content"] == f"memory-{index}"
        draft = await api(
            "POST",
            "/prompts/inspect",
            {
                "preset": state["presets"][0],
                "config_revision": state["config"]["revision"],
                "project_id": project["id"],
                "skills": ["project-check"],
            },
        )
        content = "\n".join(message["content"] for message in draft["compiled"]["messages"])
        assert f"memory-{index}" in content and f"report project-{index}" in content
    command_tool = "powershell" if os.name == "nt" else "bash"
    command = (
        "Set-Content -Encoding Ascii -LiteralPath command.txt -Value project-0; [Console]::Write((Get-Content -Raw README.md))"
        if os.name == "nt"
        else "printf project-0 > command.txt; cat README.md"
    )
    await finish(
        await start(sessions[0]["id"], f"/tool {command_tool} " + json.dumps({"command": command}))
    )
    assert (roots[0] / "command.txt").exists() and not (roots[1] / "command.txt").exists()
    await finish(
        await start(sessions[1]["id"], '/tool write_text {"path":"file.txt","content":"project-1"}')
    )
    assert (roots[1] / "file.txt").read_text() == "project-1" and not (
        roots[0] / "file.txt"
    ).exists()
    print(
        "PASS project-specific file tools, native command sandbox, AGENTS, memory, Skills, and draft preview",
        flush=True,
    )

    current = await api("GET", f'/sessions/{sessions[0]["id"]}')
    moved = await api(
        "PATCH",
        f'/sessions/{current["id"]}',
        {"expected_revision": current["revision"], "project_id": projects[1]["id"]},
    )
    future = await finish(await start(moved["id"], "/read README.md"))
    assert "project-1" in future["messages"][-1]["content"]
    frozen = await api("GET", f'/runs/{original["id"]}')
    assert (
        frozen["workspace_root"] == str(roots[0])
        and "project-0" in frozen["messages"][-1]["content"]
    )
    current = await api("GET", f'/sessions/{moved["id"]}')
    copied = await api(
        "POST",
        f'/sessions/{moved["id"]}/copy',
        {
            "expected_revision": current["revision"],
            "operation": "fork",
            "through_run_id": original["id"],
        },
    )
    assert copied["project_id"] == projects[1]["id"]
    copied_run = (await api("GET", f'/sessions/{copied["id"]}/runs'))[0]
    assert copied_run["workspace_root"] == str(roots[0])
    await finish(await start(copied["id"], "/read README.md"))
    slow = await start(sessions[1]["id"], "/slow")
    current = await api("GET", f'/sessions/{sessions[1]["id"]}')
    rejected = await client.patch(
        f'/sessions/{current["id"]}',
        json={"expected_revision": current["revision"], "project_id": projects[0]["id"]},
    )
    assert rejected.status_code == 409
    await api("POST", f'/runs/{slow["id"]}/cancel')
    print(
        "PASS idle workspace changes, immutable historical roots, fork membership, and busy-session rejection",
        flush=True,
    )


async def main() -> None:
    """Own an isolated HTTP server and sibling folders, leaving user projects untouched."""
    Path("var").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="projects-", dir="var") as directory:
        root = Path(directory).resolve()
        workspaces = [root / name for name in ("default", "project-0", "project-1")]
        for workspace in workspaces:
            workspace.mkdir()
            (workspace / "README.md").write_text(workspace.name, encoding="utf-8")
            (workspace / "AGENTS.md").write_text(
                f"Use evidence from {workspace.name}.", encoding="utf-8"
            )
        settings = Settings(
            data_dir=root / "data", workspace_root=workspaces[0], provider="fake", _env_file=None
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host="127.0.0.1", port=8769, log_level="warning")
        )
        serving = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(20):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8769/api/v1", trust_env=False, timeout=30
            ) as client:
                await exercise(client, workspaces[1:])
            assert not (workspaces[0] / ".glassbox").exists()
        finally:
            server.should_exit = True
            await serving


if __name__ == "__main__":
    asyncio.run(main())
