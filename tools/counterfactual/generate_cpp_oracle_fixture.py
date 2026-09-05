# 生成由当前 Python 权威实现计算的公开 C++ 反事实夹具。
"""Generate the public C++ counterfactual fixture from the Python oracle."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_counterfactual_batch_executor import (
    _resolve_projection_gap,
)
from src.services.battle_buff_counterfactual_projection_support import (
    HitProjection,
    aggregate_quantification,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_buff_interval_index import (
    buff_interval_applies_to_hit,
)


FIXTURE_DIR = ROOT / "native" / "counterfactual-core" / "tests" / "fixtures"
REQUEST_PATH = FIXTURE_DIR / "ordinary-buffs.request.json"
ORACLE_PATH = FIXTURE_DIR / "ordinary-buffs.oracle.json"


def request_fixture() -> dict[str, Any]:
    """Return synthetic, public data covering the first native slice."""

    return {
        "schema_version": "counterfactual-request-v1",
        "model_version": "counterfactual-core-v2",
        "dataset_version": "public-fixture-v1",
        "axis": {
            "complete": True,
            "range_start_us": 0,
            "range_end_us": 8_000_000,
        },
        "characters": [{
            "character_id": 1,
            "character_level": 80.0,
            "stats": {
                "AtkBase": 100.0,
                "AtkUp": 0.0,
                "AtkAdd": 0.0,
                "CritBase": 0.25,
                "CritDamageBase": 0.5,
                "DamageUpGeneralBase": 0.0,
                "DamageUpChaosBase": 0.0,
                "DamageUpCosmosBase": 0.0,
                "DamagePenetrateChaos": 0.0,
                "DamagePenetrateCosmos": 0.0,
                "DefIgnore": 0.0,
            },
        }],
        "target_profiles": [
            {
                "scope_half": "upper",
                "target_id": "cosmos-target",
                "damage_attribute": "cosmos",
                "resistance": 0.25,
                "enemy_defense_base": 160.0,
            },
            {
                "scope_half": "upper",
                "target_id": "psychically-target",
                "damage_attribute": "psychically",
                "resistance": 0.20,
                "enemy_defense_base": 220.0,
            },
        ],
        "hits": [
            {
                "event_id": "upper-non-critical",
                "sequence": 1,
                "relative_time_us": 1_000_000,
                "scope_half": "upper",
                "target_id": "shared-wire-id",
                "character_id": 1,
                "damage": 1380.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "chaos",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "non_critical",
                "critical_rate": None,
            },
            {
                "event_id": "upper-critical",
                "sequence": 2,
                "relative_time_us": 2_000_000,
                "scope_half": "upper",
                "target_id": "shared-wire-id",
                "character_id": 1,
                "damage": 2160.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "chaos",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "critical",
                "critical_rate": None,
            },
            {
                "event_id": "lower-same-wire-id",
                "sequence": 3,
                "relative_time_us": 3_000_000,
                "scope_half": "lower",
                "target_id": "shared-wire-id",
                "character_id": 1,
                "damage": 1000.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "chaos",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "non_critical",
                "critical_rate": None,
            },
            {
                "event_id": "upper-other-target",
                "sequence": 4,
                "relative_time_us": 3_500_000,
                "scope_half": "upper",
                "target_id": "other-target",
                "character_id": 1,
                "damage": 900.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "chaos",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "non_critical",
                "critical_rate": None,
            },
            {
                "event_id": "upper-cosmos-target",
                "sequence": 5,
                "relative_time_us": 5_000_000,
                "scope_half": "upper",
                "target_id": "cosmos-target",
                "character_id": 1,
                "damage": 1200.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "cosmos",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "non_critical",
                "critical_rate": None,
            },
            {
                "event_id": "upper-psychically-target",
                "sequence": 6,
                "relative_time_us": 6_000_000,
                "scope_half": "upper",
                "target_id": "psychically-target",
                "character_id": 1,
                "damage": 1100.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "psychically",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "non_critical",
                "critical_rate": None,
            },
            {
                "event_id": "upper-cosmos-defense",
                "sequence": 7,
                "relative_time_us": 7_000_000,
                "scope_half": "upper",
                "target_id": "cosmos-target",
                "character_id": 1,
                "damage": 1300.0,
                "direction": "outgoing",
                "classification": "direct",
                "damage_attribute": "cosmos",
                "scaling_property_id": "Atk",
                "critical_policy": "character",
                "critical_state": "non_critical",
                "critical_rate": None,
            },
        ],
        "buffs": [
            {
                "buff_key": "buff:complete-damage-up",
                "intervals": [{
                    "interval_id": "complete-upper",
                    "start_us": 0,
                    "end_us": 3_000_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "self",
                    "target_id": "",
                    "modifiers": [{
                        "property_id": "DamageUpGeneralBase",
                        "operation": "additive",
                        "value": 0.15,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
            {
                "buff_key": "buff:partial-damage-and-penetration",
                "intervals": [{
                    "interval_id": "partial-upper",
                    "start_us": 0,
                    "end_us": 3_000_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "self",
                    "target_id": "",
                    "modifiers": [
                        {
                            "property_id": "DamageUpGeneralBase",
                            "operation": "additive",
                            "value": 0.15,
                            "value_confidence": "high",
                            "calculation_asset_path": "",
                        },
                        {
                            "property_id": "DamagePenetrateChaos",
                            "operation": "additive",
                            "value": 0.10,
                            "value_confidence": "high",
                            "calculation_asset_path": "",
                        },
                    ],
                }],
            },
            {
                "buff_key": "buff:unavailable-target-resistance",
                "intervals": [{
                    "interval_id": "target-upper",
                    "start_us": 0,
                    "end_us": 4_000_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "target",
                    "target_id": "shared-wire-id",
                    "modifiers": [{
                        "property_id": "DamageResistChaosBase",
                        "operation": "additive",
                        "value": -0.20,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
            {
                "buff_key": "buff:not-applicable-outside-range",
                "intervals": [{
                    "interval_id": "future",
                    "start_us": 9_000_000,
                    "end_us": 10_000_000,
                    "scope_half": "",
                    "source_character_id": 1,
                    "target_scope": "self",
                    "target_id": "",
                    "modifiers": [{
                        "property_id": "DamageUpGeneralBase",
                        "operation": "additive",
                        "value": 0.50,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
            {
                "buff_key": "buff:frozen-critical-branch",
                "intervals": [{
                    "interval_id": "critical-upper",
                    "start_us": 0,
                    "end_us": 3_000_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "self",
                    "target_id": "",
                    "modifiers": [{
                        "property_id": "CritDamageBase",
                        "operation": "additive",
                        "value": 0.30,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
            {
                "buff_key": "buff:cosmos-target-resistance",
                "intervals": [{
                    "interval_id": "cosmos-target-resistance",
                    "start_us": 4_500_000,
                    "end_us": 5_500_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "target",
                    "target_id": "cosmos-target",
                    "modifiers": [{
                        "property_id": "DamageResistCosmosBase",
                        "operation": "additive",
                        "value": -0.10,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
            {
                "buff_key": "buff:psychically-defense-ignore-cancelled",
                "intervals": [{
                    "interval_id": "psychically-defense-ignore",
                    "start_us": 5_500_000,
                    "end_us": 6_500_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "self",
                    "target_id": "",
                    "modifiers": [{
                        "property_id": "DefIgnore",
                        "operation": "additive",
                        "value": 0.30,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
            {
                "buff_key": "buff:cosmos-defense-ignore",
                "intervals": [{
                    "interval_id": "cosmos-defense-ignore",
                    "start_us": 6_500_000,
                    "end_us": 7_500_000,
                    "scope_half": "upper",
                    "source_character_id": 1,
                    "target_scope": "self",
                    "target_id": "",
                    "modifiers": [{
                        "property_id": "DefIgnore",
                        "operation": "additive",
                        "value": 0.20,
                        "value_confidence": "high",
                        "calculation_asset_path": "",
                    }],
                }],
            },
        ],
    }


def _hit(row: dict[str, Any]) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=row["event_id"],
        sequence=row["sequence"],
        relative_time_us=row["relative_time_us"],
        character_id=row["character_id"],
        character_name=f"角色{row['character_id']}",
        skill_name="公开夹具技能",
        damage_name="公开夹具伤害",
        damage_component=row["classification"],
        attack_type="skill",
        damage_attribute=row["damage_attribute"],
        target_id=row["target_id"],
        target_name="公开夹具目标",
        damage=row["damage"],
        direction=row["direction"],
        is_follow_up=False,
        classification=row["classification"],
        scope_half=row["scope_half"],
    )


def _baseline(row: dict[str, Any]) -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=row["character_id"],
        character_name=f"角色{row['character_id']}",
        source="public-fixture-v1",
        stats=tuple(
            BattleCharacterStat(key, key, float(value), False)
            for key, value in sorted(row["stats"].items())
        ),
        character_level=float(row["character_level"]),
    )


def _interval(row: dict[str, Any]) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=row["interval_id"],
        buff_asset_path=f"/PublicFixture/{row['interval_id']}",
        buff_name=row["interval_id"],
        source_effect_definition_id="public-fixture-v1",
        source_kind="fixture",
        source_character_id=row["source_character_id"],
        source_character_name=f"角色{row['source_character_id']}",
        target_scope=row["target_scope"],
        start_us=row["start_us"],
        end_us=row["end_us"],
        stacks=1,
        duration_policy="HasDuration",
        state_confidence="高",
        value_confidence="高",
        inference_basis="public-fixture-v1",
        trigger_event_type="fixture",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=tuple(
            BattleBuffModifierEvidence(
                property_id=modifier["property_id"],
                modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="ScalableFloat",
                magnitude_value=modifier["value"],
                calculation_asset_path=modifier["calculation_asset_path"],
                value_confidence="高",
            )
            for modifier in row["modifiers"]
        ),
        target_id=row["target_id"],
    )


def _ratio_row(ratio: Any, hit: BattleAnalysisHit) -> dict[str, Any]:
    candidate = (
        None
        if ratio.quantified_ratio is None
        else float(hit.damage) * ratio.quantified_ratio
    )
    return {
        "event_id": hit.event_id,
        "status": ratio.status,
        "quantified_ratio": ratio.quantified_ratio,
        "candidate_damage": candidate,
        "gap_codes": [gap.code for gap in ratio.gaps],
    }


def _target_condition(
    request: dict[str, Any],
    hit: BattleAnalysisHit,
) -> BattleTargetCondition | None:
    profile = next(
        (
            row
            for row in request["target_profiles"]
            if row["scope_half"] == hit.scope_half
            and row["target_id"] == hit.target_id
            and row["damage_attribute"] == hit.damage_attribute
        ),
        None,
    )
    if profile is None:
        return None
    return BattleTargetCondition(
        target_name="公开夹具目标",
        enemy_level=90.0,
        scene="outer_realm",
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=((hit.damage_attribute, float(profile["resistance"])),),
        enemy_defense_base=profile["enemy_defense_base"],
    )


def _validate_oracle_subset(request: dict[str, Any]) -> None:
    """Reject wire variants this fixture adapter does not faithfully normalize."""

    if not request["axis"]["complete"]:
        raise ValueError("public Python oracle supports only complete axes")
    for buff in request["buffs"]:
        for interval in buff["intervals"]:
            for modifier in interval["modifiers"]:
                if modifier["operation"] != "additive":
                    raise ValueError("public Python oracle supports only additive modifiers")
                if modifier["value_confidence"] != "high":
                    raise ValueError("public Python oracle supports only high-confidence values")


def python_oracle(request: dict[str, Any]) -> dict[str, Any]:
    """Run the normalized fixture through current Python domain services."""

    _validate_oracle_subset(request)
    baselines = {
        row["character_id"]: _baseline(row) for row in request["characters"]
    }
    hits = tuple(_hit(row) for row in request["hits"])
    raw_intervals = {
        buff["buff_key"]: tuple(buff["intervals"])
        for buff in request["buffs"]
    }
    domain_intervals = {
        key: tuple(_interval(row) for row in rows)
        for key, rows in raw_intervals.items()
    }
    results = []
    for buff in request["buffs"]:
        buff_key = buff["buff_key"]
        projections = []
        rows = []
        for hit in hits:
            relevant_all = tuple(
                interval
                for key, group in domain_intervals.items()
                for interval, raw in zip(group, raw_intervals[key], strict=True)
                if not raw["scope_half"] or raw["scope_half"] == hit.scope_half
            )
            relevant_group = tuple(
                interval
                for interval, raw in zip(
                    domain_intervals[buff_key],
                    raw_intervals[buff_key],
                    strict=True,
                )
                if not raw["scope_half"] or raw["scope_half"] == hit.scope_half
            )
            candidate_intervals = tuple(
                interval
                for key, group in domain_intervals.items()
                if key != buff_key
                for interval, raw in zip(group, raw_intervals[key], strict=True)
                if not raw["scope_half"] or raw["scope_half"] == hit.scope_half
            )
            original_projection = BattleBuffAttributeProjectionService.project_hit(
                hit, relevant_all
            )
            candidate_projection = BattleBuffAttributeProjectionService.project_hit(
                hit, candidate_intervals
            )
            group_projection = BattleBuffAttributeProjectionService.project_hit(
                hit, relevant_group
            )
            evidence = BattleSkillDamageEvidence(
                event_id=hit.event_id,
                damage_id="public-fixture",
                ability_id="public-fixture",
                damage_attribute=hit.damage_attribute,
                damage_source_category="skill",
                fixed_crit_rate=0.0,
                scaling_property_id=next(
                    row["scaling_property_id"]
                    for row in request["hits"]
                    if row["event_id"] == hit.event_id
                ),
                scaling_multiplier=1.0,
                multiplier_coefficient=1.0,
                effective_skill_level=1,
                evidence_basis="public-fixture-v1",
            )
            hit_row = next(
                row for row in request["hits"] if row["event_id"] == hit.event_id
            )
            replay = BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=hit.damage,
                critical_damage=hit.damage,
                selected_damage=hit.damage,
                selected_error_percent=0.0,
                critical_state=hit_row["critical_state"],
                confidence="高",
                factors=(),
                critical_rate=hit_row["critical_rate"],
                expected_damage=hit.damage,
                critical_policy=hit_row["critical_policy"],
            )
            group_active = any(
                buff_interval_applies_to_hit(interval, hit)
                and (
                    interval.target_scope != "target"
                    or interval.target_id == hit.target_id
                )
                for interval in relevant_group
            )
            if not group_active:
                ratio = BattleCounterfactualRatio.not_applicable(
                    method="normalized_interval_not_applicable",
                    explanation="The frozen Buff group does not cover this hit.",
                )
            else:
                ratio = BattleHitCounterfactualRatioService.compare(
                    hit=hit,
                    original_baseline=baselines[hit.character_id],
                    candidate_baseline=baselines[hit.character_id],
                    original_projection=original_projection,
                    candidate_projection=candidate_projection,
                    skill_evidence=evidence,
                    original_replay=replay,
                    candidate_replay=None,
                    target_condition=_target_condition(request, hit),
                )
                ratio = _resolve_projection_gap(
                    ratio,
                    group_projection=group_projection,
                    group_intervals=relevant_group,
                )
            rows.append(_ratio_row(ratio, hit))
            projections.append(HitProjection(
                hit=hit,
                predicted_damage=(
                    hit.damage
                    if ratio.quantified_ratio is None
                    else hit.damage * ratio.quantified_ratio
                ),
                quantification=ratio,
            ))
        quantified_increment = sum(
            row.hit.damage - row.predicted_damage
            for row in projections
            if row.quantification.status in {"complete", "partial"}
        )
        aggregate = aggregate_quantification(
            hit_projections=projections,
            vital_projections=(),
            fixed_derived_damage=0.0,
            proven_unchanged_hit_damage=0.0,
            quantified_increment=quantified_increment,
        )
        results.append({
            "buff_key": buff_key,
            "status": aggregate.status,
            "basis_damage": aggregate.basis_damage,
            "fully_quantified_damage": aggregate.fully_quantified_damage,
            "partially_quantified_damage": aggregate.partially_quantified_damage,
            "unavailable_damage": aggregate.unavailable_damage,
            "proven_unchanged_damage": aggregate.proven_unchanged_damage,
            "quantified_increment": aggregate.quantified_increment,
            "gap_codes": sorted({gap.code for gap in aggregate.gaps}),
            "hits": rows,
        })
    return {
        "schema_version": "counterfactual-response-v1",
        "model_version": request["model_version"],
        "dataset_version": request["dataset_version"],
        "results": results,
    }


def main() -> None:
    request = request_fixture()
    oracle = python_oracle(request)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    REQUEST_PATH.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ORACLE_PATH.write_text(
        json.dumps(oracle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {REQUEST_PATH.relative_to(ROOT)}")
    print(f"wrote {ORACLE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
