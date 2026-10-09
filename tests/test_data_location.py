"""Glassbox's data lives in a per-user folder, never in the project or the source checkout."""

import sys

import pytest

from glassbox.domain import AppError
from glassbox.infrastructure.config import default_data_dir
from glassbox.infrastructure.workspace.files import Workspace


@pytest.mark.skipif(sys.platform != "linux", reason="XDG locations apply on Linux and WSL")
def test_the_default_folder_follows_the_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / "glassbox"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert default_data_dir().parent.name == "share"


def test_memory_is_kept_outside_the_project(tmp_path):
    root, data = tmp_path / "project", tmp_path / "data"
    root.mkdir()
    workspace = Workspace(root, data)
    memory = workspace.memory()
    assert memory["content"] == ""
    assert workspace.memory_file.is_relative_to(data) and memory["path"] == str(
        workspace.memory_file
    )
    saved = workspace.save_memory("New notes", memory["hash"])
    assert saved["hash"] != memory["hash"]
    assert workspace.memory_file.read_text(encoding="utf-8") == "New notes"
    assert (workspace.memory_file.parent / "workspace.txt").read_text().strip() == str(
        root.resolve()
    )
    with pytest.raises(AppError) as conflict:
        workspace.save_memory("Stale edit", memory["hash"])
    assert conflict.value.code == "file_conflict"
    # Another workspace has its own memory.
    other = tmp_path / "other"
    other.mkdir()
    assert Workspace(other, data).memory()["content"] == ""
