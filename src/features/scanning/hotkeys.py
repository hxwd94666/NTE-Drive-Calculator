# 监听扫描与鉴定使用的全局热键并转发停止、截图和完成动作。
"""Windows-native hotkey listener with keyboard-package fallback."""

from __future__ import annotations

import ctypes
import re
import sys
import threading
import time

from src.utils.logger import logger


def register_scan_hotkeys(owner, mode) -> None:
    owner._hk_mode = mode
    owner._hk_active = True
    owner._hk_thread_id = None
    owner._hk_thread = threading.Thread(
        target=owner._hotkey_poll_loop, daemon=True
    )
    owner._hk_thread.start()


def hotkey_to_vk(owner, hotkey):
    del owner
    text = str(hotkey or "").strip().upper()
    match = re.fullmatch(r"F(\d{1,2})", text)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 24:
            return 0x70 + number - 1
    if len(text) == 1 and ("A" <= text <= "Z" or "0" <= text <= "9"):
        return ord(text)
    return None


def win_hotkey_loop(owner):
    if sys.platform != "win32":
        return False
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

    actions = {}
    registrations = []
    hotkeys = [(1, owner._hk_stop, "stop")]
    if owner._hk_mode in ("semi", "identify"):
        hotkeys.extend(
            [
                (2, owner._hk_capture, "capture"),
                (3, owner._hk_finish, "finish"),
            ]
        )
    for hotkey_id, hotkey, action in hotkeys:
        vk = owner._hotkey_to_vk(hotkey)
        if not vk:
            continue
        if not user32.RegisterHotKey(None, hotkey_id, mod_norepeat, vk):
            for registered_id in registrations:
                user32.UnregisterHotKey(None, registered_id)
            return False
        registrations.append(hotkey_id)
        actions[hotkey_id] = action
    if not registrations:
        return False

    owner._hk_thread_id = kernel32.GetCurrentThreadId()
    message = Message()
    try:
        while owner._hk_active:
            while user32.PeekMessageW(
                ctypes.byref(message), None, 0, 0, pm_remove
            ):
                if message.message != wm_hotkey:
                    continue
                action = actions.get(int(message.wParam))
                if action == "stop":
                    owner._on_hk_stop()
                elif action == "capture":
                    owner._on_hk_capture()
                elif action == "finish":
                    owner._on_hk_finish()
            time.sleep(0.03)
    finally:
        for hotkey_id in registrations:
            user32.UnregisterHotKey(None, hotkey_id)
        owner._hk_thread_id = None
    return True


def hotkey_poll_loop(owner) -> None:
    if owner._win_hotkey_loop():
        return
    import keyboard as keyboard_module

    while owner._hk_active:
        try:
            if keyboard_module.is_pressed(owner._hk_stop.lower()):
                owner._on_hk_stop()
                time.sleep(0.5)
            if owner._hk_mode in ("semi", "identify"):
                if keyboard_module.is_pressed(owner._hk_capture.lower()):
                    owner._on_hk_capture()
                    time.sleep(0.3)
                if keyboard_module.is_pressed(owner._hk_finish.lower()):
                    owner._on_hk_finish()
                    time.sleep(0.5)
        except Exception as exc:
            logger.debug("扫描热键轮询异常，继续监听: {}", exc)
        time.sleep(0.05)


def unregister_scan_hotkeys(owner) -> None:
    owner._hk_active = False
    if sys.platform == "win32" and getattr(owner, "_hk_thread_id", None):
        try:
            ctypes.windll.user32.PostThreadMessageW(
                int(owner._hk_thread_id), 0, 0, 0
            )
        except Exception as exc:
            logger.debug("唤醒扫描热键线程失败，可能线程已退出: {}", exc)


def on_hotkey_stop(owner) -> None:
    worker = (
        getattr(owner, "_scan_worker", None)
        or getattr(owner, "_gamepad_worker", None)
        or getattr(owner, "_gamepad_pipeline_worker", None)
    )
    if worker and worker.scanner:
        logger.warning(
            "收到停止热键 {}，准备停止当前扫描/状态同步任务。",
            owner._hk_stop,
        )
        if hasattr(worker.scanner, "emergency_stop"):
            worker.scanner.emergency_stop()
        else:
            worker.scanner._stopped = True
        worker.scanner._finish_flag = True
    else:
        logger.warning(
            "收到停止热键 {}，但当前没有可停止的扫描器。",
            owner._hk_stop,
        )


def on_hotkey_capture(owner) -> None:
    if getattr(owner, "_hk_mode", None) == "identify":
        owner.identification_controller.capture_foreground()
        return
    worker = getattr(owner, "_scan_worker", None)
    if worker and worker.scanner:
        worker.scanner._capture_flag = True


def on_hotkey_finish(owner) -> None:
    if getattr(owner, "_hk_mode", None) == "identify":
        owner.identification_controller.finish_capture()
        return
    worker = getattr(owner, "_scan_worker", None)
    if worker and worker.scanner:
        worker.scanner._finish_flag = True
