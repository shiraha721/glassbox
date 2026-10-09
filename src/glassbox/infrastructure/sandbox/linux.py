"""Bash execution in native Linux namespaces using bubblewrap."""

import asyncio
import codecs
import json
import os
import queue
import shutil
import signal
from pathlib import Path

from glassbox.domain import AppError
from glassbox.infrastructure.sandbox.command_io import (
    CommandControl,
    CommandOutput,
    OutputCallback,
    command_result,
)
from glassbox.infrastructure.workspace.files import DEPENDENCY_DIRS, Workspace, private_name
from glassbox.infrastructure.workspace.toolchains import validate_toolchains

# The driver's first output line names the process group, before any untrusted command runs.
PID_MARKER = b"GLASSBOX_PID:"


def process_group(line: bytes) -> int | None:
    """Read the driver's process-group line; anything else means the sandbox did not start."""
    value = line.strip().removeprefix(PID_MARKER)
    return int(value) if line.startswith(PID_MARKER) and value.isdigit() else None


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
        """Mask server state and credentials even when they live inside the workspace.

        Other links are allowed: they resolve inside the sandbox's own mount namespace, where
        host paths outside the bound system directories do not exist and masked workspace
        paths stay masked. Only an excluded name that is itself a link cannot be masked safely,
        because bubblewrap would follow it when mounting the mask."""
        hidden = []
        for directory, dirs, files in os.walk(self.workspace.root, followlinks=False):
            # Dependency trees stay visible but are not searched for user credentials; this
            # keeps the per-command scan fast in projects with large node_modules folders.
            dirs[:] = [name for name in dirs if name.casefold() not in DEPENDENCY_DIRS]
            for name in [*dirs, *files]:
                path = Path(directory) / name
                if not private_name(name) and path != self.workspace.data_dir:
                    continue
                if path.is_symlink() or path.is_junction():
                    raise AppError(
                        "sandbox_path",
                        "Excluded paths cannot be links; replace this link before running "
                        f"commands: {path.relative_to(self.workspace.root)}",
                    )
                hidden.append(path)
                if name in dirs:
                    dirs.remove(name)
        return hidden

    @staticmethod
    def _system_mounts() -> list[str]:
        """Expose read-only system programs and configuration with the host's root layout.

        /etc is needed by ordinary tools (Debian's awk is an /etc/alternatives link, getpwuid
        reads /etc/passwd). It is read-only and files restricted to root stay unreadable."""
        args = ["--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
        for name in ("bin", "sbin", "lib", "lib32", "lib64", "libx32"):
            host = Path("/") / name
            if host.is_symlink():
                args.extend(["--symlink", os.readlink(host), str(host)])
            elif host.is_dir():
                args.extend(["--ro-bind", str(host), str(host)])
        return args

    async def _arguments(
        self,
        command: str,
        cwd: str,
        mode: str,
        root: str,
        toolchains: list[tuple[Path, Path]] | None = None,
    ) -> list[str]:
        directory = self.workspace.path(cwd)
        if not directory.is_dir():
            raise AppError("tool_error", "Bash cwd must be an existing workspace directory.")
        mount = "--ro-bind" if mode == "read-only" else "--bind"
        args = [
            shutil.which("bwrap") or "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            *self._system_mounts(),
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
            "LANG",
            "C.UTF-8",
            mount,
            root,
            "/workspace",
        ]
        args.extend(["--dir", "/toolchains"])
        paths = []
        for index, (configured, path) in enumerate(toolchains or [], 1):
            mountpoint = f"/toolchains/{index}"
            args.extend(["--ro-bind", str(path), mountpoint])
            # Host paths stay valid too: virtual environments link to their base interpreter.
            for alias in dict.fromkeys([path, configured]):
                args.extend(["--ro-bind", str(path), str(alias)])
            args.extend(["--setenv", f"GLASSBOX_TOOLCHAIN_{index}", mountpoint])
            paths.extend([mountpoint, mountpoint + "/bin"])
        args.extend(
            [
                "--setenv",
                "PATH",
                ":".join([*paths, "/usr/bin", "/bin"]),
                "--setenv",
                "PYTHONPATH",
                "/workspace/.glassbox/packages",
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--setenv",
                "PIP_NO_INDEX",
                "1",
                "--setenv",
                "PIP_DISABLE_PIP_VERSION_CHECK",
                "1",
            ]
        )
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
    async def _drain(stream: asyncio.StreamReader, output: CommandOutput, key: str) -> None:
        """Drain a pipe completely so the child cannot block, retaining bounded text."""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while chunk := await stream.read(8192):
            output.write(key, decoder.decode(chunk))
        output.write(key, decoder.decode(b"", final=True))

    @staticmethod
    async def _feed_input(stream: asyncio.StreamWriter, control: CommandControl) -> None:
        """Write explicit queued bytes to namespace stdin without terminal escape handling."""
        try:
            while not control.stop.is_set() and not control.input_stop.is_set():
                try:
                    data = await asyncio.to_thread(control.inputs.get, True, 0.1)
                except queue.Empty:
                    continue
                if data is None:
                    break
                stream.write(data)
                await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            control.input_closed = True
            stream.close()

    async def execute(
        self,
        command: str,
        cwd: str = ".",
        mode: str = "workspace-write",
        timeout: float = 60,
        *,
        control: CommandControl | None = None,
        on_output: OutputCallback | None = None,
        toolchain_roots: list[str] | None = None,
    ) -> str:
        """Run a command with bounded output and terminate all children on stop or timeout."""
        process, group, readers = None, None, []
        writer = None
        # Background processes persist snapshots; a slower cadence bounds their write rate.
        output = CommandOutput(on_output, interval=0.5 if control else 0.1)
        publisher = asyncio.create_task(output.drain())
        timed_out = False
        try:
            async with asyncio.timeout(timeout):
                # Bubblewrap binds trusted runtimes read-only in place; unlike the Windows
                # AppContainer, no copy is needed to avoid changing their permissions.
                roots = toolchain_roots or []
                resolved = await asyncio.to_thread(validate_toolchains, roots, self.workspace.root)
                toolchains = list(zip(map(Path, roots), resolved, strict=True))
                arguments = await self._arguments(
                    command, cwd, mode, str(self.workspace.root), toolchains
                )
                # The first line is emitted by our fixed driver before any untrusted command.
                driver = f'printf "{PID_MARKER.decode()}%s\\n" "$$"; exec "$@"'
                process = await self._spawn(
                    [
                        shutil.which("setsid") or "/usr/bin/setsid",
                        "--wait",
                        "/bin/sh",
                        "-c",
                        driver,
                        "glassbox",
                        *arguments,
                    ],
                    pipe_input=control is not None,
                )
                group = process_group(await process.stdout.readline())
                if group is None:
                    raise AppError(
                        "sandbox_unavailable",
                        "Bash requires bubblewrap and util-linux on Linux.",
                    )
                if control:
                    control.started(group)
                    writer = asyncio.create_task(self._feed_input(process.stdin, control))
                readers = [
                    asyncio.create_task(self._drain(process.stdout, output, "stdout")),
                    asyncio.create_task(self._drain(process.stderr, output, "stderr")),
                ]
                while control and process.returncode is None:
                    if control.stop.is_set():
                        break
                    await asyncio.sleep(0.05)
                if control and control.stop.is_set():
                    await self._stop(process, group)
                await process.wait()
                await self._stop(process, group)
                await asyncio.shield(asyncio.gather(*readers))
        except TimeoutError:
            timed_out = True
        except OSError as exc:
            raise AppError(
                "sandbox_unavailable",
                "Install Bash, bubblewrap, and util-linux on this Linux host.",
            ) from exc
        finally:
            if control:
                control.input_stop.set()
            # Cancellation must finish cleanup before the runtime records the cancelled call.
            if process is not None:
                if group is None and process.returncode is None:
                    try:
                        async with asyncio.timeout(3):
                            group = process_group(await process.stdout.readline())
                    except TimeoutError:
                        pass
                await asyncio.shield(self._stop(process, group))
            if readers:
                await asyncio.gather(*readers)
            if writer:
                await writer
            await output.close(publisher)
        stdout, stdout_truncated = output.result("stdout")
        stderr, stderr_truncated = output.result("stderr")
        return command_result(
            {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": process.returncode if process else None,
                "timed_out": timed_out,
                "truncated": stdout_truncated or stderr_truncated,
                "sandbox": self.backend,
                "mode": mode,
                "network": "disabled",
                "effects_may_be_partial": timed_out or bool(control and control.stop.is_set()),
                "workspace_changes": None,
            }
        )

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
