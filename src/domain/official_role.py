# 定义官方角色属性、评分和页面顺序共享的不可变领域值。
"""Qt-free value objects and constants shared by official-role services."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


DEFAULT_THEORY_PROPERTY_IDS: Final[tuple[str, ...]] = (
    "DamageUpGeneralBase",
    "CritBase",
    "CritDamageBase",
    "AtkUp",
)

OFFICIAL_ROLE_TAB_ORDER_SCOPE: Final = "official_role_tabs"

ELEMENT_DAMAGE_PROPERTY_BY_TYPE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "CHAOS": "DamageUpChaosBase",
        "COSMOS": "DamageUpCosmosBase",
        "INCANTATION": "DamageUpIncantationBase",
        "LAKSHANA": "DamageUpLakshanaBase",
        "NATURE": "DamageUpNatureBase",
        "PSYCHE": "DamageUpPsycheBase",
        "PSYCHICALLY": "DamageUpPsychicallyBase",
    }
)

# One comparable marginal roll used by the role panel.
ROLE_PANEL_MARGINAL_UNITS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "CritBase": 0.01,
        "CritDamageBase": 0.02,
        "DamageUpGeneralBase": 0.01,
        "AtkUp": 0.0125,
        "AtkAdd": 8.0,
        "HPMaxUp": 0.0125,
        "HPMaxAdd": 100.0,
        "DefUp": 0.0175,
        "DefAdd": 8.0,
        "ElementDamage": 0.0125,
    }
)


@dataclass(frozen=True, slots=True)
class OfficialAttributeSummaryValue:
    key: str
    label: str
    value: float
    percent: bool
    weight_property_ids: tuple[str, ...]
