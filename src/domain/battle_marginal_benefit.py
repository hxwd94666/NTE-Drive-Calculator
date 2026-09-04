# 定义空幕主属性与弧盘固定轴收益的不可变展示结果。
"""Immutable summaries for battle equipment and fork marginal benefits."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.battle_counterfactual_quantification import QuantificationStatus


@dataclass(frozen=True, slots=True)
class BattleMarginalDelta:
    """One team/role projection with explicit status and evidence coverage."""

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
    is_current: bool  # Same property as the current core, regardless of quality.
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


__all__ = [
    "BattleCoreMainStatMarginal",
    "BattleForkMarginal",
    "BattleMarginalBenefits",
    "BattleMarginalDelta",
]
