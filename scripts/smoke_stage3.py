"""Verify context and process workflows through a real disposable HTTP service."""

import argparse
import asyncio
import ctypes
import ipaddress
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn

import rtagent.downloads as downloads
from rtagent.config import Settings
from rtagent.domain import uid
from rtagent.main import create_app


async def wait_until(read, ready, seconds: float = 45):
    """Bound state waits so broken jobs cannot leave acceptance hanging."""
    async with asyncio.timeout(seconds):
        while True:
            value = await read()
            if ready(value):
                return value
            await asyncio.sleep(0.1)


async def verify(
    client: httpx.AsyncClient, workspace: Path, network: bool, public_dns: bool = False
) -> None:
    """Exercise actual effects with explicit simulated decisions and disposable data."""

    async def api(method: str, path: str, body=None, status: int | None = None):
        """Check response status before accepting any lifecycle projection."""
        response = await client.request(method, path, json=body, headers={"Idempotency-Key": uid()})
        assert response.status_code == status if status else response.is_success, response.text
        return response.json() if response.content else None

    boot = await api("GET", "/bootstrap")
    config = boot["config"]
    for name in ("start_process", "send_process_input", "fetch_url"):
        config["tools"][name] = "allow"
    config["toolchain_roots"] = [str(Path(sys.base_prefix).resolve())]
    config["network_hosts"] = ["files.pythonhosted.org"] if network else []
    config = await api("PUT", "/config", config)
    preset = next(item for item in boot["presets"] if item["id"] == "general-agent")
    session = await api("POST", "/sessions", {"title": "Daily agent acceptance"})

    async def run(text: str, live_output: bool = False):
        """Submit a fresh revision and require terminal success, not only HTTP acceptance."""
        current = await api("GET", f"/sessions/{session['id']}")
        started = await api(
            "POST",
            "/runs",
            {
                "session_id": session["id"],
                "expected_session_revision": current["revision"],
                "preset_id": preset["id"],
                "preset_revision": preset["revision"],
                "config_revision": config["revision"],
                "input": text,
            },
        )
        if live_output:
            live = await wait_until(
                lambda: api("GET", f"/runs/{started['id']}"),
                lambda value: value.get("active_tool")
                and "early-output" in value["active_tool"].get("stdout", ""),
            )
            assert live["status"] == "running", live
            events = await api("GET", f"/runs/{started['id']}/event-log")
            assert any(event["type"] == "tool.output" for event in events)
        result = await wait_until(
            lambda: api("GET", f"/runs/{started['id']}"),
            lambda value: value["status"]
            in {"succeeded", "failed", "cancelled", "waiting_approval"},
            90,
        )
        assert result["status"] == "succeeded", result
        return result

    shell = "powershell" if os.name == "nt" else "bash"
    streaming = (
        "[Console]::WriteLine('early-output'); Start-Sleep -Seconds 4; python -c \"print('python-ready')\""
        if os.name == "nt"
        else "printf 'early-output\\n'; sleep 4; python3 -c \"print('python-ready')\""
    )
    streamed = await run(
        "/tool " + shell + " " + json.dumps({"command": streaming}), live_output=True
    )
    trace = await api("GET", f"/runs/{streamed['id']}/trace?raw=true")
    tool_call = next(item for item in trace["calls"] if item["kind"] == "tool")
    assert "early-output" in tool_call["data"]["output"]["stdout"], tool_call
    assert "python-ready" in json.loads(tool_call["result"])["stdout"], tool_call
    for number in range(3):
        await run(f"Turn {number} " + "Source evidence stays inspectable. " * 100)
    original = await api("GET", f"/sessions/{session['id']}")
    reduction = await api(
        "POST",
        f"/sessions/{session['id']}/compact",
        {
            "expected_revision": original["revision"],
            "config_revision": config["revision"],
            "keep_recent_turns": 1,
        },
    )
    context = await wait_until(
        lambda: api("GET", f"/sessions/{session['id']}/context"),
        lambda value: not value["compacting_id"],
    )
    assert context["active_compaction_id"] == reduction["id"], context
    current = await api("GET", f"/sessions/{session['id']}")
    assert current["messages"] == original["messages"]
    assert context["effective_estimated_tokens"] < context["raw_estimated_tokens"]
    copied = await api(
        "POST",
        f"/sessions/{session['id']}/copy",
        {
            "operation": "duplicate",
            "expected_revision": current["revision"],
        },
    )
    copy_context = await api("GET", f"/sessions/{copied['id']}/context")
    assert copy_context["active_compaction_id"] != reduction["id"]
    assert copy_context["effective_messages"] == context["effective_messages"]
    resumed = await run("Continue using the explicit earlier summary.")
    trace = await api("GET", f"/runs/{resumed['id']}/trace?raw=true")
    assert any(
        message["source"].startswith("context:compaction:")
        for message in trace["calls"][0]["data"]["canonical_messages"]
    )
    paused = asyncio.Event()

    async def paused_summary(*_args):
        """Keep a model call executing while an actual API cancellation is checked."""
        paused.set()
        await asyncio.Event().wait()
        yield {}

    with patch("rtagent.context.generate", paused_summary):
        cancelled = await api(
            "POST",
            f"/sessions/{session['id']}/compact",
            {
                "expected_revision": (await api("GET", f"/sessions/{session['id']}"))["revision"],
                "config_revision": config["revision"],
                "keep_recent_turns": 1,
            },
        )
        await asyncio.wait_for(paused.wait(), 5)
        await api("POST", f"/sessions/{session['id']}/compactions/{cancelled['id']}/cancel")
        retained = await api("GET", f"/sessions/{session['id']}/context")
        assert retained["active_compaction_id"] == reduction["id"] and not retained["compacting_id"]
        record = await api(
            "GET", f"/sessions/{session['id']}/compactions/{cancelled['id']}?redacted=false"
        )
        assert (
            record["status"] == "cancelled"
            and record["data"]["calls"][-1]["status"] == "outcome_unknown"
        )
    command = (
        "[Console]::WriteLine('waiting'); $line=[Console]::ReadLine(); "
        "[IO.File]::WriteAllText((Join-Path $env:RTAGENT_WORKSPACE 'input.txt'), $line); "
        "[Console]::WriteLine('received:'+$line)"
        if os.name == "nt"
        else "printf 'waiting\\n'; read -r line; printf '%s' \"$line\" > input.txt; printf 'received:%s\\n' \"$line\""
    )
    await run("/tool start_process " + json.dumps({"command": command, "timeout": 90}))
    processes = await wait_until(
        lambda: api("GET", f"/sessions/{session['id']}/processes"),
        lambda value: value and "waiting" in value[-1]["stdout"],
    )
    process = processes[-1]
    assert process["status"] == "running", process
    await api(
        "POST",
        f"/sessions/{copied['id']}/processes/{process['id']}/input",
        {"text": "wrong"},
        status=404,
    )
    config = await api("PUT", "/config", {**config, "sandbox_mode": "read-only"})
    await api(
        "POST",
        f"/sessions/{session['id']}/processes/{process['id']}/input",
        {"text": "blocked"},
        status=403,
    )
    config = await api("PUT", "/config", {**config, "sandbox_mode": "workspace-write"})
    await api(
        "PATCH",
        f"/sessions/{session['id']}",
        {
            "expected_revision": (await api("GET", f"/sessions/{session['id']}"))["revision"],
            "archived": True,
        },
        status=409,
    )
    await api(
        "POST",
        f"/sessions/{session['id']}/processes/{process['id']}/input",
        {"text": "actual pipe input \u03bb\n", "eof": True},
    )
    finished = (
        await wait_until(
            lambda: api("GET", f"/sessions/{session['id']}/processes"),
            lambda value: value[-1]["status"] not in {"starting", "running", "stopping"},
        )
    )[-1]
    assert finished["status"] == "exited", finished
    assert (workspace / "input.txt").read_text(encoding="utf-8-sig") == "actual pipe input \u03bb"
    assert "received:actual pipe input \u03bb" in finished["stdout"]
    assert (
        len((await api("GET", f"/sessions/{session['id']}/processes"))[0]["result"]["stdout"]) > 0
    )
    sleeper = (
        "$child = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -WindowStyle Hidden "
        "-ArgumentList '-NoProfile', '-Command', 'Start-Sleep -Seconds 90' -PassThru; "
        "[Console]::WriteLine('child:'+$child.Id); [Console]::WriteLine('alive'); Start-Sleep -Seconds 90"
        if os.name == "nt"
        else "printf 'alive\\n'; sleep 90"
    )
    await run("/tool start_process " + json.dumps({"command": sleeper, "timeout": 90}))
    live = (
        await wait_until(
            lambda: api("GET", f"/sessions/{session['id']}/processes"),
            lambda value: "alive" in value[-1]["stdout"],
        )
    )[-1]
    stopped = await api("POST", f"/sessions/{session['id']}/processes/{live['id']}/stop")
    assert stopped["status"] == "stopped", stopped
    assert stopped["result"]["effects_may_be_partial"]
    if os.name == "nt":
        child_pid = int(
            next(line[6:] for line in live["stdout"].splitlines() if line.startswith("child:"))
        )
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        for pid in (live["pid"], child_pid):
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                try:
                    code = ctypes.c_ulong()
                    kernel.GetExitCodeProcess.argtypes = [
                        ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_ulong),
                    ]
                    assert (
                        kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259
                    )
                finally:
                    kernel.CloseHandle(handle)
    assert await api("GET", f"/sessions/{copied['id']}/processes") == []
    denied = await run(
        "/tool fetch_url " + json.dumps({"url": "https://127.0.0.1/secret", "path": "secret.txt"})
    )
    assert "network_denied" in denied["messages"][1]["content"]
    if network:
        async with httpx.AsyncClient(trust_env=False) as public:
            metadata = (await public.get("https://pypi.org/pypi/six/1.17.0/json")).json()
        wheel = next(item for item in metadata["urls"] if item["filename"].endswith(".whl"))
        downloaded = await run(
            "/tool fetch_url "
            + json.dumps(
                {"url": wheel["url"], "path": "six.whl", "sha256": wheel["digests"]["sha256"]}
            )
        )
        if public_dns and "error" in json.loads(downloaded["messages"][1]["content"]):
            assert "network_denied" in downloaded["messages"][1]["content"], downloaded
            async with httpx.AsyncClient(trust_env=False) as resolver:
                answers = (
                    await resolver.get(
                        "https://dns.google/resolve",
                        params={"name": "files.pythonhosted.org", "type": "A"},
                    )
                ).json()["Answer"]
            addresses = [answer["data"] for answer in answers if answer["type"] == 1]
            assert addresses and all(ipaddress.ip_address(value).is_global for value in addresses)

            async def pinned_address(host: str) -> str:
                """Use a live public-DNS acceptance fixture only in the disposable test service."""
                assert host == "files.pythonhosted.org"
                return addresses[0]

            print(
                "Acceptance fixture: OS proxy DNS refused; actual HTTPS transfer uses verified public DNS.",
                flush=True,
            )
            with patch.object(downloads, "public_address", pinned_address):
                downloaded = await run(downloaded["input"])
        receipt = json.loads(downloaded["messages"][1]["content"])
        assert receipt.get("sha256") == wheel["digests"]["sha256"], receipt
        interpreter = "python" if os.name == "nt" else "python3"
        install = (
            interpreter
            + " -c \"import zipfile; zipfile.ZipFile('six.whl').extractall('.rtagent/packages')\"; "
            + interpreter
            + " -c \"import six; print('dependency:'+six.__version__)\""
        )
        installed = await run("/tool " + shell + " " + json.dumps({"command": install}))
        assert "dependency:1.17.0" in installed["messages"][1]["content"], installed
    print(
        "PASS actual HTTP: compaction/copy/continued context, streamed native commands, read-only runtime, stdin/background/stop, download policy"
        + (", public dependency download/import" if network else "")
    )


async def main(network: bool = False, public_dns: bool = False) -> None:
    """Own the listening server and scratch resources, including cleanup after a failed assertion."""
    with tempfile.TemporaryDirectory(
        prefix="rtagent-stage3-", ignore_cleanup_errors=True
    ) as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        settings = Settings(
            workspace_root=workspace, data_dir=root / "data", provider="fake", _env_file=None
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host="127.0.0.1", port=8771, log_level="error")
        )
        serving = asyncio.create_task(server.serve())
        try:
            await wait_until(lambda: asyncio.sleep(0, result=server.started), bool, 15)
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8771/api/v1", trust_env=False, timeout=60
            ) as client:
                await verify(client, workspace, network, public_dns)
        finally:
            server.should_exit = True
            await serving


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network", action="store_true", help="Opt into the public HTTPS dependency check."
    )
    parser.add_argument(
        "--public-dns",
        action="store_true",
        help="Use a verified public-DNS acceptance fixture when OS proxy DNS is refused.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.network, args.public_dns))
