"""Opt-in real-model acceptance for search, guarded patches, scoped instructions, plans, and presets."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from acceptance_stage2 import api, run_task, start_server

from rtagent.domain import ProviderConfig
from rtagent.infrastructure.config import Settings, resolve_secret

FIXTURES = {
    "AGENTS.md": "Keep changes focused. Preserve check.ps1 and KEEP.txt. Use native PowerShell; no network or dependencies are needed. Follow instructions for the src directory.\n",
    "src/AGENTS.override.md": "Keep the public Get-Total function name and parameters stable. Preserve its English synopsis comment.\n",
    "src/Total.psm1": "function Get-Total {\n    <# .SYNOPSIS Calculate the total price of a quantity. #>\n    param([decimal]$Quantity, [decimal]$UnitPrice)\n    return $Quantity + $UnitPrice\n}\nExport-ModuleMember -Function Get-Total\n",
    "total.ps1": 'param([decimal]$Quantity, [decimal]$UnitPrice)\nImport-Module "$PSScriptRoot/src/Total.psm1" -Force\nGet-Total -Quantity 1 -UnitPrice $UnitPrice\n',
    "check.ps1": "$ErrorActionPreference = 'Stop'\nImport-Module \"$PSScriptRoot/src/Total.psm1\" -Force\nif ((Get-Total -Quantity 3 -UnitPrice 7) -ne 21) { throw 'Module total is wrong' }\nif ((& \"$PSScriptRoot/total.ps1\" -Quantity 3 -UnitPrice 7) -ne 21) { throw 'CLI ignores quantity' }\nWrite-Output 'AGENT_TOOLS_CHECK_OK'\n",
    "src/Export.psm1": "function Get-ExportPath {\n    <# .SYNOPSIS Resolve a caller-selected export path. #>\n    param([string]$Root, [string]$UserPath)\n    return [IO.Path]::GetFullPath((Join-Path $Root $UserPath))\n}\nExport-ModuleMember -Function Get-ExportPath\n",
    "KEEP.txt": "An unrelated file that must remain unchanged.\n",
}


async def main() -> None:
    """Use the configured real provider in an isolated project and retain local, uncommitted evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow real model requests.")
    parser.add_argument("--studio-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    if not args.live or os.name != "nt":
        parser.error(
            "This Windows acceptance requires --live; use smoke_agent_tools.py for offline HTTP checks."
        )
    async with httpx.AsyncClient(trust_env=False, timeout=20) as source:
        response = await source.get(args.studio_url.rstrip("/") + "/api/v1/bootstrap")
        response.raise_for_status()
        provider = ProviderConfig.model_validate(response.json()["config"]["provider"])
    if (
        provider.adapter != "openai"
        or not provider.model
        or (provider.credential_ref and not resolve_secret(provider.credential_ref))
    ):
        parser.error("Configure a real provider and its server-side credential first.")
    root = Path("var") / ("agent-tools-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    workspace = (root / "workspace").resolve()
    workspace.mkdir(parents=True)
    for name, text in FIXTURES.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    settings = Settings(
        data_dir=(root / "data").resolve(),
        workspace_root=workspace,
        provider="openai",
        openai_model=provider.model,
        openai_base_url=provider.base_url,
        _env_file=None,
    )
    server, serving = await start_server(settings)
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8768/api/v1", trust_env=False, timeout=45
        ) as client:
            state = await api(client, "GET", "/bootstrap")
            config = state["config"]
            config["provider"] = provider.model_copy(
                update={"max_output_tokens": max(4096, provider.max_output_tokens)}
            ).model_dump()
            await api(client, "PUT", "/config", config)
            project = await api(
                client,
                "POST",
                "/projects",
                {"name": "Agent tools acceptance", "workspace_root": str(workspace)},
            )
            project_id = project["id"]
            summary, trace = await run_task(
                client,
                "Guarded multi-file repair",
                """Repair the wrong module total and the CLI quantity handling. Use update_plan for inspect/repair/verify, search_files and search_text to locate the relevant files, and read_instructions for src/Total.psm1. Reproduce with the supplied check.ps1 in native PowerShell. Read current file hashes with read_text and make one coordinated apply_patch for src/Total.psm1 and total.ps1. Preserve the check, export module, and KEEP.txt. Run check.ps1 again, mark verified plan steps complete, and report only actual results. Do not use shell commands to edit the source files.""",
                project_id,
                preset_id="coding",
            )
            assert {
                "read_instructions",
                "read_text",
                "apply_patch",
                "update_plan",
                "powershell",
            } <= set(summary["tools"])
            assert all(
                call["status"] == "succeeded"
                for call in trace["calls"]
                if call["kind"] == "tool" and call["data"]["name"] == "apply_patch"
            )
            run = await api(client, "GET", f'/runs/{summary["run_id"]}')
            assert run["preset_name"] == "Coding" and run["plan"]
            assert all(item["status"] == "completed" for item in run["plan"]["plan"])
            studio = server.config.app.state.studio
            registry = await studio.workspace_registry(project_id)
            current = await studio.repository.config()
            checked = json.loads(
                await registry.execute(
                    registry.catalog["powershell"],
                    {"command": "& ./check.ps1", "timeout": 20},
                    current,
                )
            )
            assert checked["exit_code"] == 0 and "AGENT_TOOLS_CHECK_OK" in checked["stdout"]
            for name in ("check.ps1", "KEEP.txt", "src/Export.psm1"):
                assert (workspace / name).read_text(encoding="utf-8") == FIXTURES[name]
            before_review = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            state = await api(client, "GET", "/bootstrap")
            state["config"]["sandbox_mode"] = "read-only"
            await api(client, "PUT", "/config", state["config"])
            review, review_trace = await run_task(
                client,
                "Read-only evidence review",
                """Use $code-review to locate and review the workspace's Get-ExportPath function for an export path traversal bug. First call search_files with pattern **/*.psm1 and search_text with query Get-ExportPath to locate its definition and references. Read applicable instructions and the relevant source ranges. If possible, demonstrate path resolution harmlessly with PowerShell without writing files. Report a concrete trigger, causal path, file/line location, impact, and mitigation. Do not change any file.""",
                project_id,
                preset_id="code-review",
            )
            run = await api(client, "GET", f'/runs/{review["run_id"]}')
            assert run["preset_name"] == "Code review"
            assert {"search_files", "search_text"} <= set(review["tools"])
            first = next(call for call in review_trace["calls"] if call["kind"] == "model")
            assert "skill:code-review" in {
                message["source"] for message in first["data"]["canonical_messages"]
            }
            assert "apply_patch" not in {
                tool["function"]["name"]
                for tool in first["data"]["prepared_request"].get("tools", [])
            }
            assert before_review == {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            answer = "\n".join(
                item["content"] for item in run["messages"] if item["role"] == "assistant"
            )
            assert "Export.psm1" in answer and ".." in answer and "powershell" in review["tools"]
            report = {
                "timestamp": datetime.now(UTC).isoformat(),
                "model": provider.model,
                "project_id": project_id,
                "results": [summary, review],
            }
            (root / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"PASS real agent tool acceptance; evidence: {root.as_posix()}/acceptance.json",
                flush=True,
            )
    finally:
        server.should_exit = True
        await serving


if __name__ == "__main__":
    asyncio.run(main())
