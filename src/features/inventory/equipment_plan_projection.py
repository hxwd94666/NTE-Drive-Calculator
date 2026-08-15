# 将持久化配装方案投影为界面展示模型。
"""Official snapshot-to-display conversions for saved equipment plans."""

from __future__ import annotations

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力",
    "AtkUp": "攻击力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%",
    "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力",
    "DefUp": "防御力%",
    "HealUp": "治疗加成",
    "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2",
    "hen3": "H_3",
    "hen4": "H_4",
    "shu2": "V_2",
    "shu3": "V_3",
    "shu4": "V_4",
    "z3": "Trap_4_H",
    "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL",
    "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


def display_stat_label(property_id: object) -> str:
    """Return the same display label used by saved-plan equipment cards."""

    raw_property_id = str(property_id or "")
    return _OFFICIAL_STAT_LABELS.get(raw_property_id, raw_property_id or "未知属性")


def official_stat_values(stats):
    values = {}
    for stat in stats or []:
        property_id = str(stat.get("property_id") or "")
        label = display_stat_label(property_id)
        value = float(stat.get("value", 0.0) or 0.0)
        if stat.get("percent"):
            value *= 100.0
        values[label] = round(value, 6)
    return values


def display_shape_id(geometry):
    value = str(geometry or "").removeprefix("EquipmentGeometry_").casefold()
    return _OFFICIAL_SHAPE_LABELS.get(value, str(geometry or "未知形状"))
