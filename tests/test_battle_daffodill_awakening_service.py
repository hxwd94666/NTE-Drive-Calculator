# 验证达芙蒂尔觉醒只消费固定轴已有动作、目标和倾陷结算。
from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleInferredAction,
    BattleRangeRoleSummary,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_daffodill_awakening_service import (
    BattleDaffodillAwakeningService,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)
from src.services.battle_inferred_character_fact_service import (
    BattleInferredCharacterFactService,
)


def _build(*effects: str) -> dict[str, Any]:
    return {
        "characters": [{
            "character_id": 1054,
            "observed_name": "达芙蒂尔",
            "profile": {
                "awakening_level": len(effects),
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": list(effects),
            },
        }],
    }


def _hit(
    event_id: str,
    at_us: int,
    *,
    attack_type: str = "E技能",
    effect_id: str = "",
    target_id: str = "target-1",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=at_us,
        relative_time_us=at_us,
        character_id=1054,
        character_name="达芙蒂尔",
        skill_name=attack_type,
        damage_name=attack_type,
        damage_component="skill",
        attack_type=attack_type,
        damage_attribute="chaos",
        target_id=target_id,
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id=effect_id,
    )


def _action(
    action_id: str,
    kind: str,
    start_us: int,
    end_us: int,
    *event_ids: str,
    character_id: int = 1054,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=action_id,
        character_id=character_id,
        character_name="达芙蒂尔" if character_id == 1054 else "队友",
        action_name=kind,
        input_kind=kind,
        input_sequence=kind,
        start_us=start_us,
        end_us=end_us,
        hits=len(event_ids),
        damage=100.0 * len(event_ids),
        identity_confidence="高",
        timing_confidence="中",
        inference_basis="fixture",
        evidence_event_ids=tuple(event_ids),
        gameplay_effect_ids=(),
    )


def _snapshot(
    *,
    hits: tuple[BattleAnalysisHit, ...],
    intervals=(),
    replays: tuple[BattleHitReplayResult, ...] = (),
) -> BattleAnalysisSnapshot:
    total = sum(hit.damage for hit in hits)
    return BattleAnalysisSnapshot(
        battle_record_id=27,
        capability_level="formal_hit",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=1_000_000,
        timeline_end_us=1_000_000,
        range_start_us=0,
        range_end_us=1_000_000,
        duration_seconds=1.0,
        total_damage=total,
        total_dps=total,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=(BattleRangeRoleSummary(
            character_id=1054,
            character_name="达芙蒂尔",
            hits=len(hits),
            damage=total,
            dps=total,
            share_percent=100.0,
        ),),
        skills=(),
        targets=(),
        baselines=(BattleCharacterBaseline(1054, "达芙蒂尔", "fixture", ()),),
        buff_intervals=tuple(intervals),
        effective_damage=total,
        effective_dps=total,
        hit_replays=replays,
    )


class BattleDaffodillAwakeningServiceTests(unittest.TestCase):
    def test_exact_extra_topple_ge_does_not_supplement_effect_five(self) -> None:
        evidence = {"hits": [{
            "direction": "outgoing",
            "character_id": 1054,
            "gameplay_effect_name": "GE_Player_Daffodill_ExtraUnbalance_Damage",
            "sequence_text": "42",
        }]}

        facts = BattleInferredCharacterFactService.infer(evidence)
        self.assertEqual((), facts)

    def test_qte_stacks_are_consumed_by_e_and_effect_one_doubles_value(self) -> None:
        actions = (
            _action(
                "qte-1", "QTE", 10, 20, "qte-hit-1", character_id=1004,
            ),
            _action("qte-2", "QTE", 30, 40, "qte-hit-2"),
            _action("e-1", "E", 50, 80, "e-hit-1"),
            _action("e-2", "E", 90, 120, "e-hit-2"),
        )

        base = BattleDaffodillAwakeningService.infer(
            build=_build(), actions=actions, hits=(), battle_end_us=200,
        )
        awakened = BattleDaffodillAwakeningService.infer(
            build=_build("Effect1"), actions=actions, hits=(), battle_end_us=200,
        )

        base_e = next(row for row in base if row.interval_id.startswith("buff:daffodill:qte-e"))
        awakened_e = next(
            row for row in awakened if row.interval_id.startswith("buff:daffodill:qte-e")
        )
        self.assertEqual(2, base_e.stacks)
        self.assertEqual(1.0, base_e.modifiers[0].magnitude_value)
        self.assertEqual(2.0, awakened_e.modifiers[0].magnitude_value)
        self.assertEqual(1, sum(
            row.interval_id.startswith("buff:daffodill:qte-e") for row in base
        ))

    def test_insight_effect_four_projects_to_both_topple_rows(self) -> None:
        q_hit = _hit("q-hit", 100, attack_type="Q技能")
        base_topple = _hit(
            "topple", 200,
            attack_type="Passive Damage",
            effect_id="Buff_Tenacity_damage",
        )
        extra_topple = _hit(
            "extra", 203,
            attack_type="Passive Damage",
            effect_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
        )
        intervals = BattleDaffodillAwakeningService.infer(
            build=_build("Effect4", "Effect5"),
            actions=(_action("q-1", "Q", 80, 110, "q-hit"),),
            hits=(q_hit, base_topple, extra_topple),
            battle_end_us=500,
        )

        for hit in (base_topple, extra_topple):
            projection = BattleBuffAttributeProjectionService.project_hit(hit, intervals)
            modifier = next(
                row for row in projection.modifiers if row.property_id == "UnbalDamageUp"
            )
            self.assertEqual(0.15, modifier.additive_value)

    def test_effect_five_emits_candidate_settlement_only_when_selected(self) -> None:
        q_hit = _hit("q-hit", 100, attack_type="Q技能")
        base_topple = _hit(
            "topple", 200,
            attack_type="Passive Damage",
            effect_id="Buff_Tenacity_damage",
        )
        inactive = BattleDaffodillAwakeningService.infer(
            build=_build(),
            actions=(_action("q-1", "Q", 80, 110, "q-hit"),),
            hits=(q_hit, base_topple),
            battle_end_us=300,
        )
        active = BattleDaffodillAwakeningService.infer(
            build=_build("Effect5"),
            actions=(_action("q-1", "Q", 80, 110, "q-hit"),),
            hits=(q_hit, base_topple),
            battle_end_us=300,
        )

        self.assertFalse(any(
            row.source_effect_definition_id.endswith("Effect5") for row in inactive
        ))
        settlement = next(
            row for row in active
            if row.source_effect_definition_id.endswith("Effect5")
        )
        self.assertEqual(1, settlement.stacks)
        self.assertEqual(("topple",), settlement.evidence_event_ids[-1:])

    def test_only_effect_three_allows_two_insight_stacks(self) -> None:
        first_q_hit = _hit("q-hit-1", 100, attack_type="Q技能")
        second_q_hit = _hit("q-hit-2", 150, attack_type="Q技能")
        base_topple = _hit(
            "topple", 200,
            attack_type="Passive Damage",
            effect_id="Buff_Tenacity_damage",
        )
        actions = (
            _action("q-1", "Q", 80, 110, "q-hit-1"),
            _action("q-2", "Q", 130, 160, "q-hit-2"),
        )
        hits = (first_q_hit, second_q_hit, base_topple)

        without_effect_three = BattleDaffodillAwakeningService.infer(
            build=_build("Effect5"), actions=actions, hits=hits, battle_end_us=300,
        )
        with_effect_three = BattleDaffodillAwakeningService.infer(
            build=_build("Effect3", "Effect5"),
            actions=actions,
            hits=hits,
            battle_end_us=300,
        )

        base_settlement = next(
            row for row in without_effect_three
            if row.source_effect_definition_id.endswith("Effect5")
        )
        stacked_settlement = next(
            row for row in with_effect_three
            if row.source_effect_definition_id.endswith("Effect5")
        )
        self.assertEqual(1, base_settlement.stacks)
        self.assertEqual(2, stacked_settlement.stacks)

    def test_build_margin_adds_selected_effect_five_settlement(self) -> None:
        q_hit = _hit("q-hit", 100, attack_type="Q技能")
        base_topple = _hit(
            "topple", 200,
            attack_type="Passive Damage",
            effect_id="Buff_Tenacity_damage",
        )
        extra_topple = _hit(
            "extra", 203,
            attack_type="Passive Damage",
            effect_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
        )
        base_topple = replace(base_topple, damage=700.0)
        extra_topple = replace(extra_topple, damage=250.0)
        candidate_intervals = BattleDaffodillAwakeningService.infer(
            build=_build("Effect5"),
            actions=(_action("q-1", "Q", 80, 110, "q-hit"),),
            hits=(q_hit, extra_topple, base_topple),
            battle_end_us=300,
        )
        base_replay = BattleHitReplayResult(
            event_id="topple",
            observed_damage=700.0,
            non_critical_damage=700.0,
            critical_damage=None,
            selected_damage=700.0,
            selected_error_percent=0.0,
            critical_state="not_applicable",
            confidence="高",
            factors=(BattleHitReplayFactor(
                factor_id="topple_character:1054",
                label="达芙蒂尔倾陷贡献",
                value=250.75,
                evidence_basis="fixture",
            ),),
        )
        extra_replay = BattleHitReplayResult(
            event_id="extra",
            observed_damage=250.0,
            non_critical_damage=250.0,
            critical_damage=None,
            selected_damage=250.0,
            selected_error_percent=0.0,
            critical_state="not_applicable",
            confidence="高",
            factors=(),
        )
        hits = (q_hit, base_topple, extra_topple)
        original = _snapshot(
            hits=hits, replays=(base_replay, extra_replay),
        )
        candidate = _snapshot(
            hits=hits,
            intervals=candidate_intervals,
            replays=(base_replay, extra_replay),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original, candidate=candidate,
        )

        derived = next(
            row for row in result.hits
            if row.quantification.method == "candidate_derived_daffodill_effect5"
        )
        self.assertEqual(0.0, derived.baseline_damage)
        self.assertEqual(250.0, derived.candidate_damage)
        self.assertEqual(1_050.0, result.baseline_damage)
        self.assertEqual(1_300.0, result.candidate_damage)
        projected = BattleBuildTimelineProjectionService.project(candidate, result)
        self.assertIn(derived.event_id, {hit.event_id for hit in projected.hits})
        self.assertIn(
            derived.event_id,
            {
                event_id
                for group in projected.timeline_damage_groups
                for event_id in group.evidence_event_ids
            },
        )
        self.assertEqual(1_300.0, projected.effective_damage)

    def test_resonance_six_requires_reliable_topple_duration(self) -> None:
        effects = tuple(f"Effect{index}" for index in range(1, 7))
        base_topple = _hit(
            "topple", 200,
            attack_type="Passive Damage",
            effect_id="Buff_Tenacity_damage",
        )
        extra_topple = _hit(
            "extra", 203,
            attack_type="Passive Damage",
            effect_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
        )

        unresolved = BattleDaffodillAwakeningService.infer(
            build=_build(*effects), actions=(), hits=(base_topple, extra_topple),
            battle_end_us=2_000_000,
        )
        resolved = BattleDaffodillAwakeningService.infer(
            build=_build(*effects), actions=(), hits=(base_topple, extra_topple),
            battle_end_us=2_000_000, topple_duration_us=1_000_000,
        )

        self.assertFalse(any(
            row.source_effect_definition_id.endswith("resonance_6")
            for row in unresolved
        ))
        resonance = next(
            row for row in resolved
            if row.source_effect_definition_id.endswith("resonance_6")
        )
        self.assertEqual(201, resonance.start_us)
        base_projection = BattleBuffAttributeProjectionService.project_hit(
            base_topple, resolved
        )
        extra_projection = BattleBuffAttributeProjectionService.project_hit(
            extra_topple, resolved
        )
        self.assertFalse(any(
            row.property_id == "DamageResistChaosBase"
            for row in base_projection.modifiers
        ))
        self.assertEqual(-0.15, next(
            row.additive_value for row in extra_projection.modifiers
            if row.property_id == "DamageResistChaosBase"
        ))


if __name__ == "__main__":
    unittest.main()
