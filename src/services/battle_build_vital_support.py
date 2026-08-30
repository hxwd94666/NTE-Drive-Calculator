# 配装反事实生命结算只接受正式来源逐击。
"""Strict source helpers for build vital counterfactuals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_report import BattleAnalysisHit

_LACRIMOSA_NIGHTMARE_EFFECTS = frozenset({
    "ge_player_lacrimosa_blood_damage",
    "ge_player_lacrimosa_blood_damage_lv6",
})


def safe_ratio(candidate: float, baseline: float) -> float | None:
    if baseline <= 0 or candidate < 0:
        return None
    value = candidate / baseline
    if value != value or value == float("inf"):
        return None
    return max(0.0, min(100.0, value))


def linked_lacrimosa_vital_hits(
    evidence_event_ids: Sequence[str],
    character_id: int | None,
    target_id: str,
    scope_half: str,
    projected_by_event: Mapping[str, BattleBuildHitCounterfactual],
    original_hits: Mapping[str, BattleAnalysisHit],
) -> tuple[BattleBuildHitCounterfactual, ...]:
    return tuple(
        projected_by_event[event_id]
        for event_id in evidence_event_ids
        if event_id in projected_by_event
        and projected_by_event[event_id].character_id == character_id
        and event_id in original_hits
        and original_hits[event_id].direction == "outgoing"
        and original_hits[event_id].target_id == target_id
        and original_hits[event_id].scope_half == scope_half
        and original_hits[event_id].gameplay_effect_id.casefold()
        in _LACRIMOSA_NIGHTMARE_EFFECTS
    )


def projected_hit_damage(row: BattleBuildHitCounterfactual) -> float:
    for value in (row.candidate_damage, row.known_projection_damage):
        if value is not None:
            return max(0.0, float(value))
    return max(0.0, float(row.baseline_damage))
