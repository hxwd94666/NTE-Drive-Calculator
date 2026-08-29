# 独占扫描、鉴定与战报共用的 Windows 全局热键会话和监听线程。
"""Application-wide global hotkey manager with a keyboard-package fallback."""

from __future__ import annotations

import ctypes
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from src.utils.logger import logger


HotkeyAction = Literal["stop", "capture", "finish", "battle_rerecord"]
HotkeyCallback = Callable[[], None]
ThreadFactory = Callable[..., threading.Thread]


@dataclass(frozen=True, slots=True)
class HotkeyConfiguration:
    capture: str
    finish: str
    stop: str
    battle_rerecord: str


class GlobalHotkeyManager:
    """Own one application-wide hotkey session independent of feature controllers."""

    def __init__(
        self,
        *,
        capture_hotkey: str,
        finish_hotkey: str,
        stop_hotkey: str,
        battle_rerecord_hotkey: str,
        thread_factory: ThreadFactory = threading.Thread,
    ) -> None:
        self._configuration = HotkeyConfiguration(
            capture=str(capture_hotkey),
            finish=str(finish_hotkey),
            stop=str(stop_hotkey),
            battle_rerecord=str(battle_rerecord_hotkey),
        )
        self._thread_factory = thread_factory
        self._lock = threading.RLock()
        self._generation = 0
        self._active_owner: str | None = None
        self._active_configuration: HotkeyConfiguration | None = None
        self._callbacks: dict[HotkeyAction, HotkeyCallback] = {}
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None

    @property
    def configuration(self) -> HotkeyConfiguration:
        with self._lock:
            return self._configuration

    @property
    def active_owner(self) -> str | None:
        with self._lock:
            return self._active_owner

    def update_configuration(
        self,
        *,
        capture_hotkey: str,
        finish_hotkey: str,
        stop_hotkey: str,
        battle_rerecord_hotkey: str,
    ) -> None:
        """Update bindings for the next session without mutating a live listener."""

        configuration = HotkeyConfiguration(
            capture=str(capture_hotkey).strip(),
            finish=str(finish_hotkey).strip(),
            stop=str(stop_hotkey).strip(),
            battle_rerecord=str(battle_rerecord_hotkey).strip(),
        )
        if not all((
            configuration.capture,
            configuration.finish,
            configuration.stop,
            configuration.battle_rerecord,
        )):
            raise ValueError("全局热键不能为空")
        with self._lock:
            self._configuration = configuration

    def start(
        self,
        *,
        owner: str,
        on_stop: HotkeyCallback | None = None,
        on_capture: HotkeyCallback | None = None,
        on_finish: HotkeyCallback | None = None,
        on_battle_rerecord: HotkeyCallback | None = None,
    ) -> None:
        """Replace any previous session and bind callbacks owned by one feature."""

        owner_name = str(owner).strip()
        if not owner_name:
            raise ValueError("热键会话 owner 不能为空")
        self.stop()
        callbacks: dict[HotkeyAction, HotkeyCallback] = {}
        if on_stop is not None:
            callbacks["stop"] = on_stop
        if on_capture is not None:
            callbacks["capture"] = on_capture
        if on_finish is not None:
            callbacks["finish"] = on_finish
        if on_battle_rerecord is not None:
            callbacks["battle_rerecord"] = on_battle_rerecord
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._active_owner = owner_name
            self._active_configuration = self._configuration
            self._callbacks = callbacks
            thread = self._thread_factory(
                target=self._listen,
                args=(generation,),
                name=f"global-hotkeys-{owner_name}",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def stop(self, *, owner: str | None = None) -> None:
        """Stop the active session; an owner may only stop its own bindings."""

        with self._lock:
            if owner is not None and self._active_owner != owner:
                return
            thread = self._thread
            thread_id = self._thread_id
            self._generation += 1
            self._active_owner = None
            self._active_configuration = None
            self._callbacks = {}
            self._thread = None
            self._thread_id = None
        if sys.platform == "win32" and thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(int(thread_id), 0, 0, 0)
            except Exception as exc:
                logger.debug("唤醒全局热键线程失败，可能线程已退出: {}", exc)
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=1.0)

    def close(self) -> None:
        self.stop()

    @staticmethod
    def hotkey_to_vk(hotkey: str) -> int | None:
        text = str(hotkey or "").strip().upper()
        match = re.fullmatch(r"F(\d{1,2})", text)
        if match:
            number = int(match.group(1))
            if 1 <= number <= 24:
                return 0x70 + number - 1
        if len(text) == 1 and ("A" <= text <= "Z" or "0" <= text <= "9"):
            return ord(text)
        return None

    def _listen(self, generation: int) -> None:
        if self._win_hotkey_loop(generation):
            return
        self._keyboard_poll_loop(generation)

    def _session_snapshot(
        self,
        generation: int,
    ) -> tuple[HotkeyConfiguration, dict[HotkeyAction, HotkeyCallback]] | None:
        with self._lock:
            if (
                generation != self._generation
                or self._active_owner is None
                or self._active_configuration is None
            ):
                return None
            return self._active_configuration, dict(self._callbacks)

    def _dispatch(self, generation: int, action: HotkeyAction) -> None:
        snapshot = self._session_snapshot(generation)
        if snapshot is None:
            return
        callback = snapshot[1].get(action)
        if callback is not None:
            try:
                callback()
            except Exception as exc:
                logger.exception("全局热键动作执行失败 action={}: {}", action, exc)

    def _win_hotkey_loop(self, generation: int) -> bool:
        if sys.platform != "win32":
            return False
        snapshot = self._session_snapshot(generation)
        if snapshot is None:
            return True
        configuration, callbacks = snapshot
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        wm_hotkey = 0x0312
        pm_remove = 0x0001
        mod_norepeat = 0x4000

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class Message(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_size_t),
                ("time", ctypes.c_uint),
                ("pt", Point),
            ]

        action_ids: tuple[tuple[int, str, HotkeyAction], ...] = (
            (1, configuration.stop, "stop"),
            (2, configuration.capture, "capture"),
            (3, configuration.finish, "finish"),
            (4, configuration.battle_rerecord, "battle_rerecord"),
        )
        actions: dict[int, HotkeyAction] = {}
        registrations: list[int] = []
        for hotkey_id, hotkey, action in action_ids:
            if action not in callbacks:
                continue
            virtual_key = self.hotkey_to_vk(hotkey)
            if virtual_key is None:
                continue
            if not user32.RegisterHotKey(None, hotkey_id, mod_norepeat, virtual_key):
                for registered_id in registrations:
                    user32.UnregisterHotKey(None, registered_id)
                return False
            registrations.append(hotkey_id)
            actions[hotkey_id] = action
        if not registrations:
            return False

        with self._lock:
            if generation == self._generation:
                self._thread_id = int(kernel32.GetCurrentThreadId())
        message = Message()
        try:
            while self._session_snapshot(generation) is not None:
                while user32.PeekMessageW(
                    ctypes.byref(message),
                    None,
                    0,
                    0,
                    pm_remove,
                ):
                    if message.message == wm_hotkey:
                        message_action = actions.get(int(message.wParam))
                        if message_action is not None:
                            self._dispatch(generation, message_action)
                time.sleep(0.03)
        finally:
            for hotkey_id in registrations:
                user32.UnregisterHotKey(None, hotkey_id)
            with self._lock:
                if generation == self._generation:
                    self._thread_id = None
        return True

    def _keyboard_poll_loop(self, generation: int) -> None:
        import keyboard as keyboard_module

        pressed_actions: set[HotkeyAction] = set()
        while True:
            snapshot = self._session_snapshot(generation)
            if snapshot is None:
                return
            configuration, callbacks = snapshot
            keys: dict[HotkeyAction, str] = {
                "stop": configuration.stop,
                "capture": configuration.capture,
                "finish": configuration.finish,
                "battle_rerecord": configuration.battle_rerecord,
            }
            try:
                for action in callbacks:
                    pressed = keyboard_module.is_pressed(keys[action].lower())
                    if pressed and action not in pressed_actions:
                        pressed_actions.add(action)
                        self._dispatch(generation, action)
                    elif not pressed:
                        pressed_actions.discard(action)
            except Exception as exc:
                logger.debug("全局热键轮询异常，继续监听: {}", exc)
            time.sleep(0.05)
