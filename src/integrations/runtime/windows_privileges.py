# 读取当前进程完整性等级，并通过 Windows 管理员确认重启同一程序。
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys


HIGH_INTEGRITY_RID = 0x3000
ADMIN_RESTART_ARGUMENT = "--admin-restarted"


def current_process_integrity() -> int:
    """只读当前 token 的强制完整性 RID；不以管理员组或提升标记替代。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    token = wintypes.HANDLE()
    # TOKEN_QUERY 只读；伪进程句柄无需关闭，真实 token 归本函数所有。
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        # TokenIntegrityLevel=25，第一次只取容量，第二次读取同一 token。
        advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise OSError("process_integrity_size_invalid")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 25, buffer, size.value, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_MANDATORY_LABEL 的首字段是 SID 指针，必须按指针宽度读取。
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not sid:
            raise OSError("process_integrity_sid_missing")
        count = advapi32.GetSidSubAuthorityCount(sid).contents.value
        if not count:
            raise OSError("process_integrity_sid_invalid")
        return int(advapi32.GetSidSubAuthority(sid, count - 1).contents.value)
    finally:
        kernel32.CloseHandle(token)


def admin_restart_command() -> tuple[str, str, str]:
    """冻结解释器、入口、启动参数和工作目录，兼容源码及打包程序。"""
    args = list(sys.argv[1:])
    if not getattr(sys, "frozen", False):
        args.insert(0, str(Path(sys.argv[0]).resolve()))
    # 标记只限制自动重启次数，不授予权限；子进程仍从 token 重新核验。
    args = [arg for arg in args if arg != ADMIN_RESTART_ARGUMENT]
    args.append(ADMIN_RESTART_ARGUMENT)
    return _gui_interpreter(), subprocess.list2cmdline(args), os.getcwd()


def _gui_interpreter() -> str:
    """源码运行时提升后的进程改用同目录 pythonw.exe：提升进程无法接回 IDE 控制台，
    用 python.exe 会额外弹出独立控制台窗口；日志仍写入账号日志文件。打包程序不变。"""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False) or executable.name.casefold() != "python.exe":
        return sys.executable
    windowed = executable.with_name("pythonw.exe")
    return str(windowed) if windowed.is_file() else sys.executable


def request_admin_restart() -> bool:
    """仅请求系统 runas 确认；取消/启动失败返回 False，不修改 token 或系统策略。"""
    return request_elevated_process(*admin_restart_command())


def request_elevated_process(executable: str, parameters: str, directory: str) -> bool:
    """经系统 UAC 确认以管理员身份启动指定命令；用户取消或启动失败返回 False。"""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    # HINSTANCE 与指针同宽，避免 64 位返回值被默认 c_int 截断。
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    result = shell32.ShellExecuteW(None, "runas", executable, parameters, directory, 1)
    return int(result or 0) > 32
