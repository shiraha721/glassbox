"""Bash execution in native Linux namespaces using bubblewrap."""

import asyncio
import json
import os
import signal
from pathlib import Path

from rtagent.domain import AppError
from rtagent.workspace import Workspace, excluded_name

OUTPUT_LIMIT = 24_000  # Per stream; leave room for the JSON envelope in a tool result.


class BashSandbox:
    """Expose one workspace mount, temporary scratch space, and no host network."""

    def __init__(self, workspace: Workspace) -> None:
        """Keep host-specific launch details out of model-visible command arguments."""
        self.workspace = workspace
        self.backend = "bubblewrap"
        self.status_cache: dict | None = None

    async def _spawn(self, args: list[str], pipe_input: bool = False) -> asyncio.subprocess.Process:
        # Only the explicit `bash -c` argument is shell code. Host launch arguments are never joined.
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if pipe_input else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    def _hidden_paths(self) -> list[Path]:
        """Mask server state and credentials even when they live inside the workspace."""
        hidden = []
        for directory, dirs, files in os.walk(self.workspace.root, followlinks=False):
            for name in [*dirs, *files]:
                path = Path(directory) / name
                excluded = excluded_name(name) or path == self.workspace.data_dir
                if path.is_symlink() or path.is_junction():
                    # Links must not expose paths excluded by the workspace policy.
                    if not excluded:
                        raise AppError(
                            "sandbox_path",
                            f"Remove the linked path before running Bash: {path.relative_to(self.workspace.root)}",
                        )
                    raise AppError(
                        "sandbox_path",
                        "Linked excluded directories cannot be safely masked; use a workspace without junctions.",
                    )
                if excluded:
                    hidden.append(path)
                    if name in dirs:
                        dirs.remove(name)
        return hidden

    async def _arguments(self, command: str, cwd: str, mode: str, root: str) -> list[str]:
        directory = self.workspace.path(cwd)
        if not directory.is_dir():
            raise AppError("tool_error", "Bash cwd must be an existing workspace directory.")
        mount = "--ro-bind" if mode == "read-only" else "--bind"
        args = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home/agent",
            "--clearenv",
            "--setenv",
            "HOME",
            "/home/agent",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            mount,
            root,
            "/workspace",
        ]
        for path in await asyncio.to_thread(self._hidden_paths):
            target = "/workspace/" + path.relative_to(self.workspace.root).as_posix()
            if path.is_dir():
                args.extend(["--tmpfs", target, "--remount-ro", target])
            else:
                args.extend(["--ro-bind", "/dev/null", target])
        workdir = "/workspace/" + directory.relative_to(self.workspace.root).as_posix()
        args.extend(["--remount-ro", "/"])
        args.extend(["--chdir", workdir, "--", "/bin/bash", "--noprofile", "--norc", "-c", command])
        return args

    async def _stop(self, process: asyncio.subprocess.Process, group: int | None) -> None:
        """Kill the complete Linux process group on completion, cancellation, or timeout."""
        try:
            os.killpg(group or process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.returncode is None:
            process.kill()
        await process.wait()

    @staticmethod
    async def _drain(stream: asyncio.StreamReader) -> tuple[str, bool]:
        """Drain pipes to avoid deadlocks while retaining only a bounded output prefix."""
        chunks, size, truncated = [], 0, False
        while chunk := await stream.read(8192):
            remaining = max(0, OUTPUT_LIMIT - size)
            if remaining:
                chunks.append(chunk[:remaining])
            size += min(remaining, len(chunk))
            truncated |= len(chunk) > remaining
        return b"".join(chunks).decode("utf-8", errors="replace"), truncated

    async def execute(
        self, command: str, cwd: str = ".", mode: str = "workspace-write", timeout: float = 60
    ) -> str:
        """Run a command with bounded output and terminate all children on stop or timeout."""
        process, group, readers = None, None, []
        timed_out = False
        try:
            async with asyncio.timeout(timeout):
                root = str(self.workspace.root)
                arguments = await self._arguments(command, cwd, mode, root)
                # The first line is emitted by our fixed driver before any untrusted command.
                driver = 'printf "RTAGENT_PID:%s\\n" "$$"; exec "$@"'
                process = await self._spawn(
                    ["/usr/bin/setsid", "--wait", "/bin/sh", "-c", driver, "rtagent", *arguments]
                )
                marker = await process.stdout.readline()
                if not marker.startswith(b"RTAGENT_PID:") or not marker.strip()[12:].isdigit():
                    raise AppError(
                        "sandbox_unavailable",
                        "Bash requires bubblewrap and util-linux on Linux.",
                    )
                group = int(marker.strip()[12:])
                readers = [
                    asyncio.create_task(self._drain(process.stdout)),
                    asyncio.create_task(self._drain(process.stderr)),
                ]
                await process.wait()
                await asyncio.shield(asyncio.gather(*readers))
        except TimeoutError:
            timed_out = True
        except OSError as exc:
            raise AppError(
                "sandbox_unavailable",
                "Install Bash, bubblewrap, and util-linux on this Linux host.",
            ) from exc
        finally:
            # Cancellation must finish cleanup before the runtime records the cancelled call.
            if process is not None:
                if group is None and process.returncode is None:
                    try:
                        async with asyncio.timeout(3):
                            marker = await process.stdout.readline()
                            if marker.startswith(b"RTAGENT_PID:") and marker.strip()[12:].isdigit():
                                group = int(marker.strip()[12:])
                    except TimeoutError:
                        pass
                await asyncio.shield(self._stop(process, group))
            if readers:
                await asyncio.gather(*readers)
        output = [task.result() for task in readers] if readers else [("", False), ("", False)]
        result = {
            "stdout": output[0][0],
            "stderr": output[1][0],
            "exit_code": process.returncode if process else None,
            "timed_out": timed_out,
            "truncated": any(item[1] for item in output),
            "sandbox": self.backend,
            "mode": mode,
            "network": "disabled",
            "effects_may_be_partial": timed_out,
            "workspace_changes": None,
        }
        # JSON escaping can multiply byte size, even after the stream byte limits were applied.
        while len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
            result["stdout"] = result["stdout"][: len(result["stdout"]) // 2]
            result["stderr"] = result["stderr"][: len(result["stderr"]) // 2]
            result["truncated"] = True
        return json.dumps(result, ensure_ascii=False)

    async def status(self, refresh: bool = False) -> dict:
        """Check the real sandbox without making application startup depend on it."""
        if self.status_cache is not None and not refresh:
            return self.status_cache
        try:
            result = json.loads(
                await self.execute("printf sandbox-ready", mode="read-only", timeout=12)
            )
            ready = result["exit_code"] == 0 and result["stdout"] == "sandbox-ready"
            detail = (
                "Workspace mount only; host network and credentials are hidden."
                if ready
                else result["stderr"] or "Sandbox check timed out."
            )
        except AppError as exc:
            ready, detail = False, exc.message
        self.status_cache = {
            "backend": self.backend,
            "ready": ready,
            "network": "disabled",
            "detail": detail,
        }
        return self.status_cache
