# 验证修改配装后固定原轴的全伤害候选与分级估计。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleBuffModifierEvidence,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleHitReplayFactor,
    BattleHitReplayTerm,
    BattleInferredBuffInterval,
    BattleInferredAction,
    BattleMaxHpReductionEvent,
    BattleRangeRoleSummary,
    BattleTargetCondition,
    BattleTimelineDamageGroup,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)


def _hit(
    event_id: str,
    *,
    character_id: int,
    character_name: str,
    skill_name: str,
    damage: float,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=int(event_id.removeprefix("hit")),
        relative_time_us=int(event_id.removeprefix("hit")) * 100_000,
        character_id=character_id,
        character_name=character_name,
        skill_name=skill_name,
        damage_name=skill_name,
        damage_component="direct",
        attack_type="skill",
        damage_attribute="chaos",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=skill_name,
    )


def _replay(event_id: str, expected_damage: float) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=event_id,
        observed_damage=expected_damage,
        non_critical_damage=expected_damage,
        critical_damage=None,
        selected_damage=expected_damage,
        selected_error_percent=0.0,
        critical_state="non_critical",
        confidence="高",
        factors=(),
        expected_damage=expected_damage,
    )


def _vital_event(
    event_id: str,
    *,
    character_id: int,
    character_name: str,
    source_skill_name: str,
    damage: float,
    evidence_event_ids: tuple[str, ...],
    mechanic_kind: str = "lacrimosa_nightmare_awaken_5",
) -> BattleMaxHpReductionEvent:
    return BattleMaxHpReductionEvent(
        event_id=event_id,
        target_id="target",
        target_name="目标",
        observed_at_us=300_000,
        old_max_hp=1_000,
        new_max_hp=1_000 - damage,
        max_hp_reduction=damage,
        hp_before_settlement=1_000,
        hp_ratio_before=1.0,
        effective_hp_loss=damage,
        source_character_id=character_id,
        source_character_name=character_name,
        mechanic_kind=mechanic_kind,
        mechanic_name="生命上限结算",
        source_skill_name=source_skill_name,
        evidence_event_ids=evidence_event_ids,
        attribution_confidence="中",
        calculation_confidence="中",
        inference_basis="fixture",
    )


def _buff_interval(
    modifiers: tuple[BattleBuffModifierEvidence, ...] = (),
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id="buff-1",
        buff_asset_path="/Game/Buff_Test",
        buff_name="候选 Buff",
        source_effect_definition_id="fixture",
        source_kind="fixture",
        source_character_id=1,
        source_character_name="甲",
        target_scope="self",
        start_us=0,
        end_us=10_000_000,
        stacks=1,
        duration_policy="HasDuration",
        state_confidence="中",
        value_confidence="中",
        inference_basis="fixture",
        trigger_event_type="fixture",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=modifiers,
    )


def _snapshot(
    *,
    hits: tuple[BattleAnalysisHit, ...],
    baselines: tuple[BattleCharacterBaseline, ...],
    replays: tuple[BattleHitReplayResult, ...],
) -> BattleAnalysisSnapshot:
    total = sum(hit.damage for hit in hits)
    roles = tuple(
        BattleRangeRoleSummary(
            character_id=character_id,
            character_name=next(
                hit.character_name for hit in hits if hit.character_id == character_id
            ),
            hits=sum(hit.character_id == character_id for hit in hits),
            damage=sum(hit.damage for hit in hits if hit.character_id == character_id),
            dps=sum(hit.damage for hit in hits if hit.character_id == character_id) / 10,
            share_percent=(
                sum(hit.damage for hit in hits if hit.character_id == character_id)
                / total
                * 100
            ),
        )
        for character_id in dict.fromkeys(hit.character_id for hit in hits)
    )
    return BattleAnalysisSnapshot(
        battle_record_id=7,
        capability_level="formal_hit",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=10_000_000,
        timeline_end_us=10_000_000,
        range_start_us=0,
        range_end_us=10_000_000,
        duration_seconds=10.0,
        total_damage=total,
        total_dps=total / 10,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=roles,
        skills=(),
        targets=(),
        baselines=baselines,
        effective_damage=total,
        effective_dps=total / 10,
        hit_replays=replays,
    )


class BattleBuildCounterfactualServiceTests(unittest.TestCase):
    def test_changed_unmodeled_buff_does_not_promote_unknown_hit_to_unchanged(self) -> None:
        hit = _hit(
            "hit1", character_id=1, character_name="甲", skill_name="未知机制", damage=100,
        )
        original = _snapshot(hits=(hit,), baselines=(), replays=())

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=replace(original, buff_intervals=(_buff_interval(),)),
        )

        self.assertEqual("unavailable", result.hits[0].quantification.status)
        self.assertIsNone(result.hits[0].candidate_damage)
        self.assertIsNone(result.candidate_damage)

    def test_build_fallback_consumes_per_hit_buff_projection(self) -> None:
        hit = _hit(
            "hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100,
        )
        baseline = BattleCharacterBaseline(
            1, "甲", "fixture", (BattleCharacterStat("AtkBase", "攻击力", 100, False),),
        )
        unavailable_replay = replace(
            _replay("hit1", 100),
            non_critical_damage=None,
            selected_damage=None,
            expected_damage=None,
            critical_state="unreplayable",
        )
        original = _snapshot(
            hits=(hit,), baselines=(baseline,), replays=(unavailable_replay,),
        )
        modifier = BattleBuffModifierEvidence(
            property_id="DamageUpGeneralBase",
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="ScalableFloat",
            magnitude_value=0.1,
            calculation_asset_path="",
            value_confidence="高",
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=replace(original, buff_intervals=(_buff_interval((modifier,)),)),
        )

        self.assertEqual("complete", result.hits[0].quantification.status)
        self.assertAlmostEqual(110.0, result.hits[0].candidate_damage)

    def test_zero_candidate_is_not_replaced_by_baseline_in_composition(self) -> None:
        hit = _hit(
            "hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100,
        )
        scaling = BattleHitReplayFactor(
            factor_id="scaling",
            label="攻击力乘区",
            value=100.0,
            evidence_basis="fixture",
            terms=(BattleHitReplayTerm(
                term_id="AtkBase",
                property_id="AtkBase",
                label="攻击力",
                value=100.0,
                source_group="fixture",
                source_name="fixture",
                is_percent=False,
                evidence_basis="fixture",
            ),),
        )
        replay = replace(
            _replay("hit1", 100),
            non_critical_damage=None,
            selected_damage=None,
            expected_damage=None,
            critical_state="unreplayable",
            factors=(scaling,),
        )
        original_baseline = BattleCharacterBaseline(
            1, "甲", "fixture", (BattleCharacterStat("AtkBase", "攻击力", 100, False),),
        )
        candidate_baseline = replace(
            original_baseline,
            stats=(BattleCharacterStat("AtkBase", "攻击力", 0, False),),
        )
        original = _snapshot(
            hits=(hit,), baselines=(original_baseline,), replays=(replay,),
        )
        candidate = _snapshot(
            hits=(hit,), baselines=(candidate_baseline,), replays=(replay,),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(0.0, result.candidate_damage)
        self.assertEqual(0.0, result.roles[0].candidate_damage)
        self.assertEqual(0.0, sum(
            row.total_damage for row in result.composition.roles
        ))
        self.assertEqual(0.0, result.composition.other_total_damage)

    def test_comparison_preserves_original_resolved_critical_branch(self) -> None:
        hit = _hit(
            "hit1",
            character_id=1,
            character_name="甲",
            skill_name="技能甲",
            damage=200.0,
        )
        original_replay = replace(
            _replay("hit1", 150.0),
            observed_damage=200.0,
            non_critical_damage=100.0,
            critical_damage=200.0,
            selected_damage=200.0,
            critical_state="critical",
        )
        candidate_replay = replace(
            _replay("hit1", 260.0),
            observed_damage=200.0,
            non_critical_damage=120.0,
            critical_damage=400.0,
            selected_damage=120.0,
            critical_state="non_critical",
        )

        comparison = BattleBuildCounterfactualService.compare(
            original=_snapshot(hits=(hit,), baselines=(), replays=(original_replay,)),
            candidate=_snapshot(hits=(hit,), baselines=(), replays=(candidate_replay,)),
        )

        self.assertEqual(
            "structured_selected",
            comparison.hits[0].quantification.method,
        )
        self.assertEqual(400.0, comparison.hits[0].candidate_damage)

    def test_unknown_rows_do_not_become_numeric_candidates(self) -> None:
        hits = (
            _hit("hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100),
            _hit("hit2", character_id=1, character_name="甲", skill_name="技能甲", damage=50),
            _hit("hit3", character_id=2, character_name="乙", skill_name="技能乙", damage=80),
            _hit("hit4", character_id=3, character_name="丙", skill_name="未知机制", damage=20),
        )
        original_baselines = (
            BattleCharacterBaseline(
                1, "甲", "original", (BattleCharacterStat("AtkBase", "攻击力", 100, False),)
            ),
            BattleCharacterBaseline(
                2, "乙", "original", (BattleCharacterStat("AtkBase", "攻击力", 100, False),)
            ),
        )
        candidate_baselines = (
            original_baselines[0],
            BattleCharacterBaseline(
                2, "乙", "edited", (BattleCharacterStat("AtkBase", "攻击力", 110, False),)
            ),
        )
        original = _snapshot(
            hits=hits,
            baselines=original_baselines,
            replays=(_replay("hit1", 100),),
        )
        candidate = _snapshot(
            hits=hits,
            baselines=candidate_baselines,
            replays=(_replay("hit1", 120),),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(4, len(result.hits))
        self.assertEqual(
            ("complete", "unavailable", "unavailable", "unavailable"),
            tuple(hit.quantification.status for hit in result.hits),
        )
        self.assertEqual(60.0, result.hits[1].heuristic_projection_damage)
        self.assertIsNone(result.hits[1].candidate_damage)
        self.assertIsNone(result.hits[3].heuristic_projection_damage)
        self.assertIsNone(result.candidate_damage)
        self.assertAlmostEqual(270.0, result.known_projection_damage)
        self.assertAlmostEqual(280.0, result.heuristic_projection_damage)
        self.assertAlmostEqual(100.0, result.structured_damage)
        self.assertAlmostEqual(40.0, result.structured_percent)
        self.assertAlmostEqual(8.0, result.known_gain_percent)
        self.assertIsNone(result.gain_percent)
        self.assertEqual("partial", result.quantification.status)
        self.assertEqual(3, len(result.roles))
        self.assertEqual(3, len(result.composition.roles))
        timeline = BattleBuildTimelineProjectionService.project(original, result)
        unavailable_names = {
            hit.damage_name for hit in timeline.hits if hit.event_id in {"hit2", "hit3", "hit4"}
        }
        self.assertTrue(all("原轴占位·未量化" in name for name in unavailable_names))

    def test_comparison_rejects_different_ranges(self) -> None:
        hits = (_hit("hit1", character_id=1, character_name="甲", skill_name="A", damage=10),)
        original = _snapshot(hits=hits, baselines=(), replays=())
        candidate = _snapshot(hits=hits, baselines=(), replays=())
        candidate = replace(candidate, range_end_us=9_000_000)

        with self.assertRaisesRegex(ValueError, "同一分析时段"):
            BattleBuildCounterfactualService.compare(
                original=original,
                candidate=candidate,
            )

    def test_attributed_max_hp_damage_follows_linked_nightmare_hit(self) -> None:
        hit = _hit(
            "hit1",
            character_id=1004,
            character_name="安魂曲",
            skill_name="噩梦",
            damage=100,
        )
        hit = replace(
            hit,
            gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage",
        )
        original = _snapshot(
            hits=(hit,),
            baselines=(),
            replays=(_replay("hit1", 100),),
        )
        original = replace(
            original,
            effective_damage=130,
            effective_dps=13,
            max_hp_events=(_vital_event(
                "vital1",
                character_id=1004,
                character_name="安魂曲",
                source_skill_name="噩梦",
                damage=30,
                evidence_event_ids=("hit1",),
            ),),
            roles=(replace(
                original.roles[0],
                damage=130,
                dps=13,
                max_hp_reduction_damage=30,
                max_hp_reduction_events=1,
            ),),
        )
        candidate = _snapshot(
            hits=(hit,),
            baselines=(),
            replays=(_replay("hit1", 120),),
        )
        candidate = replace(candidate, max_hp_events=original.max_hp_events)

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertAlmostEqual(156.0, result.candidate_damage)
        self.assertAlmostEqual(156.0, result.roles[0].candidate_damage)
        self.assertAlmostEqual(
            36.0,
            result.roles[0].candidate_damage - 120.0,
        )
        self.assertEqual(
            "linked_source_hit_ratio_observed_anchor",
            result.vital_events[0].quantification.method,
        )
        self.assertEqual((1000.0, 1000.0, 36.0), result.vital_events[0].candidate_state)

    def test_enabling_lacrimosa_effect_five_adds_expected_settlement(self) -> None:
        hit = replace(
            _hit(
                "hit1",
                character_id=1004,
                character_name="安魂曲",
                skill_name="噩梦",
                damage=100,
            ),
            gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage",
        )
        original = _snapshot(
            hits=(hit,), baselines=(), replays=(_replay("hit1", 100),),
        )
        candidate = _snapshot(
            hits=(hit,), baselines=(), replays=(_replay("hit1", 100),),
        )
        estimate = replace(
            _vital_event(
                "max-hp-estimate:target:1",
                character_id=1004,
                character_name="安魂曲",
                source_skill_name="噩梦",
                damage=100,
                evidence_event_ids=("hit1",),
                mechanic_kind="lacrimosa_nightmare_awaken_5_estimated",
            ),
            included_in_effective_damage=False,
            evidence_kind="description_estimated",
            inference_basis="噩梦伤害 × 200% × 50% 生命比例期望",
        )
        candidate = replace(candidate, estimated_max_hp_events=(estimate,))

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(200.0, result.candidate_damage)
        self.assertEqual(200.0, result.roles[0].candidate_damage)
        self.assertEqual(100.0, result.vital_events[0].candidate_damage)
        self.assertEqual(
            "mechanic_enabled_expected_hp_ratio",
            result.vital_events[0].quantification.method,
        )
        projected = BattleBuildTimelineProjectionService.project(candidate, result)
        self.assertEqual(100.0, projected.max_hp_reduction_damage)
        self.assertEqual(200.0, projected.effective_damage)
        self.assertTrue(projected.max_hp_events[0].included_in_effective_damage)

    def test_fadia_max_hp_damage_follows_source_current_max_hp(self) -> None:
        hit = _hit(
            "hit1",
            character_id=1039,
            character_name="法帝娅",
            skill_name="罪感熔炉",
            damage=100,
        )
        original = _snapshot(
            hits=(hit,),
            baselines=(BattleCharacterBaseline(
                1039,
                "法帝娅",
                "original",
                (),
                inherent_hp=10_000,
                source_max_hp=20_000,
            ),),
            replays=(_replay("hit1", 100),),
        )
        event = _vital_event(
            "vital1",
            character_id=1039,
            character_name="法帝娅",
            source_skill_name="罪感熔炉",
            damage=200,
            evidence_event_ids=("hit1",),
            mechanic_kind="fadia_dark_star_max_hp_transfer",
        )
        original = replace(
            original,
            effective_damage=300,
            effective_dps=30,
            max_hp_events=(event,),
            roles=(replace(
                original.roles[0],
                damage=300,
                dps=30,
                max_hp_reduction_damage=200,
                max_hp_reduction_events=1,
            ),),
        )
        candidate = _snapshot(
            hits=(hit,),
            baselines=(BattleCharacterBaseline(
                1039,
                "法帝娅",
                "candidate",
                (),
                inherent_hp=10_000,
                source_max_hp=24_000,
            ),),
            replays=(_replay("hit1", 100),),
        )
        candidate = replace(candidate, max_hp_events=(event,))

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(340.0, result.candidate_damage)
        self.assertEqual(240.0, result.vital_events[0].candidate_damage)
        self.assertEqual(
            "fadia_source_max_hp_ratio",
            result.vital_events[0].quantification.method,
        )

    def test_adjusted_timeline_changes_hit_group_and_action_damage_only(self) -> None:
        hits = (
            _hit("hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100),
            _hit("hit2", character_id=1, character_name="甲", skill_name="技能甲", damage=50),
        )
        original = _snapshot(
            hits=hits,
            baselines=(),
            replays=(_replay("hit1", 100),),
        )
        candidate = _snapshot(
            hits=hits,
            baselines=(),
            replays=(_replay("hit1", 120),),
        )
        original = replace(
            original,
            effective_damage=180,
            effective_dps=18,
            max_hp_events=(_vital_event(
                "vital1",
                character_id=1,
                character_name="甲",
                source_skill_name="技能甲",
                damage=30,
                evidence_event_ids=("hit1",),
            ),),
            roles=(replace(
                original.roles[0],
                damage=180,
                dps=18,
                max_hp_reduction_damage=30,
                max_hp_reduction_events=1,
            ),),
            inferred_actions=(BattleInferredAction(
                action_id="action:1",
                character_id=1,
                character_name="甲",
                action_name="技能甲",
                input_kind="skill",
                input_sequence="E",
                start_us=0,
                end_us=500_000,
                hits=2,
                damage=150,
                identity_confidence="中",
                timing_confidence="中",
                inference_basis="fixture",
                evidence_event_ids=("hit1", "hit2"),
                gameplay_effect_ids=(),
            ),),
            timeline_damage_groups=(
                BattleTimelineDamageGroup(
                    group_id="group:skill",
                    character_id=1,
                    character_name="甲",
                    direction="outgoing",
                    channel_key="direct",
                    channel_label="直伤",
                    damage_name="技能甲",
                    source_skill_name="技能甲",
                    ability_id="技能甲",
                    start_us=100_000,
                    end_us=200_001,
                    hits=2,
                    damage=150,
                    evidence_event_ids=("hit1", "hit2"),
                ),
                BattleTimelineDamageGroup(
                    group_id="vital:vital1",
                    character_id=1,
                    character_name="甲",
                    direction="outgoing",
                    channel_key="max_hp_reduction",
                    channel_label="生命上限结算",
                    damage_name="生命上限结算",
                    source_skill_name="技能甲",
                    ability_id="fixture",
                    start_us=300_000,
                    end_us=300_001,
                    hits=1,
                    damage=30,
                    evidence_event_ids=(),
                ),
            ),
        )
        candidate = replace(candidate, max_hp_events=original.max_hp_events)
        comparison = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        projected = BattleBuildTimelineProjectionService.project(
            original,
            comparison,
        )

        self.assertEqual((120.0, 50.0), tuple(hit.damage for hit in projected.hits))
        self.assertEqual(170.0, projected.inferred_actions[0].damage)
        self.assertEqual(170.0, projected.timeline_damage_groups[0].damage)
        self.assertEqual(30.0, projected.timeline_damage_groups[1].damage)
        self.assertIn("候选未量化", projected.timeline_damage_groups[1].detail_lines[-2])
        self.assertEqual(30.0, projected.roles[0].max_hp_reduction_damage)
        self.assertEqual((100.0, 50.0), tuple(hit.damage for hit in original.hits))

    def test_target_sensitive_peer_does_not_cross_target(self) -> None:
        hit_a = _hit(
            "hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100,
        )
        hit_b = replace(
            _hit(
                "hit2", character_id=1, character_name="甲", skill_name="技能甲", damage=50,
            ),
            target_id="target-b",
        )
        original = _snapshot(hits=(hit_a, hit_b), baselines=(), replays=(_replay("hit1", 100),))
        candidate = _snapshot(hits=(hit_a, hit_b), baselines=(), replays=(_replay("hit1", 120),))

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual("unavailable", result.hits[1].quantification.status)
        self.assertIsNone(result.hits[1].heuristic_projection_damage)
        self.assertIsNone(result.hits[1].candidate_damage)

    def test_unknown_scaling_and_target_profile_remain_separate(self) -> None:
        hit = _hit(
            "hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100,
        )
        original_baseline = BattleCharacterBaseline(
            1,
            "甲",
            "original",
            (
                BattleCharacterStat("AtkBase", "攻击力", 100, False),
                BattleCharacterStat("AtkUp", "攻击力提升", 0, True),
                BattleCharacterStat("DefIgnore", "防御忽略", 0, True),
            ),
        )
        original = _snapshot(
            hits=(hit,), baselines=(original_baseline,), replays=(),
        )
        attack_candidate = replace(
            original_baseline,
            stats=tuple(
                replace(row, value=0.1) if row.property_id == "AtkUp" else row
                for row in original_baseline.stats
            ),
        )
        defense_candidate = replace(
            original_baseline,
            stats=tuple(
                replace(row, value=0.1) if row.property_id == "DefIgnore" else row
                for row in original_baseline.stats
            ),
        )

        attack_result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=_snapshot(
                hits=(hit,), baselines=(attack_candidate,), replays=(),
            ),
        )
        defense_result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=_snapshot(
                hits=(hit,), baselines=(defense_candidate,), replays=(),
            ),
        )

        self.assertEqual("unavailable", attack_result.hits[0].quantification.status)
        self.assertIsNone(attack_result.hits[0].candidate_damage)
        self.assertEqual("unavailable", defense_result.hits[0].quantification.status)
        self.assertIsNone(defense_result.hits[0].known_projection_damage)

        profiled_original = replace(
            original,
            target_condition=BattleTargetCondition(
                target_name="身份未知",
                enemy_level=80,
                scene="outer_realm",
                defense_reduction=0.0,
                vulnerability=0.0,
                resistances=(("chaos", 0.2),),
                enemy_defense_base=600,
                resolved_monster_id="",
            ),
        )
        profiled_result = BattleBuildCounterfactualService.compare(
            original=profiled_original,
            candidate=replace(
                profiled_original,
                baselines=(defense_candidate,),
            ),
        )
        self.assertEqual("complete", profiled_result.hits[0].quantification.status)
        self.assertIsNotNone(profiled_result.hits[0].candidate_damage)


if __name__ == "__main__":
    unittest.main()
