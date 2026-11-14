# 绘制装备锁定与弃置状态图标。
"""仓库与配装共用锁图标；只绘制已传入状态，不设置游戏标记。"""

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
    """按已传入的锁状态与可用性绘制仓库按钮。"""

    light_locked = active and current_theme_name() == "light"
    background = "#f2cc60" if light_locked else "#3a2f13" if active else "#21262d"
    foreground = "#9a6700" if light_locked else "#e3b341" if active else "#8b949e"
    if not available:
        background, foreground = "#21262d", "#484f58"
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
    """复用仓库卡片绘制入口生成图标。"""

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


def allocation_lock_icon(active: bool) -> QIcon:
    """配装锁使用无底色大图标，不改仓库按钮样式或游戏锁状态。"""
    canvas = QPixmap(48, 48)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    foreground = '#e3b341' if active else '#8b949e'
    color = QColor(theme_color(foreground))
    shackle = QPainterPath()
    shackle.moveTo(14, 23)
    shackle.lineTo(14, 15)
    shackle.cubicTo(14, 2, 34, 2, 34, 15)
    shackle.lineTo(34, 23)
    painter.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(shackle)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(8, 21, 32, 23), 4, 4)
    # 钥匙孔保持透明，复用所在卡片背景。
    painter.setCompositionMode(QPainter.CompositionMode_Clear)
    painter.drawEllipse(QRectF(21, 27, 6, 6))
    painter.drawRoundedRect(QRectF(22.5, 31, 3, 7), 1, 1)
    painter.end()
    canvas.setDevicePixelRatio(2)
    icon = QIcon()
    for mode in (QIcon.Normal, QIcon.Active, QIcon.Selected):
        icon.addPixmap(canvas, mode)
    return icon
