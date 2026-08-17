# 约束弹窗几何尺寸，避免高 DPI 缩放后超出当前屏幕工作区。
"""Screen-aware geometry helpers for top-level application windows."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication, QDialog


_QT_WIDGET_MAX_SIZE = 16_777_215


def _available_geometry(dialog: QDialog) -> QRect:
    parent = dialog.parentWidget()
    screen = parent.screen() if parent is not None else dialog.screen()
    if screen is None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
    return screen.availableGeometry() if screen is not None else QRect()


def constrained_window_size(
    requested: QSize,
    available: QRect,
    *,
    margin: int = 24,
) -> QSize:
    """Cap a logical Qt window size to a screen work area on every DPI scale."""

    inset = max(0, int(margin)) * 2
    maximum_width = max(1, int(available.width()) - inset)
    maximum_height = max(1, int(available.height()) - inset)
    return QSize(
        min(maximum_width, max(1, int(requested.width()))),
        min(maximum_height, max(1, int(requested.height()))),
    )


def fit_dialog_to_available_screen(
    dialog: QDialog,
    preferred_size: QSize | None = None,
    *,
    margin: int = 24,
    available_geometry: QRect | None = None,
) -> QSize:
    """Shrink and center a dialog inside its current screen's available area.

    Existing smaller dialogs retain their requested dimensions. Oversized
    fixed/minimum sizes are relaxed only as far as required by the work area,
    so title bars and bottom actions remain reachable across common 100%–200%
    scaling ratios and mixed-DPI monitors.
    """

    available = available_geometry or _available_geometry(dialog)
    if available.isEmpty():
        return dialog.size()
    requested = preferred_size or dialog.size().expandedTo(dialog.minimumSize())
    target = constrained_window_size(requested, available, margin=margin)

    old_minimum = dialog.minimumSize()
    old_maximum = dialog.maximumSize()
    if old_minimum.width() == old_maximum.width() and old_minimum.width() > target.width():
        dialog.setMaximumWidth(_QT_WIDGET_MAX_SIZE)
    if old_minimum.height() == old_maximum.height() and old_minimum.height() > target.height():
        dialog.setMaximumHeight(_QT_WIDGET_MAX_SIZE)
    dialog.setMinimumSize(
        min(old_minimum.width(), target.width()),
        min(old_minimum.height(), target.height()),
    )
    dialog.resize(target)

    parent = dialog.parentWidget()
    anchor = parent.window() if parent is not None else None
    center = (
        anchor.mapToGlobal(anchor.rect().center())
        if anchor is not None and anchor.isVisible()
        else available.center()
    )
    x = center.x() - target.width() // 2
    y = center.y() - target.height() // 2
    x = max(available.left(), min(x, available.right() - target.width() + 1))
    y = max(available.top(), min(y, available.bottom() - target.height() + 1))
    dialog.move(x, y)
    return target
