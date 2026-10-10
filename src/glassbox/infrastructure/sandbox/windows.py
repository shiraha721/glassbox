"""Native Windows AppContainer execution, with no network capabilities or host fallback.

Only this module uses Win32 FFI. Each command gets a new AppContainer identity,
a filtered workspace copy, and a kill-on-close Job Object covering its children.
"""

import asyncio
import codecs
import ctypes as C
import json
import msvcrt
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from ctypes import wintypes as W
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from glassbox.domain import AppError
from glassbox.infrastructure.sandbox.command_io import (
    CommandControl,
    CommandOutput,
    OutputCallback,
    command_result,
)
from glassbox.infrastructure.sandbox.files import SandboxFiles
from glassbox.infrastructure.workspace.files import Workspace
from glassbox.infrastructure.workspace.toolchains import copy_toolchains

if sys.platform != "win32":
    raise ImportError("The AppContainer sandbox exists only on Windows.")

VOID = C.c_void_p
SIZE = C.c_size_t
KERNEL = C.WinDLL("kernel32", use_last_error=True)
USERENV = C.WinDLL("userenv", use_last_error=True)
ADVAPI = C.WinDLL("advapi32", use_last_error=True)


class StartupInfo(C.Structure):
    """Match STARTUPINFOW, including native pointer alignment on 64-bit Windows."""

    _fields_ = [
        ("cb", W.DWORD),
        ("reserved", W.LPWSTR),
        ("desktop", W.LPWSTR),
        ("title", W.LPWSTR),
        ("x", W.DWORD),
        ("y", W.DWORD),
        ("xsize", W.DWORD),
        ("ysize", W.DWORD),
        ("xchars", W.DWORD),
        ("ychars", W.DWORD),
        ("fill", W.DWORD),
        ("flags", W.DWORD),
        ("show", W.WORD),
        ("reserved_size", W.WORD),
        ("reserved_pointer", VOID),
        ("stdin", W.HANDLE),
        ("stdout", W.HANDLE),
        ("stderr", W.HANDLE),
    ]


class StartupInfoEx(C.Structure):
    """Pass the AppContainer and explicit inherited-handle list at process creation."""

    _fields_ = [("info", StartupInfo), ("attributes", VOID)]


class ProcessInfo(C.Structure):
    """Receive the suspended process and initial thread handles."""

    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


class SecurityCapabilities(C.Structure):
    """Supply a package SID and zero capabilities, including no network capability."""

    _fields_ = [("sid", VOID), ("capabilities", VOID), ("count", W.DWORD), ("reserved", W.DWORD)]


class JobLimits(C.Structure):
    """Match JOBOBJECT_EXTENDED_LIMIT_INFORMATION without third-party dependencies."""

    _fields_ = [
        ("process_time", C.c_int64),
        ("job_time", C.c_int64),
        ("flags", W.DWORD),
        ("min_working_set", SIZE),
        ("max_working_set", SIZE),
        ("process_count", W.DWORD),
        ("affinity", SIZE),
        ("priority", W.DWORD),
        ("scheduling", W.DWORD),
        ("io_counters", C.c_uint64 * 6),
        ("process_memory", SIZE),
        ("job_memory", SIZE),
        ("peak_process_memory", SIZE),
        ("peak_job_memory", SIZE),
    ]


def _bind(library, name, result, arguments):
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


_create_profile = _bind(
    USERENV,
    "CreateAppContainerProfile",
    W.LONG,
    [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, VOID, W.DWORD, C.POINTER(VOID)],
)
_delete_profile = _bind(USERENV, "DeleteAppContainerProfile", W.LONG, [W.LPCWSTR])
_sid_string = _bind(ADVAPI, "ConvertSidToStringSidW", W.BOOL, [VOID, C.POINTER(W.LPWSTR)])
_free_sid = _bind(ADVAPI, "FreeSid", VOID, [VOID])
_local_free = _bind(KERNEL, "LocalFree", VOID, [VOID])
_init_attributes = _bind(
    KERNEL, "InitializeProcThreadAttributeList", W.BOOL, [VOID, W.DWORD, W.DWORD, C.POINTER(SIZE)]
)
_update_attribute = _bind(
    KERNEL, "UpdateProcThreadAttribute", W.BOOL, [VOID, W.DWORD, SIZE, VOID, SIZE, VOID, VOID]
)
_delete_attributes = _bind(KERNEL, "DeleteProcThreadAttributeList", None, [VOID])
_create_process = _bind(
    KERNEL,
    "CreateProcessW",
    W.BOOL,
    [
        W.LPCWSTR,
        W.LPWSTR,
        VOID,
        VOID,
        W.BOOL,
        W.DWORD,
        VOID,
        W.LPCWSTR,
        C.POINTER(StartupInfoEx),
        C.POINTER(ProcessInfo),
    ],
)
_create_job = _bind(KERNEL, "CreateJobObjectW", W.HANDLE, [VOID, W.LPCWSTR])
_set_job = _bind(KERNEL, "SetInformationJobObject", W.BOOL, [W.HANDLE, C.c_int, VOID, W.DWORD])
_query_job = _bind(
    KERNEL, "QueryInformationJobObject", W.BOOL, [W.HANDLE, C.c_int, VOID, W.DWORD, VOID]
)
_assign_job = _bind(KERNEL, "AssignProcessToJobObject", W.BOOL, [W.HANDLE, W.HANDLE])
_terminate_job = _bind(KERNEL, "TerminateJobObject", W.BOOL, [W.HANDLE, W.UINT])
_terminate_process = _bind(KERNEL, "TerminateProcess", W.BOOL, [W.HANDLE, W.UINT])
_resume = _bind(KERNEL, "ResumeThread", W.DWORD, [W.HANDLE])
_wait = _bind(KERNEL, "WaitForSingleObject", W.DWORD, [W.HANDLE, W.DWORD])
_exit_code = _bind(KERNEL, "GetExitCodeProcess", W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)])
_close = _bind(KERNEL, "CloseHandle", W.BOOL, [W.HANDLE])


def _check(success) -> None:
    if not success:
        raise C.WinError(C.get_last_error())


def _grant(path: Path, sid: str, access: str) -> None:
    """Grant only disposable paths; never change a project or system directory ACL."""
    command = [
        str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"),
        str(path),
        "/grant",
        f"*{sid}:(OI)(CI)({access})",
        "/T",
        "/Q",
    ]
    result = subprocess.run(
        command, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode:
        raise AppError(
            "sandbox_unavailable", "Could not set private AppContainer file permissions."
        )


def _drain(descriptor: int, output: CommandOutput, key: str) -> None:
    """Drain a pipe completely so the child cannot block, retaining bounded text."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        while chunk := stream.read(8192):
            output.write(key, decoder.decode(chunk))
    output.write(key, decoder.decode(b"", final=True))


def _feed_input(stream: BinaryIO, control: CommandControl) -> None:
    """Write queued UTF-8 input only to the explicitly inherited sandbox stdin pipe."""
    try:
        with stream:
            while not control.stop.is_set() and not control.input_stop.is_set():
                try:
                    data = control.inputs.get(timeout=0.1)
                except queue.Empty:
                    continue
                if data is None:
                    break
                stream.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        control.input_closed = True


class WindowsSandbox:
    """Execute Windows PowerShell in an OS-enforced, disposable AppContainer."""

    backend = "windows-appcontainer"

    def __init__(self, workspace: Workspace) -> None:
        """Keep the host workspace separate from the directory visible to commands."""
        self.workspace = workspace
        self.status_cache: dict | None = None

    def _run_process(
        self,
        root: Path,
        sid: VOID,
        command: str,
        cwd: str,
        deadline: float,
        stop: threading.Event,
        output: CommandOutput,
        control: CommandControl | None = None,
        toolchains: list[Path] | None = None,
    ) -> dict:
        system = Path(os.environ["SystemRoot"]) / "System32"
        executable = system / "WindowsPowerShell/v1.0/powershell.exe"
        scratch = root / "scratch"
        # These profile variables let Windows locate private AppContainer storage. Do not
        # forward the host PATH, provider keys, proxies, or the rest of the server environment.
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "USERPROFILE", "LOCALAPPDATA", "APPDATA")
            if key in os.environ
        }
        environment.update(
            {
                "PATH": ";".join(
                    [
                        *[
                            str(path)
                            for root_path in (toolchains or [])
                            for path in (root_path, root_path / "bin")
                        ],
                        str(system),
                        str(executable.parent),
                    ]
                ),
                "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                "HOME": str(scratch),
                "TEMP": str(scratch),
                "TMP": str(scratch),
                "GLASSBOX_WORKSPACE": str(root / "workspace"),
                "GLASSBOX_CWD": cwd,
                "PYTHONPATH": str(root / "workspace" / ".glassbox" / "packages"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PIP_NO_INDEX": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        environment.update(
            {
                f"GLASSBOX_TOOLCHAIN_{index}": str(path)
                for index, path in enumerate(toolchains or [], 1)
            }
        )
        # A PSDrive rooted at the permitted folder avoids PowerShell traversing a host drive.
        script = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
            "[Console]::InputEncoding = [Text.UTF8Encoding]::new(); "
            "$OutputEncoding = [Console]::OutputEncoding; $ErrorActionPreference = 'Stop'; "
            "$ProgressPreference = 'SilentlyContinue'; "
            "New-PSDrive -Name Workspace -PSProvider FileSystem -Root $env:GLASSBOX_WORKSPACE "
            "-Scope Global | Out-Null; Set-Location -LiteralPath ('Workspace:\\' + $env:GLASSBOX_CWD); "
            "& {\n" + command + "\n}; if ($LASTEXITCODE) { exit $LASTEXITCODE }"
        )
        script_path = root / "command.ps1"
        script_path.write_text(script, encoding="utf-8-sig")
        args = [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
        block = C.create_unicode_buffer(
            "\0".join(
                f"{k}={v}" for k, v in sorted(environment.items(), key=lambda x: x[0].upper())
            )
            + "\0\0"
        )
        result: dict = {"timed_out": False}
        with ExitStack() as cleanup:
            job = _create_job(None, None)
            _check(job)
            cleanup.callback(_close, job)
            limits = JobLimits()
            limits.flags, limits.process_count = (
                0x2000 | 0x8,
                32,
            )  # Kill on close; bounded children.
            _check(_set_job(job, 9, C.byref(limits), C.sizeof(limits)))
            streams, readers = [], []
            for key in ("stdout", "stderr"):
                read, write = os.pipe()
                writer = cleanup.enter_context(os.fdopen(write, "wb", buffering=0))
                streams.append(writer)
                reader = threading.Thread(target=_drain, args=(read, output, key), daemon=True)
                reader.start()
                readers.append(reader)
            input_writer = None
            if control:
                read, write = os.pipe()
                null = cleanup.enter_context(os.fdopen(read, "rb", buffering=0))
                input_stream = cleanup.enter_context(os.fdopen(write, "wb", buffering=0))
                input_writer = threading.Thread(
                    target=_feed_input, args=(input_stream, control), daemon=True
                )
            else:
                null = cleanup.enter_context(open(os.devnull, "rb"))
            handles = (W.HANDLE * 3)(*[msvcrt.get_osfhandle(f.fileno()) for f in (null, *streams)])
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            size = SIZE()
            _init_attributes(None, 2, 0, C.byref(size))
            attributes = C.create_string_buffer(size.value)
            _check(_init_attributes(attributes, 2, 0, C.byref(size)))
            cleanup.callback(_delete_attributes, attributes)
            capabilities = SecurityCapabilities(sid, None, 0, 0)
            _check(
                _update_attribute(
                    attributes,
                    0,
                    0x20009,
                    C.byref(capabilities),
                    C.sizeof(capabilities),
                    None,
                    None,
                )
            )
            _check(
                _update_attribute(attributes, 0, 0x20002, handles, C.sizeof(handles), None, None)
            )
            startup = StartupInfoEx()
            startup.info.cb, startup.info.flags = C.sizeof(startup), 0x100
            startup.info.stdin, startup.info.stdout, startup.info.stderr = handles
            startup.attributes = C.cast(attributes, VOID)
            process = ProcessInfo()
            # Suspended creation closes the race between process launch and Job assignment.
            flags = 0x80000 | 0x400 | 0x8000000 | 0x4
            _check(
                _create_process(
                    str(executable),
                    C.create_unicode_buffer(subprocess.list2cmdline(args)),
                    None,
                    None,
                    True,
                    flags,
                    block,
                    str(root / "workspace" / cwd),
                    C.byref(startup),
                    C.byref(process),
                )
            )
            cleanup.callback(_close, process.process)
            cleanup.callback(_close, process.thread)
            try:
                _check(_assign_job(job, process.process))
                if _resume(process.thread) == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                if control:
                    assert input_writer is not None  # Created above whenever there is control.
                    input_writer.start()
                    control.started(process.pid)
                    null.close()
                for stream in streams:
                    stream.close()
                while _wait(process.process, 50) == 0x102:
                    if stop.is_set() or time.monotonic() >= deadline:
                        result["timed_out"] = not stop.is_set()
                        break
            finally:
                if control:
                    control.input_stop.set()
                _check(_terminate_job(job, 1))
                # The root may still be suspended if assigning it to the Job failed.
                if _wait(process.process, 0) == 0x102:
                    _terminate_process(process.process, 1)
                _wait(process.process, 5000)
                # A child can close its inherited pipes and outlive the root. Wait for the
                # Job's active-process count, not just the root handle or pipe EOF.
                accounting = C.create_string_buffer(48)
                cleanup_deadline = time.monotonic() + 5
                while True:
                    _check(_query_job(job, 1, accounting, len(accounting), None))
                    if W.DWORD.from_buffer(accounting, 40).value == 0:
                        break
                    if time.monotonic() >= cleanup_deadline:
                        raise AppError(
                            "sandbox_cleanup", "Sandbox child processes did not terminate."
                        )
                    time.sleep(0.02)
                for stream in streams:
                    stream.close()
                for reader in readers:
                    reader.join(5)
                if input_writer and input_writer.ident:
                    input_writer.join(5)
                if any(reader.is_alive() for reader in readers) or (
                    input_writer and input_writer.is_alive()
                ):
                    raise AppError(
                        "sandbox_cleanup", "Sandbox output handles did not close after termination."
                    )
            code = W.DWORD()
            _check(_exit_code(process.process, C.byref(code)))
            result["exit_code"] = code.value
        return result

    def _execute(
        self,
        command: str,
        cwd: str,
        mode: str,
        timeout: float,
        stop: threading.Event,
        output: CommandOutput,
        control: CommandControl | None = None,
        toolchain_roots: list[str] | None = None,
    ) -> dict:
        directory = self.workspace.path(cwd)
        if not directory.is_dir():
            raise AppError("tool_error", "Command cwd must be an existing workspace directory.")
        relative = str(directory.relative_to(self.workspace.root))
        deadline = time.monotonic() + timeout
        profile = "glassbox." + uuid4().hex
        sid, sid_text = VOID(), W.LPWSTR()
        status = _create_profile(
            profile, profile, "Disposable agent command", None, 0, C.byref(sid)
        )
        if status < 0:
            raise AppError(
                "sandbox_unavailable",
                f"AppContainer creation failed (0x{status & 0xffffffff:08x}).",
            )
        root = None
        try:
            _check(_sid_string(sid, C.byref(sid_text)))
            sid_value = sid_text.value
            assert sid_value  # The conversion succeeded, so the string is set.
            root = Path(tempfile.mkdtemp(prefix="glassbox-sandbox-"))
            copied = SandboxFiles(self.workspace, root / "workspace")
            copied.open()
            (root / "scratch").mkdir()
            _grant(root, sid_value, "RX")
            _grant(root / "workspace", sid_value, "RX" if mode == "read-only" else "M")
            _grant(root / "scratch", sid_value, "M")
            toolchains = copy_toolchains(
                toolchain_roots or [], root / "toolchains", self.workspace.root, stop, deadline
            )
            if toolchains:
                _grant(root / "toolchains", sid_value, "RX")
            if stop.is_set() or time.monotonic() >= deadline:
                result = {"exit_code": None, "timed_out": not stop.is_set()}
            else:
                result = self._run_process(
                    root, sid, command, relative, deadline, stop, output, control, toolchains
                )
            result.update(
                {
                    "sandbox": self.backend,
                    "shell": "powershell",
                    "mode": mode,
                    "network": "disabled",
                    "effects_may_be_partial": result["timed_out"] or stop.is_set(),
                    "workspace_changes": copied.reconcile() if mode == "workspace-write" else None,
                }
            )
            return result
        except OSError as exc:
            raise AppError("sandbox_unavailable", f"Windows sandbox failed: {exc}") from exc
        finally:
            if sid_text:
                _local_free(C.cast(sid_text, VOID))
            _free_sid(sid)
            _delete_profile(profile)
            if root is not None:
                # This is the exact private root we created, never a model-supplied delete target.
                if (
                    root.resolve().parent != Path(tempfile.gettempdir()).resolve()
                    or root.is_junction()
                ):
                    raise AppError(
                        "sandbox_cleanup", "The private sandbox directory changed unexpectedly."
                    )
                shutil.rmtree(root)

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
        """Run blocking Win32 calls off the event loop and finish cleanup on cancellation."""
        stop = control.stop if control else threading.Event()
        # Background processes persist snapshots; a slower cadence bounds their write rate.
        output = CommandOutput(on_output, interval=0.5 if control else 0.1)
        publisher = asyncio.create_task(output.drain())
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._execute,
                command,
                cwd,
                mode,
                timeout,
                stop,
                output,
                control,
                toolchain_roots,
            )
        )
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stop.set()
            await asyncio.shield(worker)
            raise
        finally:
            await output.close(publisher)
        stdout, stdout_truncated = output.result("stdout")
        stderr, stderr_truncated = output.result("stderr")
        return command_result(
            {
                "stdout": stdout,
                "stderr": stderr,
                **result,
                "truncated": stdout_truncated or stderr_truncated,
            }
        )

    async def status(self, refresh: bool = False) -> dict:
        """Probe a real AppContainer without making ordinary app startup depend on it."""
        if self.status_cache is not None and not refresh:
            return self.status_cache
        try:
            result = json.loads(
                await self.execute(
                    "[Console]::Write('sandbox-ready')", mode="read-only", timeout=12
                )
            )
            ready = result["exit_code"] == 0 and result["stdout"] == "sandbox-ready"
            detail = (
                "Windows AppContainer · PowerShell · private workspace copy · network disabled."
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
