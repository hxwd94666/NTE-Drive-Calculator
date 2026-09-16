# 后台等待游戏进程并绑定创建身份，退出后恢复查找且支持立即取消。
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ntpath
import os
from threading import Event, Lock, Thread
from typing import Callable, Literal, Protocol


@dataclass(frozen=True)
class GameProcessIdentity:
    pid: int
    creation_time: int


@dataclass(frozen=True)
class GameProcessEvent:
    kind: Literal["waiting", "started", "exited", "error"]
    process: GameProcessIdentity | None = None
    error: str = ""
    generation: int = 0


class WatchedGameProcess(Protocol):
    identity: GameProcessIdentity

    def wait_exited(self, timeout: float) -> bool: ...

    def close(self) -> None: ...


class GameProcessBackend(Protocol):
    def find_game(self) -> WatchedGameProcess | None: ...


class GameProcessWatcher:
    """Callbacks run on a worker: callers must marshal them to their UI thread.

    Each start generation is independent. Ignore queued events whose generation
    differs from ``generation``, especially after stop/start or account changes.
    No discovery scans run while an open game process handle is being watched.
    """

    def __init__(
        self,
        on_change: Callable[[GameProcessEvent], None],
        *,
        backend: GameProcessBackend | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("进程查找间隔必须大于零")
        self._on_change = on_change
        self._backend = backend
        self._poll_interval = poll_interval
        self._lock = Lock()
        self._cancel = Event()
        self._cancel.set()
        self._thread: Thread | None = None
        self._generation = 0
        self._closed = False

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive() and not self._cancel.is_set())

    @property
    def worker_alive(self) -> bool:
        """True until the latest worker has released its bound process handle."""
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._thread and self._thread.is_alive() and not self._cancel.is_set():
                return
            self._generation += 1
            self._cancel = Event()
            self._thread = Thread(
                target=self._run,
                args=(self._cancel, self._generation),
                name="game-process-watcher",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Cancel immediately; bound-handle cleanup completes within a short wait."""
        with self._lock:
            self._cancel.set()
            self._generation += 1

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            self._generation += 1

    def _run(self, cancel: Event, generation: int) -> None:
        def emit(kind, process=None, error=""):
            if not cancel.is_set():
                self._on_change(GameProcessEvent(kind, process, error, generation))

        emit("waiting")
        try:
            backend = self._backend or WindowsGameProcessBackend()
        except OSError:
            emit("error", error="当前系统无法监控游戏进程")
            return
        while not cancel.is_set():
            process = None
            try:
                process = backend.find_game()
                if process is None:
                    cancel.wait(self._poll_interval)
                    continue
                # The process may have exited between enumeration and binding.
                if cancel.is_set() or process.wait_exited(0):
                    continue
                emit("started", process.identity)
                while not cancel.is_set():
                    if process.wait_exited(0.1):
                        emit("exited", process.identity)
                        emit("waiting")
                        break
            except OSError:
                emit("error", process.identity if process is not None else None,
                     error="无法监控游戏进程，请检查权限并确认只运行一个游戏")
                cancel.wait(self._poll_interval)
            finally:
                if process is not None:
                    process.close()


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
        ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
        ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
    ]


class _WindowsGameProcess:
    def __init__(self, kernel, handle, identity: GameProcessIdentity) -> None:
        self._kernel, self._handle, self.identity = kernel, handle, identity

    def wait_exited(self, timeout: float) -> bool:
        result = self._kernel.WaitForSingleObject(self._handle, max(0, round(timeout * 1000)))
        if result == 0:
            return True
        if result == 258:  # WAIT_TIMEOUT
            return False
        raise OSError("无法等待游戏进程退出")

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


class WindowsGameProcessBackend:
    """Read-only Toolhelp discovery followed by a synchronized process handle."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("仅支持 Windows 游戏进程")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = (
            ("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            ("Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)], wintypes.BOOL),
            ("Process32NextW", [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)], wintypes.BOOL),
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("GetProcessTimes", [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL),
            ("QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD,
                                           wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        )
        for name, args, result in declarations:
            function = getattr(kernel, name)
            function.argtypes, function.restype = args, result
        self._kernel = kernel

    def find_game(self) -> WatchedGameProcess | None:
        kernel = self._kernel
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError("无法读取游戏进程列表")
        matches = []
        try:
            entry = _ProcessEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                if entry.name.casefold() == "htgame.exe":
                    matches.append(int(entry.pid))
                found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise OSError("读取游戏进程列表失败")
        finally:
            kernel.CloseHandle(snapshot)
        if not matches:
            return None
        if len(matches) != 1:
            raise OSError("检测到多个游戏进程")
        return self._bind(matches[0])

    def _bind(self, pid: int) -> WatchedGameProcess | None:
        kernel = self._kernel
        handle = kernel.OpenProcess(0x00101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not handle:
            if ctypes.get_last_error() == 87:  # Process exited before OpenProcess.
                return None
            raise OSError("无法打开游戏进程")
        bound = False
        try:
            image = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(image))
            if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                raise OSError("无法核对游戏进程映像")
            if ntpath.basename(image.value).casefold() != "htgame.exe":
                return None  # The PID was recycled before the handle was opened.
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                raise OSError("无法核对游戏进程创建时间")
            creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            process = _WindowsGameProcess(kernel, handle, GameProcessIdentity(pid, creation))
            bound = True
            return process
        finally:
            if not bound:
                kernel.CloseHandle(handle)
