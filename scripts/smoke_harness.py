"""Verify CLI/RPC, embedding hooks/packages and Messages over actual local HTTP transports."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from rtagent.domain import AppError, ResumeRequest, RunEvent, StudioConfig, ToolCall, ToolDefinition
from rtagent.domain.hooks import Hooks
from rtagent.harness import Harness
from rtagent.infrastructure.config import Settings
from rtagent.infrastructure.workspace.files import Workspace
from rtagent.interfaces.client import AgentClient
from rtagent.interfaces.http.app import create_app


async def command(args: list[str], stdin: bytes | None = None) -> str:
    """Invoke the actual CLI with a bounded wait and preserve failures as acceptance evidence."""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "rtagent",
        *args,
        stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(45):
            output, error = await child.communicate(stdin)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode == 0, error.decode(errors="replace")
    return output.decode("utf-8")


async def embedding(root: Path) -> None:
    """Use real trusted extension callbacks and prove they cannot override denied permissions."""
    seen: list[str] = []
    hits = 0
    effects = 0

    async def observe(event: RunEvent) -> None:
        """Collect committed lifecycle evidence without changing its original payload."""
        seen.append(event.type)

    async def guard(call: ToolCall, _config: StudioConfig) -> str | None:
        """Apply a concrete protected-file rule in addition to the core permission decision."""
        return (
            "Fixture protects protected.txt." if call.args.get("path") == "protected.txt" else None
        )

    async def summary(args: dict, workspace: Workspace) -> str:
        """Inspect actual project files through the same bounded Workspace API."""
        nonlocal hits
        hits += 1
        return json.dumps(
            await asyncio.to_thread(workspace.search_files, args.get("pattern", "**/*.py"))
        )

    async def uncertain_effect(_args: dict, workspace: Workspace) -> str:
        """Produce an actual file effect before failing so recovery must not replay dispatch."""
        nonlocal effects
        effects += 1
        await asyncio.to_thread(workspace.write, "effect.txt", "Created once.")
        raise RuntimeError("Fixture fails after its effect.")

    settings = Settings(
        data_dir=root / "embed-data",
        workspace_root=root / "workspace",
        provider="fake",
        _env_file=None,
    )
    harness = Harness(settings, Hooks(on_event=observe, before_tool=guard))
    harness.registry.register(
        ToolDefinition(
            name="workspace_summary",
            description="Inspect Python source paths.",
            input_schema={
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
        summary,
        writes_workspace=False,
    )
    harness.registry.register(
        ToolDefinition(
            name="uncertain_effect",
            description="Fixture uncertain file effect.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        uncertain_effect,
    )
    try:
        harness.registry.register(
            ToolDefinition(
                name="invalid_schema",
                description="Invalid fixture.",
                input_schema={"type": "invalid"},
            ),
            summary,
        )
        raise AssertionError("Invalid extension schema registered")
    except AppError as exc:
        assert exc.code == "local_tool_schema"
    async with harness:
        config = await harness.repository.config()
        config.tools["workspace_summary"] = "allow"
        config.tools["uncertain_effect"] = "allow"
        await harness.studio.save_config(config)
        started = await harness.run('/tool workspace_summary {"pattern":"**/*.py"}')
        finished = await harness.wait(started.id, 30)
        assert finished.status == "succeeded" and hits == 1, finished
        assert "source.py" in finished.messages[1].content
        protected = await harness.run(
            '/tool write_text {"path":"protected.txt","content":"blocked"}'
        )
        protected = await harness.wait(protected.id, 30)
        assert "Fixture protects" in protected.messages[1].content
        assert not (root / "workspace" / "protected.txt").exists()
        config = await harness.repository.config()
        config.tools["workspace_summary"] = "deny"
        await harness.studio.save_config(config)
        # Fake decisions only choose exposed tools; invoke policy directly through a frozen call scenario later in focused tests.
        assert "workspace_summary" not in {
            item.name for item in harness.registry.selected(await harness.repository.config())
        }
        assert hits == 1
        assert {"run.started", "tool.started", "tool.completed", "run.completed"} <= set(seen), seen
        started = await harness.run("/tool uncertain_effect {}")
        interrupted = await harness.wait(started.id, 30)
        assert (
            interrupted.status == "interrupted"
            and effects == 1
            and len(interrupted.unknown_calls) == 1
        )
        call_id = interrupted.unknown_calls[0]["id"]
        await harness.resume(
            started.id,
            ResumeRequest(
                resolutions={
                    call_id: {
                        "action": "record",
                        "result": "Verified effect.txt contains Created once.",
                    }
                }
            ),
        )
        assert (await harness.wait(started.id, 30)).status == "succeeded" and effects == 1
    print(
        "PASS embedding: shared graph, trusted file tool, committed event hook, additional denial",
        flush=True,
    )


def messages_fixture() -> FastAPI:
    """Serve the actual Messages event protocol with a tool-use round trip, without a real model."""
    app = FastAPI()
    bodies: list[dict] = []
    app.state.bodies = bodies

    @app.post("/v1/messages")
    async def messages(request: Request) -> StreamingResponse:
        """Check role extraction and tool pairing before emitting incremental stream fragments."""
        body = await request.json()
        bodies.append(body)
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert body["system"] and all(value["role"] != "system" for value in body["messages"])
        paired = any(
            block["type"] == "tool_result"
            for value in body["messages"]
            for block in value["content"]
        )
        if paired:
            result = next(
                block
                for value in body["messages"]
                for block in value["content"]
                if block["type"] == "tool_result"
            )
            assert (
                result["tool_use_id"] == "toolu_fixture_1"
                and "fixture evidence" in result["content"]
            )
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_fixture",
                    "model": "fixture-messages",
                    "usage": {"input_tokens": 42, "output_tokens": 0},
                },
            }
        ]
        if paired:
            events += [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Verified fixture evidence."},
                },
                {"type": "content_block_stop", "index": 0},
            ]
        else:
            events += [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_fixture_1",
                        "name": "read_text",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '"evidence.txt"}'},
                },
                {"type": "content_block_stop", "index": 0},
            ]
        events += [
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn" if paired else "tool_use"},
                "usage": {"output_tokens": 17},
            },
            {"type": "message_stop"},
        ]

        async def stream():
            """Use genuine streamed HTTP bytes so the adapter must parse SSE and JSON deltas."""
            for event in events:
                yield "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                await asyncio.sleep(0.01)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


async def http_workflow(base_url: str, fixture: FastAPI) -> None:
    """Exercise all external entry points and package reuse against the same listening server."""
    async with AgentClient(base_url) as client:
        result = json.loads(
            await command(["--url", base_url, "run", "/read evidence.txt", "--wait", "--json"])
        )
        assert (
            result["status"] == "succeeded"
            and "fixture evidence" in result["messages"][1]["content"]
        )
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "run.get", "params": {"run_id": result["id"]}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "events.list",
                "params": {"run_id": result["id"]},
            },
            {"jsonrpc": "2.0", "id": 3, "method": "rpc.schema"},
        ]
        responses = [
            json.loads(line)
            for line in (
                await command(
                    ["--url", base_url, "rpc"],
                    ("\n".join(json.dumps(value) for value in requests) + "\n").encode(),
                )
            ).splitlines()
        ]
        assert responses[0]["result"]["id"] == result["id"]
        assert (
            responses[1]["result"][-1]["seq"] > 0
            and "run.start" in responses[2]["result"]["methods"]
        )
        invalid = [
            "{",
            json.dumps({"jsonrpc": "2.0", "id": True, "method": "rpc.schema"}),
            json.dumps({"jsonrpc": "2.0", "id": "unknown", "method": "missing"}),
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "run.submit", "params": {"task": ""}}),
        ]
        errors = [
            json.loads(line)
            for line in (
                await command(["--url", base_url, "rpc"], ("\n".join(invalid) + "\n").encode())
            ).splitlines()
        ]
        assert [value["error"]["code"] for value in errors] == [
            -32700,
            -32600,
            -32601,
            -32602,
        ], errors
        package = await client.request("GET", "/presets/coding/export")
        imported = await client.request("POST", "/presets/import", package)
        assert imported["id"] != package["preset"]["id"] and imported["revision"] == 1
        assert imported["blocks"][0]["id"] != package["preset"]["blocks"][0]["id"]
        skill = {
            "format": "rtagent.skill.v1",
            "name": "fixture-review",
            "files": {
                "SKILL.md": "---\nname: fixture-review\ndescription: Review fixture evidence.\n---\n\nRead resources/guide.md and evidence.txt.\n",
                "resources/guide.md": "Cite the source file and distinguish checks from assumptions.\n",
            },
        }
        saved = await client.request("POST", "/workspace/skills/import", skill)
        exported = await client.request("GET", "/workspace/skills/fixture-review/export")
        assert exported == skill and not saved["builtin"]
        started = await client.run(
            "Use the fixture-review skill.", preset_id=imported["id"], skills=["fixture-review"]
        )
        assert (await client.wait(started.id, 30)).status == "succeeded"
        trace = await client.request("GET", f"/runs/{started.id}/trace?raw=true")
        assert any(
            value["source"] == "skill:fixture-review"
            for value in trace["calls"][0]["data"]["canonical_messages"]
        )
        malicious = {
            **skill,
            "name": "invalid-import",
            "files": {"SKILL.md": skill["files"]["SKILL.md"], "../escape.txt": "bad"},
        }
        try:
            await client.request("POST", "/workspace/skills/import", malicious)
            raise AssertionError("Traversal package accepted")
        except AppError as exc:
            assert exc.status in {403, 422}
        boot = await client.request("GET", "/bootstrap")
        config = boot["config"]
        config["providers"].append(
            {
                "id": "fixture-messages",
                "name": "Messages fixture",
                "adapter": "anthropic",
                "base_url": "http://127.0.0.1:8773/v1",
                "credential_ref": "",
                "models": ["fixture-messages"],
            }
        )
        config["provider"].update(
            profile_id="fixture-messages", adapter="anthropic", model="fixture-messages"
        )
        await client.request("PUT", "/config", config)
        started = await client.run("Read evidence.txt and report it.")
        finished = await client.wait(started.id, 30)
        assert (
            finished.status == "succeeded"
            and finished.messages[-1].content == "Verified fixture evidence."
        ), finished
        trace = await client.request("GET", f"/runs/{started.id}/trace?raw=true")
        calls = [value for value in trace["calls"] if value["kind"] == "model"]
        assert len(calls) == 2 and len(fixture.state.bodies) == 2
        assert all(
            value["data"]["wire_request_body"] == value["data"]["prepared_request"]
            for value in calls
        )
        assert calls[0]["data"]["transformations"]
        assert finished.usage["input_tokens"] == 84 and finished.usage["output_tokens"] == 34
    print(
        "PASS actual HTTP: CLI/RPC projections/events, new preset identities, Skill resources/import/use, Messages SSE/tool pairing/capture/usage",
        flush=True,
    )


async def main() -> None:
    """Keep test services/data separate and await every process/connection during cleanup."""
    with tempfile.TemporaryDirectory(
        prefix="rtagent-harness-", ignore_cleanup_errors=True
    ) as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "source.py").write_text('"""Fixture source."""\n', encoding="utf-8")
        (workspace / "evidence.txt").write_text("fixture evidence", encoding="utf-8")
        await embedding(root)
        fixture = messages_fixture()
        servers = [
            uvicorn.Server(
                uvicorn.Config(
                    create_app(
                        Settings(
                            data_dir=root / "http-data",
                            workspace_root=workspace,
                            provider="fake",
                            _env_file=None,
                        )
                    ),
                    host="127.0.0.1",
                    port=8772,
                    log_level="error",
                )
            ),
            uvicorn.Server(uvicorn.Config(fixture, host="127.0.0.1", port=8773, log_level="error")),
        ]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        try:
            async with asyncio.timeout(20):
                while not all(server.started for server in servers):
                    await asyncio.sleep(0.05)
            await http_workflow("http://127.0.0.1:8772", fixture)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
