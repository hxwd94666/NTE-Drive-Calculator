# 绘制兼容明暗主题的自定义角色问号头像。
"""按主题和设备像素比绘制自建角色头像，不借用官方角色图。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QRegion

from src.app.theme import current_theme_name


def temporary_role_portrait(path: str, size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """只在展示时去掉临时宽画布透明边、等比居中；原始 PNG 和来源哈希不改。"""
    source = QPixmap(path)
    if source.isNull():
        return source
    bounds = QRegion(source.mask()).boundingRect() if source.hasAlphaChannel() else source.rect()
    if not bounds.isEmpty():
        source = source.copy(bounds)
    pixels = max(size, round(size * device_pixel_ratio))
    result = QPixmap(pixels, pixels)
    result.fill(Qt.transparent)
    scaled = source.scaled(pixels, pixels, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(result)
    painter.drawPixmap((pixels - scaled.width()) // 2, (pixels - scaled.height()) // 2, scaled)
    painter.end()
    result.setDevicePixelRatio(device_pixel_ratio)
    return result


def custom_role_portrait(size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """生成问号头像并保留设备像素比；只处理展示，不绑定角色身份。"""

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
