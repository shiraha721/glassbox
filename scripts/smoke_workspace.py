"""Exercise a real local HTTP server and OS tools in a disposable workspace.

Run with `uv run python scripts/smoke_workspace.py`. Model decisions are simulated;
HTTP, SQLite, the tool loop, OS isolation, and filesystem effects are real.
Uses native Windows AppContainer/PowerShell or Linux bubblewrap/Bash. No keys are used.
"""

import asyncio
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import uvicorn

from glassbox.infrastructure.config import Settings
from glassbox.interfaces.http.app import create_app

COMMAND_TOOL = "powershell" if os.name == "nt" else "bash"


def command(windows: str, linux: str) -> str:
    """Use the host's native command syntax without translating model-generated code."""
    return windows if os.name == "nt" else linux


def assert_windows_process_stopped(pid: int) -> None:
    """Check the real child handle; output alone cannot prove process-tree cleanup."""
    import ctypes as C

    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [
        C.c_ulong,
        C.c_int,
        C.c_ulong,
    ], C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_ulong]
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        assert C.get_last_error() == 87, "The child must be gone, not merely inaccessible."
        return
    try:
        assert kernel.WaitForSingleObject(handle, 0) == 0, "A sandbox child is still running."
    finally:
        kernel.CloseHandle(handle)


async def exercise(client: httpx.AsyncClient, workspace: Path) -> None:
    """Submit direct Runs and check persistent results through public HTTP endpoints."""

    async def api(method: str, path: str, body=None, key=None):
        response = await client.request(
            method, path, json=body, headers={"Idempotency-Key": key} if key else {}
        )
        assert response.is_success, f"{response.status_code}: {response.text}"
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
        COMMAND_TOOL,
        {
            "command": command(
                "Set-Content -Encoding Ascii -NoNewline -LiteralPath evidence.txt -Value live-command; "
                "[Console]::Write((Get-Content -Raw evidence.txt)); "
                "if ((Test-Path .env) -or (Test-Path .ENV)) { exit 1 }",
                "printf live-command > evidence.txt; cat evidence.txt; test ! -s .env; test ! -s .ENV",
            )
        },
    )
    assert result["exit_code"] == 0 and result["stdout"] == "live-command", result
    assert (workspace / "evidence.txt").read_text() == "live-command"
    if os.name == "nt":
        # Probe original absolute Windows paths, not merely paths missing from the private copy.
        original = str(workspace).replace("'", "''")
        outside = str(workspace.parent).replace("'", "''")
        _, result = await tool(
            COMMAND_TOOL,
            {
                "command": (
                    f"foreach ($name in @('{original}\\.env', '{original}\\.ENV', '{outside}\\host-only.txt')) {{ "
                    "try { $f = [IO.File]::OpenRead($name); $f.Dispose(); exit 11 } catch {} }; "
                    f"try {{ [IO.File]::WriteAllText('{outside}\\outside-write.txt', 'leak'); exit 12 }} catch {{}}; "
                    "if ($env:GLASSBOX_SMOKE_SECRET) { exit 13 }; [Console]::Write('host-blocked')"
                )
            },
        )
        assert result["exit_code"] == 0 and result["stdout"] == "host-blocked", result
        assert not (workspace.parent / "outside-write.txt").exists()
    # The HTTP server is a real reachable host TCP listener; connect attempts must fail inside.
    _, result = await tool(
        COMMAND_TOOL,
        {
            "command": command(
                "$tcp = [Net.Sockets.TcpClient]::new(); try { "
                "$pending = $tcp.BeginConnect('127.0.0.1', 8766, $null, $null); "
                "$null = $pending.AsyncWaitHandle.WaitOne(2000); "
                "if ($tcp.Connected) { exit 1 }; [Console]::Write('network-blocked') "
                "} finally { $tcp.Dispose() }",
                "if (echo probe > /dev/tcp/127.0.0.1/8766) 2>/dev/null; then exit 1; else echo network-blocked; fi",
            )
        },
    )
    assert result["exit_code"] == 0 and result["stdout"].strip() == "network-blocked", result
    if os.name == "nt":
        child_code = base64.b64encode("Start-Sleep -Seconds 30".encode("utf-16-le")).decode()
        _, result = await tool(
            COMMAND_TOOL,
            {
                "command": (
                    "$child = [Diagnostics.ProcessStartInfo]::new(); "
                    "$child.FileName = $env:SystemRoot + '\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'; "
                    "$child.WorkingDirectory = $env:GLASSBOX_WORKSPACE; "
                    "$child.UseShellExecute = $false; $child.CreateNoWindow = $true; "
                    f"$child.Arguments = '-NoProfile -NonInteractive -EncodedCommand {child_code}'; "
                    "$process = [Diagnostics.Process]::Start($child); [Console]::Write($process.Id)"
                )
            },
        )
        assert result["exit_code"] == 0, result
        assert_windows_process_stopped(int(result["stdout"]))
    _, result = await tool("read_text", {"path": "evidence.txt"})
    await tool(
        "write_text",
        {"path": "evidence.txt", "content": "updated", "expected_hash": result["hash"]},
    )
    await tool("edit_text", {"path": "evidence.txt", "old_text": "updated", "new_text": "edited"})
    assert (workspace / "evidence.txt").read_text() == "edited"
    print(
        "PASS direct Run, retry identity, native commands/files, host/network isolation, child termination"
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

    _, result = await tool(
        COMMAND_TOOL,
        {
            "command": command(
                "Start-Sleep -Seconds 3; Set-Content -LiteralPath timeout-leak -Value leak",
                "sleep 3; touch timeout-leak",
            ),
            "timeout": 1.2,
        },
    )
    assert result["timed_out"]
    cancel_args = {
        "command": command(
            "Start-Sleep -Seconds 3; Set-Content -LiteralPath cancellation-leak -Value leak",
            "sleep 3; touch cancellation-leak",
        )
    }
    run = await start(f"/tool {COMMAND_TOOL} {json.dumps(cancel_args)}")
    async with asyncio.timeout(15):
        while True:
            trace = await api("GET", f'/runs/{run["id"]}/trace')
            if any(
                call["kind"] == "tool" and call["status"] == "executing" for call in trace["calls"]
            ):
                break
            await asyncio.sleep(0.05)
    await asyncio.sleep(1)
    cancelled = await api("POST", f'/runs/{run["id"]}/cancel')
    assert cancelled["status"] == "cancelled"
    await asyncio.sleep(3)
    assert (
        not (workspace / "timeout-leak").exists() and not (workspace / "cancellation-leak").exists()
    )
    state["config"]["sandbox_mode"] = "read-only"
    state["config"] = await api("PUT", "/config", state["config"])
    _, result = await tool(
        COMMAND_TOOL,
        {
            "command": command(
                "Set-Content -LiteralPath readonly-leak -Value leak", "touch readonly-leak"
            )
        },
    )
    assert result["exit_code"] != 0 and not (workspace / "readonly-leak").exists()
    print("PASS command timeout, cancellation cleanup, and OS read-only enforcement")


async def main() -> None:
    """Own a temporary server, database, and workspace for the complete live check."""
    Path("var").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="smoke-", dir="var") as directory:
        root = Path(directory).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".env").write_text("CANARY=not-for-the-agent\n", encoding="utf-8")
        (root / "host-only.txt").write_text("outside-canary", encoding="utf-8")
        (workspace / "AGENTS.md").write_text("Report evidence accurately.\n", encoding="utf-8")
        settings = Settings(
            data_dir=root / "data",
            workspace_root=workspace,
            provider="fake",
            _env_file=None,
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host="127.0.0.1", port=8766, log_level="warning")
        )
        serving = asyncio.create_task(server.serve())
        previous_canary = os.environ.get("GLASSBOX_SMOKE_SECRET")
        os.environ["GLASSBOX_SMOKE_SECRET"] = "not-forwarded-to-commands"
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
            if previous_canary is None:
                os.environ.pop("GLASSBOX_SMOKE_SECRET", None)
            else:
                os.environ["GLASSBOX_SMOKE_SECRET"] = previous_canary


if __name__ == "__main__":
    asyncio.run(main())
