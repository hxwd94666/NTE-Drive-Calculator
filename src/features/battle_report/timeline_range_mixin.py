# 让统一时间轴只布局当前分析范围，同时保留整场绝对轴秒。
"""Range projection and coordinate mapping for the battle timeline widget."""

from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import Any

from src.services.battle_timeline_time_service import (
    projected_range_duration_us,
    unproject_timeline_time_us,
)
from src.features.battle_report.timeline_layout import LABEL_WIDTH, RIGHT_MARGIN


class BattleTimelineRangeMixin:
    """Project full immutable evidence into the currently selected axis range."""

    _analysis: Any

    def widget_x_for_display_time(self, display_time_us: int) -> float:
        plot_width = max(1.0, self.width() - LABEL_WIDTH - RIGHT_MARGIN)
        ratio = min(
            1.0,
            max(
                0.0,
                (int(display_time_us) - self._display_origin_us())
                / self._display_span_us(),
            ),
        )
        return LABEL_WIDTH + plot_width * ratio

    def _visible_analysis(self):
        analysis = self._analysis
        if analysis is None:
            return None
        required = (
            "range_start_us",
            "range_end_us",
            "timeline_hits",
            "inferred_actions",
            "inferred_inputs",
            "timeline_damage_groups",
        )
        if not is_dataclass(analysis) or not all(
            hasattr(analysis, name) for name in required
        ):
            return analysis
        start_us = analysis.range_start_us
        end_us = analysis.range_end_us
        cache_key = (id(analysis), int(start_us), int(end_us))
        if cache_key == getattr(self, "_visible_analysis_cache_key", None):
            return getattr(self, "_visible_analysis_cache", analysis)
        hits = tuple(
            hit
            for hit in analysis.timeline_hits
            if start_us <= hit.relative_time_us < end_us
        )
        hit_ids = {hit.event_id for hit in hits}
        actions = tuple(
            action
            for action in analysis.inferred_actions
            if hit_ids.intersection(action.evidence_event_ids)
        )
        action_ids = {action.action_id for action in actions}
        inputs = tuple(
            row for row in analysis.inferred_inputs if row.action_id in action_ids
        )
        groups = tuple(
            group
            for group in analysis.timeline_damage_groups
            if (
                hit_ids.intersection(group.evidence_event_ids)
                or (
                    group.channel_key.startswith("max_hp_reduction")
                    and start_us <= group.start_us < end_us
                )
            )
        )
        visible = replace(
            analysis,
            timeline_hits=hits,
            inferred_actions=actions,
            inferred_inputs=inputs,
            timeline_damage_groups=groups,
        )
        self._visible_analysis_cache_key = cache_key
        self._visible_analysis_cache = visible
        return visible

    def _x_for_raw_time(
        self,
        raw_time_us: int,
        plot_left: float,
        plot_width: float,
    ) -> float:
        ratio = min(
            1.0,
            max(
                0.0,
                (self._projected_time(raw_time_us) - self._display_origin_us())
                / self._display_span_us(),
            ),
        )
        return plot_left + plot_width * ratio

    def _display_time_for_x(self, x: float) -> int:
        plot_width = max(1.0, self.width() - LABEL_WIDTH - RIGHT_MARGIN)
        ratio = min(1.0, max(0.0, (x - LABEL_WIDTH) / plot_width))
        return self._display_origin_us() + round(self._display_span_us() * ratio)

    def _raw_time_for_x(
        self,
        x: float,
        *,
        prefer_interval_end: bool = False,
    ) -> int:
        analysis = self._analysis
        if analysis is None:
            return 0
        return unproject_timeline_time_us(
            self._display_time_for_x(x),
            battle_start_us=analysis.battle_start_us,
            battle_end_us=getattr(
                analysis,
                "range_end_us",
                analysis.timeline_end_us,
            ),
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
            prefer_interval_end=prefer_interval_end,
        )

    def _display_origin_us(self) -> int:
        analysis = self._analysis
        if analysis is None:
            return 0
        return self._projected_time(
            getattr(analysis, "range_start_us", analysis.battle_start_us)
        )

    def _display_span_us(self) -> int:
        analysis = self._analysis
        if analysis is None:
            return 1
        return max(
            1,
            projected_range_duration_us(
                getattr(analysis, "range_start_us", analysis.battle_start_us),
                getattr(analysis, "range_end_us", analysis.timeline_end_us),
                intervals=analysis.time_stop_intervals,
                mode=self._time_mode,
            ),
        )
