"""Opt-in live-model acceptance for the native Windows local agent workflow."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn

from rtagent.config import Settings, resolve_secret
from rtagent.domain import ProviderConfig, StudioConfig
from rtagent.main import create_app

FIXTURES = {
    "AGENTS.md": """# Acceptance workspace

Keep changes small. Preserve check.ps1, order.json, and KEEP.txt exactly.
Use PowerShell and the provided check.ps1; no dependencies or network are needed.
Code and documentation use English. Report only checks actually executed.
Project codename: ORCHID-42. When asked, remember it in project memory.
""",
    "KEEP.txt": "An unrelated user file. Preserve these bytes.\n",
    "order.json": '{"Subtotal":100,"DiscountRate":0.2,"TaxRate":0.1}\n',
    "src/Invoice.psm1": """function Get-InvoiceTotal {
    <# .SYNOPSIS Calculate a discounted invoice and its tax. #>
    param([decimal]$Subtotal, [decimal]$DiscountRate = 0, [decimal]$TaxRate = 0.1)
    $net = [decimal]::Round($Subtotal * (1 - $DiscountRate), 2)
    $tax = [decimal]::Round($Subtotal * $TaxRate, 2)
    [pscustomobject]@{ Net = $net; Tax = $tax; Total = $net + $tax }
}
Export-ModuleMember -Function Get-InvoiceTotal
""",
    "invoice.ps1": """<# .SYNOPSIS Print an invoice from the supplied JSON order. #>
param([string]$Path = 'order.json')
$ErrorActionPreference = 'Stop'
Import-Module "$PSScriptRoot/src/Invoice.psm1" -Force
$order = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
Get-InvoiceTotal -Subtotal $order.Subtotal -DiscountRate 0 -TaxRate $order.TaxRate | ConvertTo-Json -Compress
""",
    "check.ps1": """<# .SYNOPSIS Verify the invoice's public contract without external dependencies. #>
$ErrorActionPreference = 'Stop'
Import-Module "$PSScriptRoot/src/Invoice.psm1" -Force
$value = Get-InvoiceTotal -Subtotal 100 -DiscountRate 0.2 -TaxRate 0.1
if ($value.Net -ne 80 -or $value.Tax -ne 8 -or $value.Total -ne 88) { throw 'Discounted tax is wrong' }
$rounded = Get-InvoiceTotal -Subtotal 20.105 -DiscountRate 0 -TaxRate 0.1
if ($rounded.Net -ne 20.11 -or $rounded.Tax -ne 2.01 -or $rounded.Total -ne 22.12) { throw 'Round midpoint away from zero' }
foreach ($bad in @(@{Subtotal=-1}, @{Subtotal=1;DiscountRate=-0.1}, @{Subtotal=1;DiscountRate=1.1}, @{Subtotal=1;TaxRate=-0.1}, @{Subtotal=1;TaxRate=1.1})) {
    $rejected = $false
    try { Get-InvoiceTotal @bad | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'Invalid amount or rate accepted' }
}
$cli = & "$PSScriptRoot/invoice.ps1" -Path "$PSScriptRoot/order.json" | ConvertFrom-Json
if ($cli.Net -ne 80 -or $cli.Tax -ne 8 -or $cli.Total -ne 88) { throw 'CLI ignored the order discount' }
Write-Output 'INVOICE_CHECK_OK'
""",
    "src/Export.psm1": """function Get-ExportPath {
    <# .SYNOPSIS Resolve an HTTP client's requested export filename. #>
    param([string]$Root, [string]$UserPath)
    $candidate = Join-Path -Path $Root -ChildPath $UserPath
    return [IO.Path]::GetFullPath($candidate)
}
function Save-Export {
    <# .SYNOPSIS Write supplied text using the export path resolver. #>
    param([string]$Root, [string]$UserPath, [string]$Text)
    $target = Get-ExportPath -Root $Root -UserPath $UserPath
    [IO.File]::WriteAllText($target, $Text)
}
Export-ModuleMember -Function Get-ExportPath, Save-Export
""",
    "public/example.txt": "Export root sample.\n",
    "private/note.txt": "Fixture data, never a host credential.\n",
}


async def api(client: httpx.AsyncClient, method: str, path: str, body: Any = None) -> Any:
    """Use actual HTTP and fail on application errors without printing request credentials."""
    response = await client.request(
        method, path, json=body, headers={"Idempotency-Key": uuid4().hex} if path == "/runs" else {}
    )
    if not response.is_success:
        raise RuntimeError(f"{method} {path} returned {response.status_code}: {response.text}")
    return response.json()


async def start_server(settings: Settings) -> tuple[uvicorn.Server, asyncio.Task]:
    """Open a dedicated live HTTP server so acceptance cannot alter the user's database."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=8768, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    async with asyncio.timeout(20):
        while not server.started:
            if serving.done():
                await serving
                raise RuntimeError("The acceptance server exited before startup.")
            await asyncio.sleep(0.05)
    return server, serving


async def run_task(
    client: httpx.AsyncClient,
    title: str,
    task: str,
    project_id: str | None = None,
    preset_id: str | None = None,
) -> tuple[dict, dict]:
    """Submit directly to Run, wait for completion, and validate original model/tool evidence."""
    state = await api(client, "GET", "/bootstrap")
    session = await api(client, "POST", "/sessions", {"title": title, "project_id": project_id})
    preset = next(
        (item for item in state["presets"] if item["id"] == preset_id), state["presets"][0]
    )
    run = await api(
        client,
        "POST",
        "/runs",
        {
            "session_id": session["id"],
            "expected_session_revision": session["revision"],
            "preset_id": preset["id"],
            "preset_revision": preset["revision"],
            "config_revision": state["config"]["revision"],
            "input": task,
        },
    )
    async with asyncio.timeout(620):
        while run["status"] not in {
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "waiting_approval",
        }:
            await asyncio.sleep(0.5)
            run = await api(client, "GET", f'/runs/{run["id"]}')
    assert run["status"] == "succeeded", f'{title}: {run["status"]}: {run["error"]}'
    trace = await api(client, "GET", f'/runs/{run["id"]}/trace?raw=true')
    models = [call for call in trace["calls"] if call["kind"] == "model"]
    assert models and all(call["status"] == "succeeded" for call in models)
    for call in models:
        data = call["data"]
        assert not data.get("simulated") and data["wire_request_body"] == data["prepared_request"]
        messages = data["canonical_messages"]
        assert sum(message["source"] == "current_input" for message in messages) == 1
        pending = set()
        for message in messages:
            if message["role"] == "assistant":
                assert not pending, "Missing tool results before the next model response."
                pending.update(item["id"] for item in message["tool_calls"])
            elif message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
        assert not pending, "The next model request omitted a tool result."
    assert {"workspace:AGENTS.md", "workspace:skills"} <= {
        message["source"] for message in models[0]["data"]["canonical_messages"]
    }
    tools = [call for call in trace["calls"] if call["kind"] == "tool"]
    assert all(call["status"] in {"succeeded", "failed"} for call in tools)
    summary = {
        "run_id": run["id"],
        "session_id": session["id"],
        "status": run["status"],
        "model_calls": len(models),
        "tool_calls": len(tools),
        "tools": sorted({call["data"]["name"] for call in tools}),
        "usage": run["usage"],
    }
    print(
        f'PASS {title}: {summary["model_calls"]} real model calls, {summary["tool_calls"]} tool calls',
        flush=True,
    )
    return summary, trace


async def verify_invoice(server: uvicorn.Server, project_id: str) -> None:
    """Independently verify behavior in AppContainer instead of trusting the model's claims."""
    studio = server.config.app.state.studio
    config = await studio.repository.config()
    registry = await studio.workspace_registry(project_id)
    result = json.loads(
        await registry.execute(
            registry.catalog["powershell"],
            {"command": "& ./check.ps1", "timeout": 20},
            config,
        )
    )
    assert result["exit_code"] == 0 and "INVOICE_CHECK_OK" in result["stdout"], result


async def exercise(
    client: httpx.AsyncClient, server: uvicorn.Server, workspace: Path, project_id: str
) -> list[dict]:
    """Accept multi-file repair, guarded memory writes, skill generation, and evidence-based analysis."""
    results = []
    unchanged = {
        name: (workspace / name).read_bytes()
        for name in ("AGENTS.md", "KEEP.txt", "check.ps1", "order.json")
    }
    summary, trace = await run_task(
        client,
        "Multi-file coding",
        """Fix the invoice module and CLI in this workspace. Inspect files and run check.ps1 before and after changes; preserve that check and order.json.
Rules: subtotal must be nonnegative; discount and tax rates must be between 0 and 1. Round the discounted net to 2 decimals, then calculate tax from that rounded net; round tax to 2 decimals, both using midpoint away from zero. The CLI must use DiscountRate from its JSON input. Do not add dependencies. Add concise docstrings/comments explaining the rounding order.
After the check succeeds, remember the project codename from AGENTS.md and the net-first invoice rule in project memory, preserving any existing notes. Report actual evidence.""",
        project_id,
    )
    assert {"read_text", "powershell", "read_memory", "save_memory"} <= set(summary["tools"])
    assert {"write_text", "edit_text"} & set(summary["tools"])
    commands = [
        json.loads(call["result"])
        for call in trace["calls"]
        if call["kind"] == "tool" and call["data"]["name"] == "powershell"
    ]
    assert any(result.get("exit_code") != 0 for result in commands)
    assert any(
        result.get("exit_code") == 0 and "INVOICE_CHECK_OK" in result.get("stdout", "")
        for result in commands
    )
    assert all((workspace / name).read_bytes() == value for name, value in unchanged.items())
    await verify_invoice(server, project_id)
    memory = await api(client, "GET", f"/workspace?project_id={project_id}")
    assert "ORCHID-42" in memory["memory"]["content"]
    results.append(summary)

    summary, trace = await run_task(
        client,
        "Skill creation",
        """$skill-creator Create a focused skill named invoice-review for this project's invoice changes. Inspect the actual files and commands. Include the net-first rounding/validation rules, checking both module and CLI, preserving the existing checks, and running check.ps1. Save with save_skill, then load_skill to verify the saved instructions and exercise the workflow. Do not change the invoice implementation or check.ps1.""",
        project_id,
    )
    assert {"save_skill", "load_skill", "powershell"} <= set(summary["tools"])
    assert any(
        call["data"]["name"] == "load_skill"
        and call["data"]["args"].get("name") == "invoice-review"
        for call in trace["calls"]
        if call["kind"] == "tool"
    )
    skill = await api(client, "GET", f"/workspace/skills/invoice-review?project_id={project_id}")
    assert (
        skill["path"] == ".agents/skills/invoice-review/SKILL.md"
        and "check.ps1" in skill["content"]
    )
    assert all((workspace / name).read_bytes() == value for name, value in unchanged.items())
    results.append(summary)

    summary, trace = await run_task(
        client,
        "Security analysis",
        """Review src/Export.psm1 for an HTTP export endpoint whose UserPath is attacker-controlled and whose intended Root is public/. Identify a concrete boundary violation with file/line evidence. Reproduce path resolution only, using a harmless ../private/note.txt fixture in this workspace; do not invoke Save-Export or write outside the public export root. Save an English report in findings.md describing trigger, observed path, root cause, impact, mitigation, and limits of the check. Preserve the export module itself.""",
        project_id,
    )
    assert {"read_text", "powershell", "write_text"} <= set(summary["tools"])
    assert (workspace / "src/Export.psm1").read_text(encoding="utf-8") == FIXTURES[
        "src/Export.psm1"
    ]
    finding = (workspace / "findings.md").read_text(encoding="utf-8")
    assert "Get-ExportPath" in finding and "private" in finding and ".." in finding
    results.append(summary)
    return results


async def main() -> None:
    """Require live opt-in, preserve acceptance evidence locally, and close both server lifecycles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow real model requests using the running application's provider.",
    )
    parser.add_argument(
        "--studio-url",
        default="http://127.0.0.1:8001",
        help="Read current provider settings from this local service.",
    )
    args = parser.parse_args()
    if not args.live or os.name != "nt":
        parser.error(
            "This Windows acceptance requires --live; offline sandbox checks use smoke_workspace.py."
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
        parser.error(
            "Configure a real OpenAI-compatible provider and its server-side credential first."
        )
    root = Path("var") / ("stage2-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    workspace = (root / "workspace").resolve()
    workspace.mkdir(parents=True)
    for name, content in FIXTURES.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    default_workspace = root / "default-workspace"
    default_workspace.mkdir()
    settings = Settings(
        data_dir=(root / "data").resolve(),
        workspace_root=default_workspace.resolve(),
        _env_file=None,
        provider="openai",
        openai_model=provider.model,
        openai_base_url=provider.base_url,
    )
    server, serving = await start_server(settings)
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8768/api/v1", trust_env=False, timeout=45
        ) as client:
            state = await api(client, "GET", "/bootstrap")
            config = StudioConfig.model_validate(state["config"])
            config.provider = provider.model_copy(
                update={"max_output_tokens": max(4096, provider.max_output_tokens)}
            )
            await api(client, "PUT", "/config", config.model_dump())
            project = await api(
                client,
                "POST",
                "/projects",
                {"name": "Stage 2 acceptance", "workspace_root": str(workspace)},
            )
            project_id = project["id"]
            view = await api(client, "GET", f"/workspace?project_id={project_id}")
            assert view["sandbox"]["ready"], view["sandbox"]
            results = await exercise(client, server, workspace, project_id)
        server.should_exit = True
        await serving
        server, serving = await start_server(settings)
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8768/api/v1", trust_env=False, timeout=45
        ) as client:
            summary, trace = await run_task(
                client,
                "Reuse after restart",
                """In this fresh conversation, use saved project memory and $invoice-review to verify the invoice implementation again. State the project codename found in memory and the remembered calculation rule, then run the skill's actual check. Do not edit implementation, memory, or skill files. Report only observed results.""",
                project_id,
            )
            first = next(call for call in trace["calls"] if call["kind"] == "model")
            assert {"workspace:memory", "skill:invoice-review"} <= {
                message["source"] for message in first["data"]["canonical_messages"]
            }
            assert "powershell" in summary["tools"]
            run = await api(client, "GET", f'/runs/{summary["run_id"]}')
            reply = "\n".join(
                message["content"] for message in run["messages"] if message["role"] == "assistant"
            )
            assert "ORCHID-42" in reply and "INVOICE_CHECK_OK" in reply
            await verify_invoice(server, project_id)
            results.append(summary)
            report = {
                "timestamp": datetime.now(UTC).isoformat(),
                "model": provider.model,
                "sandbox": "Windows AppContainer / PowerShell",
                "project_id": project_id,
                "results": results,
            }
            (root / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"PASS Stage 2 Windows acceptance; local evidence: {root.as_posix()}/acceptance.json",
                flush=True,
            )
    finally:
        server.should_exit = True
        await serving


if __name__ == "__main__":
    asyncio.run(main())
