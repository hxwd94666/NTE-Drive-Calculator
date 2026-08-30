# 定义固定轴属性边际与配装反事实的不可变结果。
"""Immutable fixed-axis marginal and build-counterfactual results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    QuantificationStatus,
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
    """Whole-team fixed-axis comparison between original and edited builds."""

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
    composition: Any
    assumptions: tuple[str, ...]
    vital_events: tuple[BattleBuildVitalCounterfactual, ...] = ()


__all__ = [
    "BattleBuildCounterfactual",
    "BattleBuildHitCounterfactual",
    "BattleBuildRoleCounterfactual",
    "BattleBuildVitalCounterfactual",
    "BattleMarginalResult",
]
