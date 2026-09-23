# 绘制兼容明暗主题的自定义角色问号头像。
"""Shared, resolution-aware custom role portrait."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

from src.app.theme import current_theme_name


def custom_role_portrait(size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """Paint a theme-aware question mark instead of borrowing an official image."""

    background, border, glyph = {
        "light": ("#eaf2ff", "#8cb7ea", "#1f5fa8"),
        "black": ("#202327", "#525860", "#d4d9df"),
    }.get(current_theme_name(), ("#19273a", "#3c638b", "#d9eaff"))
    pixels = max(size, round(size * device_pixel_ratio))
    portrait = QPixmap(pixels, pixels)
    portrait.fill(Qt.transparent)
    painter = QPainter(portrait)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(border), 1))
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(QRectF(0.5, 0.5, pixels - 1, pixels - 1), 5 * device_pixel_ratio, 5 * device_pixel_ratio)
    scale = pixels / 100
    question = QPainterPath()
    question.moveTo(29 * scale, 35 * scale)
    question.cubicTo(30 * scale, 23 * scale, 38 * scale, 18 * scale, 50 * scale, 18 * scale)
    question.cubicTo(64 * scale, 18 * scale, 72 * scale, 27 * scale, 71 * scale, 39 * scale)
    question.cubicTo(70 * scale, 50 * scale, 51 * scale, 52 * scale, 50 * scale, 67 * scale)
    painter.setPen(QPen(QColor(glyph), max(2, pixels * 0.085), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(question)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(glyph))
    painter.drawEllipse(QPointF(50 * scale, 82 * scale), 4.5 * scale, 4.5 * scale)
    painter.end()
    portrait.setDevicePixelRatio(device_pixel_ratio)
    return portrait
