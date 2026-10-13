# 战报所选目标档案与捕获实例映射的领域值对象。
"""Target-profile value objects kept separate from the large battle domain module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.battle_report import BattleTargetCondition


@dataclass(frozen=True, slots=True)
class BattleSelectedTargetProfile:
    """保存环境时冻结的正式目标候选与槽位，不读取后续静态或界面状态。"""

    static_target_id: str
    selection_target_id: str
    target_name: str
    monster_class_path: str
    monster_count: int
    max_hp: float
    monster_level: float
    defense_base: float | None
    defense_up: float
    defense_add: float
    topple_limit: float
    resistances: tuple[tuple[str, float], ...]
    profile_set: str = ""
    pack_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleTargetInstanceResolution:
    """采集实例到冻结档案的派生映射，候选歧义及未知初始生命独立保留。"""

    scope_half: str
    captured_target_id: str
    resolved_monster_id: str
    default_monster_id: str
    possible_monster_ids: tuple[str, ...]
    resolution_mode: str
    initial_max_hp: float | None
    target_condition: "BattleTargetCondition | None"
