# 用同一分析快照协调时间轴范围聚焦和逐击详情，迟到回调不触碰新快照。
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.features.battle_report.hits.formula_dialog import BattleHitFormulaDialog
from src.features.battle_report.timeline.layout import TimelineSelection
from src.features.battle_report.timeline.range import select_axis_evidence


class BattleAnalysisTimelineMixin:
    """页面拥有快照和控件；这里仅执行与当前快照绑定的时间轴交互。"""

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
            # Qt 布局完成后才定位；期间切换战报、半场或清空页面会使旧请求失效。
            if self._analysis is not analysis:
                return
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

    def _render_timeline_selection_detail(
        self,
        selected: TimelineSelection,
    ) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if selected.kind != "hit" or not isinstance(
            selected.payload,
            BattleAnalysisHit,
        ):
            return
        hit = selected.payload
        # 按正式 event_id 匹配已给出的重放；缺项交详情 Provider，不在 UI 猜公式或暴击。
        replay = next(
            (
                row
                for row in getattr(analysis, "hit_replays", ())
                if row.event_id == hit.event_id
            ),
            None,
        )
        details = getattr(self, "_hit_details", None)
        projection, active_buffs = ((None, ()) if details is None
                                   else details.for_hit(hit, formula=True))
        dialog = getattr(self, "_hit_formula_dialog", None)
        if dialog is None:
            dialog = BattleHitFormulaDialog(self, game_ui_asset_root=self._game_ui_asset_root)
            self._hit_formula_dialog = dialog
        dialog.show_for_hit(
            hit,
            replay,
            active_buffs=active_buffs,
            projection=projection,
            participant_names={b.character_id: b.character_name for b in analysis.baselines},
            target_resolutions=analysis.target_instance_resolutions,
        )

    def _hide_hit_formula_dialog(self) -> None:
        dialog = getattr(self, "_hit_formula_dialog", None)
        if dialog is not None:
            dialog.hide()
