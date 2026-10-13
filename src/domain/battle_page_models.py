# 统一战报页面的冻结输入、只读结果及属性标识，不读取外部状态或执行公式。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.battle_counterfactual import BattleMarginalResult
from src.domain.battle_encounter import BattleEncounterCandidateMatch, BattleEncounterTargetPreset
from src.domain.battle_hit_details import BattlePageHitDetails
from src.domain.battle_marginal_benefit import BattleMarginalBenefits
from src.domain.battle_report import BattleAnalysisSnapshot, BattleTargetCondition


# frozen 仅冻结字段绑定；目录字典等成员仍遵循原来的浅只读契约。
_ELEMENT_PROPERTIES = (
    "DamageUpChaosBase", "DamageUpCosmosBase", "DamageUpIncantationBase",
    "DamageUpLakshanaBase", "DamageUpNatureBase", "DamageUpPsycheBase", "DamageUpPsychicallyBase",
)
_PENETRATION_PROPERTIES = (
    "DamagePenetrateChaos", "DamagePenetrateCosmos", "DamagePenetrateIncantation",
    "DamagePenetrateLakshana", "DamagePenetrateNature", "DamagePenetratePsyche", "DamagePenetratePsychically",
)
CHARACTER_PANEL_DYNAMIC_PROPERTIES = frozenset({
    "CritBase", "CritDamageBase", "DamageUpGeneralBase", "MagBase",
    "AtkUp", "AtkAdd", "HPMaxUp", "HPMaxAdd", "DefUp", "DefAdd",
    "DefIgnore", "UnbalIntensityBase", *_ELEMENT_PROPERTIES, *_PENETRATION_PROPERTIES,
})


@dataclass(frozen=True, slots=True)
class BattleCoreMainStatCounterfactual:
    """仅本场计算的主属性变化，不产生装备写入权限。"""

    character_id: int
    main_stat: dict[str, Any] | None

@dataclass(frozen=True, slots=True)
class BattleMarginalCandidate:
    """冻结 worker 字段绑定；成员保持原浅冻结语义，不作持久化载荷。"""

    battle_record_id: int
    profiles: tuple[dict[str, Any], ...]
    equipment_editable: bool
    disabled_inferred_fact_ids: frozenset[str] = frozenset()
    core_main_stat_counterfactual: BattleCoreMainStatCounterfactual | None = None


@dataclass(frozen=True, slots=True)
class BattleMarginalPanelResult:
    character_id: int
    results: tuple[BattleMarginalResult, ...]
    drive_property_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadResult:
    analysis: BattleAnalysisSnapshot | None
    target_catalog: dict[str, object] | None
    target_catalog_error: Exception | None = None
    marginal_benefits: BattleMarginalBenefits | None = None
    marginal_panel: BattleMarginalPanelResult | None = None
    candidate_display_analysis: BattleAnalysisSnapshot | None = None
    hit_details: BattlePageHitDetails | None = None


# 此版本仅是旧模型默认与离线恢复门禁，原生生产者的非空修订不要求相同。
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
