# 保存固定轴配装、Buff 和边际结果及冻结候选，不授予装备写入权限。
from __future__ import annotations

# frozen 只冻结字段绑定；候选字典保持既有浅冻结，防御性复制由会话 Service 负责。
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any
from src.domain.battle_report.analysis.composition import BattleDamageComposition
from src.domain.battle_report.counterfactual.quantification import (
    BattleCounterfactualRatio, BattleDamageQuantification, QuantificationStatus,
)


@dataclass(frozen=True, slots=True)
class BattleMarginalResult:
    property_id: str
    label: str
    unit: float
    is_percent: bool
    baseline_damage: float
    known_projection_damage: float | None
    quantified_role_gain_percent: float | None
    quantified_team_gain_percent: float | None
    full_role_gain_percent: float | None
    full_team_gain_percent: float | None
    damage_share_percent: float
    quantification: BattleDamageQuantification
    assumption: str
    role_denominator_status: QuantificationStatus = "complete"
    team_denominator_status: QuantificationStatus = "complete"
    panel_value: float = 0.0
    weighted_effective_value: float | None = None
    related_damage: float = 0.0
    related_role_share_percent: float = 0.0
    role_share_percent: float = 0.0
    related_team_share_percent: float = 0.0
    estimated_role_gain_percent: float | None = None
    estimated_team_gain_percent: float | None = None


@dataclass(frozen=True, slots=True)
class BattleBuildHitCounterfactual:
    """One fixed-axis hit projected from the frozen build to an edited build."""

    event_id: str
    character_id: int | None
    character_name: str
    skill_name: str
    damage_name: str
    baseline_damage: float
    known_projection_damage: float | None
    candidate_damage: float | None
    heuristic_projection_damage: float | None
    quantification: BattleCounterfactualRatio
    baseline_formula_damage: float | None = None
    candidate_formula_damage: float | None = None
    source_event_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleBuildVitalCounterfactual:
    """One max-HP settlement; state is (max HP, current HP, reduction)."""

    event_id: str
    character_id: int | None
    character_name: str
    mechanic_kind: str
    mechanic_name: str
    baseline_damage: float
    known_projection_damage: float | None
    candidate_damage: float | None
    heuristic_projection_damage: float | None
    quantification: BattleCounterfactualRatio
    candidate_state: tuple[float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class BattleBuildRoleCounterfactual:
    """One role's full-axis damage projection under the edited build."""

    character_id: int
    character_name: str
    baseline_damage: float
    known_projection_damage: float | None
    candidate_damage: float | None
    heuristic_projection_damage: float | None
    known_gain_percent: float | None
    gain_percent: float | None
    known_team_gain_percent: float | None
    team_gain_percent: float | None
    quantification: BattleDamageQuantification
    structured_damage: float
    structured_percent: float


@dataclass(frozen=True, slots=True)
class BattleBuildCounterfactual:
    """原始冻结配装与单场编辑副本的固定轴对比，已知、完整和启发式结果分列。"""

    model_version: str
    baseline_damage: float
    known_projection_damage: float | None
    candidate_damage: float | None
    heuristic_projection_damage: float | None
    known_gain_percent: float | None
    gain_percent: float | None
    baseline_dps: float
    known_projection_dps: float | None
    candidate_dps: float | None
    heuristic_projection_dps: float | None
    quantification: BattleDamageQuantification
    structured_damage: float
    structured_percent: float
    roles: tuple[BattleBuildRoleCounterfactual, ...]
    hits: tuple[BattleBuildHitCounterfactual, ...]
    composition: BattleDamageComposition
    assumptions: tuple[str, ...]
    vital_events: tuple[BattleBuildVitalCounterfactual, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleDamageCoverage:
    """在原始固定轴伤害内分别记录已覆盖与待确认部分，零分母保留未知。"""

    basis_damage: float = 0.0
    covered_damage: float = 0.0
    unresolved_damage: float = 0.0

    def __post_init__(self) -> None:
        values = (self.basis_damage, self.covered_damage, self.unresolved_damage)
        if any(not isfinite(value) or value < 0.0 for value in values):
            raise ValueError("damage coverage values must be finite and non-negative")
        tolerance = max(1e-9, self.basis_damage * 1e-9)
        if self.covered_damage + self.unresolved_damage > self.basis_damage + tolerance:
            raise ValueError("covered and unresolved damage exceed coverage basis")

    @property
    def covered_percent(self) -> float | None:
        if self.basis_damage <= 0.0:
            return None
        return self.covered_damage / self.basis_damage * 100.0

    @property
    def unresolved_percent(self) -> float | None:
        if self.basis_damage <= 0.0:
            return None
        return self.unresolved_damage / self.basis_damage * 100.0


@dataclass(frozen=True, slots=True)
class BattleBuffBeneficiaryResult:
    """受益角色的已量化部分与完整收益分开；部分或未知状态不暴露完整收益。"""

    character_id: int
    character_name: str
    affected_hits: int
    quantified_hits: int
    baseline_damage: float
    without_quantified_effect_damage: float | None
    quantified_damage_gain: float | None
    quantified_recipient_gain_percent: float | None
    quantified_team_contribution_percent: float | None
    without_buff_damage: float | None
    damage_gain: float | None
    recipient_gain_percent: float | None
    team_contribution_percent: float | None
    quantification: BattleDamageQuantification
    damage_coverage: BattleDamageCoverage = BattleDamageCoverage()

    def __post_init__(self) -> None:
        status = self.quantification.status
        quantified_core = (
            self.without_quantified_effect_damage,
            self.quantified_damage_gain,
            self.quantified_recipient_gain_percent,
        )
        full_core = (
            self.without_buff_damage,
            self.damage_gain,
            self.recipient_gain_percent,
        )
        if status == "unavailable":
            if any(value is not None for value in (
                *quantified_core,
                self.quantified_team_contribution_percent,
                *full_core,
                self.team_contribution_percent,
            )):
                raise ValueError("unavailable beneficiary must not expose gain values")
            return
        if any(value is None for value in quantified_core):
            raise ValueError(f"{status} beneficiary requires quantified gain values")
        if status == "partial":
            if any(value is not None for value in (
                *full_core,
                self.team_contribution_percent,
            )):
                raise ValueError("partial beneficiary must not expose complete gain values")
            return
        if any(value is None for value in full_core):
            raise ValueError(f"{status} beneficiary requires complete gain values")
        if status == "not_applicable":
            zero_values = (
                self.quantified_damage_gain,
                self.quantified_recipient_gain_percent,
                self.quantified_team_contribution_percent,
                self.damage_gain,
                self.recipient_gain_percent,
                self.team_contribution_percent,
            )
            if any(
                value is not None
                and not isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9)
                for value in zero_values
            ):
                raise ValueError("not_applicable beneficiary requires zero gain")


@dataclass(frozen=True, slots=True)
class BattleBuffCounterfactualResult:
    """只移除所选范围内的一项推断来源，收益与证据缺口按量化状态保存。"""

    buff_key: str
    source_character_id: int
    source_character_name: str
    buff_name: str
    buff_asset_path: str
    source_effect_definition_id: str
    target_scope: str
    interval_count: int
    coverage_seconds: float
    affected_hits: int
    quantified_hits: int
    baseline_damage: float
    without_quantified_effect_damage: float | None
    quantified_damage_gain: float | None
    quantified_gain_percent: float | None
    without_buff_damage: float | None
    damage_gain: float | None
    gain_percent: float | None
    confidence: str
    method: str
    explanation: str
    quantification: BattleDamageQuantification
    beneficiaries: tuple[BattleBuffBeneficiaryResult, ...] = ()
    quantified_unattributed_damage_gain: float | None = None
    unattributed_damage_gain: float | None = None
    evidence_event_ids: tuple[str, ...] = ()
    damage_coverage: BattleDamageCoverage = BattleDamageCoverage()

    def __post_init__(self) -> None:
        if len(set(self.evidence_event_ids)) != len(self.evidence_event_ids):
            raise ValueError("Buff counterfactual evidence event ids must be unique")
        status = self.quantification.status
        quantified = (
            self.without_quantified_effect_damage,
            self.quantified_damage_gain,
            self.quantified_gain_percent,
            self.quantified_unattributed_damage_gain,
        )
        complete = (
            self.without_buff_damage,
            self.damage_gain,
            self.gain_percent,
            self.unattributed_damage_gain,
        )
        if status == "unavailable":
            if any(value is not None for value in (*quantified, *complete)):
                raise ValueError("unavailable Buff result must not expose gain values")
            return
        if any(value is None for value in quantified):
            raise ValueError(f"{status} Buff result requires quantified gain values")
        if status == "partial":
            if any(value is not None for value in complete):
                raise ValueError("partial Buff result must not expose complete gain values")
            return
        if any(value is None for value in complete):
            raise ValueError(f"{status} Buff result requires complete gain values")
        if status == "not_applicable":
            zero_values = (
                self.quantified_damage_gain,
                self.quantified_gain_percent,
                self.quantified_unattributed_damage_gain,
                self.damage_gain,
                self.gain_percent,
                self.unattributed_damage_gain,
            )
            if any(
                value is None
                or not isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9)
                for value in zero_values
            ):
                raise ValueError("not_applicable Buff result requires zero gain")


@dataclass(frozen=True, slots=True)
class BattleMarginalDelta:
    """队伍与角色各保留量化状态、覆盖和缺口，None 不作为零收益。"""

    team_status: QuantificationStatus
    role_status: QuantificationStatus
    baseline_team_damage: float
    projected_team_damage: float | None
    team_gain_damage: float | None
    team_gain_percent: float | None
    baseline_role_damage: float
    projected_role_damage: float | None
    role_gain_damage: float | None
    role_gain_percent: float | None
    team_coverage_percent: float
    role_coverage_percent: float
    gap_explanations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleCoreMainStatMarginal:
    """One gold main-stat candidate against no-main and current-core states."""

    property_id: str
    label: str
    value: float
    is_percent: bool
    is_current: bool  # 仅比较当前空幕的属性身份，与品质无关。
    contribution: BattleMarginalDelta
    replacement: BattleMarginalDelta


@dataclass(frozen=True, slots=True)
class BattleForkMarginal:
    """A/B/C decomposition for one equipped fork on the fixed battle axis."""

    fork_id: str
    fork_name: str
    no_fork_team_damage: float | None
    no_fork_role_damage: float | None
    permanent: BattleMarginalDelta | None
    skill: BattleMarginalDelta | None
    comprehensive: BattleMarginalDelta | None
    closure_team_damage: float | None = None
    closure_role_damage: float | None = None
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class BattleMarginalBenefits:
    """Selected-role benefits computed together with one marginal load."""

    character_id: int
    core_main_stats: tuple[BattleCoreMainStatMarginal, ...] = ()
    core_notice: str = ""
    fork: BattleForkMarginal | None = None


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
