"""Read the workspace's Git status and diffs on this computer, for the user's review.

Git runs outside the sandbox, so every call disables what could execute repository-provided
programs (fsmonitor, hooks, external diff and textconv drivers), takes no optional locks and
gets a minimal environment. Nothing here writes to the repository."""

import asyncio
import os
import shutil
from pathlib import Path

from glassbox.domain import AppError, GitDiff, GitFile, GitStatus
from glassbox.domain.git import EMPTY_TREE, new_file_diff, parse_status, split_diff
from glassbox.infrastructure.workspace.files import TEXT_FILE_LIMIT, Workspace, private_name

GIT_ENVIRONMENT = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TZ",
    "XDG_CONFIG_HOME",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
)
SAFE_OPTIONS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "core.quotepath=false",
    "-c",
    "color.ui=false",
    "--no-pager",
)
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color")
STATUS_LIMIT = 2 * 1024 * 1024
DIFF_LIMIT = 512 * 1024
FILE_LIMIT = 2000


async def _git(root: Path, *args: str, limit: int = STATUS_LIMIT) -> tuple[int, str, str, bool]:
    """Run one read-only Git command; returns exit code, stdout, stderr and truncation."""
    executable = shutil.which("git")
    if executable is None:
        raise AppError("git_unavailable", "Git is not installed on this computer.")
    environment = {key: os.environ[key] for key in GIT_ENVIRONMENT if key in os.environ}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    process = await asyncio.create_subprocess_exec(
        executable,
        *SAFE_OPTIONS,
        *args,
        cwd=root,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = bytearray(), bytearray()

    async def collect(stream: asyncio.StreamReader | None, kept: bytearray, size: int) -> None:
        while stream is not None and (chunk := await stream.read(65_536)):
            if len(kept) <= size:
                kept.extend(chunk[: size + 1 - len(kept)])
                if len(kept) > size:
                    # Enough output: stop Git, then drain the pipe so its end is seen.
                    process.kill()

    try:
        async with asyncio.timeout(15):
            await asyncio.gather(
                collect(process.stdout, stdout, limit), collect(process.stderr, stderr, 4096)
            )
            code = await process.wait()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise AppError("git_timeout", "Git did not answer within 15 seconds.") from None
    return (
        code,
        stdout[:limit].decode("utf-8", "replace"),
        stderr[:4096].decode("utf-8", "replace").strip(),
        len(stdout) > limit,
    )


def _private(path: str) -> bool:
    """Credentials, Git metadata and databases: names are shown, content never is."""
    return not path or any(private_name(part) for part in Path(path).parts)


async def status(workspace: Workspace) -> GitStatus:
    """The branch and changed files of the repository containing the workspace folder."""
    try:
        code, prefix, error, _ = await _git(workspace.root, "rev-parse", "--show-prefix")
    except AppError as exc:
        return GitStatus(repository=False, reason=exc.message)
    if code != 0:
        reason = error.splitlines()[0].removeprefix("fatal: ") if error else None
        return GitStatus(repository=False, reason=reason)
    code, output, error, truncated = await _git(
        workspace.root,
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
    )
    if code != 0 and not truncated:
        raise AppError("git_failed", error or "git status failed.")
    parsed = parse_status(output, prefix.strip())
    files = [GitFile(**item, private=_private(item["path"])) for item in parsed.pop("files")]
    parsed.pop("initial")
    return GitStatus(
        repository=True,
        files=files[:FILE_LIMIT],
        truncated=truncated or len(files) > FILE_LIMIT,
        **parsed,
    )


async def _base(root: Path) -> str:
    """HEAD, or the empty tree before the first commit."""
    code, _, _, _ = await _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    return "HEAD" if code == 0 else EMPTY_TREE


async def diff(workspace: Workspace, path: str | None = None) -> GitDiff:
    """Uncommitted changes against HEAD, including untracked text files, without private files.

    With `path`, only that file; it must be a path the file tools could open."""
    if path is not None:
        workspace.path(path)  # Rejects traversal, links and private names.
    current = await status(workspace)
    if not current.repository:
        raise AppError("git_unavailable", current.reason or "This folder is not a Git repository.")
    selected = [item for item in current.files if path is None or item.path == path]
    tracked = [item for item in selected if item.kind != "untracked"]
    untracked = [item for item in selected if item.kind == "untracked"]
    omitted = sorted({item.path for item in selected if item.private})
    text, truncated = "", current.truncated
    if tracked:
        code, output, error, truncated_diff = await _git(
            workspace.root,
            "diff",
            *DIFF_OPTIONS,
            await _base(workspace.root),
            "--",
            *(f":(literal){path}" for path in ([path] if path else ["."])),
            limit=DIFF_LIMIT,
        )
        if code != 0 and not truncated_diff:
            raise AppError("git_failed", error or "git diff failed.")
        truncated |= truncated_diff
        for name, section in split_diff(output):
            if _private(name):
                omitted.append(name or "(unnamed file)")
            else:
                text += section
    for item in untracked:
        if item.private or len(text) >= DIFF_LIMIT:
            truncated |= not item.private
            continue
        try:
            target = workspace.path(item.path)
        except AppError:
            omitted.append(item.path)  # Linked or dependency paths the file tools skip.
            continue
        try:
            content = workspace.read_path(target, TEXT_FILE_LIMIT)
        except AppError:
            text += f"diff --git a/{item.path} b/{item.path}\nnew file (binary or unreadable)\n"
            continue
        text += new_file_diff(item.path, content)
    if len(text) > DIFF_LIMIT:
        text, truncated = text[:DIFF_LIMIT].rsplit("\n", 1)[0] + "\n", True
    return GitDiff(path=path, diff=text, truncated=truncated, omitted=sorted(set(omitted)))
