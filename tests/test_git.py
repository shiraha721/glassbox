"""The Git panel reads status and diffs on the host without running repository programs."""

import shutil
import subprocess

import pytest

from glassbox.domain.git import parse_status, split_diff

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")


def test_porcelain_status_is_parsed_relative_to_the_workspace():
    output = "\0".join(
        [
            "# branch.oid 1234",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -1",
            "1 .M N... 100644 100644 100644 abc abc app/main file.py",
            "2 R. N... 100644 100644 100644 abc abc R100 app/new.py",
            "app/old.py",
            "u UU N... 100644 100644 100644 100644 a b c app/conflict.py",
            "? app/notes.txt",
            "",
        ]
    )
    status = parse_status(output, "app/")
    assert (status["branch"], status["upstream"], status["ahead"], status["behind"]) == (
        "main",
        "origin/main",
        2,
        1,
    )
    assert [(f["path"], f["kind"], f["index"], f["worktree"]) for f in status["files"]] == [
        ("main file.py", "changed", ".", "M"),
        ("new.py", "renamed", "R", "."),
        ("conflict.py", "unmerged", "U", "U"),
        ("notes.txt", "untracked", "?", "?"),
    ]
    assert status["files"][1]["original_path"] == "old.py"
    assert parse_status("# branch.oid (initial)\0# branch.head (detached)\0")["branch"] is None


def test_diffs_split_by_file_and_quoted_headers_are_unnamed():
    text = (
        "diff --git a/a.py b/a.py\n+one\n"
        'diff --git "a/odd\\tname" "b/odd\\tname"\n+secret\n'
        "diff --git a/b.py b/b.py\n+two\n"
    )
    assert split_diff(text) == [
        ("a.py", "diff --git a/a.py b/a.py\n+one\n"),
        ("", 'diff --git "a/odd\\tname" "b/odd\\tname"\n+secret\n'),
        ("b.py", "diff --git a/b.py b/b.py\n+two\n"),
    ]


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@needs_git
async def test_status_and_diff_hide_private_content(glassbox_client, tmp_path):
    """Changed files are listed; diffs include text changes but never private files."""
    client, _app = glassbox_client
    root = tmp_path / "workspace"
    git(root, "init", "-q", "-b", "main")
    (root / ".env.local").write_text("TOKEN=old\n", encoding="utf-8")
    git(root, "add", "README.md", ".env.local")
    git(root, "commit", "-q", "-m", "start")
    (root / "README.md").write_text("Local tool evidence\nchanged line\n", encoding="utf-8")
    (root / ".env.local").write_text("TOKEN=new-secret\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=untracked-secret\n", encoding="utf-8")
    (root / "notes.txt").write_text("a note\n", encoding="utf-8")
    # A repository-provided program must never run when Glassbox reads Git.
    git(root, "config", "core.fsmonitor", f"touch {root / 'pwned'}; false")

    status = (await client.get("/api/v1/workspace/git")).json()
    assert status["repository"] and status["branch"] == "main"
    files = {item["path"]: item for item in status["files"]}
    assert files["README.md"]["worktree"] == "M" and not files["README.md"]["private"]
    assert files["notes.txt"]["kind"] == "untracked"
    assert files[".env"]["private"] and files[".env.local"]["private"]

    whole = (await client.get("/api/v1/workspace/git/diff")).json()
    assert "+changed line" in whole["diff"] and "+a note" in whole["diff"]
    assert "secret" not in whole["diff"]
    assert whole["omitted"] == [".env", ".env.local"]
    one = (await client.get("/api/v1/workspace/git/diff", params={"path": "README.md"})).json()
    assert one["diff"].startswith("diff --git a/README.md") and "a note" not in one["diff"]
    denied = await client.get("/api/v1/workspace/git/diff", params={"path": ".env"})
    assert denied.status_code == 403
    assert not (root / "pwned").exists()


@needs_git
async def test_a_folder_outside_git_says_so(glassbox_client):
    client, _app = glassbox_client
    status = (await client.get("/api/v1/workspace/git")).json()
    assert status["repository"] is False and "not a git repository" in status["reason"].lower()
