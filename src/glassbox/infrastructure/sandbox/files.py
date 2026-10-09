"""Stage a filtered Windows workspace and copy back edits after its processes stop."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from glassbox.domain import AppError
from glassbox.infrastructure.workspace.files import Workspace, excluded_name

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 10_000


class SandboxFiles:
    """Keep credentials out of the sandbox without modifying the original project's ACLs."""

    def __init__(self, workspace: Workspace, destination: Path) -> None:
        """Bind one disposable directory and remember original file fingerprints."""
        self.workspace, self.path = workspace, destination
        self.before: dict[str, str] = {}
        self.directories: set[str] = set()

    def _collect(self, root: Path) -> tuple[dict[str, bytes], set[str], list[str]]:
        def fail_walk(error: OSError) -> None:
            # An unreadable directory must not look like deleted files during reconciliation.
            raise error

        files, directories, targets, ignored = {}, set(), set(), []
        total, count = 0, 0
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=fail_walk):
            for name in [*dirs, *names]:
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                if excluded_name(name) or path in {self.workspace.data_dir, self.path.parent}:
                    if name in dirs:
                        dirs.remove(name)
                    if len(ignored) < 20:
                        ignored.append(relative)
                    continue
                # Resolve host destinations before any writes, rejecting aliases and traversal.
                target = str(self.workspace.path(relative)).casefold()
                if path.is_symlink() or path.is_junction() or target in targets:
                    raise AppError(
                        "sandbox_path", f"Unsupported linked or aliased path: {relative}"
                    )
                targets.add(target)
                count += 1
                if count > MAX_FILES:
                    raise AppError("sandbox_workspace_limit", "Sandbox exceeds 10,000 entries.")
                if path.is_dir():
                    directories.add(relative)
                elif path.is_file() and path.stat().st_nlink == 1:
                    with path.open("rb") as handle:
                        data = handle.read(MAX_BYTES - total + 1)
                    total += len(data)
                    if total > MAX_BYTES:
                        raise AppError("sandbox_workspace_limit", "Sandbox files exceed 64 MiB.")
                    files[relative] = data
                else:
                    raise AppError(
                        "sandbox_path", f"Unsupported special or hard-linked file: {relative}"
                    )
        return files, directories, ignored

    def open(self) -> None:
        """Copy bounded ordinary files, without importing host permissions or excluded paths."""
        files, self.directories, _ = self._collect(self.workspace.root)
        self.path.mkdir()
        for name in sorted(self.directories, key=lambda item: item.count("/")):
            (self.path / name).mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (self.path / name).write_bytes(data)
            self.before[name] = hashlib.sha256(data).hexdigest()

    def reconcile(self) -> dict:
        """Preflight concurrent edits before copying changes back; this is not a transaction."""
        files, directories, ignored = self._collect(self.path)
        changed = {
            name
            for name in self.before.keys() | files.keys()
            if self.before.get(name)
            != (hashlib.sha256(files[name]).hexdigest() if name in files else None)
        }
        with self.workspace.lock:
            for name in changed:
                target = self.workspace.path(name)
                current = (
                    hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                )
                if current != self.before.get(name):
                    raise AppError(
                        "file_conflict",
                        f"Host file changed during command execution: {name}. Sandbox edits were not copied back.",
                        409,
                    )
            for name in changed - files.keys():
                self.workspace.path(name).unlink()
            for name in sorted(self.directories - directories, key=lambda item: -item.count("/")):
                try:
                    self.workspace.path(name).rmdir()
                except OSError:
                    pass  # Preserve directories containing new host-side files.
            for name in sorted(directories, key=lambda item: item.count("/")):
                self.workspace.path(name).mkdir(parents=True, exist_ok=True)
            for name in changed & files.keys():
                target = self.workspace.path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(files[name])
                try:
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        return {"changed_files": len(changed), "ignored_paths": ignored}
