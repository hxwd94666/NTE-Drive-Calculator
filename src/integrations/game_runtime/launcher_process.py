# 按所选安装目录核对官方启动器进程，查询失败时保留未知状态。
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path

class LauncherProcessProbeError(RuntimeError):
    pass


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
        ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
        ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
    ]


def selected_launcher_running(launcher_path: str | Path) -> bool:
    """Check the exact trusted launcher image, not just a matching process name."""
    launcher = Path(launcher_path).resolve()
    if os.name != "nt":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes, snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    first, following = kernel.Process32FirstW, kernel.Process32NextW
    for function in (first, following):
        function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)], wintypes.BOOL
    open_process = kernel.OpenProcess
    open_process.argtypes, open_process.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    query = kernel.QueryFullProcessImageNameW
    query.argtypes, query.restype = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    handle = snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise LauncherProcessProbeError("启动器进程列表读取失败，请重新检测。")
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = first(handle, ctypes.byref(entry))
        while found:
            if entry.name.casefold() == launcher.name.casefold():
                process = open_process(0x1000, False, entry.pid)
                if not process:
                    raise LauncherProcessProbeError("启动器进程身份尚未核对，请关闭启动器后重新检测。")
                try:
                    image = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(image))
                    if not query(process, 0, image, ctypes.byref(size)):
                        raise LauncherProcessProbeError("启动器进程路径尚未核对，请重新检测。")
                    if Path(image.value).resolve() == launcher:
                        return True
                finally:
                    close(process)
            found = following(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise LauncherProcessProbeError("启动器进程枚举未完成，请重新检测。")
        return False
    finally:
        close(handle)
