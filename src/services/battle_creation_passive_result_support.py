# 统一组装创生被动反事实结果，避免评估器同时承担展示投影职责。
"""Result assembly for creation-passive counterfactual evaluations."""

from __future__ import annotations

from src.domain.battle_buff_counterfactual import (
    BattleBuffBeneficiaryResult,
    BattleBuffCounterfactualResult,
    BattleDamageCoverage,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)
from src.services.battle_character_passive_service import EnabledCharacterPassive


def build_creation_passive_result(
    enabled: EnabledCharacterPassive,
    team_damage: float,
    event_ids: tuple[str, ...],
    quantification: BattleDamageQuantification,
    *,
    affected_hits: int,
    quantified_hits: int,
    confidence: str,
    method: str,
    explanation: str,
    beneficiaries: tuple[BattleBuffBeneficiaryResult, ...] = (),
    quantified_unattributed_damage_gain: float | None = None,
    unattributed_damage_gain: float | None = None,
    damage_coverage: BattleDamageCoverage = BattleDamageCoverage(),
) -> BattleBuffCounterfactualResult:
    """Project one evaluated passive through the shared Buff result contract."""

    status = quantification.status
    gain = quantification.quantified_increment
    without_quantified = None if gain is None else max(0.0, team_damage - gain)
    quantified_percent = (
        None
        if gain is None
        else gain / without_quantified * 100.0
        if without_quantified > 0.0
        else 0.0
    )
    complete = status in {"complete", "not_applicable"}
    definition = enabled.definition
    source_key = (
        f"character_passive:{enabled.source_character_id}:"
        f"{definition.ability_id}"
    )
    return BattleBuffCounterfactualResult(
        buff_key=source_key,
        source_character_id=enabled.source_character_id,
        source_character_name=enabled.source_character_name,
        buff_name=definition.name,
        buff_asset_path=definition.asset_path,
        source_effect_definition_id=source_key,
        target_scope="team",
        interval_count=0,
        coverage_seconds=0.0,
        affected_hits=affected_hits,
        quantified_hits=quantified_hits,
        baseline_damage=team_damage,
        without_quantified_effect_damage=without_quantified,
        quantified_damage_gain=gain,
        quantified_gain_percent=quantified_percent,
        without_buff_damage=without_quantified if complete else None,
        damage_gain=gain if complete else None,
        gain_percent=quantified_percent if complete else None,
        confidence=confidence,
        method=method,
        explanation=explanation,
        quantification=quantification,
        beneficiaries=beneficiaries,
        quantified_unattributed_damage_gain=(
            gain
            if quantified_unattributed_damage_gain is None
            else quantified_unattributed_damage_gain
        ),
        unattributed_damage_gain=(
            gain
            if complete and unattributed_damage_gain is None
            else unattributed_damage_gain
        ),
        evidence_event_ids=event_ids,
        damage_coverage=damage_coverage,
    )


__all__ = ["build_creation_passive_result"]
