# 只读查询已运行游戏的映像路径，并限定注册表及常见布局发现范围。
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def running_game_executables() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes, snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    first, following = kernel.Process32FirstW, kernel.Process32NextW
    for function in (first, following):
        function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    open_process = kernel.OpenProcess
    open_process.argtypes, open_process.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    handle = snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        return ()
    result = []
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = first(handle, ctypes.byref(entry))
        while found:
            if entry.name.casefold() == "htgame.exe":
                process = open_process(0x1000, False, entry.pid)
                if process:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buffer))
                        if query(process, 0, buffer, ctypes.byref(size)):
                            path = Path(buffer.value)
                            if path.is_file() and path.name.casefold() == "htgame.exe":
                                result.append(path.resolve())
                    finally:
                        close(process)
            found = following(handle, ctypes.byref(entry))
    finally:
        close(handle)
    return tuple(result)
