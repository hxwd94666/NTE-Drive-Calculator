# 覆盖统一分析时段、主/追加拆分和真实逐击加权边际。
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.domain.battle_report import (
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleLinkoCoattackInference,
)
from src.services.battle_action_inference_service import (
    BattleActionAnimationCandidate,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_axis_hit_projection_service import (
    project_battle_axis_hits,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from tests.battle_counterfactual_fixtures import (
    attack_buff_rule as _attack_buff_rule,
    build_fixture as _build,
    evidence_fixture as _evidence,
)


class BattleCounterfactualAnalysisServiceTests(unittest.TestCase):
    def test_formal_damage_tags_own_dot_and_attachment_classification(self) -> None:
        def classification(
            damage_name: str = "伤害",
            *,
            ability_name: str = "",
            gameplay_effect_name: str = "",
            gameplay_tags: tuple[str, ...] = (),
            follow_up: bool = False,
        ) -> str:
            row = {
                "sequence_text": "1",
                "sequence_order": 1,
                "relative_time_us": 1,
                "character_id": 1072,
                "character_name": "灵可",
                "direction": "outgoing",
                "damage": 0.0 if follow_up else 1.0,
                "follow_up_damage": 1.0 if follow_up else 0.0,
                "ability_name": ability_name,
                "gameplay_effect_name": gameplay_effect_name,
                "damage_name": damage_name,
                "follow_up_damage_name": damage_name,
                "follow_up_labels": (damage_name,),
                "formal_gameplay_tags": gameplay_tags,
                "target_id": "target",
                "target_name": "目标",
            }
            return project_battle_axis_hits((row,))[0].classification

        self.assertEqual(
            "dot",
            classification(
                ability_name="GA_Mismo_UltraSkill",
                gameplay_effect_name="GE_Player_Mismo_UltraSkill_Damage",
                gameplay_tags=("State.Damage.Dot",),
            ),
        )
        self.assertEqual(
            "attachment",
            classification(
                ability_name="GA_Kuhara_Melee",
                gameplay_effect_name="GE_Player_Kuhara_Seed_Damage",
                gameplay_tags=("State.Damage.Attachment",),
            ),
        )
        self.assertEqual(
            "reaction",
            classification(
                "浊燃",
                ability_name="GA_Source_QTE",
                gameplay_effect_name="GE_Player_Mismo_UltraSkill_Damage",
                gameplay_tags=("State.Damage.Dot",),
                follow_up=True,
            ),
        )
        self.assertEqual(
            "reaction",
            classification(
                "浊燃",
                gameplay_effect_name="Buff_Reaction_5_new_1036",
                gameplay_tags=("State.Damage.Dot",),
            ),
        )
        self.assertEqual(
            "mechanic",
            classification(
                "摄食模式",
                gameplay_effect_name="GE_Player_Sagiri_Branch_Kill_Damage",
            ),
        )

    def test_range_uses_half_open_boundary_and_keeps_full_timeline(self) -> None:
        result = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
            requested_start_us=0,
            requested_end_us=3_000_000,
        )

        self.assertEqual(3, len(result.timeline_hits))
        self.assertEqual(2, len(result.hits))
        self.assertEqual(1200.0, result.total_damage)
        self.assertEqual("weave", result.hits[1].classification)
        self.assertEqual(1, len(result.targets))
        self.assertEqual(1, len(result.inferred_actions))
        self.assertEqual("battle-action-window-v15", result.action_inference_version)
        self.assertEqual("battle-unified-timeline-v5", result.timeline_projection_version)
        self.assertEqual("battle-counterfactual-v24", result.formula_model_version)

    def test_linko_formula_inference_is_derived_before_range_projection(self) -> None:
        inference = BattleLinkoCoattackInference(
            event_id="1:primary",
            trigger_kind="skill",
            action_character_id=1036,
            definition_owner_character_id=1036,
            panel_character_id=1072,
            skill_level_character_id=1072,
            skill_level_ability_id="GA_Radio072_QTE",
            damage_attribute_source_character_id=1036,
            damage_attribute="incantation",
            damage_attribute_source="initiator_character_static_profile",
            confidence="中",
            inference_basis="测试派生入口",
            evidence_event_ids=("1:primary",),
        )
        with patch(
            "src.services.battle_counterfactual_analysis_service."
            "BattleLinkoCoattackInferenceService.infer",
            return_value=(inference,),
        ) as infer:
            result = BattleCounterfactualAnalysisService.analyze(
                battle_record_id=7,
                evidence=_evidence(),
                build=_build(),
                capability_level="hit_axis",
                requested_start_us=2_000_000,
                character_elements={1036: "CHARACTER_ELEMENT_TYPE_INCANTATION"},
            )

        self.assertEqual((inference,), result.linko_coattack_inferences)
        self.assertEqual(
            "linko-coattack-v1",
            result.linko_coattack_inference_version,
        )
        self.assertNotIn("1:primary", {hit.event_id for hit in result.hits})
        self.assertIn("1:primary", {hit.event_id for hit in result.timeline_hits})
        self.assertEqual(
            {1036: "CHARACTER_ELEMENT_TYPE_INCANTATION"},
            infer.call_args.kwargs["character_elements"],
        )

    def test_follow_up_uses_its_own_formal_timestamp(self) -> None:
        evidence = _evidence()
        evidence["hits"][0]["timestamp_unix_us"] = 101_000_000
        evidence["hits"][0]["follow_up_timestamp_unix_us"] = 102_500_000

        result = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
        )

        follow_up = next(
            row for row in result.timeline_hits if row.event_id == "1:follow_up"
        )
        self.assertEqual(2_500_000, follow_up.relative_time_us)
        self.assertEqual(1, len(result.inferred_inputs))
        self.assertEqual(3, len(result.timeline_damage_groups))

    def test_typed_reaction_follow_up_is_not_overridden_by_source_qte(self) -> None:
        evidence = _evidence()
        source = evidence["hits"][0]
        source.update({
            "ability_name": "GA_Lingke_QTE",
            "gameplay_effect_name": "GE_Player_Lingke_QTE1_Damage",
            "follow_up_damage_name": "黯星",
            "follow_up_damage_component": "follow_up",
            "follow_up_attack_type": "黯星",
            "follow_up_damage_attribute": "psychically",
            "follow_up_labels": ["黯星"],
        })

        result = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
        )

        follow_up = next(
            row for row in result.timeline_hits if row.event_id == "1:follow_up"
        )
        self.assertEqual("reaction", follow_up.classification)
        self.assertTrue(any(
            group.channel_key == "reaction_nova"
            for group in result.timeline_damage_groups
        ))

    def test_dynamic_attack_buff_is_retained_when_scaling_is_unknown(self) -> None:
        plain = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
        )
        buffed = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
            buff_rules=(_attack_buff_rule("ABILITY_EVENT|A|GA_Test|"),),
        )
        plain_margin = BattleMarginalCalculationService.calculate(
            analysis=plain,
            character_id=1072,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]
        buffed_margin = BattleMarginalCalculationService.calculate(
            analysis=buffed,
            character_id=1072,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]

        self.assertEqual(plain_margin.baseline_damage, buffed_margin.baseline_damage)
        self.assertEqual("unavailable", plain_margin.quantification.status)
        self.assertEqual("unavailable", buffed_margin.quantification.status)
        self.assertIsNone(buffed_margin.full_role_gain_percent)
        self.assertIn("已将 1 个动态 Buff 区间", buffed_margin.assumption)
        self.assertEqual(
            "battle-buff-attribute-v22",
            buffed.buff_attribute_projection_version,
        )

    def test_static_equipped_buff_is_retained_when_scaling_is_unknown(self) -> None:
        plain = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
        )
        equipped = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
            buff_rules=(_attack_buff_rule("STATIC_EQUIPPED_SOURCE"),),
        )
        plain_margin = BattleMarginalCalculationService.calculate(
            analysis=plain,
            character_id=1072,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]
        equipped_margin = BattleMarginalCalculationService.calculate(
            analysis=equipped,
            character_id=1072,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]

        self.assertEqual("unavailable", plain_margin.quantification.status)
        self.assertEqual("unavailable", equipped_margin.quantification.status)
        self.assertIsNone(equipped_margin.full_role_gain_percent)
        self.assertIn("已将 1 个动态 Buff 区间", equipped_margin.assumption)

    def test_defense_ignore_requires_user_confirmed_target_condition(self) -> None:
        unconfirmed = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
        )
        confirmed = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
            target_condition={
                "confirmed": True,
                "target_name": "墨菲斯托",
                "enemy_level": 90,
                "scene": "outer_realm",
                "defense_reduction": 0.0,
                "vulnerability": 0.0,
                "resistances": {
                    "chaos": 0.2,
                    "cosmos": 0.5,
                    "incantation": 0.2,
                    "lakshana": 0.5,
                    "nature": 0.2,
                    "psyche": 0.5,
                    "psychically": 0.2,
                },
            },
        )
        missing = BattleMarginalCalculationService.calculate(
            analysis=unconfirmed,
            character_id=1072,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]
        available = BattleMarginalCalculationService.calculate(
            analysis=confirmed,
            character_id=1072,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]

        self.assertEqual("unavailable", missing.quantification.status)
        self.assertIsNone(missing.full_role_gain_percent)
        self.assertGreater(available.full_role_gain_percent, 0.0)
        self.assertEqual("user_confirmed", confirmed.target_condition.source_kind)

    def test_selected_witch_buff_projects_over_the_whole_battle(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
            target_condition={
                "confirmed": True,
                "target_name": "训练目标",
                "enemy_level": 90,
                "scene": "outer_realm",
                "resistances": {key: 0.2 for key in (
                    "chaos", "cosmos", "incantation", "lakshana",
                    "nature", "psyche", "psychically",
                )},
                "witch_buff_id": "Buff_Divination_DamageUpGeneralBase",
                "witch_buff_name_zh": "通用伤害提升15%",
                "witch_buff_property_id": "DamageUpGeneralBase",
                "witch_buff_value": 0.15,
                "witch_buff_is_percent": True,
            },
        )

        interval = next(
            row for row in analysis.timeline_buff_intervals
            if row.source_character_name == "魔女赐福"
        )
        projection = BattleBuffAttributeProjectionService.project_hit(
            analysis.timeline_hits[0],
            analysis.timeline_buff_intervals,
        )

        self.assertEqual(0, interval.start_us)
        self.assertEqual(analysis.battle_end_us, interval.end_us)
        modifier = next(
            row for row in projection.modifiers
            if row.property_id == "DamageUpGeneralBase"
        )
        self.assertEqual(0.15, modifier.additive_value)

    def test_element_damage_only_replays_hits_of_the_same_attribute(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=1072,
            edited_values={},
            units={"DamageUpNatureBase": 0.10},
        )[0]

        nature_damage = sum(
            hit.damage
            for hit in analysis.hits
            if hit.character_id == 1072
            and hit.damage_attribute == "nature"
            and hit.classification in {"direct", "direct_follow_up", "weave"}
        )
        self.assertEqual(
            nature_damage,
            result.quantification.fully_quantified_damage,
        )
        self.assertLess(
            result.quantification.fully_quantified_damage,
            result.baseline_damage,
        )

    def test_ring_strength_only_replays_identified_weave_damage(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
        )
        weave = next(hit for hit in analysis.hits if hit.classification == "weave")
        strength = 1.0 + 0.20 * 100.0 / (100.0 + 180.0)
        followup = 1.30 * strength - 1.0
        analysis = replace(
            analysis,
            hit_replays=(BattleHitReplayResult(
                event_id=weave.event_id,
                observed_damage=weave.damage,
                non_critical_damage=weave.damage,
                critical_damage=None,
                selected_damage=weave.damage,
                selected_error_percent=0.0,
                critical_state="not_applicable",
                confidence="高",
                critical_policy="disabled",
                factors=(
                    BattleHitReplayFactor(
                        factor_id="weave_strength",
                        label="覆纹环合强度区",
                        value=strength,
                        evidence_basis="正式覆纹公式",
                    ),
                    BattleHitReplayFactor(
                        factor_id="weave_followup",
                        label="覆纹追加倍率",
                        value=followup,
                        evidence_basis="灵可弱点感应 30% 分支",
                    ),
                ),
                formula_type="覆纹",
            ),),
        )
        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=1072,
            edited_values={},
            units={"MagBase": 10.0},
        )[0]

        self.assertEqual(200.0, result.quantification.fully_quantified_damage)
        self.assertAlmostEqual(
            200 / 1500 * 100,
            result.quantification.fully_quantified_damage
            / result.quantification.basis_damage
            * 100,
        )
        self.assertGreater(result.full_role_gain_percent, 0.0)

    def test_qte_own_hit_is_direct_even_when_attack_type_names_a_reaction(self) -> None:
        evidence = _evidence()
        qte_hit = dict(evidence["hits"][0])
        qte_hit.update(
            {
                "follow_up_damage": 0,
                "ability_name": "GA_Lacrimosa_QTE",
                "gameplay_effect_name": "GE_Player_Lacrimosa_QTE1_Damage",
                "damage_display_name": "援护技伤害",
                "attack_type": "环合·浊燃",
            }
        )
        evidence["hits"] = [qte_hit]

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
        )

        self.assertEqual("direct", analysis.hits[0].classification)
        self.assertEqual("direct", analysis.timeline_damage_groups[0].channel_key)

    def test_time_stop_offsets_are_projected_from_record_evidence(self) -> None:
        evidence = _evidence()
        evidence["time_stop_intervals"] = [
            {
                "raw_interval": {
                    "start_offset_seconds": 1.25,
                    "end_offset_seconds": 2.75,
                }
            }
        ]

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
        )

        self.assertEqual(((1_250_000, 2_750_000),), analysis.time_stop_intervals)

    def test_q_action_is_anchored_when_analysis_loads_time_stop_evidence(self) -> None:
        evidence = _evidence()
        q_hit = dict(evidence["hits"][0])
        q_hit.update(
            {
                "relative_time_us": 3_000_000,
                "follow_up_damage": 0,
                "ability_name": "GA_Lingke_UltraSkill",
                "gameplay_effect_name": "GE_Player_Lingke_UltraSkill1_Damage",
                "damage_display_name": "极轨终结伤害",
                "attack_type": "Q技能",
            }
        )
        evidence["hits"] = [q_hit]
        evidence["time_stop_intervals"] = [
            {
                "raw_interval": {
                    "start_offset_seconds": 1.0,
                    "end_offset_seconds": 4.0,
                }
            }
        ]

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
        )

        self.assertEqual("Q", analysis.inferred_actions[0].input_kind)
        self.assertEqual(1_000_000, analysis.inferred_actions[0].start_us)
        self.assertEqual(4_000_000, analysis.inferred_actions[0].end_us)

    def test_animation_tail_extends_timeline_without_changing_damage_range(self) -> None:
        evidence = _evidence()
        hit = dict(evidence["hits"][0])
        hit.update(
            {
                "follow_up_damage": 0,
                "ability_name": "GA_Lingke_Skill",
                "gameplay_effect_name": "GE_Player_Lingke_Skill1_Damage",
                "attack_type": "E技能",
            }
        )
        evidence["hits"] = [hit]

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Lingke_Skill",
                    selector_key="Skill1",
                    montage_asset_path="/Game/Animation/Lingke_Skill",
                    effect_hit_offsets_us=(
                        ("GE_Player_Lingke_Skill1_Damage", (250_000,)),
                    ),
                    trigger_end_offsets_us=(900_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(1_200_000,),
                    duration_us=1_200_000,
                ),
            ),
        )

        self.assertEqual(1_000_001, analysis.battle_end_us)
        self.assertEqual(1_650_000, analysis.timeline_end_us)
        self.assertEqual(1_000_001, analysis.range_end_us)

    def test_max_hp_settlement_is_added_to_effective_damage_without_rewriting_hits(self) -> None:
        evidence = _evidence()
        first = dict(evidence["hits"][0])
        first.update(
            {
                "character_id": 1039,
                "character_name": "法帝娅",
                "gameplay_effect_name": "Buff_Reaction_4_new",
                "target_max_hp": 5_000,
                "target_hp_before": 4_000,
            }
        )
        second = dict(first)
        second.update(
            {
                "sequence_text": "2",
                "sequence_order": 2,
                "relative_time_us": 2_000_000,
                "damage": 100,
                "follow_up_damage": 0,
                "target_max_hp": 4_500,
                "target_hp_before": 3_600,
            }
        )
        evidence["hits"] = [first, second]
        build = {
            "characters": [
                {
                    "character_id": 1039,
                    "observed_name": "法帝娅",
                    "breakthrough_stage": 2,
                    "stats": [],
                }
            ]
        }

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=build,
            capability_level="hit_axis",
        )

        self.assertEqual(1_300.0, analysis.total_damage)
        self.assertEqual(380.0, analysis.max_hp_reduction_damage)
        self.assertEqual(1_680.0, analysis.effective_damage)
        self.assertEqual(1, len(analysis.max_hp_events))
        self.assertEqual(1, len(analysis.timeline_max_hp_events))
        self.assertEqual(1, analysis.roles[0].max_hp_reduction_events)
        self.assertEqual(380.0, analysis.roles[0].max_hp_reduction_damage)
        self.assertEqual(1_680.0, analysis.roles[0].damage)
        self.assertEqual(1_680.0, analysis.targets[0].effective_damage)
        self.assertTrue(
            any(
                group.channel_key == "max_hp_reduction"
                for group in analysis.timeline_damage_groups
            )
        )

    def test_missing_target_identity_uses_one_consistent_single_target_fallback(self) -> None:
        evidence = _evidence()
        rows = []
        for source in evidence["hits"]:
            row = dict(source)
            row.pop("target_id", None)
            row.pop("target_name", None)
            rows.append(row)
        evidence["hits"] = rows

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
        )

        self.assertEqual(1, len(analysis.targets))
        self.assertEqual("unknown", analysis.targets[0].target_id)
        self.assertEqual("未知目标", analysis.targets[0].target_name)
        self.assertEqual("single_target_assumed", analysis.target_identity_mode)

    def test_one_confirmed_target_binds_missing_outgoing_target_identity(self) -> None:
        evidence = _evidence()
        evidence["hits"] = [
            {
                key: value
                for key, value in source.items()
                if key not in {"target_id", "target_name"}
            }
            for source in evidence["hits"]
        ]

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=_build(),
            capability_level="hit_axis",
            target_condition={
                "confirmed": True,
                "target_name": "墨菲斯托",
                "enemy_level": 90,
                "scene": "outer_realm",
                "resistances": {key: 0.2 for key in (
                    "chaos", "cosmos", "incantation", "lakshana",
                    "nature", "psyche", "psychically",
                )},
                "selected_target_ids": ["boss_05_BP_DiyBoss"],
                "primary_target_id": "boss_05_BP_DiyBoss",
            },
        )

        self.assertEqual("user_confirmed_single_target", analysis.target_identity_mode)
        self.assertEqual(1, len(analysis.targets))
        self.assertEqual("boss_05_BP_DiyBoss", analysis.targets[0].target_id)
        self.assertEqual("墨菲斯托", analysis.targets[0].target_name)
        self.assertTrue(all(
            hit.target_id == "boss_05_BP_DiyBoss"
            for hit in analysis.timeline_hits
            if hit.direction == "outgoing"
        ))

    def test_confirmed_single_target_normalizes_fadia_overlap_for_all_summaries(
        self,
    ) -> None:
        evidence = {
            "axis_complete": True,
            "time_stop_intervals": [],
            "hits": [
                {
                    "sequence_text": "77",
                    "sequence_order": 77,
                    "relative_time_us": 19_890_185,
                    "character_id": 1039,
                    "character_name": "法帝娅",
                    "direction": "outgoing",
                    "damage": 57_600.0,
                    "gameplay_effect_name": "Buff_Reaction_4_new",
                    "target_hp_before": 4_983_823.5,
                    "target_hp_after": 4_926_223.5,
                    "target_max_hp": 5_373_245.0,
                },
                {
                    "sequence_text": "78",
                    "sequence_order": 78,
                    "relative_time_us": 22_523_896,
                    "character_id": 1003,
                    "character_name": "早雾",
                    "direction": "outgoing",
                    "damage": 60_756.0,
                    "ability_name": "GA_Sagiri_UltraSkill",
                    "gameplay_effect_name": "GE_Player_Sagiri_UltraSkill1_Damage",
                    "target_hp_before": 4_983_823.5,
                    "target_hp_after": 4_923_067.5,
                    "target_max_hp": 5_373_245.0,
                },
            ],
        }
        condition = {
            "confirmed": True,
            "target_name": "墨菲斯托",
            "enemy_level": 90,
            "scene": "feast",
            "resistances": {},
            "selected_target_ids": ["boss_05_BP_DiyBoss"],
            "primary_target_id": "boss_05_BP_DiyBoss",
        }

        unconfirmed = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build={"characters": []},
            capability_level="hit_axis",
        )
        confirmed = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build={"characters": []},
            capability_level="hit_axis",
            target_condition=condition,
        )

        self.assertEqual(118_356.0, unconfirmed.total_damage)
        self.assertEqual(118_356.0, confirmed.total_damage)
        self.assertEqual(0.0, confirmed.damage_correction_total)
        self.assertEqual(0.0, confirmed.timeline_damage_correction_total)
        self.assertEqual(57_600.0, confirmed.damage_overlap_correction_total)
        self.assertEqual(
            57_600.0,
            confirmed.timeline_damage_overlap_correction_total,
        )
        sagiri = next(hit for hit in confirmed.hits if hit.character_id == 1003)
        self.assertEqual(60_756.0, sagiri.raw_damage)
        self.assertEqual(60_756.0, sagiri.damage)
        sagiri_role = next(row for row in confirmed.roles if row.character_id == 1003)
        self.assertEqual(60_756.0, sagiri_role.damage)

if __name__ == "__main__":
    unittest.main()
