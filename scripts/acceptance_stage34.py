"""Opt-in real-model acceptance for daily-agent work and the shared extensible harness."""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from rtagent.client import AgentClient
from rtagent.config import Settings, resolve_secret
from rtagent.domain import ProviderConfig, StudioConfig, ToolCall, ToolDefinition
from rtagent.harness import Harness
from rtagent.hooks import Hooks
from rtagent.main import create_app
from rtagent.workspace import Workspace

FIXTURES = {
    "AGENTS.md": "This project is RIVER. Preserve check.py, data/*.csv and KEEP.txt. Use English docstrings/comments. Use the native Windows sandbox, not host execution. Python is explicitly available through the trusted runtime configuration. Workspace commands have no network.\n",
    "pipeline/__init__.py": '"""CSV event reporting."""\n',
    "pipeline/records.py": '"""Read event rows."""\nfrom pathlib import Path\n\ndef records(path: str) -> list[dict[str, str]]:\n    """Read CSV records from the supplied file."""\n    lines = Path(path).read_text().splitlines()\n    return [dict(zip(lines[0].split(","), line.split(","))) for line in lines[1:]]\n',
    "pipeline/aggregate.py": '"""Summarize event rows."""\n\ndef summarize(rows: list[dict[str, str]]) -> dict:\n    """Return counts and distinct users."""\n    counts = {}\n    users = set()\n    for row in rows:\n        action = row["action"]\n        counts[action] = counts.get(action, 0) + 1\n        users.add(row["user"])\n    return {"counts": counts, "users": sorted(users), "total": len(rows)}\n',
    "cli.py": '"""Print one JSON event report."""\nimport json\nimport sys\nfrom pipeline.records import records\nfrom pipeline.aggregate import summarize\n\nif __name__ == "__main__":\n    print(json.dumps(summarize(records("data/events.csv"))))\n',
    "worker.py": '"""Receive one JSON report request per stdin line."""\nimport json\nimport sys\nfrom pipeline.records import records\nfrom pipeline.aggregate import summarize\n\nprint("READY", flush=True)\nfor line in sys.stdin:\n    request = json.loads(line)\n    print(json.dumps(summarize(records(request["path"]))), flush=True)\n',
    "data/events.csv": 'user,action\n Alice , Login \nbob,LOGIN\nalice,logout\nbob, logout \ncharlie, login\n,login\ndana,\n"Quoted, User",login\n',
    "data/other.csv": "user,action\n Bob , LOGOUT \n",
    "check.py": '"""Independent immutable acceptance check."""\nimport json\nimport subprocess\nimport sys\nfrom pipeline.records import records\nfrom pipeline.aggregate import summarize\n\nexpected = {"counts": {"login": 4, "logout": 2}, "users": ["alice", "bob", "charlie", "quoted, user"], "total": 6}\nassert summarize(records("data/events.csv")) == expected\nreply = subprocess.run([sys.executable, "cli.py", "data/other.csv"], check=True, capture_output=True, text=True)\nassert json.loads(reply.stdout) == {"counts": {"logout": 1}, "users": ["bob"], "total": 1}\nprint("RIVER_CHECK_OK")\n',
    "KEEP.txt": "Unrelated evidence must remain unchanged.\n",
}


async def guard(call: ToolCall, _config: StudioConfig) -> str | None:
    """Add a concrete fixture-file guard without granting any otherwise denied permission."""
    return "Preserve the independent checker." if call.args.get("path") == "check.py" else None


async def workspace_summary(_args: dict, workspace: Workspace) -> str:
    """Expose actual bounded source inventory through an explicit trusted extension."""
    return json.dumps(await asyncio.to_thread(workspace.search_files, "**/*.py"))


def configure(harness: Harness) -> None:
    """Register the same extension for Web/CLI/RPC and embedded execution."""
    harness.registry.register(
        ToolDefinition(
            name="workspace_summary",
            description="List actual project Python sources with bounded scan metadata.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        workspace_summary,
        writes_workspace=False,
    )


async def serve(settings: Settings) -> tuple[uvicorn.Server, asyncio.Task]:
    """Own an isolated service on port 8775 using the real configured provider."""
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, hooks=Hooks(before_tool=guard), configure=configure),
            host="127.0.0.1",
            port=8775,
            log_level="error",
        )
    )
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(20):
        while not server.started:
            if task.done():
                await task
            await asyncio.sleep(0.05)
    return server, task


async def settle(client: AgentClient, run_id: str, approve: bool = True):
    """Wait for actual completion; explicitly approve only this disposable fixture's native tools."""
    async with asyncio.timeout(620):
        while True:
            view = await client.get(run_id)
            if view.status == "waiting_approval" and approve:
                assert view.approval and not str(view.approval["name"]).startswith("mcp_")
                await client.request(
                    "POST",
                    f"/runs/{run_id}/approvals/{view.approval['id']}",
                    {"decision": "approve"},
                )
            elif view.status in {
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "waiting_approval",
            }:
                return view
            await asyncio.sleep(0.2)


async def evidence(client: AgentClient, run_id: str, root: Path, label: str) -> dict:
    """Audit non-simulated requests, immutable payload capture and every tool-result pairing."""
    trace = await client.request("GET", f"/runs/{run_id}/trace?raw=true")
    models = [call for call in trace["calls"] if call["kind"] == "model"]
    assert models and all(call["status"] == "succeeded" for call in models)
    for call in models:
        data = call["data"]
        assert not data["simulated"] and data["wire_request_body"] == data["prepared_request"]
        pending = set()
        for message in data["canonical_messages"]:
            if message["role"] == "assistant":
                assert not pending
                pending.update(tool["id"] for tool in message["tool_calls"])
            elif message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
        assert not pending
    (root / f"{label}.trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tools = [call for call in trace["calls"] if call["kind"] == "tool"]
    result = {
        "label": label,
        "run_id": run_id,
        "model_calls": len(models),
        "tool_calls": len(tools),
        "tools": sorted({call["data"]["name"] for call in tools}),
    }
    print(f"PASS {label}: {len(models)} real requests, {len(tools)} tool calls", flush=True)
    return result


async def main() -> None:
    """Complete substantial repair/process/context/restart work without touching user conversations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Opt into real model usage and fixture-tool approvals."
    )
    args = parser.parse_args()
    if not args.live or os.name != "nt":
        parser.error("Windows real-model acceptance requires --live.")
    async with AgentClient() as source:
        provider = ProviderConfig.model_validate(
            (await source.request("GET", "/bootstrap"))["config"]["provider"]
        )
    if (
        provider.adapter != "openai"
        or not provider.model
        or (provider.credential_ref and not resolve_secret(provider.credential_ref))
    ):
        parser.error("Configure the real OpenAI-compatible provider on port 8001 first.")
    root = (Path("var") / ("stage34-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    for name, text in FIXTURES.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    settings = Settings(
        data_dir=root / "data",
        workspace_root=workspace,
        provider="openai",
        openai_base_url=provider.base_url,
        openai_model=provider.model,
        _env_file=None,
    )
    summaries = []
    server, serving = await serve(settings)
    try:
        async with AgentClient("http://127.0.0.1:8775") as client:
            boot = await client.request("GET", "/bootstrap")
            config = boot["config"]
            config["provider"] = provider.model_copy(
                update={"max_output_tokens": max(4096, provider.max_output_tokens)}
            ).model_dump()
            config["toolchain_roots"] = [str(Path(sys.base_prefix).resolve())]
            config["tools"]["workspace_summary"] = "allow"
            await client.request("PUT", "/config", config)
            package = await client.request("GET", "/presets/coding/export")
            imported = await client.request("POST", "/presets/import", package)
            skill = {
                "format": "rtagent.skill.v1",
                "name": "csv-review",
                "files": {
                    "SKILL.md": "---\nname: csv-review\ndescription: Inspect and repair RIVER CSV reporting.\n---\n\nRead resources/rules.md before changing source files. Verify with the immutable checker.\n",
                    "resources/rules.md": "Use CSV parsing with quoted-field support. Trim/casefold user and action values, skip blank values, sort distinct users and action keys. Preserve data and check.py.\n",
                },
            }
            await client.request("POST", "/workspace/skills/import", skill)
            session = await client.request(
                "POST", "/sessions", {"title": "RIVER daily agent acceptance"}
            )

            async def task(label: str, text: str):
                """Continue one conversation and retain audited evidence for each useful turn."""
                view = await client.run(
                    text, session_id=session["id"], preset_id=imported["id"], skills=["csv-review"]
                )
                view = await settle(client, view.id)
                assert view.status == "succeeded", view.error
                summaries.append(await evidence(client, view.id, root, label))
                return view

            await task(
                "repair",
                "Use workspace_summary and $csv-review to inspect this multi-file CSV reporter. Reproduce check.py in the native sandbox, repair quoted-field CSV parsing, normalization/blank-row handling and cli.py's ignored path argument while keeping public function names. Add meaningful English docstrings/comments, use a short recorded plan and guarded patches. Preserve check.py, CSV fixtures and KEEP.txt. Run python check.py again and report only actual evidence.",
            )
            studio = server.config.app.state.studio
            current = await studio.repository.config()
            checked = json.loads(
                await studio.registry.execute(
                    studio.registry.catalog["powershell"], {"command": "python check.py"}, current
                )
            )
            assert checked["exit_code"] == 0 and "RIVER_CHECK_OK" in checked["stdout"], checked
            await task(
                "background",
                "Start python -u worker.py with start_process in the native sandbox, observe READY with poll_process and leave it waiting for explicit stdin. Report its process ID; do not run the stdin worker as a foreground command or stop it yet.",
            )
            processes = await client.request("GET", f"/sessions/{session['id']}/processes")
            active = next(item for item in processes if item["status"] == "running")
            assert "READY" in active["stdout"], active
            await task(
                "pipe-input",
                f"Use send_process_input on process {active['id']} to send one JSON line {{\"path\":\"data/events.csv\"}} followed by a newline. Poll its actual output and verify total=6, login=4, logout=2 and four normalized users. Then stop_process and verify cleanup. No new process is needed.",
            )
            finished_process = (
                await client.request("GET", f"/sessions/{session['id']}/processes")
            )[0]
            assert (
                finished_process["status"] == "stopped"
                and '"total": 6' in finished_process["stdout"]
            ), finished_process
            original = await client.request("GET", f"/sessions/{session['id']}")
            compaction = await client.request(
                "POST",
                f"/sessions/{session['id']}/compact",
                {
                    "expected_revision": original["revision"],
                    "config_revision": (await client.request("GET", "/bootstrap"))["config"][
                        "revision"
                    ],
                    "keep_recent_turns": 1,
                },
            )
            async with asyncio.timeout(180):
                while True:
                    context = await client.request("GET", f"/sessions/{session['id']}/context")
                    if not context["compacting_id"]:
                        break
                    await asyncio.sleep(0.2)
            assert (
                context["active_compaction_id"] == compaction["id"]
                and context["effective_estimated_tokens"] < context["raw_estimated_tokens"]
            ), context
            reduction = await client.request(
                "GET", f"/sessions/{session['id']}/compactions/{compaction['id']}?redacted=false"
            )
            assert all(
                call["wire_request_body"] == call["prepared_request"] and not call["simulated"]
                for call in reduction["data"]["calls"]
            )
            (root / "compaction.json").write_text(
                json.dumps(reduction, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "rtagent",
                "--url",
                "http://127.0.0.1:8775",
                "run",
                "Continue RIVER after the explicit context summary. Add an optional --action filter to cli.py; normalize its value and filter valid normalized rows before summarizing. Preserve existing usage, source APIs, checker/data and unrelated files. Verify python check.py and python cli.py data/events.csv --action LOGIN in the sandbox; login report must have total=4 and four distinct users. Record project facts/commands in workspace memory after verification.",
                "--session",
                session["id"],
                "--preset",
                imported["id"],
                "--skill",
                "csv-review",
                "--wait",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                output, error = await asyncio.wait_for(child.communicate(), 620)
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            assert child.returncode == 0, error.decode(errors="replace")
            cli_view = json.loads(output)
            assert cli_view["status"] == "succeeded", cli_view
            summaries.append(await evidence(client, cli_view["id"], root, "cli-after-compaction"))
            trace = await client.request("GET", f"/runs/{cli_view['id']}/trace?raw=true")
            assert any(
                value["source"].startswith("context:compaction:")
                for value in trace["calls"][0]["data"]["canonical_messages"]
            )
            result = json.loads(
                await studio.registry.execute(
                    studio.registry.catalog["powershell"],
                    {"command": "python check.py; python cli.py data/events.csv --action LOGIN"},
                    await studio.repository.config(),
                )
            )
            assert result["exit_code"] == 0 and '"total": 4' in result["stdout"], result
            approval = await client.run(
                "Call echo once with text stage4-restart-proof, then report its returned value."
            )
            approval = await settle(client, approval.id, approve=False)
            assert approval.status == "waiting_approval" and approval.approval
            pending = studio.runner.tasks.get(approval.id)
            if pending:
                await asyncio.shield(pending)
    finally:
        server.should_exit = True
        await serving
    server, serving = await serve(settings)
    try:
        async with AgentClient("http://127.0.0.1:8775") as client:
            restored = await client.get(approval.id)
            assert restored.status == "waiting_approval" and restored.approval == approval.approval
            await client.request(
                "POST",
                f"/runs/{approval.id}/approvals/{approval.approval['id']}",
                {"decision": "approve"},
            )
            assert (await settle(client, approval.id)).status == "succeeded"
            summaries.append(await evidence(client, approval.id, root, "approval-restart"))
            assert (await client.request("GET", f"/sessions/{session['id']}/context"))[
                "active_compaction_id"
            ] == compaction["id"]
    finally:
        server.should_exit = True
        await serving
    embed_settings = Settings(
        data_dir=root / "embed-data",
        workspace_root=workspace,
        provider="openai",
        openai_base_url=provider.base_url,
        openai_model=provider.model,
        _env_file=None,
    )
    embedded = Harness(embed_settings, Hooks(before_tool=guard))
    configure(embedded)
    async with embedded:
        config = await embedded.repository.config()
        config.provider = provider
        config.tools["workspace_summary"] = "allow"
        await embedded.studio.save_config(config)
        view = await embedded.run(
            "Use workspace_summary, read workspace memory and load csv-review. Give a concise evidence-based overview of RIVER's implemented CLI and its check command. Do not edit files."
        )
        view = await embedded.wait(view.id, 600)
        assert view.status == "succeeded", view.error
        trace = await embedded.repository.trace(view.id)
        assert any(value["data"].get("name") == "workspace_summary" for value in trace)
        (root / "embedding.trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summaries.append(
            {
                "label": "embedding",
                "run_id": view.id,
                "model_calls": sum(value["kind"] == "model" for value in trace),
                "tool_calls": sum(value["kind"] == "tool" for value in trace),
            }
        )
    for name in ("AGENTS.md", "check.py", "data/events.csv", "data/other.csv", "KEEP.txt"):
        assert (workspace / name).read_text() == FIXTURES[name], name
    summary = {
        "provider": provider.model,
        "results": summaries,
        "compaction_calls": len(reduction["data"]["calls"]),
        "real_model_calls": sum(value["model_calls"] for value in summaries)
        + len(reduction["data"]["calls"]),
        "tool_calls": sum(value["tool_calls"] for value in summaries),
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"PASS Stage 3/4 real acceptance: {summary['real_model_calls']} model requests, {summary['tool_calls']} tool calls; evidence {root}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
