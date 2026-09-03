# 绘制横向滚动时保持固定的时间轴泳道说明列。
"""Sticky lane-label overlay for the unified battle timeline."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from src.app.theme import current_theme_name, theme_color
from src.features.battle_report.timeline_layout import (
    LABEL_WIDTH,
    ROLE_COLORS,
    TOP,
    TimelineLane,
)


def paint_sticky_lane_labels(
    painter: QPainter,
    lanes: Sequence[TimelineLane],
    *,
    sticky_left: float,
    plot_bottom: float,
    draw_avatar: Callable[[QPainter, int, str, QRectF, QColor], None],
) -> None:
    """Paint one opaque label column over horizontally scrolling content."""

    if not lanes:
        return
    overlay = QRectF(
        sticky_left,
        TOP,
        LABEL_WIDTH,
        max(1.0, plot_bottom - TOP),
    )
    painter.save()
    painter.setClipRect(overlay)
    light_theme = current_theme_name() == "light"
    painter.fillRect(overlay, QColor(theme_color("#0d1117")))
    for index, lane in enumerate(lanes):
        if lane.kind == "input":
            background = QColor("#fff8c5" if light_theme else "#17150f")
        elif lane.kind == "action":
            background = QColor("#ddf4ff" if light_theme else "#101927")
        else:
            if light_theme:
                background = QColor("#f6f8fa" if index % 2 == 0 else "#eef2f6")
            else:
                background = QColor("#10151d" if index % 2 == 0 else "#121821")
        painter.fillRect(
            QRectF(sticky_left, lane.top, LABEL_WIDTH, lane.height),
            background,
        )
        painter.setPen(QColor(theme_color("#30363d")))
        painter.drawLine(
            round(sticky_left),
            lane.top,
            round(sticky_left + LABEL_WIDTH),
            lane.top,
        )
        text_left = sticky_left + 10
        if lane.character_id is not None:
            avatar_rect = QRectF(
                sticky_left + 8,
                lane.top + (lane.height - 22) / 2,
                22,
                22,
            )
            draw_avatar(
                painter,
                lane.character_id,
                lane.character_name,
                avatar_rect,
                ROLE_COLORS[lane.role_index % len(ROLE_COLORS)],
            )
            text_left = sticky_left + 36
        painter.setPen(QColor(theme_color("#c9d1d9")))
        painter.drawText(
            round(text_left),
            lane.top,
            round(sticky_left + LABEL_WIDTH - text_left - 8),
            lane.height,
            Qt.AlignVCenter | Qt.AlignLeft,
            lane.label,
        )
    painter.restore()
    painter.setPen(QPen(QColor(theme_color("#30363d")), 1))
    painter.drawLine(
        round(sticky_left + LABEL_WIDTH),
        TOP,
        round(sticky_left + LABEL_WIDTH),
        round(plot_bottom),
    )
