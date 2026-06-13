"""Window capture and coordinate conversion utilities for game screenshots."""

import ctypes
import ctypes.wintypes
from dataclasses import dataclass

import mss
import numpy as np


DWMWA_EXTENDED_FRAME_BOUNDS = 9
SM_CXSCREEN = 0
SM_CYSCREEN = 1
BASE_GAME_ASPECT = 16 / 9


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)

    def to_mss_monitor(self) -> dict:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


def _primary_screen_rect() -> WindowRect:
    user32 = ctypes.windll.user32
    return WindowRect(0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN))


def get_foreground_window_rect() -> WindowRect:
    """Return the foreground window rectangle, falling back to the primary screen."""
    if not hasattr(ctypes, "windll"):
        return _primary_screen_rect()

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return _primary_screen_rect()

    rect = ctypes.wintypes.RECT()
    try:
        dwmapi = ctypes.windll.dwmapi
        result = dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return WindowRect(rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass

    if user32.GetWindowRect(hwnd, ctypes.byref(rect)) and rect.right > rect.left and rect.bottom > rect.top:
        return WindowRect(rect.left, rect.top, rect.right, rect.bottom)
    return _primary_screen_rect()


def get_foreground_client_rect() -> WindowRect:
    """Return foreground window client area in screen coordinates.

    The extended frame bounds include the title bar and resize borders for
    normal windows. Game UI coordinates are relative to the client area, so
    screenshots and clicks should use this rectangle whenever Windows exposes it.
    """
    if not hasattr(ctypes, "windll"):
        return _primary_screen_rect()

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return _primary_screen_rect()

    client = ctypes.wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        return get_foreground_window_rect()

    point = ctypes.wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        return get_foreground_window_rect()

    rect = WindowRect(
        point.x,
        point.y,
        point.x + max(1, client.right - client.left),
        point.y + max(1, client.bottom - client.top),
    )
    if rect.width <= 1 or rect.height <= 1:
        return get_foreground_window_rect()
    return rect


def capture_foreground_window(sct: mss.MSS | None = None):
    """Capture the foreground client area and return (screenshot, rect)."""
    rect = get_foreground_client_rect()
    if sct is not None:
        return sct.grab(rect.to_mss_monitor()), rect
    with mss.MSS() as local_sct:
        return local_sct.grab(rect.to_mss_monitor()), rect


def scale_region(
    region: tuple[int, int, int, int],
    target_width: int,
    target_height: int,
    base_size: tuple[int, int],
    preserve_aspect: bool = True,
    content_rect: tuple[int, int, int, int] | None = None,
):
    if content_rect is not None:
        left, top, content_width, content_height = content_rect
    elif preserve_aspect:
        left, top, content_width, content_height = fit_content_rect(target_width, target_height, base_size)
    else:
        left, top, content_width, content_height = 0, 0, target_width, target_height
    scale_x = content_width / base_size[0]
    scale_y = content_height / base_size[1]
    x1, y1, x2, y2 = region
    return (
        max(0, min(target_width, left + round(x1 * scale_x))),
        max(0, min(target_height, top + round(y1 * scale_y))),
        max(0, min(target_width, left + round(x2 * scale_x))),
        max(0, min(target_height, top + round(y2 * scale_y))),
    )


def fit_content_rect(target_width: int, target_height: int, base_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Fit the base game aspect ratio inside a window, preserving letterbox offsets."""
    base_w, base_h = base_size
    base_aspect = base_w / base_h
    target_aspect = target_width / target_height
    if target_aspect >= base_aspect:
        content_height = target_height
        content_width = round(content_height * base_aspect)
        left = round((target_width - content_width) / 2)
        top = 0
    else:
        content_width = target_width
        content_height = round(content_width / base_aspect)
        left = 0
        top = round((target_height - content_height) / 2)
    return left, top, max(1, content_width), max(1, content_height)


def crop_window_border_from_image(image: np.ndarray, target_aspect: float = BASE_GAME_ASPECT) -> np.ndarray:
    """Crop common Windows non-client borders from already captured screenshots.

    Some older screenshots were captured from DWM extended frame bounds, e.g.
    1924x1127 for a 1920x1080 game client or 2564x1487 for a 2560x1440 client.
    The client area is typically offset by 2 px horizontally and 45 px from the
    top. This keeps imported/old screenshots compatible after client capture is
    fixed for new scans.
    """
    if image is None or image.ndim < 2:
        return image
    height, width = image.shape[:2]

    # Windows 11 normal border: left/right about 2 px, title/top about 45 px,
    # bottom about 2 px. Accept nearby sizes to tolerate DPI/theme differences.
    best = None
    for border_x in range(0, 9):
        for top in range(28, 65):
            for bottom in range(0, 9):
                content_w = width - border_x * 2
                content_h = height - top - bottom
                if content_w < 1000 or content_h < 700:
                    continue
                aspect = content_w / content_h
                aspect_error = abs(aspect - target_aspect)
                if aspect_error > 0.003:
                    continue
                grid_error = abs(round(content_w / 16) * 9 - content_h)
                if grid_error > 3:
                    continue
                # Prefer the closest 16:9 crop, then the common Windows frame
                # shape (2 px side borders, 45 px title/top, 2 px bottom).
                common_frame_error = abs(border_x - 2) + abs(top - 45) + abs(bottom - 2)
                score = (aspect_error, grid_error, common_frame_error)
                if best is None or score < best[0]:
                    best = (score, border_x, top, content_w, content_h)

    if best is not None:
        _, border_x, top, content_w, content_h = best
        return image[top:top + content_h, border_x:border_x + content_w].copy()

    return image
