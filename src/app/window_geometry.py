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
    # resize使用客户区尺寸，move使用顶层边框坐标；原生标题栏必须占用可用屏幕预算。
    # 未创建原生框架时差值可能为零；首次显示后调用同一helper可获得当前屏幕/DPI的实际值。
    frame, client = dialog.frameGeometry(), dialog.geometry()
    frame_width = max(0, frame.width() - client.width())
    frame_height = max(0, frame.height() - client.height())
    client_area = QRect(available.topLeft(), QSize(max(1, available.width() - frame_width),
                                                  max(1, available.height() - frame_height)))
    target = constrained_window_size(requested, client_area, margin=margin)
    outer = QSize(target.width() + frame_width, target.height() + frame_height)

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
    x = center.x() - outer.width() // 2
    y = center.y() - outer.height() // 2
    requested_margin = max(0, int(margin))
    horizontal_margin = min(
        requested_margin,
        max(0, (available.width() - outer.width()) // 2),
    )
    vertical_margin = min(
        requested_margin,
        max(0, (available.height() - outer.height()) // 2),
    )
    minimum_x = available.left() + horizontal_margin
    maximum_x = available.right() - horizontal_margin - outer.width() + 1
    minimum_y = available.top() + vertical_margin
    maximum_y = available.bottom() - vertical_margin - outer.height() + 1
    x = max(minimum_x, min(x, maximum_x))
    y = max(minimum_y, min(y, maximum_y))
    dialog.move(x, y)
    return target
