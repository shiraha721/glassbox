"""Studio data lives in a per-user folder, never in the project or the source checkout."""

import sqlite3
import sys
from contextlib import closing

import pytest

from rtagent.domain import AppError
from rtagent.infrastructure.config import Settings, default_data_dir, move_legacy_data
from rtagent.infrastructure.workspace.files import Workspace


def legacy_store(folder):
    """An earlier version's `var/`: a WAL database with an unmerged log, keys and Skills."""
    folder.mkdir()
    database = sqlite3.connect(folder / "app.db")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE sessions (title TEXT)")
    database.execute("INSERT INTO sessions VALUES ('kept')")
    database.commit()
    (folder / "credentials.json").write_text('{"KEY": "x"}', encoding="utf-8")
    (folder / "skills" / "mine").mkdir(parents=True)
    (folder / "skills" / "mine" / "SKILL.md").write_text("skill", encoding="utf-8")
    (folder / "e2e-123").mkdir()  # test output that is not studio data
    return database


@pytest.mark.skipif(sys.platform != "linux", reason="XDG locations apply on Linux and WSL")
def test_the_default_folder_follows_the_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / "rtagent"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert default_data_dir().parent.name == "share"


def test_old_data_moves_once_and_only_when_no_one_has_it_open(tmp_path):
    legacy, target = tmp_path / "var", tmp_path / "data"
    database = legacy_store(legacy)
    # A studio still running from the old folder holds the database: nothing moves.
    with pytest.raises(RuntimeError, match="open in another process"):
        move_legacy_data(legacy, target)
    assert (legacy / "app.db").exists() and not (target / "app.db").exists()
    database.close()

    moved = move_legacy_data(legacy, target)
    assert moved == ["credentials.json", "skills", "app.db"]
    assert (target / "skills" / "mine" / "SKILL.md").read_text(encoding="utf-8") == "skill"
    with closing(sqlite3.connect(target / "app.db")) as copy:
        assert copy.execute("SELECT title FROM sessions").fetchall() == [("kept",)]
    # The log was folded into the file, so no WAL file was left behind or carried along.
    assert not list(target.glob("app.db-*")) and not list(legacy.glob("app.db*"))
    assert (legacy / "e2e-123").is_dir() and "data" in (legacy / "MOVED.txt").read_text()
    assert move_legacy_data(legacy, target) == []


@pytest.mark.skipif(sys.platform != "linux", reason="XDG locations apply on Linux and WSL")
def test_only_the_default_location_adopts_old_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    legacy_store(tmp_path / "var").close()
    explicit = Settings(_env_file=None, data_dir=tmp_path / "chosen")
    explicit.initialize()
    assert (tmp_path / "var" / "app.db").exists()
    settings = Settings(_env_file=None)
    settings.initialize()
    assert settings.data_dir == tmp_path / "xdg" / "rtagent"
    assert (settings.data_dir / "app.db").exists() and not (tmp_path / "var" / "app.db").exists()


def test_memory_is_kept_outside_the_project(tmp_path):
    root, data = tmp_path / "project", tmp_path / "data"
    (root / ".rtagent" / "packages").mkdir(parents=True)
    (root / ".rtagent" / "MEMORY.md").write_text("Old notes", encoding="utf-8")
    workspace = Workspace(root, data)
    # Memory an earlier version kept in the project moves out on first use.
    memory = workspace.memory()
    assert memory["content"] == "Old notes"
    assert not (root / ".rtagent" / "MEMORY.md").exists()
    assert (root / ".rtagent" / "packages").is_dir()  # agent-installed packages stay
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
