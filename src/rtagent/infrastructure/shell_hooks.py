"""Run user-configured hook commands on this computer with bounded time and output."""

import asyncio
import json
import os
import signal
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtagent.domain import ShellHook

# Enough to find programs and reach the desktop session (for notifications); saved provider
# keys and the rest of the service's environment never reach a hook.
HOOK_ENVIRONMENT = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TZ",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)
OUTPUT_LIMIT = 16_384


@dataclass(frozen=True)
class HookOutcome:
    """What a hook did: its exit code (None if it never finished) and bounded output."""

    exit_code: int | None
    stdout: str
    stderr: str
    seconds: float
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """True only for a hook that ran to completion with exit code 0."""
        return self.exit_code == 0

    def record(self, index: int, hook: ShellHook) -> dict[str, Any]:
        """A receipt entry; output is cut short because it is evidence, not data."""
        return {
            "hook": index,
            "event": hook.event,
            "exit_code": self.exit_code,
            "seconds": round(self.seconds, 3),
            "error": self.error,
            "stderr": self.stderr[:2000],
        }


async def _collect(stream: asyncio.StreamReader | None, kept: bytearray) -> None:
    """Keep the first OUTPUT_LIMIT bytes and drain the rest so the hook never blocks."""
    while stream is not None and (chunk := await stream.read(65_536)):
        if len(kept) < OUTPUT_LIMIT:
            kept.extend(chunk[: OUTPUT_LIMIT - len(kept)])


async def exited(process: asyncio.subprocess.Process) -> None:
    """Wait for the hook's own exit; `Process.wait` also waits for every pipe to close."""
    while process.returncode is None:
        await asyncio.sleep(0.02)


def _kill(process: asyncio.subprocess.Process) -> None:
    """Stop the hook and, on POSIX, everything it started in its session."""
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_hook(hook: ShellHook, payload: dict[str, Any], cwd: Path) -> HookOutcome:
    """Run one hook through the system shell with the event JSON on stdin.

    The command is the user's own text; event data is passed only on stdin and in
    RTAGENT_HOOK_EVENT, never interpolated into the command line."""
    environment = {key: os.environ[key] for key in HOOK_ENVIRONMENT if key in os.environ}
    environment["RTAGENT_HOOK_EVENT"] = hook.event
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=environment,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        return HookOutcome(None, "", "", 0, f"The hook could not start ({exc.strerror}).")

    stdout, stderr = bytearray(), bytearray()

    async def exchange() -> None:
        assert process.stdin is not None
        readers = asyncio.gather(_collect(process.stdout, stdout), _collect(process.stderr, stderr))
        try:
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False).encode())
                await process.stdin.drain()
                process.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass  # A hook may exit without reading its input.
            await exited(process)
            try:
                async with asyncio.timeout(1):
                    await asyncio.shield(readers)
            except TimeoutError:
                # Something the hook started still holds its output: stop it with the hook,
                # so a hook never leaves processes attached to the service behind.
                _kill(process)
                with suppress(TimeoutError):
                    async with asyncio.timeout(1):
                        await asyncio.shield(readers)
        finally:
            readers.cancel()
            with suppress(asyncio.CancelledError):
                await readers

    try:
        async with asyncio.timeout(hook.timeout):
            await exchange()
    except TimeoutError:
        _kill(process)
        with suppress(TimeoutError):
            async with asyncio.timeout(5):
                await exited(process)
        return HookOutcome(
            None,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
            time.monotonic() - started,
            f"Timed out after {hook.timeout:g} s.",
        )
    except BaseException:
        _kill(process)
        raise
    return HookOutcome(
        process.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
        time.monotonic() - started,
    )
