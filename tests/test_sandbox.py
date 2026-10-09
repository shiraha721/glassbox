"""Linux command isolation checked against real bubblewrap namespaces."""

import json
import shutil
import sys

import pytest

from glassbox.infrastructure.sandbox.linux import BashSandbox
from glassbox.infrastructure.workspace.files import Workspace

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"), reason="Requires Linux bubblewrap."
)


async def run(sandbox: BashSandbox, command: str, **options) -> dict:
    """Execute one command and decode its tool result."""
    return json.loads(await sandbox.execute(command, timeout=30, **options))


def write(path, text: str) -> None:
    """Create a fixture file and its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_commands_see_project_files_and_links_but_never_private_data(tmp_path):
    root = tmp_path / "workspace"
    write(root / "AGENTS.md", "project instructions")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    write(root / "node_modules/pkg/index.js", "dependency module")
    write(root / "var/notes.txt", "project var folder")
    write(root / ".env", "SECRET=env")
    write(root / "nested/.env.local", "SECRET=nested")
    write(root / ".git/config", "url = https://token@example.com/repo")
    write(root / "state/app.db", "SECRET=database")
    sandbox = BashSandbox(Workspace(root, tmp_path / "data"))
    result = await run(
        sandbox,
        "cat CLAUDE.md node_modules/pkg/index.js var/notes.txt; "
        "cat .env nested/.env.local .git/config state/app.db 2>/dev/null; "
        "awk 'BEGIN { print \"awk-ok\" }'; whoami >/dev/null && echo user-ok",
    )
    output = result["stdout"]
    for expected in ("project instructions", "dependency module", "project var folder"):
        assert expected in output
    assert "awk-ok" in output and "user-ok" in output
    assert "SECRET" not in output and "token" not in output


async def test_commands_have_no_network(tmp_path):
    sandbox = BashSandbox(Workspace(tmp_path))
    result = await run(sandbox, "exec 3<>/dev/tcp/1.1.1.1/53 && echo connected")
    assert result["exit_code"] != 0 and "connected" not in result["stdout"]
    assert result["network"] == "disabled"


async def test_long_output_keeps_its_beginning_and_end(tmp_path):
    frames = []

    async def on_output(frame):
        frames.append(frame)

    sandbox = BashSandbox(Workspace(tmp_path))
    serialized = await sandbox.execute(
        "seq 1 200000; echo summary >&2", timeout=30, on_output=on_output
    )
    result = json.loads(serialized)
    assert len(serialized.encode()) <= 65536
    assert result["truncated"] is True
    assert result["stdout"].startswith("1\n2\n") and result["stdout"].endswith("199999\n200000\n")
    assert "characters omitted" in result["stdout"]
    assert result["stderr"] == "summary\n"
    # Consumers receive bounded snapshots, never the whole stream.
    assert frames and all(len(frame["text"]) < 30_000 for frame in frames)


async def test_trusted_runtimes_are_mounted_at_their_host_paths(tmp_path):
    release = tmp_path / "runtime-1.0"
    write(release / "bin/runtime-tool", "#!/bin/sh\necho runtime-ok\n")
    (release / "bin/runtime-tool").chmod(0o755)
    (tmp_path / "runtime").symlink_to(release)
    root = tmp_path / "workspace"
    (root / ".venv/bin").mkdir(parents=True)
    # Like a virtual environment, link through the runtime's versioned alias.
    (root / ".venv/bin/tool").symlink_to(tmp_path / "runtime/bin/runtime-tool")
    sandbox = BashSandbox(Workspace(root))
    result = await run(
        sandbox,
        ".venv/bin/tool; runtime-tool; echo $GLASSBOX_TOOLCHAIN_1",
        toolchain_roots=[str(tmp_path / "runtime")],
    )
    assert result["stdout"].split() == ["runtime-ok", "runtime-ok", "/toolchains/1"]
