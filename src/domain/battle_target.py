# 战报所选目标档案与捕获实例映射的领域值对象。
"""Target-profile value objects kept separate from the large battle domain module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.battle_report import BattleTargetCondition


@dataclass(frozen=True, slots=True)
class BattleSelectedTargetProfile:
    """Immutable target candidate/slot frozen when an environment is saved."""

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
    """Derived mapping from one captured target instance to a frozen profile."""

    scope_half: str
    captured_target_id: str
    resolved_monster_id: str
    default_monster_id: str
    possible_monster_ids: tuple[str, ...]
    resolution_mode: str
    initial_max_hp: float
    target_condition: BattleTargetCondition | None
