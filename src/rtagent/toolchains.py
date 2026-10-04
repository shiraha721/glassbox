"""Explicit trusted runtime directories exposed read-only inside native command isolation."""

import os
import threading
import time
from pathlib import Path

from rtagent.domain import AppError
from rtagent.workspace import excluded_name

TOOLCHAIN_LIMIT = 256 * 1024 * 1024


def validate_toolchains(roots: list[str], workspace: Path) -> list[Path]:
    """Reject missing, linked or overly broad runtime roots before granting sandbox access."""
    result = []
    for value in roots:
        path = Path(value)
        if not path.is_absolute() or not path.is_dir() or path.is_symlink() or path.is_junction():
            raise AppError(
                "toolchain_path", "Choose an existing absolute, unlinked runtime directory."
            )
        resolved = path.resolve()
        if (
            resolved == Path(resolved.anchor)
            or resolved == Path.home().resolve()
            or workspace.is_relative_to(resolved)
        ):
            raise AppError(
                "toolchain_path",
                "Choose a specific runtime directory, not a drive, home or workspace ancestor.",
            )
        result.append(resolved)
    return result


def copy_toolchains(
    roots: list[str],
    destination: Path,
    workspace: Path,
    stop: threading.Event | None = None,
    deadline: float | None = None,
) -> list[Path]:
    """Copy bounded ordinary runtime files without changing any original directory ACL."""
    copied, count, total = [], 0, 0
    for index, source in enumerate(validate_toolchains(roots, workspace), 1):
        target = destination / str(index)
        target.mkdir(parents=True)
        for directory, dirs, files in os.walk(source, followlinks=False):
            for name in [*dirs, *files]:
                if (stop and stop.is_set()) or (deadline and time.monotonic() >= deadline):
                    raise AppError(
                        "toolchain_timeout", "Runtime preparation stopped before command launch."
                    )
                item = Path(directory) / name
                if excluded_name(name):
                    if name in dirs:
                        dirs.remove(name)
                    continue
                if item.is_symlink() or item.is_junction():
                    raise AppError("toolchain_path", "Runtime directories cannot contain links.")
                count += 1
                if count > 20000:
                    raise AppError("toolchain_limit", "Runtime copies exceed 20,000 entries.")
                output = target / item.relative_to(source)
                if item.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                elif item.is_file():
                    size = item.stat().st_size
                    if size + total > TOOLCHAIN_LIMIT:
                        raise AppError("toolchain_limit", "Runtime copies exceed 256 MiB.")
                    with item.open("rb") as handle:
                        content = handle.read(size + 1)
                    if len(content) != size:
                        raise AppError(
                            "toolchain_changed", "Runtime file changed while preparing the sandbox."
                        )
                    total += len(content)
                    if total > TOOLCHAIN_LIMIT:
                        raise AppError("toolchain_limit", "Runtime copies exceed 256 MiB.")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(content)
                else:
                    raise AppError(
                        "toolchain_path", "Runtime directories must contain ordinary files."
                    )
        copied.append(target)
    return copied
