# 复用命中语义与区间集合完全一致的 Buff 投影结果。
"""Request-local exact cache for per-hit Buff projections."""

from __future__ import annotations

from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitBuffProjection,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_interval_index import (
    BattleBuffIntervalQuery,
    buff_interval_applies_to_hit,
)


def _hit_projection_signature(hit: BattleAnalysisHit) -> BattleAnalysisHit:
    return replace(
        hit,
        event_id="",
        sequence=0,
        relative_time_us=0,
        damage=0.0,
        raw_damage=None,
        overkill_damage=None,
        damage_correction_kind="",
        damage_correction_confidence="",
        damage_correction_basis="",
        damage_overlap_correction=0.0,
    )


class BattleHitBuffProjectionCache:
    """Cache only exact immutable hit/active/temporal projection inputs."""

    def __init__(self, intervals: BattleBuffIntervalQuery) -> None:
        self._intervals = intervals
        self._cache: dict[object, BattleHitBuffProjection] = {}

    def project(self, hit: BattleAnalysisHit) -> BattleHitBuffProjection:
        temporal = self._intervals.temporal_for_hit(hit)
        active = tuple(
            interval
            for interval in temporal
            if buff_interval_applies_to_hit(interval, hit)
        )
        key = (_hit_projection_signature(hit), temporal, active)
        projection = self._cache.get(key)
        if projection is None:
            projection = BattleBuffAttributeProjectionService.project_hit(
                hit,
                self._intervals,
                active_intervals=active,
                temporal_intervals=temporal,
            )
            self._cache[key] = projection
            return projection
        return replace(projection, event_id=hit.event_id)


__all__ = ["BattleHitBuffProjectionCache"]
