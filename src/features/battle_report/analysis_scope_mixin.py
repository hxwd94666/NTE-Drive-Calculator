# 战报范围切换时统一筛选轴证据，并让完整时间轴定位到当前时段。
"""Selected-range helpers for the long battle analysis page."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from src.features.battle_report.timeline_range_mixin import select_axis_evidence


class BattleAnalysisScopeMixin:
    """Keep range-following presentation behavior out of the page layout."""

    _analysis: Any

    def _selected_axis_evidence(self) -> tuple[tuple, tuple, tuple]:
        analysis = self._analysis
        if analysis is None:
            return (), (), ()
        return select_axis_evidence(analysis)

    def _focus_selected_timeline_range(self) -> None:
        """Bring the newly selected half-axis to the viewport start."""

        analysis = self._analysis
        if analysis is None:
            return
        start_display_us = self._display_time_us(analysis.range_start_us)
        end_display_us = self._display_time_us(analysis.range_end_us)

        def focus() -> None:
            try:
                scrollbar = self.timeline_scroll.horizontalScrollBar()
                viewport_width = self.timeline_scroll.viewport().width()
                left = self.timeline.widget_x_for_display_time(start_display_us)
                right = self.timeline.widget_x_for_display_time(end_display_us)
                if right - left <= viewport_width:
                    target = (left + right - viewport_width) / 2.0
                else:
                    target = left - 24.0
                scrollbar.setValue(round(target))
            except RuntimeError:
                # 页面可能已在排队回调执行前被销毁。
                return

        QTimer.singleShot(0, focus)
