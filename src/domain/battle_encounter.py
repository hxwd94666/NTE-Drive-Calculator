# 战报环境反向识别使用的不可变候选、观测与选择结果。
"""Qt-free value objects for encounter candidate matching and selection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BattleEncounterTargetPreset:
    """One frozen static target profile; ``monster_count`` expands to slots."""

    target_id: str
    target_name: str
    monster_class_path: str
    monster_count: int
    max_hp: float
    monster_level: float
    profile_set: str
    pack_id: str
    defense_base: float | None
    defense_up: float
    defense_add: float
    topple_limit: float
    resistances: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class BattleEncounterCandidate:
    """One catalog environment with all target profiles already frozen."""

    environment_kind: str
    environment_ref: str
    environment_name: str
    scope_half: str
    outer_realm_floor: int | None
    difficulty_id: int | None
    feast_options: tuple[tuple[str, str], ...]
    targets: tuple[BattleEncounterTargetPreset, ...]
    catalog_order: int = 0


@dataclass(frozen=True, slots=True)
class BattleObservedTarget:
    """Initial maximum HP evidence for one captured target instance."""

    scope_half: str
    target_id: str
    monster_id: str
    initial_max_hp: float
    first_time_us: int


@dataclass(frozen=True, slots=True)
class BattleEncounterCandidateMatch:
    """A strict injective match and every feasible target for each observation."""

    candidate: BattleEncounterCandidate
    possible_target_indexes: tuple[tuple[int, ...], ...]
    hard_identity_matches: int
    unobserved_slot_count: int


@dataclass(frozen=True, slots=True)
class BattleEncounterCandidateSelection:
    """Stable default plus the remaining strict alternatives."""

    default: BattleEncounterCandidateMatch
    alternatives: tuple[BattleEncounterCandidateMatch, ...]
    confidence: str
    selection_mode: str
    default_reason: str
