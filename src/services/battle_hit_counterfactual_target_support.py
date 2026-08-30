# 逐击反事实共享的目标防御与属性抗性比值。
"""Target-side ratio helpers for fixed-axis hit counterfactuals."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.battle_report import (
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleTargetCondition,
)
from src.services.damage_calculation_service import (
    DamageScene,
    EnemyDefenseProfileInput,
    calculate_defense_multiplier,
    calculate_enemy_defense,
    calculate_enemy_defense_from_profile,
    calculate_resistance_multiplier,
)


def safe_ratio(candidate: float, original: float) -> float | None:
    if original <= 0.0 or candidate < 0.0:
        return None
    ratio = candidate / original
    return ratio if ratio == ratio and ratio != float("inf") else None


def level_changed(
    original: BattleCharacterBaseline | None,
    candidate: BattleCharacterBaseline | None,
) -> bool:
    if original is None or candidate is None:
        return False
    return abs(original.character_level - candidate.character_level) > 1e-12


def defense_ratio(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    original_baseline: BattleCharacterBaseline | None,
    candidate_baseline: BattleCharacterBaseline | None,
    condition: BattleTargetCondition,
) -> float | None:
    if original_baseline is None or candidate_baseline is None:
        return None
    scene = (
        DamageScene.OPEN_WORLD
        if condition.scene in {"open_world", "big_world"}
        else DamageScene.OUTER_REALM
    )

    def factor(values: Mapping[str, float], character_level: float) -> float:
        penetration = min(1.0, max(-1.0, values.get("DefIgnore", 0.0)))
        if condition.enemy_defense_base is not None:
            enemy_defense = calculate_enemy_defense_from_profile(
                EnemyDefenseProfileInput(
                    defense_base=condition.enemy_defense_base,
                    defense_up=condition.enemy_defense_up,
                    defense_add=condition.enemy_defense_add,
                ),
                penetration,
                condition.defense_reduction,
            )
        else:
            enemy_defense = calculate_enemy_defense(
                condition.enemy_level,
                penetration,
                condition.defense_reduction,
                scene,
            )
        return calculate_defense_multiplier(character_level, enemy_defense)

    return safe_ratio(
        factor(candidate, candidate_baseline.character_level),
        factor(original, original_baseline.character_level),
    )


def target_resistance_delta(
    projection: BattleHitBuffProjection | None,
    attribute: str,
) -> float:
    if projection is None:
        return 0.0
    suffix = attribute.casefold()
    return sum(
        modifier.additive_value
        for modifier in projection.modifiers
        if modifier.target_scope == "target"
        and modifier.property_id.casefold().startswith("damageresist")
        and suffix in modifier.property_id.casefold()
    )


def resistance_ratio(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    attribute: str,
    penetration_property: str,
    original_target_delta: float,
    candidate_target_delta: float,
    condition: BattleTargetCondition,
) -> float | None:
    base_resistance = dict(condition.resistances).get(attribute, 0.20)
    original_resistance = (
        base_resistance
        + original_target_delta
        - original.get(penetration_property, 0.0)
    )
    candidate_resistance = (
        base_resistance
        + candidate_target_delta
        - candidate.get(penetration_property, 0.0)
    )
    return safe_ratio(
        max(0.0, calculate_resistance_multiplier(candidate_resistance)),
        max(0.0, calculate_resistance_multiplier(original_resistance)),
    )
