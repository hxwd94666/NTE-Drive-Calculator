# 提供鼠标视觉扫描的前台窗口截图后端。
"""Foreground-window capture backend for mouse visual scans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from src.scanner.window_capture import WindowRect, get_foreground_client_rect


@dataclass(frozen=True)
class MouseCapturedFrame:
    image: np.ndarray
    rect: WindowRect
    hwnd: int


class MouseFrameProvider(Protocol):
    def capture(self) -> MouseCapturedFrame: ...

    def close(self) -> None: ...


class ForegroundWindowFrameProvider:
    """Capture the current foreground client area and expose its HWND."""

    def __init__(self) -> None:
        import mss

        self._sct = mss.MSS()

    @staticmethod
    def _foreground_hwnd() -> int:
        try:
            import ctypes

            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def capture(self) -> MouseCapturedFrame:
        rect = get_foreground_client_rect()
        screenshot = self._sct.grab(rect.to_mss_monitor())
        image = np.array(screenshot)
        if image.ndim != 3 or image.shape[2] < 3:
            raise RuntimeError("前台窗口截图格式无效")
        return MouseCapturedFrame(
            image=image[:, :, :3].copy(),
            rect=rect,
            hwnd=self._foreground_hwnd(),
        )

    def close(self) -> None:
        close = getattr(self._sct, "close", None)
        if callable(close):
            close()
