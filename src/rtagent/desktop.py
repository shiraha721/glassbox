"""Own the short-lived desktop folder dialog used by the local Web application."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rtagent.domain import AppError, FolderSelection


class FolderPicker:
    """Run one native dialog without blocking agent execution or leaving an orphan on disconnect."""

    def __init__(self) -> None:
        """Create a process-local dialog owner; opening the app never opens a window."""
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.closed = False

    async def close(self) -> None:
        """Dismiss and reap the owned dialog before the Web application shuts down."""
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def choose(self, initial_path: str) -> FolderSelection:
        """Return selected, cancelled or unavailable; only unavailability requests manual entry."""
        if self.closed:
            raise AppError("folder_picker_closed", "The application is shutting down.", 503)
        if self.lock.locked():
            raise AppError("folder_picker_busy", "A folder dialog is already open.", 409)
        async with self.lock:
            self.task = asyncio.current_task()
            process = None
            try:
                initial = Path(initial_path).expanduser()
                if not initial.is_absolute() or not initial.is_dir():
                    initial = Path.home()
                command = self._command(str(initial))
                if command is None:
                    return FolderSelection(
                        status="unavailable",
                        detail="No desktop folder dialog is available. Enter an absolute folder path.",
                    )
                # Spawn is shielded so cancellation during process creation cannot lose its handle.
                spawning = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *command,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        **(
                            {"creationflags": subprocess.CREATE_NO_WINDOW}
                            if os.name == "nt"
                            else {}
                        ),
                    )
                )
                try:
                    process = await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    process = await spawning
                    raise
                self.process = process
                async with asyncio.timeout(300):
                    output, _ = await process.communicate()
                if process.returncode == 1:
                    return FolderSelection(status="cancelled")
                if process.returncode != 0:
                    return FolderSelection(
                        status="unavailable",
                        detail="The system folder dialog could not open. Enter a folder path.",
                    )
                value = output.decode("utf-8").rstrip("\r\n")
                path = json.loads(value)["path"] if os.name == "nt" else value
                if not path:
                    return FolderSelection(status="cancelled")
                return FolderSelection(status="selected", path=str(Path(path).resolve(strict=True)))
            except TimeoutError:
                return FolderSelection(
                    status="unavailable",
                    detail="The folder dialog timed out. Retry or enter a folder path.",
                )
            except (OSError, ValueError, KeyError, TypeError):
                return FolderSelection(
                    status="unavailable",
                    detail="The system folder dialog is unavailable. Enter an absolute folder path.",
                )
            finally:
                if process and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass  # The dialog can close between the exit check and termination.
                    await process.wait()
                self.process = None
                self.task = None

    @staticmethod
    def _command(initial_path: str) -> list[str] | None:
        """Use the Windows Shell API or an installed Linux desktop dialog, without shell parsing."""
        if os.name == "nt":
            # Windows venv python.exe is a redirector; its child would survive killing the stub.
            # The GUI helper uses only stdlib, so launch the real interpreter and source directly.
            executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
            return [
                str(executable),
                str(Path(__file__).with_name("windows_dialog.py")),
                initial_path,
            ]
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return None
        if executable := shutil.which("zenity"):
            return [
                executable,
                "--file-selection",
                "--directory",
                "--title=Select workspace folder",
                "--filename=" + initial_path.rstrip("/") + "/",
            ]
        if executable := shutil.which("kdialog"):
            return [
                executable,
                "--title",
                "Select workspace folder",
                "--getexistingdirectory",
                initial_path,
            ]
        return None
