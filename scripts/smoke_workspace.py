"""Exercise a real local HTTP server and OS tools in a disposable workspace.

Run with `uv run python scripts/smoke_workspace.py`. Model decisions are simulated;
HTTP, SQLite, the tool loop, Bash isolation, and filesystem effects are real.
Requires the configured Linux/WSL bubblewrap installation. No credentials are used.
"""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import uvicorn

from rtagent.config import Settings
from rtagent.main import create_app


async def exercise(client: httpx.AsyncClient, workspace: Path) -> None:
    """Submit direct Runs and check persistent results through public HTTP endpoints."""

    async def api(method: str, path: str, body=None, key=None):
        response = await client.request(
            method, path, json=body, headers={"Idempotency-Key": key} if key else {}
        )
        response.raise_for_status()
        return response.json()

    state = await api("GET", "/bootstrap")
    view = await api("GET", "/workspace")
    assert view["sandbox"]["ready"], view["sandbox"]
    assert any(skill["name"] == "skill-creator" for skill in view["skills"])

    async def start(text: str, skills=None):
        session = await api("POST", "/sessions", {"title": "Live smoke"})
        body = {
            "session_id": session["id"],
            "expected_session_revision": session["revision"],
            "preset_id": state["presets"][0]["id"],
            "preset_revision": state["presets"][0]["revision"],
            "config_revision": state["config"]["revision"],
            "input": text,
            "skills": skills or [],
        }
        key = uuid4().hex
        run = await api("POST", "/runs", body, key)
        repeated = await api("POST", "/runs", body, key)
        assert repeated["id"] == run["id"]
        return run

    async def finish(run):
        async with asyncio.timeout(25):
            while run["status"] not in {"succeeded", "failed", "cancelled"}:
                await asyncio.sleep(0.08)
                run = await api("GET", f'/runs/{run["id"]}')
        assert run["status"] == "succeeded", run.get("error")
        return run

    async def tool(name: str, args: dict, skills=None):
        run = await finish(await start(f"/tool {name} {json.dumps(args)}", skills))
        result = next(
            message["content"] for message in run["messages"] if message["role"] == "tool"
        )
        return run, json.loads(result)

    _, result = await tool(
        "bash",
        {
            "command": "printf live-bash > evidence.txt; cat evidence.txt; test ! -e /mnt/c; test ! -e /mnt/d; test ! -s .env; test ! -s .ENV"
        },
    )
    assert result["exit_code"] == 0 and result["stdout"] == "live-bash", result
    assert (workspace / "evidence.txt").read_text() == "live-bash"
    # A real host TCP listener must be unreachable from the private network namespace.
    _, result = await tool(
        "bash",
        {
            "command": "if (echo probe > /dev/tcp/127.0.0.1/8766) 2>/dev/null; then exit 1; else echo network-blocked; fi"
        },
    )
    assert result["stdout"].strip() == "network-blocked"
    _, result = await tool("read_text", {"path": "evidence.txt"})
    await tool(
        "write_text",
        {"path": "evidence.txt", "content": "updated", "expected_hash": result["hash"]},
    )
    await tool("edit_text", {"path": "evidence.txt", "old_text": "updated", "new_text": "edited"})
    assert (workspace / "evidence.txt").read_text() == "edited"
    print(
        "PASS direct Run, idempotent retry, actual Bash/file tools, hidden host files, network isolation"
    )

    _, memory = await tool("read_memory", {})
    await tool(
        "save_memory",
        {"content": "Use structured evidence in reports.", "expected_hash": memory["hash"]},
    )
    await tool(
        "save_skill",
        {
            "name": "evidence-review",
            "description": "Review workspace evidence for a report.",
            "instructions": "Read the evidence, state findings, and cite file paths.",
        },
        ["skill-creator"],
    )
    _, skill = await tool("load_skill", {"name": "evidence-review"})
    assert skill["name"] == "evidence-review"
    run = await finish(await start("$evidence-review review the project"))
    trace = await api("GET", f'/runs/{run["id"]}/trace')
    sources = {item["source"] for item in trace["calls"][0]["data"]["canonical_messages"]}
    assert {"workspace:AGENTS.md", "workspace:memory", "skill:evidence-review"} <= sources
    assert (workspace / ".agents/skills/evidence-review/SKILL.md").exists()
    print(
        "PASS memory persistence, skill-creator selection, skill save/load, recorded context provenance"
    )

    _, result = await tool("bash", {"command": "sleep 3; touch timeout-leak", "timeout": 0.8})
    assert result["timed_out"]
    run = await start('/tool bash {"command":"sleep 3; touch cancellation-leak"}')
    async with asyncio.timeout(15):
        while True:
            trace = await api("GET", f'/runs/{run["id"]}/trace')
            if any(
                call["kind"] == "tool" and call["status"] == "executing" for call in trace["calls"]
            ):
                break
            await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    cancelled = await api("POST", f'/runs/{run["id"]}/cancel')
    assert cancelled["status"] == "cancelled"
    await asyncio.sleep(3)
    assert (
        not (workspace / "timeout-leak").exists() and not (workspace / "cancellation-leak").exists()
    )
    state["config"]["sandbox_mode"] = "read-only"
    state["config"] = await api("PUT", "/config", state["config"])
    _, result = await tool("bash", {"command": "touch readonly-leak"})
    assert result["exit_code"] != 0 and not (workspace / "readonly-leak").exists()
    print("PASS Bash timeout, cancellation process cleanup, and read-only mount enforcement")


async def main() -> None:
    """Own a temporary server, database, and workspace for the complete live check."""
    Path("var").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="smoke-", dir="var") as directory:
        root = Path(directory).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".env").write_text("CANARY=not-for-the-agent\n", encoding="utf-8")
        (workspace / "AGENTS.md").write_text("Report evidence accurately.\n", encoding="utf-8")
        settings = Settings(
            data_dir=root / "data",
            workspace_root=workspace,
            provider="fake",
            _env_file=None,
            wsl_distro=Settings().wsl_distro,
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host="127.0.0.1", port=8766, log_level="warning")
        )
        serving = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8766/api/v1", timeout=30, trust_env=False
            ) as client:
                await exercise(client, workspace)
        finally:
            server.should_exit = True
            await serving


if __name__ == "__main__":
    asyncio.run(main())
