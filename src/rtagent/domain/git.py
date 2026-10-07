"""Parse Git's machine-readable status and split unified diffs by file."""

import re
from typing import Any

# Git's well-known hash of the empty tree: the diff base before the first commit.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DIFF_START = re.compile(r"^diff --git ", re.M)
PLAIN_HEADER = re.compile(r"diff --git a/(.*?) b/(.*)")


def parse_status(output: str, prefix: str = "") -> dict[str, Any]:
    """Read `git status --porcelain=v2 --branch -z` output.

    Paths are repository-relative; `prefix` (from `git rev-parse --show-prefix`) is removed
    so they become relative to the workspace folder inside the repository."""

    def local(path: str) -> str:
        return path[len(prefix) :] if prefix and path.startswith(prefix) else path

    status: dict[str, Any] = {
        "branch": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "initial": False,
        "files": [],
    }
    entries = output.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if entry.startswith("# "):
            key, _, value = entry[2:].partition(" ")
            if key == "branch.oid":
                status["initial"] = value == "(initial)"
            elif key == "branch.head":
                status["branch"] = None if value == "(detached)" else value
            elif key == "branch.upstream":
                status["upstream"] = value
            elif key == "branch.ab":
                ahead, behind = value.split()
                status["ahead"], status["behind"] = int(ahead), abs(int(behind))
            continue
        kind = entry[0]
        if kind == "?":
            status["files"].append(
                {"path": local(entry[2:]), "index": "?", "worktree": "?", "kind": "untracked"}
            )
            continue
        fields = entry.split(" ")
        codes = fields[1]
        if kind == "1":
            path, original, label = " ".join(fields[8:]), None, "changed"
        elif kind == "2":
            # The original path follows as the next NUL-separated entry.
            path, label = " ".join(fields[9:]), "renamed"
            original = local(entries[index]) if index < len(entries) else None
            index += 1
        elif kind == "u":
            path, original, label = " ".join(fields[10:]), None, "unmerged"
        else:
            continue
        status["files"].append(
            {
                "path": local(path),
                "original_path": original,
                "index": codes[0],
                "worktree": codes[1],
                "kind": label,
            }
        )
    return status


def split_diff(text: str) -> list[tuple[str, str]]:
    """Split a multi-file unified diff into (new path, section) pairs, in order.

    A header Git had to quote (a path with control characters or quotes) yields an empty
    path, so callers can withhold a section whose file they cannot identify."""
    starts = [match.start() for match in DIFF_START.finditer(text)]
    sections = []
    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(text)
        section = text[start:end]
        header = PLAIN_HEADER.fullmatch(section.split("\n", 1)[0])
        sections.append((header[2] if header else "", section))
    return sections


def new_file_diff(path: str, text: str) -> str:
    """Show an untracked text file as a diff that adds every line."""
    lines = text.splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{body}"
    )
