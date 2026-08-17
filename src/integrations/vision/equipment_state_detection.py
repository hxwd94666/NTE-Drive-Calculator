# 识别视觉扫描中的装备状态。
"""Lightweight visual detection for equipment state controls."""

from __future__ import annotations

import cv2
import numpy as np

from src.scanner.window_capture import game_content_rect


TRASH_BUTTON_CENTER = (0.89375, 0.21944)
LOCK_BUTTON_CENTER = (0.93047, 0.21944)
CONFIRM_BUTTON_CENTER = (0.60410, 0.65938)
STATE_BUTTON_SIZE_RATIO = 0.025


def state_button_is_active(img: np.ndarray, center: tuple[float, float]) -> bool:
    height, width = img.shape[:2]
    if height < 100 or width < 100:
        return False
    left, top, content_width, content_height = game_content_rect(width, height)
    cx = int(round(left + content_width * center[0]))
    cy = int(round(top + content_height * center[1]))
    size = max(18, int(round(min(content_width, content_height) * STATE_BUTTON_SIZE_RATIO)))
    half = max(1, size // 2)
    roi = img[max(0, cy - half) : min(height, cy + half), max(0, cx - half) : min(width, cx + half)]
    if roi.size == 0:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    bright_fraction = float((gray > 95).mean())
    high_value = float(np.percentile(hsv[:, :, 2], 95))
    return bool(bright_fraction > 0.12 and high_value > 130.0)


def right_panel_button_state_from_image(img: np.ndarray) -> str:
    trash_active = state_button_is_active(img, TRASH_BUTTON_CENTER)
    lock_active = state_button_is_active(img, LOCK_BUTTON_CENTER)
    if trash_active and lock_active:
        return "unknown"
    if lock_active:
        return "locked"
    if trash_active:
        return "discarded"
    return "normal"


def lock_to_discard_confirmation_visible(img: np.ndarray) -> bool:
    height, width = img.shape[:2]
    if height < 100 or width < 100:
        return False
    left, top, content_width, content_height = game_content_rect(width, height)
    body = img[
        top + round(content_height * 0.35) : top + round(content_height * 0.48),
        left + round(content_width * 0.25) : left + round(content_width * 0.75),
    ]
    if body.size == 0:
        return False
    gray = cv2.cvtColor(body, cv2.COLOR_BGR2GRAY)
    return bool(float((gray > 175).mean()) > 0.72)
