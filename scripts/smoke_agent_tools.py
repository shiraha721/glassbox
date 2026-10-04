"""Verify native agent tools, presets, and recorded plans through an isolated real HTTP service."""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import httpx
import uvicorn

from rtagent.domain import digest
from rtagent.infrastructure.config import Settings
from rtagent.interfaces.http.app import create_app


async def exercise(client: httpx.AsyncClient, workspace: Path) -> str:
    """Use direct Runs to verify actual guarded files, schemas, plan projections, and read-only policy."""

    async def api(method: str, path: str, body: Any = None) -> Any:
        """Call the public API with a distinct submission identity and safe assertion output."""
        response = await client.request(
            method,
            path,
            json=body,
            headers={"Idempotency-Key": uuid4().hex} if path == "/runs" else {},
        )
        assert response.is_success, f"{response.status_code}: {response.text}"
        return response.json()

    state = await api("GET", "/bootstrap")
    assert {"Security analysis", "General agent", "Coding", "Debugging", "Code review"} <= {
        item["name"] for item in state["presets"]
    }
    assert {"search_files", "search_text", "read_instructions", "apply_patch", "update_plan"} <= {
        item["name"] for item in state["tools"]
    }
    preset = next(item for item in state["presets"] if item["id"] == "coding")

    async def tool(name: str, args: dict[str, Any]) -> tuple[dict, dict]:
        """Execute simulated model decisions with real policy, files, receipts, and OS commands."""
        session = await api("POST", "/sessions", {"title": "Agent tool smoke"})
        run = await api(
            "POST",
            "/runs",
            {
                "session_id": session["id"],
                "expected_session_revision": session["revision"],
                "preset_id": preset["id"],
                "preset_revision": preset["revision"],
                "config_revision": state["config"]["revision"],
                "input": f"/tool {name} {json.dumps(args)}",
            },
        )
        async with asyncio.timeout(30):
            while run["status"] not in {"succeeded", "failed", "cancelled", "waiting_approval"}:
                await asyncio.sleep(0.05)
                run = await api("GET", f'/runs/{run["id"]}')
        assert run["status"] == "succeeded", run.get("error")
        results = [item["content"] for item in run["messages"] if item["role"] == "tool"]
        return run, json.loads(results[0]) if results else {"not_exposed": True}

    _, found = await tool("search_files", {"pattern": "**/*.txt"})
    assert "src/deep/value.txt" in found["files"] and "root.txt" in found["files"]
    assert all(".venv" not in path and "var/" not in path for path in found["files"])
    _, found = await tool(
        "search_text", {"query": "marker", "pattern": "**/*.txt", "case_sensitive": False}
    )
    assert any(
        item["path"] == "src/deep/value.txt" and item["line"] == 2 for item in found["matches"]
    )
    _, hidden = await tool("search_text", {"query": "DO_NOT_SEARCH"})
    assert not hidden["matches"]
    _, limited = await tool("search_files", {"limit": 1})
    assert len(limited["files"]) == 1 and limited["truncated"]
    _, content = await tool("read_text", {"path": "large.txt", "start_line": 250, "line_count": 3})
    assert (
        content["start_line"] == 250 and content["end_line"] == 252 and content["next_line"] == 253
    )
    assert content["hash"] == digest((workspace / "large.txt").read_text(encoding="utf-8"))
    assert content["content"].startswith("line 249:")
    _, chain = await tool("read_instructions", {"path": "src/deep/value.txt"})
    assert [item["path"] for item in chain["documents"]] == [
        "AGENTS.md",
        "src/AGENTS.md",
        "src/deep/AGENTS.override.md",
    ]
    _, denied = await tool("read_text", {"path": ".env"})
    assert denied["error"]["code"] == "tool_denied"
    _, denied = await tool("search_files", {"path": "../"})
    assert denied["error"]["code"] == "tool_denied"
    print(
        "PASS recursive search, line ranges/full hashes, scoped instructions, and exclusions",
        flush=True,
    )

    original = (workspace / "root.txt").read_bytes()
    _, content = await tool("read_text", {"path": "root.txt"})
    _, removed = await tool("read_text", {"path": "unused.txt"})
    patch = "*** Begin Patch\n*** Update File: root.txt\n@@\n-MARKER old\n+MARKER fixed\n*** Add File: created.txt\n+created by patch\n*** Delete File: unused.txt\n*** End Patch"
    _, rejected = await tool(
        "apply_patch",
        {"patch": patch, "expected_hashes": {"root.txt": content["hash"], "unused.txt": "stale"}},
    )
    assert rejected["error"]["code"] == "file_conflict"
    assert (workspace / "root.txt").read_bytes() == original and not (
        workspace / "created.txt"
    ).exists()
    _, applied = await tool(
        "apply_patch",
        {
            "patch": patch,
            "expected_hashes": {"root.txt": content["hash"], "unused.txt": removed["hash"]},
        },
    )
    assert len(applied["files"]) == 3 and applied["atomic"] is False
    assert (workspace / "root.txt").read_bytes() == original.replace(b"MARKER old", b"MARKER fixed")
    assert (workspace / "created.txt").read_text() == "created by patch\n" and not (
        workspace / "unused.txt"
    ).exists()
    _, duplicate = await tool("read_text", {"path": "duplicates.txt"})
    _, conflict = await tool(
        "apply_patch",
        {
            "patch": "*** Begin Patch\n*** Update File: duplicates.txt\n@@\n-repeat\n+changed\n*** End Patch",
            "expected_hashes": {"duplicates.txt": duplicate["hash"]},
        },
    )
    assert conflict["error"]["code"] == "patch_conflict"
    _, traversal = await tool(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** Add File: ../outside.txt\n+bad\n*** End Patch"},
    )
    assert traversal["error"]["code"] == "tool_denied"
    print(
        "PASS guarded multi-file add/update/delete, preflight rejection, line endings, and exact contexts",
        flush=True,
    )

    plan = {
        "explanation": "Verify the actual edited files",
        "plan": [
            {"step": "Inspect files", "status": "completed"},
            {"step": "Verify result", "status": "in_progress"},
        ],
    }
    planned, result = await tool("update_plan", plan)
    assert planned["plan"] == result == plan
    trace = await api("GET", f'/runs/{planned["id"]}/trace?raw=true')
    assert any(
        item["kind"] == "tool"
        and item["data"]["name"] == "update_plan"
        and item["status"] == "succeeded"
        for item in trace["calls"]
    )
    copy = await api(
        "POST",
        f'/sessions/{planned["session_id"]}/copy',
        {"operation": "duplicate", "expected_revision": 1},
    )
    assert (await api("GET", f'/sessions/{copy["id"]}/runs'))[0]["plan"] == plan
    _, invalid = await tool(
        "update_plan",
        {
            "plan": [
                {"step": "One", "status": "in_progress"},
                {"step": "Two", "status": "in_progress"},
            ]
        },
    )
    assert invalid["error"]["code"] == "invalid_tool_arguments"

    config = {**state["config"], "sandbox_mode": "read-only"}
    state["config"] = await api("PUT", "/config", config)
    inspection = await api(
        "POST",
        "/prompts/inspect",
        {"preset": preset, "config_revision": state["config"]["revision"]},
    )
    assert "apply_patch" not in {item["name"] for item in inspection["tools"]}
    assert any(
        item["source"] == "runtime:environment" for item in inspection["compiled"]["messages"]
    )
    _, denied = await tool(
        "apply_patch", {"patch": "*** Begin Patch\n*** Add File: denied.txt\n+bad\n*** End Patch"}
    )
    assert denied["not_exposed"] and not (workspace / "denied.txt").exists()
    _, result = await tool(
        "powershell" if os.name == "nt" else "bash",
        {
            "command": (
                "[Console]::Write((Get-Content -Raw root.txt))"
                if os.name == "nt"
                else "cat root.txt"
            )
        },
    )
    assert result["exit_code"] == 0 and "MARKER fixed" in result["stdout"]
    print(
        "PASS plan receipts/copies, read-only exposure, and native command verification", flush=True
    )
    return planned["id"]


async def main() -> None:
    """Own port 8770 and temporary data, then reopen the same store to check durable projections."""
    Path("var").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="agent-tools-", dir="var") as directory:
        root = Path(directory).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        fixtures = {
            "AGENTS.md": "Use the native tool language and preserve unrelated files.\n",
            "src/AGENTS.md": "Keep module interfaces stable.\n",
            "src/deep/AGENTS.md": "This is replaced by the local override.\n",
            "src/deep/AGENTS.override.md": "Keep this directory's evidence in English.\n",
            "src/deep/value.txt": "value\nMARKER deep\n",
            "unused.txt": "Remove only with its observed hash.\n",
            "duplicates.txt": "repeat\nrepeat\n",
            "large.txt": "".join(
                f"line {i}: " + "readable detail " * 12 + "\n" for i in range(600)
            ),
            ".env": "DO_NOT_SEARCH",
            ".venv/hidden.txt": "DO_NOT_SEARCH",
            "var/private.txt": "DO_NOT_SEARCH",
        }
        for name, text in fixtures.items():
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        (workspace / "root.txt").write_bytes(b"unchanged\nMARKER old\r\nlast line without newline")
        settings = Settings(
            data_dir=root / "data", workspace_root=workspace, provider="fake", _env_file=None
        )
        run_id = ""
        for iteration in range(2):
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(settings), host="127.0.0.1", port=8770, log_level="warning"
                )
            )
            serving = asyncio.create_task(server.serve())
            try:
                async with asyncio.timeout(20):
                    while not server.started:
                        if serving.done():
                            await serving
                            raise RuntimeError("The smoke service exited before startup.")
                        await asyncio.sleep(0.05)
                async with httpx.AsyncClient(
                    base_url="http://127.0.0.1:8770/api/v1", trust_env=False, timeout=30
                ) as client:
                    if iteration == 0:
                        run_id = await exercise(client, workspace)
                    else:
                        response = await client.get(f"/runs/{run_id}")
                        response.raise_for_status()
                        assert response.json()["plan"]["plan"][1]["status"] == "in_progress"
                        print("PASS plan projection survives service restart", flush=True)
            finally:
                server.should_exit = True
                await serving


if __name__ == "__main__":
    asyncio.run(main())
