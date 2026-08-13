# 绘制装备锁定与弃置状态图标。
"""Shared equipment-state icons used by warehouse and loadout views."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from src.app.theme import current_theme_name, theme_color


def paint_warehouse_lock_button(
    painter: QPainter,
    rect: QRect,
    *,
    active: bool,
    available: bool = True,
) -> None:
    """Paint the warehouse lock action, including its rounded background."""

    light_locked = active and current_theme_name() == "light"
    background = "#f2cc60" if light_locked else "#3a2f13" if active else "#21262d"
    foreground = "#9a6700" if light_locked else "#e3b341" if active else "#8b949e"
    if not available:
        background, foreground = "#1b2027", "#484f58"
    painter.setBrush(QColor(theme_color(background)))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(rect, 4, 4)

    icon_color = QColor(theme_color(foreground))
    body = QRectF(rect.left() + 5.0, rect.top() + 10.0, 10.0, 7.0)
    painter.setPen(Qt.NoPen)
    painter.setBrush(icon_color)
    painter.drawRoundedRect(body, 1.8, 1.8)
    shackle = QPainterPath()
    shackle.moveTo(rect.left() + 6.9, rect.top() + 10.0)
    shackle.lineTo(rect.left() + 6.9, rect.top() + 8.0)
    shackle.cubicTo(
        rect.left() + 6.9,
        rect.top() + 4.6,
        rect.left() + 13.1,
        rect.top() + 4.6,
        rect.left() + 13.1,
        rect.top() + 8.0,
    )
    shackle.lineTo(rect.left() + 13.1, rect.top() + 10.0)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(
        QPen(icon_color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    )
    painter.drawPath(shackle)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(theme_color(background)))
    painter.drawEllipse(
        QRectF(rect.left() + 8.5, rect.top() + 12.0, 3.0, 3.0)
    )
    painter.drawRoundedRect(
        QRectF(rect.left() + 9.35, rect.top() + 14.0, 1.3, 1.8),
        0.6,
        0.6,
    )


def warehouse_lock_icon(active: bool, *, size: int = 20) -> QIcon:
    """Return the exact lock action artwork used on warehouse cards."""

    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    paint_warehouse_lock_button(
        painter,
        QRect(0, 0, size, size),
        active=active,
    )
    painter.end()
    return QIcon(canvas)
