"""Folder dialog selection under WSL, where the browser and the dialog run on Windows."""

import base64
import os
import shutil
import sys
from pathlib import Path

import pytest

from rtagent.infrastructure.desktop import folders
from rtagent.infrastructure.desktop.folders import UNREACHABLE, DialogCommand, FolderPicker

pytestmark = pytest.mark.skipif(os.name == "nt", reason="WSL paths exist only on Linux.")


def test_wsl_uses_the_windows_dialog_with_a_translated_initial_folder(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Debian")
    monkeypatch.setenv("WSLENV", "WT_SESSION")
    monkeypatch.setattr(folders.shutil, "which", lambda name: f"/windows/{name}")
    command = FolderPicker._command("/home/user/it's here")
    assert command is not None and command.output == "wsl"
    assert command.argv[0] == "/windows/powershell.exe"
    script = base64.b64decode(command.argv[-1]).decode("utf-16-le")
    assert script == Path(folders.__file__).with_name("wsl_dialog.ps1").read_text(encoding="utf-8")
    # The path travels as a translated environment variable, never inside the script text.
    assert command.env is not None
    assert command.env["RTAGENT_DIALOG_INITIAL"] == "/home/user/it's here"
    assert command.env["WSLENV"] == "RTAGENT_DIALOG_INITIAL/p:WT_SESSION"


def fake_dialog(exit_code: int, output: str = "") -> DialogCommand:
    """A process that behaves like the Windows dialog helper."""
    code = f"import sys; sys.stdout.write({output!r}); sys.exit({exit_code})"
    return DialogCommand([sys.executable, "-c", code], output="wsl")


async def test_wsl_selections_are_translated_or_reported(monkeypatch, tmp_path):
    chosen = "\\\\wsl.localhost\\Debian\\project"
    encoded = base64.b64encode(chosen.encode()).decode()

    async def linux_path(path: str) -> str | None:
        return str(tmp_path) if path == chosen else None

    monkeypatch.setattr(folders, "linux_path", linux_path)
    picker = FolderPicker()
    monkeypatch.setattr(picker, "_command", lambda _initial: fake_dialog(0, encoded))
    selected = await picker.choose(str(tmp_path))
    assert (selected.status, selected.path) == ("selected", str(tmp_path))
    monkeypatch.setattr(picker, "_command", lambda _initial: fake_dialog(1))
    assert (await picker.choose("")).status == "cancelled"
    other = base64.b64encode(b"\\\\wsl.localhost\\Ubuntu\\home").decode()
    monkeypatch.setattr(picker, "_command", lambda _initial: fake_dialog(0, other))
    unreachable = await picker.choose("")
    assert (unreachable.status, unreachable.detail) == ("unavailable", UNREACHABLE)


@pytest.mark.skipif(not shutil.which("wslpath"), reason="Requires WSL.")
async def test_windows_paths_map_to_wsl_paths():
    assert await folders.linux_path("C:\\Windows") == "/mnt/c/Windows"
    distro = os.environ["WSL_DISTRO_NAME"]
    assert await folders.linux_path(f"\\\\wsl.localhost\\{distro}\\home") == "/home"
    assert await folders.linux_path("\\\\server\\share\\x") is None
