# 定义逐 Buff 移除反事实及其受益角色的分层量化结果。
"""Immutable per-Buff counterfactual values with explicit unknowns."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite

from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)


@dataclass(frozen=True, slots=True)
class BattleDamageCoverage:
    """Original fixed-axis damage confirmed or unresolved under one effect."""

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
    """One recipient's complete or partial share of a Buff's fixed-axis gain."""

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
    """Selected-range damage with one inferred Buff source removed."""

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


__all__ = [
    "BattleDamageCoverage",
    "BattleBuffBeneficiaryResult",
    "BattleBuffCounterfactualResult",
]
