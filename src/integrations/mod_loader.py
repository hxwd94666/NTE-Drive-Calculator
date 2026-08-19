# 管理 nte-mod-loader 的受控提权进程和命名停止事件。
"""Windows integration for the optional upstream NTE Mod Loader."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import threading


MOD_LOADER_FILENAME = "nte-mod-loader.exe"
MOD_LOADER_ENV = "NTE_MOD_LOADER_EXE"
MOD_LOADER_SHA256 = "398039f5314d8e0843e0df2f144e7081a3e2bbe9879afb68a671c9e360af9c80"
PACKAGED_MOD_LOADER_RELATIVE_PATH = (
    Path("third_party") / "mod-loader" / "bin" / MOD_LOADER_FILENAME
)

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_CANCELLED = 1223
MOD_LOADER_STOP_TIMEOUT_MS = 15_000


class ModLoaderRuntimeError(RuntimeError):
    """The loader session could not be started or stopped safely."""


@dataclass(frozen=True)
class ModLoaderRuntimeSnapshot:
    phase: str
    loader_path: Path
    payload_path: Path
    process_id: int | None = None
    detail: str = ""


class _ShellExecuteInfoW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_stop_event_name(
    *,
    process_id: int,
    session_suffix: int | None = None,
) -> str:
    """Build the exact managed-control name accepted by upstream 0.4.1."""

    suffix = secrets.randbits(32) if session_suffix is None else session_suffix
    return (
        "Local\\NTE-DPS-TOOL-ModLoader-"
        f"{process_id & 0xFFFFFFFF:08x}{suffix & 0xFFFFFFFF:08x}"
    )


def packaged_mod_loader(application_root: str | Path) -> Path:
    """Resolve the audited loader in source and PyInstaller layouts."""

    root = Path(application_root).resolve()
    configured = os.environ.get(MOD_LOADER_ENV)
    candidates = (
        Path(configured).expanduser().resolve() if configured else None,
        root / PACKAGED_MOD_LOADER_RELATIVE_PATH,
        root / MOD_LOADER_FILENAME,
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            try:
                digest = _file_sha256(candidate)
            except OSError as exc:
                raise ModLoaderRuntimeError(
                    f"无法校验 {MOD_LOADER_FILENAME}：{candidate}"
                ) from exc
            if digest != MOD_LOADER_SHA256:
                raise ModLoaderRuntimeError(
                    f"{MOD_LOADER_FILENAME} SHA-256 与发行记录不一致：{candidate}"
                )
            return candidate.resolve()
    checked = "、".join(str(candidate) for candidate in candidates if candidate)
    raise ModLoaderRuntimeError(
        f"未找到 {MOD_LOADER_FILENAME}；已检查：{checked}"
    )


class ModLoaderRuntime:
    """Own one elevated Loader process and its cooperative stop event."""

    def __init__(self, *, application_root: str | Path) -> None:
        self._application_root = Path(application_root).resolve()
        self._lock = threading.Lock()
        self._process_handle: int | None = None
        self._stop_event_handle: int | None = None
        self._process_id: int | None = None
        self._loader_path: Path | None = None
        self._payload_path: Path | None = None

    def snapshot(self, *, payload_path: str | Path) -> ModLoaderRuntimeSnapshot:
        payload = Path(payload_path).resolve()
        try:
            loader = packaged_mod_loader(self._application_root)
        except ModLoaderRuntimeError as exc:
            return ModLoaderRuntimeSnapshot(
                "missing_loader",
                self._application_root / MOD_LOADER_FILENAME,
                payload,
                detail=str(exc),
            )
        if not payload.is_file():
            return ModLoaderRuntimeSnapshot(
                "missing_payload",
                loader,
                payload,
                detail="打包的 dwmapi.dll 不存在",
            )
        if os.name != "nt":
            return ModLoaderRuntimeSnapshot(
                "unsupported",
                loader,
                payload,
                detail="Mod Loader 仅支持 Windows",
            )
        with self._lock:
            running = self._refresh_running_locked()
            return ModLoaderRuntimeSnapshot(
                "running" if running else "stopped",
                loader,
                payload,
                self._process_id if running else None,
            )

    def start(self, *, payload_path: str | Path) -> ModLoaderRuntimeSnapshot:
        if os.name != "nt":
            raise ModLoaderRuntimeError("Mod Loader 仅支持 Windows")
        loader = packaged_mod_loader(self._application_root)
        payload = Path(payload_path).resolve()
        if not payload.is_file():
            raise ModLoaderRuntimeError(f"Mod Loader payload 不存在：{payload}")

        with self._lock:
            if self._refresh_running_locked():
                return ModLoaderRuntimeSnapshot(
                    "running",
                    self._loader_path or loader,
                    self._payload_path or payload,
                    self._process_id,
                )
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            create_event = kernel32.CreateEventW
            create_event.argtypes = (
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            create_event.restype = wintypes.HANDLE
            event_name = _managed_stop_event_name(process_id=os.getpid())
            event_handle = create_event(None, True, False, event_name)
            if not event_handle:
                raise ModLoaderRuntimeError(
                    f"无法创建 Loader 停止事件，Windows 错误 {ctypes.get_last_error()}"
                )

            parameters = (
                f'--dll "{payload}" --monitor-timeout 0 '
                f'--stop-event "{event_name}" --owner-pid {os.getpid()}'
            )
            execution = _ShellExecuteInfoW()
            execution.cbSize = ctypes.sizeof(_ShellExecuteInfoW)
            execution.fMask = _SEE_MASK_NOCLOSEPROCESS
            execution.lpVerb = "runas"
            execution.lpFile = str(loader)
            execution.lpParameters = parameters
            execution.lpDirectory = str(loader.parent)
            execution.nShow = _SW_HIDE
            shell_execute = shell32.ShellExecuteExW
            shell_execute.argtypes = (ctypes.POINTER(_ShellExecuteInfoW),)
            shell_execute.restype = wintypes.BOOL
            if not shell_execute(ctypes.byref(execution)) or not execution.hProcess:
                error_code = ctypes.get_last_error()
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = (wintypes.HANDLE,)
                close_handle.restype = wintypes.BOOL
                close_handle(event_handle)
                if error_code == _ERROR_CANCELLED:
                    raise ModLoaderRuntimeError("用户取消了 Mod Loader 管理员授权")
                raise ModLoaderRuntimeError(
                    f"无法启动 Mod Loader，Windows 错误 {error_code}"
                )

            get_process_id = kernel32.GetProcessId
            get_process_id.argtypes = (wintypes.HANDLE,)
            get_process_id.restype = wintypes.DWORD
            self._process_handle = int(execution.hProcess)
            self._stop_event_handle = int(event_handle)
            self._process_id = int(get_process_id(execution.hProcess)) or None
            self._loader_path = loader
            self._payload_path = payload
            return ModLoaderRuntimeSnapshot(
                "running",
                loader,
                payload,
                self._process_id,
            )

    def stop(self, *, timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS) -> bool:
        if os.name != "nt":
            return False
        with self._lock:
            if not self._refresh_running_locked():
                return False
            process_handle = self._process_handle
            stop_event_handle = self._stop_event_handle
            if process_handle is None or stop_event_handle is None:
                self._close_handles_locked()
                raise ModLoaderRuntimeError("Mod Loader 会话句柄不完整，已停止管理该会话")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            set_event = kernel32.SetEvent
            set_event.argtypes = (wintypes.HANDLE,)
            set_event.restype = wintypes.BOOL
            wait_for_single_object = kernel32.WaitForSingleObject
            wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            wait_for_single_object.restype = wintypes.DWORD
            if not set_event(wintypes.HANDLE(stop_event_handle)):
                raise ModLoaderRuntimeError(
                    f"无法通知 Mod Loader 停止，Windows 错误 {ctypes.get_last_error()}"
                )
            result = wait_for_single_object(
                wintypes.HANDLE(process_handle),
                max(0, int(timeout_ms)),
            )
            if result == _WAIT_TIMEOUT:
                raise ModLoaderRuntimeError(
                    "Mod Loader 未在限定时间内退出；为避免双重加载，未切换加载方式"
                )
            if result == _WAIT_FAILED:
                raise ModLoaderRuntimeError(
                    "等待 Mod Loader 退出失败，Windows 错误 "
                    f"{ctypes.get_last_error()}"
                )
            if result != _WAIT_OBJECT_0:
                raise ModLoaderRuntimeError(
                    f"等待 Mod Loader 退出失败，Windows 结果 {result}"
                )
            self._close_handles_locked()
            return True

    def close(self) -> None:
        self.stop(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)

    def _refresh_running_locked(self) -> bool:
        if self._process_handle is None:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single_object.restype = wintypes.DWORD
        result = wait_for_single_object(
            wintypes.HANDLE(self._process_handle), 0
        )
        if result == _WAIT_TIMEOUT:
            return True
        if result == _WAIT_OBJECT_0:
            self._close_handles_locked()
            return False
        if result == _WAIT_FAILED:
            raise ModLoaderRuntimeError(
                "无法检查 Mod Loader 进程状态，Windows 错误 "
                f"{ctypes.get_last_error()}"
            )
        raise ModLoaderRuntimeError(
            f"无法检查 Mod Loader 进程状态，Windows 结果 {result}"
        )

    def _close_handles_locked(self) -> None:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            for handle in (self._process_handle, self._stop_event_handle):
                if handle is not None:
                    close_handle(wintypes.HANDLE(handle))
        self._process_handle = None
        self._stop_event_handle = None
        self._process_id = None
        self._loader_path = None
        self._payload_path = None
