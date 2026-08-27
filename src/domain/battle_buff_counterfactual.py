# 定义逐 Buff 移除反事实及其受益角色分解。
"""Immutable per-Buff counterfactual values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BattleBuffBeneficiaryResult:
    """One damage recipient's share of a Buff's fixed-axis gain."""

    character_id: int
    character_name: str
    affected_hits: int
    quantified_hits: int
    baseline_damage: float
    without_buff_damage: float
    damage_gain: float
    recipient_gain_percent: float
    team_contribution_percent: float
    quantified_damage: float
    quantified_percent: float


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
    without_buff_damage: float
    damage_gain: float
    gain_percent: float
    quantified_damage: float
    quantified_percent: float
    confidence: str
    method: str
    explanation: str
    beneficiaries: tuple[BattleBuffBeneficiaryResult, ...] = ()
    unattributed_damage_gain: float = 0.0


__all__ = [
    "BattleBuffBeneficiaryResult",
    "BattleBuffCounterfactualResult",
]
