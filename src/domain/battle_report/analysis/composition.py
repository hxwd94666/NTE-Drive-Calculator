# 保存角色、技能和渠道的范围汇总，不从聚合摘要猜测逐击事实。
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DamageCompositionEntry:
    key: str
    label: str
    damage: float
    share_percent: float
    total_share_percent: float = 0.0


@dataclass(frozen=True, slots=True)
class RoleDamageComposition:
    character_id: int
    character_name: str
    total_damage: float
    entries: tuple[DamageCompositionEntry, ...]
    share_percent: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleDamageComposition:
    roles: tuple[RoleDamageComposition, ...]
    other_total_damage: float
    other_share_percent: float
    other_entries: tuple[DamageCompositionEntry, ...]
    system_total_damage: float = 0.0
    system_share_percent: float = 0.0
    system_entries: tuple[DamageCompositionEntry, ...] = ()
    pending_topple_attribution: bool = False
    unresolved_topple_attribution: bool = False


@dataclass(frozen=True, slots=True)
class BattleRangeRoleSummary:
    character_id: int
    character_name: str
    hits: int
    damage: float
    dps: float
    share_percent: float
    raw_damage: float = 0.0
    max_hp_reduction_damage: float = 0.0
    max_hp_reduction_events: int = 0


@dataclass(frozen=True, slots=True)
class BattleRangeSkillSummary:
    character_id: int | None
    character_name: str
    skill_name: str
    damage_name: str
    classification: str
    hits: int
    damage: float
    share_percent: float
    ability_id: str = ""
