"""Windows Common Item Dialog adapter, executed in its own disposable STA process."""

import ctypes
import io
import json
import sys
from collections.abc import Callable
from uuid import UUID


def select_folder(initial_path: str) -> str | None:
    """Show the native Explorer folder dialog and return a Unicode absolute path or cancellation."""
    if sys.platform != "win32":
        raise OSError("The Explorer folder dialog exists only on Windows.")
    from ctypes import wintypes

    ole = ctypes.WinDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    user = ctypes.WinDLL("user32")
    pointer = ctypes.c_void_p
    hresult = ctypes.c_long

    def check(result: int) -> None:
        """Turn failed HRESULTs into helper failures without printing internal Shell diagnostics."""
        if result < 0:
            raise OSError(f"Windows dialog failed (0x{result & 0xFFFFFFFF:08X}).")

    def method(instance: ctypes.c_void_p, index: int, *types: type) -> Callable[..., int]:
        """Bind one documented COM vtable slot; pointers never cross the helper process boundary."""
        table = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(pointer))).contents
        return ctypes.WINFUNCTYPE(hresult, pointer, *types)(table[index])

    # A noninteractive service window station cannot display a usable dialog.
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        pointer,
        wintypes.DWORD,
        pointer,
    ]
    flags = (wintypes.DWORD * 3)()
    if (
        not user.GetUserObjectInformationW(
            user.GetProcessWindowStation(), 1, flags, ctypes.sizeof(flags), None
        )
        or not flags[2] & 1
    ):
        raise OSError("The process has no interactive desktop.")

    ole.CoInitializeEx.argtypes = [pointer, wintypes.DWORD]
    ole.CoInitializeEx.restype = hresult
    ole.CoCreateInstance.argtypes = [pointer, pointer, wintypes.DWORD, pointer, pointer]
    ole.CoCreateInstance.restype = hresult
    ole.CoTaskMemFree.argtypes = [pointer]
    shell.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, pointer, pointer, pointer]
    shell.SHCreateItemFromParsingName.restype = hresult

    check(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED, on the helper's main thread.
    dialog, selected, initial, name = pointer(), pointer(), pointer(), pointer()
    clsid = ctypes.create_string_buffer(UUID("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7").bytes_le)
    iid = ctypes.create_string_buffer(UUID("D57C7288-D4AD-4768-BE02-9D969532D960").bytes_le)
    item_iid = ctypes.create_string_buffer(UUID("43826D1E-E718-42EE-BC55-A1E261C37BFE").bytes_le)
    try:
        check(ole.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(dialog)))
        options = wintypes.DWORD()
        check(method(dialog, 10, pointer)(dialog, ctypes.byref(options)))  # GetOptions
        # FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST.
        check(method(dialog, 9, wintypes.DWORD)(dialog, options.value | 0x20 | 0x40 | 0x800))
        check(method(dialog, 17, wintypes.LPCWSTR)(dialog, "Select workspace folder"))
        check(method(dialog, 18, wintypes.LPCWSTR)(dialog, "Select folder"))
        if (
            shell.SHCreateItemFromParsingName(initial_path, None, item_iid, ctypes.byref(initial))
            >= 0
        ):
            check(method(dialog, 12, pointer)(dialog, initial))  # SetFolder
        result = method(dialog, 3, wintypes.HWND)(dialog, None)  # Show pumps its own STA messages.
        if result & 0xFFFFFFFF == 0x800704C7:  # HRESULT_FROM_WIN32(ERROR_CANCELLED).
            return None
        check(result)
        check(method(dialog, 20, pointer)(dialog, ctypes.byref(selected)))  # GetResult
        check(
            method(selected, 5, wintypes.DWORD, pointer)(
                selected, 0x80058000, ctypes.byref(name)  # SIGDN_FILESYSPATH.
            )
        )
        return ctypes.wstring_at(name)
    finally:
        if name:
            ole.CoTaskMemFree(name)
        for instance in (selected, initial, dialog):
            if instance:
                method(instance, 2)(instance)  # IUnknown.Release.
        ole.CoUninitialize()


def main() -> None:
    """Emit one UTF-8 JSON result; failures signal manual fallback through the helper exit status."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        path = select_folder(sys.argv[1])
    except (OSError, ValueError, IndexError):
        raise SystemExit(2) from None
    print(json.dumps({"path": path}, ensure_ascii=False))


if __name__ == "__main__":
    main()
