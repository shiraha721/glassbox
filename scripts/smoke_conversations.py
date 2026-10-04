"""Check conversation management through a real local HTTP server with simulated models."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import uvicorn

from rtagent.infrastructure.config import Settings
from rtagent.interfaces.http.app import create_app


async def exercise(client: httpx.AsyncClient) -> None:
    """Verify copy boundaries, independent traces, metadata conflicts, and continued chatting."""

    async def api(method: str, path: str, body=None, status: int = 200):
        response = await client.request(
            method, path, json=body, headers={"Idempotency-Key": uuid4().hex}
        )
        assert response.status_code == status, f"{response.status_code}: {response.text}"
        return response.json() if status != 204 else None

    state = await api("GET", "/bootstrap")
    preset = state["presets"][0]
    source = await api("POST", "/sessions", {"title": "Original conversation"}, 201)

    async def turn(session, text: str):
        run = await api(
            "POST",
            "/runs",
            {
                "session_id": session["id"],
                "expected_session_revision": session["revision"],
                "preset_id": preset["id"],
                "preset_revision": preset["revision"],
                "config_revision": state["config"]["revision"],
                "input": text,
            },
            202,
        )
        async with asyncio.timeout(10):
            while run["status"] not in {"succeeded", "failed", "cancelled"}:
                await asyncio.sleep(0.05)
                run = await api("GET", f'/runs/{run["id"]}')
        assert run["status"] == "succeeded", run["error"]
        return await api("GET", f'/sessions/{session["id"]}'), run

    source, first = await turn(source, "First turn")
    source, second = await turn(source, "Second turn")
    assert source["title"] == "Original conversation", "An explicit title must survive first Run."
    folder = await api("POST", "/folders", {"name": "Investigations"}, 201)
    source = await api(
        "PATCH",
        f'/sessions/{source["id"]}',
        {"expected_revision": source["revision"], "folder_id": folder["id"]},
    )
    await api(
        "PATCH", f'/sessions/{source["id"]}', {"expected_revision": 0, "title": "Stale rename"}, 409
    )
    fork = await api(
        "POST",
        f'/sessions/{source["id"]}/copy',
        {
            "expected_revision": source["revision"],
            "operation": "fork",
            "through_run_id": first["id"],
        },
        201,
    )
    duplicate = await api(
        "POST",
        f'/sessions/{source["id"]}/copy',
        {"expected_revision": source["revision"], "operation": "duplicate"},
        201,
    )
    fork_runs = await api("GET", f'/sessions/{fork["id"]}/runs')
    copied_runs = await api("GET", f'/sessions/{duplicate["id"]}/runs')
    assert len(fork_runs) == 1 and len(copied_runs) == 2
    assert fork_runs[0]["source_run_id"] == first["id"]
    assert fork_runs[0]["id"] != first["id"]
    assert all(message["content"] != "Second turn" for message in fork["messages"])
    assert duplicate["messages"] == source["messages"]
    original_trace = await api("GET", f'/runs/{first["id"]}/trace')
    copied_trace = await api("GET", f'/runs/{fork_runs[0]["id"]}/trace')
    assert len(copied_trace["calls"]) == len(original_trace["calls"]) > 0
    assert copied_trace["calls"][0]["data"]["copied_from_run_id"] == first["id"]
    events = await client.get(f'/runs/{fork_runs[0]["id"]}/events')
    assert "run.copied" in events.text and "model.started" not in events.text
    print(
        "PASS independent duplicate, fork boundary, copied trace provenance, and stale-edit rejection"
    )

    source = await api(
        "PATCH",
        f'/sessions/{source["id"]}',
        {"expected_revision": source["revision"], "archived": True},
    )
    await api(
        "POST",
        "/prompts/preview",
        {
            "session_id": source["id"],
            "expected_session_revision": source["revision"],
            "preset_id": preset["id"],
            "preset_revision": preset["revision"],
            "config_revision": state["config"]["revision"],
            "input": "Blocked",
        },
        409,
    )
    source = await api(
        "PATCH",
        f'/sessions/{source["id"]}',
        {
            "expected_revision": source["revision"],
            "archived": False,
            "title": "Renamed conversation",
        },
    )
    await api(
        "PATCH",
        f'/folders/{folder["id"]}',
        {"name": "Research", "expected_revision": folder["revision"]},
    )
    await api("DELETE", f'/folders/{folder["id"]}', status=204)
    assert (await api("GET", f'/sessions/{fork["id"]}'))["folder_id"] is None
    assert len(await api("GET", f'/sessions/{source["id"]}/runs')) == 2
    await api("DELETE", f'/sessions/{source["id"]}', status=204)
    retained_trace = await api("GET", f'/runs/{fork_runs[0]["id"]}/trace')
    assert len(retained_trace["calls"]) == len(original_trace["calls"]) > 0
    fork = await api("GET", f'/sessions/{fork["id"]}')
    fork, continued = await turn(fork, "Continue the fork")
    assert continued["source_run_id"] is None and len(fork["messages"]) > 2
    assert len(await api("GET", f'/sessions/{duplicate["id"]}/runs')) == 2
    assert second["id"] != continued["id"]
    print(
        "PASS archive/restore, folder rename/delete, source deletion independence, and fork continuation"
    )


async def main() -> None:
    """Own a disposable server and database without changing the user's conversations."""
    Path("var").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="conversation-smoke-", dir="var") as directory:
        root = Path(directory).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        settings = Settings(
            data_dir=root / "data", workspace_root=workspace, provider="fake", _env_file=None
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host="127.0.0.1", port=8767, log_level="warning")
        )
        serving = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8767/api/v1", timeout=20, trust_env=False
            ) as client:
                await exercise(client)
        finally:
            server.should_exit = True
            await serving


if __name__ == "__main__":
    asyncio.run(main())
