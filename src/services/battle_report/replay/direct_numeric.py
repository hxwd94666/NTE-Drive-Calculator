# 冻结直伤公式的数值输入及离线 Python 对照实现。
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from src.services.damage_calculation_service import (
    DamageScene,
    EnemyDefenseProfileInput,
    calculate_defense_multiplier,
    calculate_enemy_defense,
    calculate_enemy_defense_from_profile,
    calculate_resistance_multiplier,
)
from src.services.battle_report.replay.support import first_replay_value as _first_value, settle_replay_damage
from src.services.battle_report.replay.formula_catalog import (
    ELEMENT_DAMAGE_PROPERTIES as _ELEMENT_DAMAGE_PROPERTIES,
    ELEMENT_PENETRATION_PROPERTIES as _ELEMENT_PENETRATION_PROPERTIES,
    ELEMENT_RESISTANCE_PROPERTIES as _ELEMENT_RESISTANCE_PROPERTIES,
)


def prepare_direct_formula_input(
    *, evidence, values, character_level, analysis, projection, **_ignored
) -> dict[str, Any]:
    condition = analysis.target_condition
    assert condition is not None
    attribute = evidence.damage_attribute.casefold()
    resistance_ids = _ELEMENT_RESISTANCE_PROPERTIES.get(attribute, ())
    return {
        "values": [[key, value] for key, value in values.items()],
        "scaling_property_id": evidence.scaling_property_id,
        "scaling_multiplier": evidence.scaling_multiplier,
        "multiplier_coefficient": evidence.multiplier_coefficient,
        "character_level": character_level,
        "damage_attribute": attribute,
        "critical_policy": evidence.critical_policy,
        "fixed_crit_rate": evidence.fixed_crit_rate,
        "skill_final_multiplier": evidence.skill_final_multiplier,
        "dot_final_multiplier": evidence.dot_final_multiplier,
        "state_multiplier": evidence.state_multiplier,
        "state_multiplier_label": bool(evidence.state_multiplier_label),
        "target": {
            "scene": condition.scene,
            "enemy_level": condition.enemy_level,
            "enemy_defense_base": condition.enemy_defense_base,
            "enemy_defense_up": condition.enemy_defense_up,
            "enemy_defense_add": condition.enemy_defense_add,
            "defense_reduction": condition.defense_reduction,
            "vulnerability": condition.vulnerability,
            "resistance": dict(condition.resistances).get(attribute, 0.20),
        },
        "target_resistance_delta": sum(
            modifier.additive_value
            for modifier in projection.modifiers
            if modifier.target_scope == "target" and modifier.property_id in resistance_ids
        ),
    }


def calculate_python_direct_formula(job: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(job["values"])
    target = job["target"]
    character_level = job["character_level"]
    attribute = job["damage_attribute"]
    if attribute == "true" or (job["state_multiplier_label"] and job["state_multiplier"] <= 0.0):
        return {"status": "unavailable", "gap_codes": ["unsupported_direct_formula"]}
    scaling_value = _first_value(values, job["scaling_property_id"])
    multiplier = job["scaling_multiplier"] * job["multiplier_coefficient"]
    element_property = _ELEMENT_DAMAGE_PROPERTIES.get(attribute)
    damage_increase = (
        1.0 + values.get("DamageUpGeneralBase", 0.0) + (values.get(element_property, 0.0) if element_property else 0.0)
    )
    vulnerability = 1.0 + target["vulnerability"]
    penetration = values.get("DefIgnore", 0.0)
    if target["enemy_defense_base"] is None:
        scene = DamageScene.OPEN_WORLD if target["scene"] == "open_world" else DamageScene.OUTER_REALM
        enemy_defense = calculate_enemy_defense(
            target["enemy_level"],
            penetration,
            target["defense_reduction"],
            scene,
        )
    else:
        enemy_defense = calculate_enemy_defense_from_profile(
            EnemyDefenseProfileInput(
                defense_base=target["enemy_defense_base"],
                defense_up=target["enemy_defense_up"],
                defense_add=target["enemy_defense_add"],
            ),
            penetration,
            target["defense_reduction"],
        )
    defense = 1.0 if attribute == "psychically" else calculate_defense_multiplier(character_level, enemy_defense)
    resistance = target["resistance"] + job["target_resistance_delta"]
    penetration_property = _ELEMENT_PENETRATION_PROPERTIES.get(attribute)
    if penetration_property:
        resistance -= values.get(penetration_property, 0.0)
    resistance_factor = calculate_resistance_multiplier(resistance)
    independent = 1.0
    for property_id, value in values.items():
        normalized = property_id.casefold()
        if "finaldamage" in normalized or "damageupfinal" in normalized:
            independent *= 1.0 + value
    skill_final_multiplier = max(1.0, job["skill_final_multiplier"])
    independent *= skill_final_multiplier
    one_stack_non_critical = (
        multiplier
        * scaling_value
        * damage_increase
        * defense
        * resistance_factor
        * vulnerability
        * independent
        * max(1.0, job["dot_final_multiplier"])
    )
    crit_damage_bonus = max(0.0, values.get("CritDamageBase", 0.50))
    stack_coefficient = max(1.0, job["state_multiplier"])
    raw_non_critical = one_stack_non_critical * stack_coefficient
    non_critical = settle_replay_damage(raw_non_critical)
    critical_disabled = job["critical_policy"] == "disabled"
    critical_unknown = job["critical_policy"] == "unknown"
    critical = None if critical_disabled else settle_replay_damage(raw_non_critical * (1.0 + crit_damage_bonus))
    critical_rate = (
        None
        if critical_unknown
        else 0.0
        if critical_disabled
        else min(
            1.0,
            max(
                0.0,
                job["fixed_crit_rate"] if job["critical_policy"] == "fixed" else values.get("CritBase", 0.05),
            ),
        )
    )
    expected = (
        None
        if critical_rate is None
        else non_critical
        if critical is None
        else non_critical * (1.0 - critical_rate) + critical * critical_rate
    )
    return {
        "status": "complete",
        "gap_codes": [],
        "raw_non_critical": raw_non_critical,
        "non_critical_damage": non_critical,
        "critical_damage": critical,
        "critical_rate": critical_rate,
        "expected_damage": expected,
        "factors": {
            "multiplier": multiplier,
            "scaling_value": scaling_value,
            "damage_increase": damage_increase,
            "defense": defense,
            "resistance_factor": resistance_factor,
            "vulnerability": vulnerability,
            "independent": independent,
        },
    }
