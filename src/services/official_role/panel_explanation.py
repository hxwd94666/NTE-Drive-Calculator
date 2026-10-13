# 解释角色面板既有直伤公式的属性来源与乘区，不复制计算公式。
from __future__ import annotations

from typing import Any, Mapping

from src.services.damage_calculation_service import DamageCalculationService, DamageScalingStat
from src.services.official_role.attributes import (
    _context_calculation_items, _equipment_property_stats, _property_stats_by_source,
    _likeability_property_stats, _world_bonus_property_stats, _element_damage_property,
    _role_panel_damage_inputs,
)
from src.services.official_role.labels import _property_label


def calculate_official_role_damage_breakdown(
    detail: Mapping[str, Any], context_key: str,
) -> dict[str, Any] | None:
    """Explain every factor of the role panel's normalized 100% direct hit."""

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    values = inputs[0]
    result = DamageCalculationService.calculate_direct(values)
    fork_stats, equipment_stats, combined_stats = _property_stats_by_source(
        detail, context_key
    )
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    scaling_names = {
        DamageScalingStat.ATTACK: "攻击力",
        DamageScalingStat.HEALTH: "生命值",
        DamageScalingStat.DEFENSE: "防御力",
    }
    scaling_components = {
        DamageScalingStat.ATTACK: (
            values.attack_base, values.attack_up, values.attack_add,
        ),
        DamageScalingStat.HEALTH: (
            values.health_base, values.health_up, values.health_add,
        ),
        DamageScalingStat.DEFENSE: (
            values.defense_base, values.defense_up, values.defense_add,
        ),
    }
    scaling_base, scaling_up, scaling_add = scaling_components[values.scaling_stat]

    damage_sources: list[dict[str, Any]] = []
    for property_id in ("DamageUpGeneralBase", "DamageUpGeneralAdd", element_property):
        if property_id and combined_stats.get(property_id, 0.0):
            damage_sources.append({
                "label": _property_label(detail, property_id),
                "value": combined_stats[property_id],
            })

    bonuses = [
        {
            "source": "角色基础", "label": "生命值",
            "value": values.health_base - combined_stats.get("HPMaxBase", 0.0),
            "percent": False,
        },
        {
            "source": "角色基础", "label": "攻击力",
            "value": values.attack_base - combined_stats.get("AtkBase", 0.0),
            "percent": False,
        },
        {
            "source": "角色基础", "label": "防御力",
            "value": values.defense_base - combined_stats.get("DefBase", 0.0),
            "percent": False,
        },
        {"source": "角色基础", "label": "暴击率", "value": 0.05, "percent": True},
        {"source": "角色基础", "label": "暴击伤害", "value": 0.50, "percent": True},
    ]
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    raw_equipment_stats = _equipment_property_stats(
        detail,
        _context_calculation_items(context),
        include_shape_bonus=False,
    )
    shape_stats = {
        property_id: value - raw_equipment_stats.get(property_id, 0.0)
        for property_id, value in equipment_stats.items()
        if value - raw_equipment_stats.get(property_id, 0.0)
    }
    for source, source_stats in (
        ("家具加成", _world_bonus_property_stats(detail)),
        ("好感度 10 级", _likeability_property_stats(detail)),
        ("弧盘", fork_stats),
        ("空幕/驱动", raw_equipment_stats),
        ("额外形状", shape_stats),
    ):
        for property_id, value in source_stats.items():
            if value:
                attribute = (detail.get("attributes") or {}).get(property_id) or {}
                bonuses.append({
                    "source": source,
                    "label": _property_label(detail, property_id),
                    "property_id": property_id,
                    "value": value,
                    "percent": bool(attribute.get("show_percent")),
                })

    factor_rows = [
        {
            "name": "技能伤害倍率",
            "value": values.skill_multiplier,
            "detail": "角色面板统一按 100% 计算",
        },
        {
            "name": f"倍率对应属性（{scaling_names[values.scaling_stat]}）",
            "value": result.scaling_attribute_value,
            "detail": (
                f"{scaling_base:g} × (1 + {scaling_up * 100:g}%) + "
                f"{scaling_add:g}"
            ),
        },
        {
            "name": "增伤区",
            "value": result.damage_increase_multiplier,
            "detail": "1 + " + (
                " + ".join(
                    f"{item['label']} {float(item['value']) * 100:g}%"
                    for item in damage_sources
                ) or "0%"
            ),
        },
        {
            "name": "暴击区",
            "value": result.critical_multiplier,
            "detail": f"1 + {values.crit_rate * 100:g}% × {values.crit_damage * 100:g}%",
        },
        {
            "name": "防御区",
            "value": result.defense_multiplier,
            "detail": (
                f"敌防 {result.enemy_defense:g}；角色 {values.character_level:g} 级 / "
                f"敌人 {values.enemy_level:g} 级；穿透 {values.defense_penetration * 100:g}% / "
                f"减防 {values.defense_reduction * 100:g}%"
            ),
        },
        {
            "name": "抗性区",
            "value": result.resistance_multiplier,
            "detail": (
                f"基础抗性 {values.boss_resistance * 100:g}% - 减抗 "
                f"{sum(values.enemy_resistance_reductions) * 100:g}% - 穿透 "
                f"{sum(values.resistance_penetrations) * 100:g}% = "
                f"{result.effective_resistance * 100:g}%"
            ),
        },
        {
            "name": "易伤区",
            "value": result.vulnerability_multiplier,
            "detail": "1 + " + (
                " + ".join(f"{value * 100:g}%" for value in values.vulnerability_increases)
                or "0%"
            ),
        },
        {
            "name": "独立乘区",
            "value": result.independent_multiplier,
            "detail": " × ".join(
                f"(1 + {value * 100:g}%)" for value in values.independent_damage_bonuses
            ) or "暂无独立增伤，乘区为 1",
        },
    ]
    return {
        "context_key": context_key,
        "damage": result.damage,
        "scaling_stat": values.scaling_stat.value,
        "element_property_id": element_property,
        "bonuses": bonuses,
        "factors": factor_rows,
        "formula_values": tuple(row["value"] for row in factor_rows),
    }
