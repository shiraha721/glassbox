"""Copy Windows workspaces into a private Linux filesystem and reconcile changed files.

DrvFS is case-insensitive: a bind mask on .env can be bypassed by opening .ENV.
Never expose that filesystem directly to an untrusted WSL command. Only filtered
files cross this boundary; normal Linux workspaces do not need the copy step.
"""

import asyncio
import hashlib
import io
import os
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from rtagent.domain import AppError
from rtagent.workspace import Workspace, excluded_name

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 10_000


class SandboxFiles:
    """Own one disposable Linux copy, with conflict checks before copying edits back."""

    def __init__(
        self, workspace: Workspace, spawn: Callable[..., Awaitable[asyncio.subprocess.Process]]
    ) -> None:
        """Remember original file fingerprints without retaining credentials or excluded files."""
        self.workspace, self.spawn = workspace, spawn
        self.path: str | None = None
        self.before: dict[str, str] = {}
        self.directories: set[str] = set()
        self.ready = False

    def _pack(self) -> bytes:
        buffer = io.BytesIO()
        total = 0
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for directory, dirs, files in os.walk(self.workspace.root, followlinks=False):
                for name in [*dirs, *files]:
                    path = Path(directory) / name
                    if excluded_name(name) or path == self.workspace.data_dir:
                        if name in dirs:
                            dirs.remove(name)
                        continue
                    relative = path.relative_to(self.workspace.root).as_posix()
                    checked = self.workspace.path(relative)
                    info = tarfile.TarInfo(relative)
                    info.mode = 0o755 if os.name == "nt" else checked.stat().st_mode & 0o777
                    if checked.is_dir():
                        self.directories.add(relative)
                        info.type = tarfile.DIRTYPE
                        archive.addfile(info)
                    else:
                        with checked.open("rb") as handle:
                            data = handle.read(MAX_BYTES - total + 1)
                        total += len(data)
                        if total > MAX_BYTES:
                            raise AppError(
                                "sandbox_workspace_limit",
                                "WSL sandbox input exceeds 64 MiB; select a smaller workspace.",
                            )
                        self.before[relative] = hashlib.sha256(data).hexdigest()
                        info.size = len(data)
                        archive.addfile(info, io.BytesIO(data))
                    if len(self.before) + len(self.directories) > MAX_FILES:
                        raise AppError(
                            "sandbox_workspace_limit", "WSL sandbox input exceeds 10,000 entries."
                        )
        return buffer.getvalue()

    async def _command(self, args: list[str], data: bytes | None = None) -> bytes:
        process = await self.spawn(args, pipe_input=data is not None)
        try:
            async with asyncio.timeout(15):
                output, _ = await process.communicate(data)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            raise AppError(
                "sandbox_workspace", "Could not prepare or clean up the private WSL workspace."
            )
        return output

    async def open(self) -> str:
        """Stage only allowed files on Linux, where case aliases cannot bypass exclusions."""
        data = await asyncio.to_thread(self._pack)
        # Assign the exact owned path before awaiting creation so cancellation can always remove it.
        self.path = "/tmp/rtagent-" + uuid4().hex
        await self._command(["/bin/mkdir", "--mode=700", "--", self.path])
        await self._command(["/usr/bin/tar", "-xf", "-", "--no-same-owner", "-C", self.path], data)
        self.ready = True
        return self.path

    async def _export(self) -> bytes:
        process = await self.spawn(["/usr/bin/tar", "-cf", "-", "-C", self.path, "."])
        chunks, size = [], 0
        try:
            async with asyncio.timeout(15):
                while chunk := await process.stdout.read(65_536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_BYTES + MAX_FILES * 1536:
                        raise AppError(
                            "sandbox_workspace_limit",
                            "WSL sandbox output exceeds the copy-back limit.",
                        )
                await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            raise AppError(
                "sandbox_workspace", "Could not collect sandbox edits; host files were not updated."
            )
        return b"".join(chunks)

    def _reconcile(self, data: bytes) -> dict:
        files, directories, targets = {}, set(), set()
        ignored = []
        size = 0
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for count, member in enumerate(archive):
                if count > MAX_FILES:
                    raise AppError(
                        "sandbox_workspace_limit", "Sandbox output exceeds 10,000 entries."
                    )
                name = member.name.removeprefix("./")
                if name in {"", "."}:
                    continue
                if any(excluded_name(part) for part in Path(name).parts):
                    if len(ignored) < 20:
                        ignored.append(name)
                    continue
                target = str(self.workspace.path(name)).casefold()
                if target in targets:
                    raise AppError(
                        "sandbox_path", "Case-colliding sandbox paths cannot be copied to Windows."
                    )
                targets.add(target)
                if member.isdir():
                    directories.add(name)
                elif member.isfile() and not member.issparse():
                    size += member.size
                    if size > MAX_BYTES:
                        raise AppError(
                            "sandbox_workspace_limit", "Sandbox output files exceed 64 MiB."
                        )
                    files[name] = archive.extractfile(member).read()
                else:
                    raise AppError(
                        "sandbox_path",
                        "Sandbox links and special files cannot be copied into the Windows workspace.",
                    )
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
                        f"Host file changed during Bash execution: {name}. Sandbox edits were not copied back.",
                        409,
                    )
            # Remove old files before making directories, so a file-to-directory edit works.
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

    async def close(self, writable: bool) -> dict | None:
        """Copy permitted changes back after process cleanup, then remove only our temporary root."""
        if self.path is None:
            return
        try:
            if self.ready and writable:
                return await asyncio.to_thread(self._reconcile, await self._export())
        finally:
            # Only the exact /tmp/rtagent-<UUID> directory created above is removed.
            await self._command(["/bin/rm", "--recursive", "--force", "--", self.path])
