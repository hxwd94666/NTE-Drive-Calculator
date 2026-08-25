# 管理当前账号的大世界异象属性加成并投影为正式属性。
"""Account-owned world bonuses shared by every official-role calculation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.storage.sqlite.user_data_dao import UserDataDao


WORLD_BONUS_SETTING_KEY = "world_bonus"


@dataclass(frozen=True)
class WorldBonusSettings:
    """Current account's unlocked SpecialFurniture attribute values."""

    yaodao_attack_add: float = 20.0
    quantao_crit_damage: float = 0.04

    def validate(self) -> None:
        values = (self.yaodao_attack_add, self.quantao_crit_damage)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("世界加成必须是有限数值。")
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


class WorldBonusSettingsService:
    """Persist world bonuses in the active account's setting-copy table."""

    def __init__(self, user_database_path: str | Path) -> None:
        self.user_database_path = Path(user_database_path).expanduser().resolve()

    def load(self) -> WorldBonusSettings:
        with UserDataDao(self.user_database_path) as dao:
            payload = dao.list_application_setting_copies().get(
                WORLD_BONUS_SETTING_KEY
            )
        return WorldBonusSettings.from_payload(payload)

    def save(self, settings: WorldBonusSettings) -> WorldBonusSettings:
        payload = settings.to_payload()
        with UserDataDao(self.user_database_path) as dao:
            if settings == WorldBonusSettings():
                dao.delete_application_setting_copy(WORLD_BONUS_SETTING_KEY)
            else:
                dao.replace_application_setting_copy(
                    WORLD_BONUS_SETTING_KEY,
                    payload,
                )
        return settings
