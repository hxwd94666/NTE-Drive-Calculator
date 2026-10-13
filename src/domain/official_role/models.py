# 定义官方角色属性、评分和页面顺序共享的不可变领域值。
"""Qt-free value objects and constants shared by official-role services."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Mapping


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


WORLD_BONUS_SETTING_KEY = "world_bonus"


@dataclass(frozen=True)
class WorldBonusSettings:
    """Current account's unlocked SpecialFurniture attribute values."""

    yaodao_attack_add: float = 20.0
    quantao_crit_damage: float = 0.04

    def validate(self) -> None:
        values = (self.yaodao_attack_add, self.quantao_crit_damage)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("家具加成必须是有限数值。")
        if not 0.0 <= float(self.yaodao_attack_add) <= 20.0:
            raise ValueError("妖刀攻击力加成必须在 0 到 20 之间。")
        if not 0.0 <= float(self.quantao_crit_damage) <= 0.04:
            raise ValueError("拳套暴击伤害加成必须在 0% 到 4% 之间。")

    def to_payload(self) -> dict[str, float]:
        self.validate()
        return {
            "yaodao_attack_add": float(self.yaodao_attack_add),
            "quantao_crit_damage": float(self.quantao_crit_damage),
        }

    @classmethod
    def from_payload(cls, value: Mapping[str, Any] | None) -> "WorldBonusSettings":
        raw = value or {}
        settings = cls(
            yaodao_attack_add=float(
                raw.get("yaodao_attack_add", cls.yaodao_attack_add)
            ),
            quantao_crit_damage=float(
                raw.get("quantao_crit_damage", cls.quantao_crit_damage)
            ),
        )
        settings.validate()
        return settings


def world_bonus_property_stats(
    value: Mapping[str, Any] | WorldBonusSettings | None,
) -> dict[str, float]:
    """Translate the account values to the game's formal property identifiers."""

    settings = (
        value
        if isinstance(value, WorldBonusSettings)
        else WorldBonusSettings.from_payload(value)
    )
    return {
        "AtkAdd": float(settings.yaodao_attack_add),
        "CritDamageBase": float(settings.quantao_crit_damage),
    }
