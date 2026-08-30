# 将公式上下文完全一致的无状态直伤合并为一次代表击计算。
"""Safe representative batching for stateless direct-hit counterfactuals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)


_BATCHABLE_CHANNELS = frozenset({"direct", "direct_follow_up"})
_SHARED_RATIO_METHODS = frozenset({
    "structured_selected",
    "structured_expected",
})


@dataclass(frozen=True, slots=True)
class BattleDirectFormulaBatch:
    """One stable representative and all hits sharing its formula ratio."""

    representative: BattleAnalysisHit
    members: tuple[BattleAnalysisHit, ...]


def _projection_signature(
    projection: BattleHitBuffProjection,
) -> tuple[tuple[str, float, str], ...]:
    return tuple(
        (
            modifier.property_id,
            float(modifier.additive_value),
            modifier.target_scope,
        )
        for modifier in projection.modifiers
    )


def _branch_formula_value(
    replay: BattleHitReplayResult,
) -> float | None:
    if replay.critical_state == "critical":
        value = replay.critical_damage
    elif replay.critical_state in {"non_critical", "not_applicable"}:
        value = replay.non_critical_damage
    elif (
        replay.critical_state == "ambiguous"
        and replay.critical_policy != "unknown"
    ):
        value = replay.expected_damage
    else:
        return None
    if value is None:
        return None
    number = float(value)
    return number if number > 0.0 and isfinite(number) else None


def _formula_key(
    hit: BattleAnalysisHit,
    *,
    formula_character_id: int | None,
    baseline: BattleCharacterBaseline | None,
    evidence: BattleSkillDamageEvidence | None,
    original_replay: BattleHitReplayResult | None,
    original_projection: BattleHitBuffProjection | None,
    candidate_projection: BattleHitBuffProjection | None,
    candidate_formula_projection: BattleHitBuffProjection | None,
    target_condition: BattleTargetCondition | None,
) -> object | None:
    channel_id, _label = classify_battle_hit_channel(hit)
    if channel_id not in _BATCHABLE_CHANNELS:
        return None
    if (
        formula_character_id is None
        or baseline is None
        or evidence is None
        or original_replay is None
        or original_projection is None
        or candidate_projection is None
        or candidate_formula_projection is None
        or target_condition is None
    ):
        return None
    branch_value = _branch_formula_value(original_replay)
    if branch_value is None:
        return None
    frozen_values = {row.property_id: float(row.value) for row in baseline.stats}
    projected_values = BattleBuffAttributeProjectionService.apply_additive(
        frozen_values,
        candidate_formula_projection,
    )
    if any(not isfinite(value) for value in projected_values.values()):
        return None
    evidence_signature = replace(evidence, event_id="")
    return (
        channel_id,
        (
            hit.damage_component,
            hit.classification,
            hit.is_follow_up,
            hit.attack_type,
            hit.damage_attribute,
            hit.ability_id,
            hit.gameplay_effect_id,
            hit.skill_name,
            hit.damage_name,
        ),
        formula_character_id,
        baseline,
        evidence_signature,
        tuple(sorted(projected_values.items())),
        _projection_signature(candidate_formula_projection),
        _projection_signature(original_projection),
        _projection_signature(candidate_projection),
        (hit.scope_half.casefold(), hit.target_id),
        target_condition,
        (
            original_replay.critical_state,
            original_replay.critical_policy,
            branch_value,
        ),
    )


class BattleDirectFormulaBatchService:
    """Plan batches only when every formula-ratio input is exactly equal."""

    @staticmethod
    def plan(
        hits: Sequence[BattleAnalysisHit],
        *,
        formula_character_id_by_event: Mapping[str, int | None],
        baselines: Mapping[int, BattleCharacterBaseline],
        evidence_by_event: Mapping[str, BattleSkillDamageEvidence],
        original_replay_by_event: Mapping[str, BattleHitReplayResult],
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        candidate_projection_by_event: Mapping[str, BattleHitBuffProjection],
        candidate_formula_projection_by_event: Mapping[
            str, BattleHitBuffProjection
        ],
        target_condition_by_event: Mapping[str, BattleTargetCondition | None],
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> tuple[BattleDirectFormulaBatch, ...]:
        grouped: dict[object, list[BattleAnalysisHit]] = {}
        singles: list[BattleDirectFormulaBatch] = []
        order: list[tuple[str, object]] = []
        for ordinal, hit in enumerate(hits, start=1):
            if ordinal == 1 or (ordinal - 1) % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback,
                    phase="buff_counterfactual_batch",
                    message="正在归并公式完全相同的逐击…",
                )
            event_id = hit.event_id
            formula_character_id = formula_character_id_by_event.get(event_id)
            key = _formula_key(
                hit,
                formula_character_id=formula_character_id,
                baseline=(
                    None
                    if formula_character_id is None
                    else baselines.get(formula_character_id)
                ),
                evidence=evidence_by_event.get(event_id),
                original_replay=original_replay_by_event.get(event_id),
                original_projection=original_projection_by_event.get(event_id),
                candidate_projection=candidate_projection_by_event.get(event_id),
                candidate_formula_projection=(
                    candidate_formula_projection_by_event.get(event_id)
                ),
                target_condition=target_condition_by_event.get(event_id),
            )
            if key is None:
                batch = BattleDirectFormulaBatch(hit, (hit,))
                singles.append(batch)
                order.append(("single", len(singles) - 1))
                continue
            if key not in grouped:
                grouped[key] = []
                order.append(("group", key))
            grouped[key].append(hit)
        batches: list[BattleDirectFormulaBatch] = []
        for ordinal, (kind, value) in enumerate(order, start=1):
            if ordinal == 1 or (ordinal - 1) % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback,
                    phase="buff_counterfactual_batch",
                    message="正在生成去重后的代表击批次…",
                )
            batches.append(
                singles[int(value)]
                if kind == "single"
                else BattleDirectFormulaBatch(
                    grouped[value][0],
                    tuple(grouped[value]),
                )
            )
        return tuple(batches)

    @staticmethod
    def ratio_can_be_shared(ratio: BattleCounterfactualRatio) -> bool:
        return (
            ratio.status == "complete"
            and ratio.method in _SHARED_RATIO_METHODS
            and ratio.quantified_ratio is not None
        )


__all__ = [
    "BattleDirectFormulaBatch",
    "BattleDirectFormulaBatchService",
]
