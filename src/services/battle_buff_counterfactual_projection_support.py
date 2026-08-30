# 汇总固定轴 Buff 反事实的逐击、生命上限与受益人投影。
"""Pure aggregation helpers for Buff counterfactual projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.domain.battle_buff_counterfactual import (
    BattleBuffBeneficiaryResult,
    BattleDamageCoverage,
)
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    BattleQuantificationGap,
    QuantificationStatus,
)
from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot

_LACRIMOSA_NIGHTMARE_EFFECTS = frozenset({
    "ge_player_lacrimosa_blood_damage",
    "ge_player_lacrimosa_blood_damage_lv6",
})


@dataclass(frozen=True, slots=True)
class HitProjection:
    hit: BattleAnalysisHit
    predicted_damage: float
    quantification: BattleCounterfactualRatio


@dataclass(frozen=True, slots=True)
class VitalProjection:
    event_id: str
    character_id: int | None
    baseline_damage: float
    predicted_damage: float
    status: QuantificationStatus
    gaps: tuple[BattleQuantificationGap, ...] = ()


def safe_ratio(candidate: float, baseline: float) -> float | None:
    if baseline <= 0 or candidate < 0:
        return None
    ratio = candidate / baseline
    if ratio != ratio or ratio == float("inf"):
        return None
    return max(0.0, min(100.0, ratio))


def vital_projections(
    analysis: BattleAnalysisSnapshot,
    hit_projections: Mapping[str, HitProjection],
    baseline_hit_damage_by_event: Mapping[str, float],
    baseline_vital_states: Mapping[
        str, tuple[float, float, float, float]
    ] | None = None,
) -> tuple[VitalProjection, ...]:
    hits_by_event = {hit.event_id: hit for hit in analysis.hits}
    cumulative_reduction_delta: dict[tuple[str, str], float] = {}
    cumulative_effective_delta: dict[tuple[str, str], float] = {}
    result = []
    for event in sorted(
        getattr(analysis, "max_hp_events", ()),
        key=lambda row: (row.observed_at_us, row.event_id),
    ):
        state = (baseline_vital_states or {}).get(event.event_id)
        base_max, base_hp, base_reduction, base_damage = state or (
            float(event.old_max_hp),
            float(event.hp_before_settlement),
            float(event.max_hp_reduction),
            float(event.effective_hp_loss),
        )
        baseline = max(0.0, base_damage)
        predicted = baseline
        character_id = (
            int(event.source_character_id)
            if event.source_character_id is not None
            and int(event.source_character_id) > 0
            else None
        )
        status: QuantificationStatus = "not_applicable"
        gaps: tuple[BattleQuantificationGap, ...] = ()
        if event.mechanic_kind == "lacrimosa_nightmare_awaken_5":
            linked_ids = tuple(
                event_id
                for event_id in event.evidence_event_ids
                if event_id in baseline_hit_damage_by_event
                and event_id in hits_by_event
                and _is_lacrimosa_nightmare_source(
                    hits_by_event[event_id],
                    character_id=character_id,
                    target_id=event.target_id,
                    scope_half=event.scope_half,
                )
            )
            linked_baseline = sum(
                baseline_hit_damage_by_event[event_id]
                for event_id in linked_ids
            )
            linked_without = sum(
                (
                    hit_projections[event_id].predicted_damage
                    if event_id in hit_projections
                    else baseline_hit_damage_by_event[event_id]
                )
                for event_id in linked_ids
            )
            ratio = safe_ratio(linked_without, linked_baseline)
            linked_rows = tuple(
                hit_projections[event_id]
                for event_id in linked_ids
                if event_id in hit_projections
            )
            if not linked_ids or linked_baseline <= 0.0:
                status = "unavailable"
                gaps = (BattleQuantificationGap(
                    code="linked_source_hit_missing",
                    dimension_id="max_hp_reduction_source",
                    dependency_scope="mechanic_specific",
                    property_ids=(),
                    explanation="安魂曲五觉结算缺少可联动的噩梦来源逐击。",
                ),)
            else:
                gaps = tuple(dict.fromkeys(
                    gap
                    for row in linked_rows
                    for gap in row.quantification.gaps
                ))
            quantified = any(
                row.quantification.status in {"complete", "partial"}
                for row in linked_rows
            )
            unresolved = any(
                row.quantification.status in {"partial", "unavailable"}
                for row in linked_rows
            )
            if status == "unavailable":
                pass
            elif quantified and unresolved:
                status = "partial"
            elif quantified:
                status = "complete"
            elif unresolved:
                status = "unavailable"
            continuity_rows = tuple(
                row
                for event_id, row in hit_projections.items()
                if event_id in hits_by_event
                and hits_by_event[event_id].target_id == event.target_id
                and hits_by_event[event_id].scope_half == event.scope_half
                and (
                    hits_by_event[event_id].relative_time_us < event.observed_at_us
                    or event_id in event.evidence_event_ids
                )
            )
            continuity_gaps = tuple(dict.fromkeys(
                gap
                for row in continuity_rows
                for gap in row.quantification.gaps
            ))
            gaps = tuple(dict.fromkeys((*gaps, *continuity_gaps)))
            continuity_unavailable = any(
                row.quantification.status == "unavailable"
                for row in continuity_rows
            )
            continuity_partial = any(
                row.quantification.status == "partial"
                for row in continuity_rows
            )
            continuity_quantified = any(
                row.quantification.status in {"complete", "partial"}
                for row in continuity_rows
            )
            if status == "not_applicable" and continuity_quantified:
                status = "partial" if continuity_partial else "complete"
            if status != "unavailable" and continuity_unavailable:
                status = (
                    "partial"
                    if quantified or continuity_quantified
                    else "unavailable"
                )
            elif status == "complete" and continuity_partial:
                status = "partial"
            if ratio is not None and status in {"complete", "partial"}:
                target_key = (event.scope_half, event.target_id)
                hit_delta = sum(
                    row.predicted_damage
                    - baseline_hit_damage_by_event.get(event_id, row.hit.damage)
                    for event_id, row in hit_projections.items()
                    if event_id in hits_by_event
                    and hits_by_event[event_id].target_id == event.target_id
                    and hits_by_event[event_id].scope_half == event.scope_half
                    and (
                        hits_by_event[event_id].relative_time_us < event.observed_at_us
                        or event_id in event.evidence_event_ids
                    )
                    and row.quantification.status in {"complete", "partial"}
                )
                current_max = max(
                    0.0,
                    base_max
                    - cumulative_reduction_delta.get(target_key, 0.0),
                )
                current_hp = max(
                    0.0,
                    min(
                        current_max,
                        base_hp
                        - hit_delta
                        - cumulative_effective_delta.get(target_key, 0.0),
                    ),
                )
                changed_reduction = max(0.0, base_reduction * ratio)
                predicted = (
                    current_hp * min(1.0, changed_reduction / current_max)
                    if current_max > 0.0
                    else 0.0
                )
                cumulative_reduction_delta[target_key] = (
                    cumulative_reduction_delta.get(target_key, 0.0)
                    + changed_reduction
                    - base_reduction
                )
                cumulative_effective_delta[target_key] = (
                    cumulative_effective_delta.get(target_key, 0.0)
                    + predicted
                    - baseline
                )
        result.append(VitalProjection(
            event_id=event.event_id,
            character_id=character_id,
            baseline_damage=baseline,
            predicted_damage=predicted,
            status=status,
            gaps=gaps,
        ))
    return tuple(result)


def _is_lacrimosa_nightmare_source(
    hit: BattleAnalysisHit,
    *,
    character_id: int | None,
    target_id: str,
    scope_half: str,
) -> bool:
    """Accept only the formal Nightmare damage row as awaken-5 source."""

    return (
        hit.direction == "outgoing"
        and hit.character_id == character_id
        and hit.target_id == target_id
        and hit.scope_half == scope_half
        and hit.gameplay_effect_id.casefold() in _LACRIMOSA_NIGHTMARE_EFFECTS
    )


def aggregate_quantification(
    *,
    hit_projections: Sequence[HitProjection],
    vital_projections: Sequence[VitalProjection],
    fixed_derived_damage: float,
    proven_unchanged_hit_damage: float,
    quantified_increment: float,
) -> BattleDamageQuantification:
    fully_quantified = sum(
        float(row.hit.damage)
        for row in hit_projections
        if row.quantification.status == "complete"
    ) + sum(
        row.baseline_damage
        for row in vital_projections
        if row.status == "complete"
    )
    partially_quantified = sum(
        float(row.hit.damage)
        for row in hit_projections
        if row.quantification.status == "partial"
    ) + sum(
        row.baseline_damage
        for row in vital_projections
        if row.status == "partial"
    )
    unavailable = sum(
        float(row.hit.damage)
        for row in hit_projections
        if row.quantification.status == "unavailable"
    ) + sum(
        row.baseline_damage
        for row in vital_projections
        if row.status == "unavailable"
    )
    proven_unchanged = (
        fixed_derived_damage
        + proven_unchanged_hit_damage
        + sum(
            float(row.hit.damage)
            for row in hit_projections
            if row.quantification.status == "not_applicable"
        )
        + sum(
            row.baseline_damage
            for row in vital_projections
            if row.status == "not_applicable"
        )
    )
    gaps = tuple(dict.fromkeys((
        *(
            gap
            for row in hit_projections
            if float(row.hit.damage) > 0.0
            for gap in row.quantification.gaps
        ),
        *(
            gap
            for row in vital_projections
            if row.baseline_damage > 0.0
            for gap in row.gaps
        ),
    )))
    quantified_damage = fully_quantified + partially_quantified
    if unavailable > 0.0 and quantified_damage <= 0.0:
        status: QuantificationStatus = "unavailable"
    elif partially_quantified > 0.0 or unavailable > 0.0:
        status = "partial"
    elif fully_quantified > 0.0:
        status = "complete"
    else:
        status = "not_applicable"
    increment = (
        None
        if status == "unavailable"
        else (0.0 if status == "not_applicable" else quantified_increment)
    )
    return BattleDamageQuantification.from_buckets(
        status=status,
        fully_quantified_damage=fully_quantified,
        partially_quantified_damage=partially_quantified,
        unavailable_damage=unavailable,
        proven_unchanged_damage=proven_unchanged,
        quantified_increment=increment,
        gaps=gaps,
    )


def beneficiary_result(
    *,
    character_id: int,
    character_name: str,
    hit_projections: Sequence[HitProjection],
    baseline_hit_damage: float,
    vital_projections: Sequence[VitalProjection],
    team_without_quantified_effect_damage: float | None,
    team_without_buff_damage: float | None,
) -> BattleBuffBeneficiaryResult:
    role_hits = tuple(
        row for row in hit_projections if row.hit.character_id == character_id
    )
    active_baseline_damage = sum(float(row.hit.damage) for row in role_hits)
    baseline_damage = baseline_hit_damage + sum(
        row.baseline_damage for row in vital_projections
    )
    without_damage = (
        baseline_hit_damage
        + sum(
            row.predicted_damage - float(row.hit.damage)
            for row in role_hits
        )
        + sum(row.predicted_damage for row in vital_projections)
    )
    known_gain = baseline_damage - without_damage
    quantification = aggregate_quantification(
        hit_projections=role_hits,
        vital_projections=vital_projections,
        fixed_derived_damage=0.0,
        proven_unchanged_hit_damage=max(
            0.0,
            baseline_hit_damage - active_baseline_damage,
        ),
        quantified_increment=known_gain,
    )
    quantified_gain = (
        None if quantification.status == "unavailable" else known_gain
    )
    full_available = quantification.status in {"complete", "not_applicable"}
    full_gain = known_gain if full_available else None
    return BattleBuffBeneficiaryResult(
        character_id=character_id,
        character_name=character_name,
        affected_hits=len(role_hits),
        quantified_hits=sum(
            row.quantification.status in {"complete", "partial"}
            for row in role_hits
        ),
        baseline_damage=baseline_damage,
        without_quantified_effect_damage=(
            without_damage
            if quantification.status != "unavailable"
            else None
        ),
        quantified_damage_gain=quantified_gain,
        quantified_recipient_gain_percent=(
            quantified_gain / without_damage * 100.0
            if quantified_gain is not None and without_damage > 0
            else (0.0 if quantified_gain is not None else None)
        ),
        quantified_team_contribution_percent=(
            quantified_gain / team_without_quantified_effect_damage * 100.0
            if quantified_gain is not None
            and team_without_quantified_effect_damage is not None
            and team_without_quantified_effect_damage > 0
            else (0.0 if quantified_gain is not None else None)
        ),
        without_buff_damage=without_damage if full_available else None,
        damage_gain=full_gain,
        recipient_gain_percent=(
            full_gain / without_damage * 100.0
            if full_gain is not None and without_damage > 0
            else (0.0 if full_gain is not None else None)
        ),
        team_contribution_percent=(
            full_gain / team_without_buff_damage * 100.0
            if full_gain is not None
            and team_without_buff_damage is not None
            and team_without_buff_damage > 0
            else (0.0 if full_gain is not None else None)
        ),
        quantification=quantification,
        damage_coverage=BattleDamageCoverage(
            basis_damage=max(0.0, baseline_damage),
            covered_damage=min(
                max(0.0, baseline_damage),
                max(0.0, active_baseline_damage) + sum(
                    max(0.0, row.baseline_damage)
                    for row in vital_projections
                    if row.status != "not_applicable"
                ),
            ),
        ),
    )


__all__ = [
    "HitProjection",
    "VitalProjection",
    "aggregate_quantification",
    "beneficiary_result",
    "safe_ratio",
    "vital_projections",
]
