# 按战报当前范围投影顶部指标，避免将整场摘要混入半场统计。
from dataclasses import dataclass

from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE, projected_range_duration_us, time_stop_overlap_us,
)


@dataclass(frozen=True, slots=True)
class BattleReportMetrics:
    damage: float | None
    damage_taken: float | None
    duration: float
    real_duration: float
    partial_clock: bool
    incomplete_scope: bool = False

    @property
    def dps(self) -> float | None:
        return None if self.damage is None else self.damage / max(0.001, self.duration)


def project_report_metrics(summary, analysis) -> BattleReportMetrics:
    battle_start = int(getattr(analysis, "battle_start_us", 0))
    battle_end = int(analysis.battle_end_us)
    start = int(getattr(analysis, "range_start_us", battle_start))
    end = int(getattr(analysis, "range_end_us", battle_end))
    scoped = start != battle_start or end != battle_end
    intervals = tuple(getattr(analysis, "time_stop_intervals", ()))
    partial_clock = getattr(analysis, "time_stop_source_kind", "") == "nte_core_partial"
    if scoped:
        # effective_damage already includes selected-range overkill corrections
        # and included HP settlements from the native analysis projection.
        observed_damage = float(analysis.effective_damage)
        has_outgoing = any(hit.direction == "outgoing" for hit in analysis.hits)
        damage = observed_damage if analysis.axis_complete or has_outgoing or observed_damage > 0 else None
        incoming = tuple(hit for hit in analysis.hits if hit.direction == "incoming")
        taken = (
            sum(float(hit.damage) for hit in incoming)
            if analysis.axis_complete or incoming else None
        )
        raw_duration_us = max(0, end - start)
    else:
        damage = max(0.0, float(summary.total_damage)
                     - (analysis.timeline_damage_correction_total if analysis.axis_complete else 0.0)
                     + sum(max(0.0, float(event.effective_hp_loss))
                           for event in getattr(analysis, "timeline_max_hp_events", ())
                           if getattr(event, "included_in_effective_damage", True)))
        taken = getattr(summary, "total_damage_taken", None)
        summary_duration_us = round(summary.duration_seconds * 1_000_000)
        observed = tuple(getattr(analysis, "observed_time_stop_intervals", ()))
        if not observed and getattr(analysis, "time_stop_source_kind", "") == "nte_core":
            observed = intervals
        if observed and getattr(summary, "dps_time_mode", "subtract_time_stop") == "subtract_time_stop":
            interval_end = max((stop or battle_start for _, stop in observed), default=battle_start)
            summary_duration_us += time_stop_overlap_us(battle_start, max(battle_end, interval_end), observed)
        raw_duration_us = max(summary_duration_us, battle_end - battle_start)
        start = battle_start
    active_us = projected_range_duration_us(
        start, start + raw_duration_us,
        intervals=() if partial_clock else intervals, mode=ACTIVE_TIME_MODE,
    )
    return BattleReportMetrics(
        damage=damage, damage_taken=taken, duration=max(0.001, active_us / 1_000_000),
        real_duration=raw_duration_us / 1_000_000, partial_clock=partial_clock,
        incomplete_scope=scoped and not analysis.axis_complete,
    )
