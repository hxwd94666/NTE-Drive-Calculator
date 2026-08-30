"""Decision-oriented panel and damage-share metrics for marginal result rows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleMaxHpReductionEvent,
)
from src.services.battle_buff_counterfactual_projection_support import VitalProjection
from src.services.battle_marginal_calculation_support import topple_character_contribution


def marginal_display_metrics(
    *,
    property_id: str,
    panel_value: float,
    relevant_hits: Sequence[BattleAnalysisHit],
    projections: Mapping[str, BattleHitBuffProjection],
    linked_vitals: Sequence[VitalProjection],
    max_hp_events: Sequence[BattleMaxHpReductionEvent],
    topple_hits: Sequence[BattleAnalysisHit],
    replays: Mapping[str, BattleHitReplayResult],
    character_id: int,
    anchor_damage: Callable[[BattleAnalysisHit], float],
    related_damage: float,
    role_damage: float,
    team_damage: float,
) -> tuple[float | None, float, float, float]:
    """Return weighted value and the three explicitly denominated shares."""

    weighted_sum = 0.0
    weighted_damage = 0.0
    unknown_damage = 0.0
    effective_by_event: dict[str, float] = {}
    hit_by_event = {row.event_id: row for row in relevant_hits}
    for hit in relevant_hits:
        value = panel_value + sum(
            float(modifier.additive_value)
            for modifier in projections[hit.event_id].modifiers
            if modifier.property_id == property_id
        )
        damage = anchor_damage(hit)
        effective_by_event[hit.event_id] = value
        weighted_sum += damage * value
        weighted_damage += damage

    vital_event_by_id = {row.event_id: row for row in max_hp_events}
    for projection in linked_vitals:
        damage = max(0.0, float(projection.baseline_damage))
        evidence = vital_event_by_id.get(projection.event_id)
        linked_ids = () if evidence is None else tuple(
            event_id
            for event_id in evidence.evidence_event_ids
            if event_id in hit_by_event
        )
        linked_weight = sum(anchor_damage(hit_by_event[event_id]) for event_id in linked_ids)
        if linked_weight <= 0.0:
            unknown_damage += damage
            continue
        value = sum(
            anchor_damage(hit_by_event[event_id]) * effective_by_event[event_id]
            for event_id in linked_ids
        ) / linked_weight
        weighted_sum += damage * value
        weighted_damage += damage

    for hit in topple_hits:
        replay = replays.get(hit.event_id)
        contribution = topple_character_contribution(
            replay,
            character_id=character_id,
            team_topple_damage=anchor_damage(hit),
        )
        if contribution is None:
            continue
        source = next((
            factor for factor in replay.factors
            if factor.factor_id == f"topple_character:{character_id}"
        ), None) if replay is not None else None
        term_values = () if source is None else tuple(
            float(term.value) for term in source.terms
            if term.property_id == property_id
        )
        value = sum(term_values) if term_values else panel_value
        weighted_sum += contribution * value
        weighted_damage += contribution

    tolerance = max(1e-6, related_damage * 1e-9)
    if unknown_damage > tolerance or weighted_damage + tolerance < related_damage:
        weighted_value = None
    else:
        weighted_value = weighted_sum / weighted_damage if weighted_damage > 0 else None
    return (
        weighted_value,
        related_damage / role_damage * 100.0 if role_damage > 0 else 0.0,
        role_damage / team_damage * 100.0 if team_damage > 0 else 0.0,
        related_damage / team_damage * 100.0 if team_damage > 0 else 0.0,
    )
