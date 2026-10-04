"""Local filesystem boundaries and real loopback MCP transport contracts."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from rtagent.domain import AppError, MCPServer, StudioConfig
from rtagent.infrastructure.tools import ToolRegistry
from rtagent.infrastructure.workspace.files import Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_server.py"


def test_file_tool_boundaries(tmp_path):
    """Reject traversal, credential names, binary text, and oversized output."""
    registry = ToolRegistry(tmp_path)
    (tmp_path / "sample.txt").write_text("Evidence", encoding="utf-8")
    assert registry.read_file("sample.txt") == "Evidence"
    for name, value in [
        (".ENV", b"secret"),
        ("sample.DB", b"secret"),
        ("binary.bin", b"\xff"),
        ("large.txt", b"a" * 65537),
    ]:
        (tmp_path / name).write_bytes(value)
        with pytest.raises(AppError):
            registry.read_file(name)
    with pytest.raises(AppError, match="outside"):
        registry.read_file("../outside.txt")


@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
async def test_mcp_discovery_and_execution(tmp_path, monkeypatch, transport):
    """Exercise the SDK handshake, discovery, and invocation over both transports."""
    monkeypatch.setenv("STUDIO_FIXTURE_SECRET", "synthetic-only")
    monkeypatch.setenv("UNRELATED_TEST_KEY", "must-not-be-forwarded")
    server = MCPServer(
        name="Fixture",
        command=sys.executable,
        args=[str(FIXTURE.resolve())],
        env={"FIXTURE_SECRET": "STUDIO_FIXTURE_SECRET"},
    )
    process = None
    try:
        if transport == "streamable_http":
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(FIXTURE),
                "--http",
                str(port),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            async with asyncio.timeout(15):
                while True:
                    if process.returncode is not None:
                        pytest.fail("The HTTP fixture exited before accepting connections.")
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(0.05)
            server = server.model_copy(
                update={"transport": transport, "url": f"http://127.0.0.1:{port}/mcp"}
            )
        config = StudioConfig(mcp_servers=[server])
        registry = ToolRegistry(tmp_path)
        catalog = await registry.discover(config)
        assert registry.errors == {}
        echo = next(tool for tool in catalog if tool.original_name == "fixture_echo")
        result = json.loads(await registry.execute(echo, {"text": "hello"}, config))
        assert result["isError"] is False
        assert "MCP evidence: hello" in str(result)
        if transport == "stdio":
            check = next(tool for tool in catalog if tool.original_name == "environment_check")
            result = json.loads(await registry.execute(check, {}, config))
            assert result["structuredContent"] == {"explicit_reference": True, "ambient_key": False}
    finally:
        if process and process.returncode is None:
            process.terminate()
            await process.wait()


async def test_changed_mcp_schema_is_rejected(tmp_path, monkeypatch):
    """A reconnect must not invoke a tool whose schema differs from the frozen catalog."""
    server = MCPServer(
        name="Changing fixture",
        command=sys.executable,
        args=[str(FIXTURE.resolve())],
        env={"FIXTURE_SCHEMA": "TEST_MCP_SCHEMA"},
    )
    config = StudioConfig(mcp_servers=[server])
    registry = ToolRegistry(tmp_path)
    catalog = await registry.discover(config)
    tool = next(item for item in catalog if item.original_name == "fixture_echo")
    monkeypatch.setenv("TEST_MCP_SCHEMA", "changed")
    with pytest.raises(AppError, match="schema changed"):
        await registry.execute(tool, {"text": "must not run"}, config)


def test_partial_patch_reports_completed_files(tmp_path, monkeypatch):
    """A failure after one atomic file write must expose that effect instead of implying rollback."""
    workspace = Workspace(tmp_path)
    original = workspace._replace

    def fail_second(path, content, expected_hash):
        """Represent a filesystem failure after the first known completed replacement."""
        if path == "second.txt":
            raise OSError("Fixture replacement failure")
        return original(path, content, expected_hash)

    monkeypatch.setattr(workspace, "_replace", fail_second)
    with pytest.raises(AppError) as error:
        workspace.apply_patch(
            "*** Begin Patch\n*** Add File: first.txt\n+one\n*** Add File: second.txt\n+two\n*** End Patch"
        )
    assert error.value.code == "partial_patch"
    assert [item["path"] for item in error.value.details["files"]] == ["first.txt"]
    assert (tmp_path / "first.txt").read_text() == "one\n"
    assert not (tmp_path / "second.txt").exists()


async def test_read_only_patch_denied_before_effect(tmp_path):
    """Even a caller holding the native schema cannot bypass read-only execution policy."""
    registry = ToolRegistry(tmp_path)
    config = StudioConfig(sandbox_mode="read-only")
    with pytest.raises(AppError) as error:
        await registry.execute(
            registry.catalog["apply_patch"],
            {"patch": "*** Begin Patch\n*** Add File: forbidden.txt\n+bad\n*** End Patch"},
            config,
        )
    assert error.value.code == "tool_denied"
    assert not (tmp_path / "forbidden.txt").exists()


def test_unicode_patch_evidence_stays_bounded(tmp_path):
    """Serialized Unicode diffs must remain within the result budget before any side effects occur."""
    workspace = Workspace(tmp_path)
    paths = [f"file-{index}.txt" for index in range(20)]
    text = "Unicode evidence: " + '\u03c0\\"\t' * 400
    patch = (
        "*** Begin Patch\n"
        + "".join(f"*** Add File: {path}\n+{text}\n" for path in paths)
        + "*** End Patch"
    )
    result = workspace.apply_patch(patch)
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 65_536
    assert len(result["files"]) == 20 and all((tmp_path / path).exists() for path in paths)
    assert any(item["diff_truncated"] for item in result["files"])
