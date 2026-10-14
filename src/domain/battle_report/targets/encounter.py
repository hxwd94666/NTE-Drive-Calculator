# 保存遭遇候选及派生选择身份，不补造未观测目标或环境证据。
from __future__ import annotations

from dataclasses import dataclass
from src.domain.battle_report.targets.profiles import BattleTargetCondition


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
    """记录严格一对一候选映射及每条观测的全部可行目标，不补造未观测实例。"""

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


# 该值只作旧模型默认与离线恢复门禁，原生生产者的非空修订不要求相同。
INFERRED_ENCOUNTER_ALGORITHM_VERSION = "battle-encounter-native-first-v6"


@dataclass(frozen=True, slots=True)
class BattleInferredTargetIdentity:
    scope_half: str
    captured_target_id: str
    target_name: str
    inferred_monster_id: str
    initial_max_hp: float | None


@dataclass(frozen=True, slots=True)
class BattleInferredEncounter:
    environment_kind: str
    environment_ref: str
    environment_name: str
    source_kind: str
    confidence: str
    inference_basis: str
    scope_half: str
    outer_realm_floor: int | None
    difficulty_id: int | None
    feast_options: tuple[tuple[str, str], ...]
    targets: tuple[BattleEncounterTargetPreset, ...]
    identities: tuple[BattleInferredTargetIdentity, ...]
    target_condition: BattleTargetCondition | None
    ambiguous: bool = False
    ambiguity_alternatives: tuple[str, ...] = ()
    target_conditions_by_half: tuple[tuple[str, BattleTargetCondition], ...] = ()
    target_mapping_conditions_by_half: tuple[tuple[str, BattleTargetCondition], ...] = ()
    selection_mode: str = "unique_hard"
    default_reason: str = ""
    alternative_environment_refs: tuple[str, ...] = ()
    formula_matches: tuple[BattleEncounterCandidateMatch, ...] = ()
    formula_profile_conflict: bool = False
    residual_fit_score: float | None = None
    residual_fit_gap: float | None = None
    algorithm_version: str = INFERRED_ENCOUNTER_ALGORITHM_VERSION
