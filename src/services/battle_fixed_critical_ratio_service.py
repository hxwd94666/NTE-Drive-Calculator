# 为正式固定 50% 暴击伤害渠道提供独立的逐击边际比值。
"""Fixed-half-critical helpers shared by battle counterfactual consumers."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from src.domain.battle_counterfactual_quantification import BattleCounterfactualRatio
from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.damage_calculation_service import calculate_critical_multiplier


CONTINUOUS_DIRECT_CHANNEL_IDS = frozenset({
    "special_nightmare",
    "special_zankou_erosion",
    "special_zankou_venom",
})
FIXED_HALF_CRIT_CHANNEL_IDS = frozenset({
    *CONTINUOUS_DIRECT_CHANNEL_IDS,
    "reaction_scorch",
})
_CONTINUOUS_DIRECT_ATTRIBUTES = {
    "special_nightmare": "chaos",
    "special_zankou_erosion": "incantation",
    "special_zankou_venom": "incantation",
}


def is_continuous_direct_hit(hit: BattleAnalysisHit) -> bool:
    channel_id, _channel_label = classify_battle_hit_channel(hit)
    return channel_id in CONTINUOUS_DIRECT_CHANNEL_IDS


def is_fixed_half_critical_hit(hit: BattleAnalysisHit) -> bool:
    channel_id, _channel_label = classify_battle_hit_channel(hit)
    return channel_id in FIXED_HALF_CRIT_CHANNEL_IDS


def continuous_direct_attribute(hit: BattleAnalysisHit) -> str:
    channel_id, _channel_label = classify_battle_hit_channel(hit)
    return _CONTINUOUS_DIRECT_ATTRIBUTES.get(channel_id, "")


def fixed_half_critical_ratio(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    replay: BattleHitReplayResult | None,
) -> float | None:
    """Use the observed branch when known, otherwise the formal 50% expectation."""

    state = "unreplayable" if replay is None else replay.critical_state
    original_damage = max(0.0, original.get("CritDamageBase", 0.5))
    candidate_damage = max(0.0, candidate.get("CritDamageBase", 0.5))
    if state == "critical":
        original_factor = 1.0 + original_damage
        candidate_factor = 1.0 + candidate_damage
    elif state == "non_critical":
        return 1.0
    else:
        original_factor = calculate_critical_multiplier(0.5, original_damage)
        candidate_factor = calculate_critical_multiplier(0.5, candidate_damage)
    if original_factor <= 0.0 or candidate_factor < 0.0:
        return None
    ratio = candidate_factor / original_factor
    return ratio if isfinite(ratio) and ratio >= 0.0 else None


def fixed_half_critical_counterfactual(
    *,
    channel_id: str,
    changed_properties: set[str],
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    replay: BattleHitReplayResult | None,
) -> BattleCounterfactualRatio | None:
    if (
        channel_id not in FIXED_HALF_CRIT_CHANNEL_IDS
        or not changed_properties
        or not changed_properties <= {"CritBase", "CritDamageBase"}
    ):
        return None
    ratio = fixed_half_critical_ratio(original, candidate, replay)
    if ratio is None:
        return None
    if changed_properties == {"CritBase"}:
        return BattleCounterfactualRatio.not_applicable(
            method="fixed_half_critical_not_applicable",
            cancelled_dimension_ids=("critical_rate",),
            explanation="正式固定 50% 暴击率不消费角色面板暴击率。",
        )
    return BattleCounterfactualRatio.complete(
        ratio,
        method="fixed_half_critical_ratio",
        confidence="高",
        dependency_scope="character_only",
        included_dimension_ids=("critical_damage",),
        cancelled_dimension_ids=("critical_rate",),
        explanation=(
            "按正式固定 50% 暴击策略，仅替换暴击伤害乘区；"
            "无法判定本击分支时使用固定期望比值。"
        ),
    )
