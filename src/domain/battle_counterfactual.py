# 定义固定轴属性边际与配装反事实的不可变结果。
"""Immutable fixed-axis marginal and build-counterfactual results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.battle_report import BattleDamageComposition


@dataclass(frozen=True, slots=True)
class BattleMarginalResult:
    property_id: str
    label: str
    unit: float
    is_percent: bool
    baseline_damage: float
    predicted_damage: float
    role_gain_percent: float
    team_dps_gain_percent: float
    supported_damage: float
    unsupported_damage: float
    coverage_percent: float
    damage_share_percent: float
    assumption: str


@dataclass(frozen=True, slots=True)
class BattleBuildHitCounterfactual:
    """One fixed-axis hit projected from the frozen build to an edited build."""

    event_id: str
    character_id: int | None
    character_name: str
    skill_name: str
    damage_name: str
    baseline_damage: float
    predicted_damage: float
    ratio: float
    method: str
    confidence: str
    explanation: str
    baseline_formula_damage: float | None = None
    candidate_formula_damage: float | None = None
    source_event_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleBuildVitalCounterfactual:
    """One attributed max-HP settlement projected under the edited build."""

    event_id: str
    character_id: int | None
    character_name: str
    mechanic_kind: str
    mechanic_name: str
    baseline_damage: float
    predicted_damage: float
    ratio: float
    method: str
    confidence: str
    explanation: str


@dataclass(frozen=True, slots=True)
class BattleBuildRoleCounterfactual:
    """One role's full-axis damage projection under the edited build."""

    character_id: int
    character_name: str
    baseline_damage: float
    predicted_damage: float
    gain_percent: float
    team_gain_percent: float
    structured_damage: float
    estimated_damage: float
    structured_percent: float


@dataclass(frozen=True, slots=True)
class BattleBuildCounterfactual:
    """Whole-team fixed-axis comparison between original and edited builds."""

    model_version: str
    baseline_damage: float
    predicted_damage: float
    gain_percent: float
    baseline_dps: float
    predicted_dps: float
    structured_damage: float
    estimated_damage: float
    structured_percent: float
    roles: tuple[BattleBuildRoleCounterfactual, ...]
    hits: tuple[BattleBuildHitCounterfactual, ...]
    composition: BattleDamageComposition
    assumptions: tuple[str, ...]
    vital_events: tuple[BattleBuildVitalCounterfactual, ...] = ()


__all__ = [
    "BattleBuildCounterfactual",
    "BattleBuildHitCounterfactual",
    "BattleBuildRoleCounterfactual",
    "BattleBuildVitalCounterfactual",
    "BattleMarginalResult",
]
