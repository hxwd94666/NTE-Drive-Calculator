# 保存冻结目标档案、采集实例映射和生命结算证据，歧义与未知单独保留。
from __future__ import annotations

from dataclasses import dataclass


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
class BattleTargetSummary:
    target_id: str
    target_name: str
    hits: int
    damage: float
    first_hp: float | None
    last_hp: float | None
    max_hp: float | None
    max_hp_reduction: float = 0.0
    max_hp_reduction_damage: float = 0.0
    effective_damage: float = 0.0
    estimated_max_hp_reduction_damage: float = 0.0
    scope_half: str = ""
    initial_hp: float | None = None
    terminal_hp: float | None = None
    observed_hp_loss: float = 0.0
    unexplained_hp_delta: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleTargetCondition:
    """One saved encounter plus the legacy primary-target display profile."""

    target_name: str
    enemy_level: float
    scene: str
    defense_reduction: float
    vulnerability: float
    resistances: tuple[tuple[str, float], ...]
    source_kind: str = "user_confirmed"
    enemy_defense_base: float | None = None
    enemy_defense_up: float = 0.0
    enemy_defense_add: float = 0.0
    enemy_topple_limit: float = 50.0
    environment_kind: str = "manual"
    environment_ref: str = ""
    environment_name: str = ""
    selected_target_ids: tuple[str, ...] = ()
    primary_target_id: str = ""
    difficulty_id: int | None = None
    feast_options: tuple[tuple[str, str], ...] = ()
    witch_buff_id: str = ""
    witch_buff_name_zh: str = ""
    witch_buff_property_id: str = ""
    witch_buff_value: float | None = None
    witch_buff_is_percent: bool = False
    selected_target_profiles: tuple["BattleSelectedTargetProfile", ...] = ()
    resolved_monster_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleMaxHpReductionEvent:
    """目标最大生命的观测变化与另行推断的归属分开保存。"""

    event_id: str
    target_id: str
    target_name: str
    observed_at_us: int
    old_max_hp: float
    new_max_hp: float
    max_hp_reduction: float
    hp_before_settlement: float
    hp_ratio_before: float
    effective_hp_loss: float
    source_character_id: int | None
    source_character_name: str
    mechanic_kind: str
    mechanic_name: str
    source_skill_name: str
    evidence_event_ids: tuple[str, ...]
    attribution_confidence: str
    calculation_confidence: str
    inference_basis: str
    evidence_kind: str = "observed"
    included_in_effective_damage: bool = True
    scope_half: str = ""


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
