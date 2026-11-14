# 将一次界面输入绑定到确认的游戏进程、窗口和客户区，失焦后本次任务永久失效。
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ntpath
from pathlib import PureWindowsPath
from typing import Callable, NoReturn, Protocol

import mss

from src.integrations.components.environment import GAME_EXECUTABLE_NAME
from src.integrations.vision.window_capture import WindowRect


@dataclass(frozen=True)
class GameWindowState:
    hwnd: int
    pid: int
    process_started: int
    executable: str
    rect: WindowRect
    minimized: bool = False

    @property
    def identity(self) -> tuple[int, int, int, str]:
        return self.hwnd, self.pid, self.process_started, _path_key(self.executable)


class WindowSystem(Protocol):
    def windows(self) -> tuple[int, ...]: ...
    def inspect(self, hwnd: int) -> GameWindowState | None: ...
    def foreground(self) -> int: ...


def _path_key(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def _grab(rect: WindowRect):
    with mss.MSS() as screen:
        return screen.grab(rect.to_mss_monitor())


class GameInputTarget:
    """不等待恢复焦点，不重选窗口；只有显式重新开始才产生新绑定。"""

    def __init__(self, system: WindowSystem, state: GameWindowState, *, grab: Callable = _grab):
        self._system = system
        self._state = state
        self._grab = grab
        self._active = False
        self._failure: str | None = None

    def _fail(self, code: str) -> NoReturn:
        self._failure = self._failure or code
        raise RuntimeError(self._failure)

    def _current(self) -> GameWindowState:
        if self._failure is not None:
            self._fail(self._failure)
        current = self._system.inspect(self._state.hwnd)
        if current is None or current.identity != self._state.identity:
            self._fail("target_window_changed")
        return current

    def bind_foreground(self) -> None:
        if self._active:
            self.check()
            return
        # owner 已完成最小化和旧版交接等待；这里只绑定，不抢焦点、不产生输入。
        current = self._current()
        if current.minimized or self._system.foreground() != current.hwnd:
            self._fail("target_activation_failed")
        self._state = current
        self._active = True
        self.check()

    def check(self) -> None:
        current = self._current()
        if not self._active or current.minimized or self._system.foreground() != current.hwnd:
            self._fail("target_focus_lost")
        if current.rect != self._state.rect:
            self._fail("target_geometry_changed")

    def capture(self):
        self.check()
        rect = self._state.rect
        image = self._grab(rect)
        # 截图耗时期间的切窗同样令图片失效；不捕获其他前台，也不回退桌面。
        self.check()
        return image, rect


def create_game_input_target(
    executable: str, *, system: WindowSystem | None = None, grab: Callable = _grab,
) -> GameInputTarget:
    raw = str(executable).strip().strip('"')
    path = PureWindowsPath(raw)
    if not path.is_absolute() or path.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise RuntimeError("target_path_required")
    system = Win32WindowSystem() if system is None else system
    candidates = []
    for hwnd in system.windows():
        state = system.inspect(hwnd)
        if state is not None and _path_key(state.executable) == _path_key(raw):
            candidates.append(state)
    if len(candidates) != 1:
        raise RuntimeError("target_window_ambiguous" if candidates else "target_window_missing")
    return GameInputTarget(system, candidates[0], grab=grab)


class Win32WindowSystem:
    """Win32 只读身份探测；句柄宽度与进程查询权限显式声明。"""

    def __init__(self):
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = (
            (self._user, "EnumWindows", [self._callback, wintypes.LPARAM], wintypes.BOOL),
            (self._user, "GetForegroundWindow", [], wintypes.HWND),
            (self._user, "IsWindow", [wintypes.HWND], wintypes.BOOL),
            (self._user, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            (self._user, "IsIconic", [wintypes.HWND], wintypes.BOOL),
            (self._user, "GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (self._user, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self._user, "GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (self._user, "ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            (self._kernel, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self._kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            (self._kernel, "QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self._kernel, "GetProcessTimes", [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
        )
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    def windows(self) -> tuple[int, ...]:
        result = []

        @self._callback
        def collect(hwnd, _param):
            if self._user.IsWindowVisible(hwnd) and not self._user.GetWindow(hwnd, 4):
                result.append(int(hwnd))
            return True

        if not self._user.EnumWindows(collect, 0):
            raise RuntimeError("target_inspection_failed")
        return tuple(result)

    def inspect(self, hwnd: int) -> GameWindowState | None:
        if not self._user.IsWindow(hwnd) or not self._user.IsWindowVisible(hwnd):
            return None
        pid = wintypes.DWORD()
        if not self._user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return None
        # 仅查询身份；不写游戏内存，不创建任何游戏连接。
        handle = self._kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            length = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(length.value)
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self._kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return None
            if not self._kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                return None
            started = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            client, origin = wintypes.RECT(), wintypes.POINT()
            if not self._user.GetClientRect(hwnd, ctypes.byref(client)):
                return None
            if not self._user.ClientToScreen(hwnd, ctypes.byref(origin)):
                return None
            width, height = client.right - client.left, client.bottom - client.top
            if width <= 1 or height <= 1:
                return None
            # 再核对 HWND 所属 PID，避免读取过程中窗口被销毁并复用。
            final_pid = wintypes.DWORD()
            self._user.GetWindowThreadProcessId(hwnd, ctypes.byref(final_pid))
            if final_pid.value != pid.value:
                return None
            return GameWindowState(hwnd, pid.value, started, buffer.value,
                WindowRect(origin.x, origin.y, origin.x + width, origin.y + height),
                bool(self._user.IsIconic(hwnd)))
        finally:
            self._kernel.CloseHandle(handle)

    def foreground(self) -> int:
        return int(self._user.GetForegroundWindow() or 0)

