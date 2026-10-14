# 生成角色选择器的 DPI 适配头像；自建角色与缺图占位只影响展示，不参与角色身份。
"""Presentation-only helpers for the legacy allocation role selector."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

from src.app.theme import current_theme_name, themed_style
from src.ui.role_portrait import custom_role_portrait


def priority_link_style(link_text: str) -> str:
    """关系按钮用不透明的中性底色；批次边界继续用红色提示，不改关系语义。"""
    if current_theme_name() == "light":
        color = "#cf222e" if link_text == ">>" else "#0969da"
        hover = "#fff5f5" if link_text == ">>" else "#f6f8fa"
        return (
            f"QPushButton{{color:{color};border:1px solid {color};background:#ffffff;"
            "border-radius:6px;font-weight:700}"
            f"QPushButton:hover{{background:{hover}}}"
            f"QPushButton:focus{{border:2px solid {color}}}"
            "QPushButton:pressed{background:#eef2f6}"
            "QPushButton:disabled{color:#6e7781;border-color:#d0d7de;background:#f6f8fa}"
        )
    color, border, background, hover = (
        ("#ff7b72", "#f85149", "#2d1117", "#3c151c") if link_text == ">>"
        else ("#58a6ff", "#58a6ff", "#21262d", "#30363d")
    )
    return themed_style(
        f"QPushButton{{color:{color};border:1px solid {border};background:{background};"
        "border-radius:6px;font-weight:700}"
        f"QPushButton:hover{{background:{hover};border-color:{color}}}"
        f"QPushButton:focus{{border:2px solid {color}}}"
        "QPushButton:pressed{background:#161b22}"
        "QPushButton:disabled{color:#6e7681;border-color:#30363d;background:#161b22}"
    )


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
