# 保存角色基线与单场修改输入，毕业假定不提升为库存或原生 UID。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class BattleCharacterStat:
    property_id: str
    label: str
    value: float
    is_percent: bool


@dataclass(frozen=True, slots=True)
class BattleCharacterSourceStat:
    """One frozen formula input before role-panel sources are combined."""

    source_group: str
    source_name: str
    property_id: str
    label: str
    value: float
    is_percent: bool


@dataclass(frozen=True, slots=True)
class BattleCharacterBaseline:
    character_id: int
    character_name: str
    source: str
    stats: tuple[BattleCharacterStat, ...]
    character_level: float = 80.0
    source_stats: tuple[BattleCharacterSourceStat, ...] = ()
    inherent_hp: float | None = None
    source_max_hp: float | None = None
    enabled_team_passive_ids: tuple[str, ...] = ()
    selected_awaken_effect_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleInferredCharacterFact:
    fact_id: str
    character_id: int
    fact_kind: str
    fact_value: str
    source_gameplay_effect_id: str
    confidence: str
    evidence_event_ids: tuple[str, ...]
    model_version: str
    inference_basis: str


GRADUATION_ASSUMPTION_TITLE = "毕业模板假定（缺少原生背包）"


GRADUATION_ASSUMPTION_WARNING = (
    "未取得完整原生背包，本场使用毕业模板假定空幕/驱动进行计算；实测战报已保留。"
)


def assumed_graduation_equipment(profile: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """只复制有正式假定标记和版本的模板物品，不提升为真实库存或 UID。"""
    assumption = profile.get("equipment_assumption")
    if not isinstance(assumption, Mapping):
        return None
    if assumption.get("kind") != "official_graduation" or assumption.get("version") != 1:
        return None
    items = assumption.get("items")
    if not isinstance(items, list):
        return None
    return deepcopy(items)


def has_graduation_assumption(build: Mapping[str, Any] | None) -> bool:
    return any(
        assumed_graduation_equipment(role.get("profile") or {}) is not None
        for role in (build or {}).get("characters") or ()
    )


def world_bonus_edit_stats(value):
    if not isinstance(value, Mapping) or set(value) != {"AtkAdd", "CritDamageBase"}:
        raise ValueError("战报家具修改副本必须完整包含攻击力和暴击伤害")
    rows = []
    for ordinal, (key, label, maximum) in enumerate((
        ("AtkAdd", "攻击力", 20.0), ("CritDamageBase", "暴击伤害", 0.04),
    )):
        number = value[key]
        if type(number) not in (int, float) or not isfinite(number) or not 0 <= number <= maximum:
            raise ValueError("战报家具修改副本数值超出正式范围")
        rows.append({"source_group": "world_bonus", "property_id": key,
                     "display_name": label, "value": float(number),
                     "is_percent": key == "CritDamageBase", "ordinal": ordinal})
    return rows
