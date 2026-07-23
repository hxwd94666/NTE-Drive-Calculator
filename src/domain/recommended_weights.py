# 规范化开发期异环工坊推荐权重为官方属性 ID。
"""Character recommendation weights shared by the static-data build tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


WORKSHOP_STAT_PROPERTY_IDS = {
    "暴击率": "CritBase",
    "暴击率%": "CritBase",
    "暴击伤害": "CritDamageBase",
    "暴击伤害%": "CritDamageBase",
    "伤害增加": "DamageUpGeneralBase",
    "伤害增加%": "DamageUpGeneralBase",
    "攻击力": "AtkAdd",
    "攻击力%": "AtkUp",
    "攻击力百分比": "AtkUp",
    "基础攻击力": "AtkBase",
    "防御力": "DefAdd",
    "防御力%": "DefUp",
    "防御力百分比": "DefUp",
    "生命值": "HPMaxAdd",
    "生命值%": "HPMaxUp",
    "生命值百分比": "HPMaxUp",
    "充能效率": "ChargeGetEfficiencyBase",
    "环合强度": "MagBase",
    "倾陷强度": "UnbalIntensityBase",
    "治疗加成": "HealUp",
    "受治疗加成": "HealBeUp",
    "通用伤害增强": "DamageUpGeneralBase",
    "光属性异能伤害增强": "DamageUpCosmosBase",
    "光属性异能伤害增强%": "DamageUpCosmosBase",
    "灵属性异能伤害增强": "DamageUpNatureBase",
    "灵属性异能伤害增强%": "DamageUpNatureBase",
    "咒属性异能伤害增强": "DamageUpIncantationBase",
    "咒属性异能伤害增强%": "DamageUpIncantationBase",
    "暗属性异能伤害增强": "DamageUpChaosBase",
    "暗属性异能伤害增强%": "DamageUpChaosBase",
    "魂属性异能伤害增强": "DamageUpPsycheBase",
    "魂属性异能伤害增强%": "DamageUpPsycheBase",
    "相属性异能伤害增强": "DamageUpLakshanaBase",
    "相属性异能伤害增强%": "DamageUpLakshanaBase",
    "心灵伤害增强": "DamageUpPsychicallyBase",
    "心灵伤害增强%": "DamageUpPsychicallyBase",
}

DEFAULT_RECOMMENDED_WEIGHTS = (
    ("DamageUpGeneralBase", 0.75),
    ("CritBase", 1.0),
    ("CritDamageBase", 1.0),
    ("AtkUp", 0.70),
)


def _positive(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _weight_items(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    config = record.get("weightConfig")
    rows = config.get("weights") if isinstance(config, Mapping) else config
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def default_character_recommendation(character_id: int) -> dict[str, Any]:
    return {
        "character_id": int(character_id),
        "source_kind": "default",
        "source_item_id": None,
        "source_name": None,
        "properties": [
            {
                "property_id": property_id,
                "weight": weight,
                "main_weight": weight,
                "ordinal": ordinal,
            }
            for ordinal, (property_id, weight) in enumerate(DEFAULT_RECOMMENDED_WEIGHTS)
        ],
    }


def parse_workshop_recommendations(
    records: Iterable[Mapping[str, Any]],
    character_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Map API rows to official IDs and fill every missing character with defaults."""

    known_ids = {int(character_id) for character_id in character_ids}
    result = {
        character_id: default_character_recommendation(character_id)
        for character_id in known_ids
    }
    for record in records:
        try:
            character_id = int(str(record.get("itemId") or "").strip())
        except ValueError:
            continue
        if character_id not in known_ids:
            continue
        properties: dict[str, dict[str, Any]] = {}
        for ordinal, row in enumerate(_weight_items(record)):
            raw_name = str(row.get("name") or row.get("key") or "").strip()
            property_id = WORKSHOP_STAT_PROPERTY_IDS.get(raw_name)
            if not property_id:
                continue
            weight = _positive(row.get("value"))
            main_weight = _positive(row.get("main_value"))
            if weight <= 0 and main_weight <= 0:
                continue
            existing = properties.get(property_id)
            if existing is None:
                properties[property_id] = {
                    "property_id": property_id,
                    "weight": weight,
                    "main_weight": main_weight,
                    "ordinal": ordinal,
                }
            else:
                existing["weight"] = max(float(existing["weight"]), weight)
                existing["main_weight"] = max(float(existing["main_weight"]), main_weight)
        if properties:
            ordered = sorted(properties.values(), key=lambda row: (int(row["ordinal"]), row["property_id"]))
            for ordinal, row in enumerate(ordered):
                row["ordinal"] = ordinal
            result[character_id] = {
                "character_id": character_id,
                "source_kind": "workshop_api",
                "source_item_id": str(record.get("itemId") or ""),
                "source_name": str(record.get("name") or "") or None,
                "properties": ordered,
            }
    return result
