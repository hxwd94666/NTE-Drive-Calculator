# 提供逐击反事实的面板、缩放、暴击与增伤纯函数。
"""Pure formula helpers shared by the hit counterfactual ratio service."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from src.domain.battle_report import (
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_fixed_critical_ratio_service import (
    CONTINUOUS_DIRECT_CHANNEL_IDS,
    FIXED_HALF_CRIT_CHANNEL_IDS,
    fixed_half_critical_ratio,
)
from src.services.damage_calculation_service import (
    calculate_attribute_value,
    calculate_critical_multiplier,
)


ELEMENT_PROPERTY = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
SCALING_PROPERTIES = {
    "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
    "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
    "Def": ("DefBase", "DefUp", "DefAdd"),
}


def projected_values(
    baseline: BattleCharacterBaseline | None,
    projection: BattleHitBuffProjection | None,
) -> dict[str, float]:
    values = (
        {} if baseline is None
        else {row.property_id: float(row.value) for row in baseline.stats}
    )
    if projection is None:
        return values
    return BattleBuffAttributeProjectionService.apply_additive(values, projection)


def scaling_id(
    evidence: BattleSkillDamageEvidence | None,
    original_replay: BattleHitReplayResult | None,
    candidate_replay: BattleHitReplayResult | None,
    *,
    channel_id: str = "",
) -> str | None:
    value = "" if evidence is None else str(evidence.scaling_property_id)
    if value in SCALING_PROPERTIES:
        return value
    if channel_id in CONTINUOUS_DIRECT_CHANNEL_IDS:
        return "Atk"
    if channel_id == "special_kuhara_formula":
        return "Atk"
    for replay in (original_replay, candidate_replay):
        if replay is None:
            continue
        candidates: set[str] = set()
        for factor in replay.factors:
            if factor.factor_id != "scaling":
                continue
            term_properties = {term.property_id for term in factor.terms}
            for candidate_id, property_ids in SCALING_PROPERTIES.items():
                if term_properties & set((*property_ids, candidate_id)):
                    candidates.add(candidate_id)
            label_id = factor.label.partition(" ")[0]
            if label_id in SCALING_PROPERTIES:
                candidates.add(label_id)
        if len(candidates) == 1:
            return next(iter(candidates))
    return None


def scaling_ratio(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    properties: tuple[str, str, str],
) -> float | None:
    base_id, up_id, add_id = properties
    original_value = calculate_attribute_value(
        original.get(base_id, 0.0),
        original.get(up_id, 0.0),
        original.get(add_id, 0.0),
    )
    candidate_value = calculate_attribute_value(
        candidate.get(base_id, 0.0),
        candidate.get(up_id, 0.0),
        candidate.get(add_id, 0.0),
    )
    return _safe_ratio(candidate_value, original_value)


def critical_ratio(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    replay: BattleHitReplayResult | None,
    *,
    channel_id: str = "",
) -> float | None:
    if channel_id in FIXED_HALF_CRIT_CHANNEL_IDS:
        return fixed_half_critical_ratio(original, candidate, replay)
    if replay is None or replay.critical_policy == "unknown":
        return None
    state = replay.critical_state
    if state == "critical":
        original_factor = 1.0 + max(0.0, original.get("CritDamageBase", 0.5))
        candidate_factor = 1.0 + max(0.0, candidate.get("CritDamageBase", 0.5))
    elif state == "non_critical":
        return 1.0
    elif state in {"ambiguous", "unreplayable", "not_applicable"}:
        if replay.critical_policy == "disabled":
            return 1.0
        if replay.critical_policy == "fixed":
            rate = min(1.0, max(0.0, float(replay.critical_rate or 0.5)))
            original_rate = candidate_rate = rate
        else:
            original_rate = min(1.0, max(0.0, original.get("CritBase", 0.05)))
            candidate_rate = min(1.0, max(0.0, candidate.get("CritBase", 0.05)))
        original_factor = calculate_critical_multiplier(
            original_rate,
            max(0.0, original.get("CritDamageBase", 0.5)),
        )
        candidate_factor = calculate_critical_multiplier(
            candidate_rate,
            max(0.0, candidate.get("CritDamageBase", 0.5)),
        )
    else:
        return None
    return _safe_ratio(candidate_factor, original_factor)


def increase_factor(values: Mapping[str, float], attribute: str) -> float:
    return max(
        0.0,
        1.0
        + values.get("DamageUpGeneralBase", 0.0)
        + values.get(ELEMENT_PROPERTY.get(attribute, ""), 0.0),
    )


def _safe_ratio(candidate: float, original: float) -> float | None:
    if original <= 0.0 or candidate < 0.0:
        return None
    ratio = candidate / original
    return ratio if isfinite(ratio) and ratio >= 0.0 else None


__all__ = [
    "ELEMENT_PROPERTY",
    "SCALING_PROPERTIES",
    "critical_ratio",
    "increase_factor",
    "projected_values",
    "scaling_id",
    "scaling_ratio",
]
