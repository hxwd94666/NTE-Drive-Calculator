# 提供角色选择器的首字拼音排序与轻量头像控件。
"""Presentation-only helpers for the legacy allocation role selector."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

from src.app.theme import themed_style
from src.ui.role_portrait import custom_role_portrait


def role_avatar(
    name: str,
    icon_path: str | Path | None,
    size: int,
    *,
    is_custom: bool = False,
    device_pixel_ratio: float | None = None,
) -> QLabel:
    """Build a portrait, custom-role question mark, or missing-art initial."""

    avatar = QLabel()
    avatar.setFixedSize(size, size)
    avatar.setAlignment(Qt.AlignCenter)
    avatar.setAccessibleName(f"{name}头像")
    avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    ratio = max(1.0, device_pixel_ratio or avatar.devicePixelRatioF())
    if is_custom:
        avatar.setAccessibleDescription("自定义角色问号头像")
        avatar.setPixmap(custom_role_portrait(size, ratio))
        return avatar
    pixmap = QPixmap(str(icon_path)) if icon_path else QPixmap()
    if not pixmap.isNull():
        pixels = max(size, round(size * ratio))
        portrait = pixmap.scaled(pixels, pixels, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        portrait.setDevicePixelRatio(ratio)
        avatar.setPixmap(portrait)
        avatar.setStyleSheet(
            themed_style("background:#1c2128;border:1px solid #30363d;border-radius:5px")
        )
    else:
        avatar.setText(next((char for char in str(name) if char.isalnum()), "?"))
        avatar.setStyleSheet(
            themed_style(
                "background:#22313f;color:#c9d1d9;border:1px solid #40536a;"
                "border-radius:5px;font-size:13px;font-weight:700"
            )
        )
    return avatar
