# 把逐击点投影到可复制的非模态公式弹窗。
"""Timeline hit-dialog behavior for the long battle analysis view."""

from __future__ import annotations

from typing import Any

from src.domain.battle_report import BattleAnalysisHit
from src.features.battle_report.timeline_layout import TimelineSelection
from src.features.battle_report.hit_formula_dialog import (
    BattleHitFormulaDialog,
)


class BattleTimelineDetailMixin:
    """Open one reusable hit dialog without owning analysis state."""

    _analysis: Any

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
        replay = next(
            (
                row
                for row in getattr(analysis, "hit_replays", ())
                if row.event_id == hit.event_id
            ),
            None,
        )
        intervals = getattr(
            analysis,
            "timeline_buff_intervals",
            getattr(analysis, "buff_intervals", ()),
        )
        active_buffs = tuple(
            row
            for row in intervals
            if row.start_us <= hit.relative_time_us < row.end_us
            and (
                row.target_scope in {"team", "target", "unknown"}
                or (
                    row.target_scope == "self"
                    and row.source_character_id == hit.character_id
                )
            )
        )
        dialog = getattr(self, "_hit_formula_dialog", None)
        if dialog is None:
            dialog = BattleHitFormulaDialog(self)
            self._hit_formula_dialog = dialog
        dialog.show_for_hit(
            hit,
            replay,
            active_buffs=active_buffs,
        )

    def _hide_hit_formula_dialog(self) -> None:
        dialog = getattr(self, "_hit_formula_dialog", None)
        if dialog is not None:
            dialog.hide()
