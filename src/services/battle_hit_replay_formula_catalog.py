# 集中维护逐击公式支持的伤害通道与分属性字段目录。
from __future__ import annotations


DIRECT_FORMULA_CHANNELS = frozenset({
    "direct",
    "direct_follow_up",
    "attachment",
    "special_lacrimosa_dissonance",
    "special_nightmare",
    "special_zankou_erosion",
    "special_zankou_venom",
})
ELEMENT_DAMAGE_PROPERTIES = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
ELEMENT_PENETRATION_PROPERTIES = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}
ELEMENT_RESISTANCE_PROPERTIES = {
    element: (
        f"DamageResist{element.title()}Base",
        f"DamageResist{element.title()}Add",
    )
    for element in ELEMENT_DAMAGE_PROPERTIES
}


__all__ = [
    "DIRECT_FORMULA_CHANNELS",
    "ELEMENT_DAMAGE_PROPERTIES",
    "ELEMENT_PENETRATION_PROPERTIES",
    "ELEMENT_RESISTANCE_PROPERTIES",
]
