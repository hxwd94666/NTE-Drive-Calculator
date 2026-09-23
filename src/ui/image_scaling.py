# 按屏幕像素比一次性缩放界面图片，避免头像二次插值模糊。
"""DPI-aware UI pixmaps that never upscale a smaller packaged source."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap


def asset_pixmap(
    path: str | Path | None,
    logical_side: int,
    device_pixel_ratio: float,
) -> QPixmap:
    """Return a centered, crisp image at no more than its source resolution."""

    source = QPixmap(str(path or ""))
    if source.isNull():
        return source
    ratio = max(1.0, float(device_pixel_ratio))
    physical_side = max(1, round(logical_side * ratio))
    if source.width() > physical_side or source.height() > physical_side:
        source = source.scaled(
            physical_side,
            physical_side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    source.setDevicePixelRatio(ratio)
    return source


__all__ = ["asset_pixmap"]
