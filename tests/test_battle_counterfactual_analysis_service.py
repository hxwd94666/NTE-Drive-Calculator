# 覆盖统一分析时段、主/追加拆分和真实逐击加权边际。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleBuffModifierEvidence
from src.services.battle_action_inference_service import (
    BattleActionAnimationCandidate,
)
from src.services.battle_buff_inference_service import BattleStaticBuffRule
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)


def _build() -> dict:
    return {
        "characters": [
            {
                "character_id": 1072,
                "observed_name": "灵可",
                "stat_snapshot_source": "frozen_v25",
                "stats": [
                    {"source_group": "resolved", "property_id": "AtkBase", "display_name": "基础攻击力", "value": 1000, "is_percent": False},
                    {"source_group": "resolved", "property_id": "AtkUp", "display_name": "攻击力提升", "value": 0.5, "is_percent": True},
                    {"source_group": "resolved", "property_id": "AtkAdd", "display_name": "固定攻击力", "value": 100, "is_percent": False},
                    {"source_group": "resolved", "property_id": "CritBase", "display_name": "暴击率", "value": 0.5, "is_percent": True},
                    {"source_group": "resolved", "property_id": "CritDamageBase", "display_name": "暴击伤害", "value": 1.0, "is_percent": True},
                    {"source_group": "resolved", "property_id": "DamageUpGeneralBase", "display_name": "通用伤害增强", "value": 0.2, "is_percent": True},
                    {"source_group": "resolved", "property_id": "DamageUpNatureBase", "display_name": "自然属性伤害增强", "value": 0.25, "is_percent": True},
                    {"source_group": "resolved", "property_id": "DefIgnore", "display_name": "防御忽略", "value": 0.10, "is_percent": True},
                    {"source_group": "resolved", "property_id": "MagBase", "display_name": "环合强度", "value": 100, "is_percent": False},
                ],
            }
        ]
    }


def _evidence() -> dict:
    return {
        "axis_complete": True,
        "hits": [
            {
                "sequence_text": "1",
                "sequence_order": 1,
                "relative_time_us": 1_000_000,
                "character_id": 1072,
                "character_name": "灵可",
                "direction": "outgoing",
                "damage": 1000,
                "follow_up_damage": 200,
                "ability_name": "普通攻击",
                "damage_name": "第一段",
                "damage_component": "skill",
                "attack_type": "normal",
                "damage_attribute": "nature",
                "follow_up_damage_name": "覆纹追加攻击",
                "follow_up_damage_component": "reaction",
                "follow_up_attack_type": "follow_up",
                "follow_up_damage_attribute": "nature",
                "follow_up_labels": ["覆纹"],
                "target_id": "monster-1",
                "target_name": "训练目标",
                "target_hp_before": 5000,
                "target_hp_after": 3800,
                "target_max_hp": 5000,
            },
            {
                "sequence_text": "2",
                "sequence_order": 2,
                "relative_time_us": 3_000_000,
                "character_id": 1072,
                "character_name": "灵可",
                "direction": "outgoing",
                "damage": 300,
                "follow_up_damage": 0,
                "ability_name": "环合",
                "damage_name": "黯星",
                "damage_component": "reaction",
                "attack_type": "reaction",
                "damage_attribute": "psychically",
                "follow_up_labels": [],
                "target_id": "monster-1",
                "target_name": "训练目标",
            },
        ],
        "time_stop_intervals": [],
    }


def _attack_buff_rule(event_type: str) -> BattleStaticBuffRule:
    return BattleStaticBuffRule(
        rule_id=f"test:{event_type}",
        source_effect_definition_id="test:attack-buff",
        source_kind="test",
        source_character_id=1072,
        source_character_name="灵可",
        source_asset_path="/Game/Test/BuffSource",
        target_asset_path="/Game/Test/AttackBuff",
        target_name="测试攻击 Buff",
        target_scope="self",
        event_type=event_type,
        effect_type="ADD",
        duration_policy="HasDuration",
        duration_seconds=10.0,
        stack_count=1,
        modifiers=(BattleBuffModifierEvidence(
            property_id="AtkUp",
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="ScalableFloat",
            magnitude_value=0.5,
            calculation_asset_path="",
            value_confidence="高",
        ),),
    )


class BattleCounterfactualAnalysisServiceTests(unittest.TestCase):
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
        self.assertEqual("battle-action-window-v12", result.action_inference_version)
        self.assertEqual("battle-unified-timeline-v5", result.timeline_projection_version)
        self.assertEqual("battle-counterfactual-v20", result.formula_model_version)

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
            "battle-buff-attribute-v20",
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

    def test_max_hp_settlement_is_not_reported_as_hp_overlap_correction(self) -> None:
        evidence = {
            "axis_complete": True,
            "time_stop_intervals": [],
            "hits": [
                {
                    "sequence_order": 1,
                    "relative_time_us": 1_000_000,
                    "character_id": 1004,
                    "character_name": "安魂曲",
                    "direction": "outgoing",
                    "damage": 250.0,
                    "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage_LV6",
                    "target_id": "boss-1",
                    "target_name": "测试目标",
                    "target_hp_before": 720.0,
                    "target_hp_after": 470.0,
                    "target_max_hp": 1_000.0,
                },
                {
                    "sequence_order": 2,
                    "relative_time_us": 1_100_000,
                    "character_id": 1036,
                    "character_name": "残虹",
                    "direction": "outgoing",
                    "damage": 1.0,
                    "gameplay_effect_name": "GE_Player_Zankou_Melee1_Damage",
                    "target_id": "boss-1",
                    "target_name": "测试目标",
                    "target_hp_before": 470.0,
                    "target_hp_after": 469.0,
                    "target_max_hp": 900.0,
                },
            ],
        }
        build = {"characters": [{
            "character_id": 1004,
            "profile": {
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": ["Effect5"],
            },
        }]}

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=build,
            capability_level="hit_axis",
        )

        self.assertEqual(251.0, analysis.total_damage)
        self.assertEqual(47.0, analysis.max_hp_reduction_damage)
        self.assertEqual(298.0, analysis.effective_damage)
        self.assertEqual(0.0, analysis.damage_correction_total)
        self.assertEqual(0.0, analysis.damage_overlap_correction_total)

    def test_description_estimate_is_visible_but_excluded_from_effective_damage(self) -> None:
        evidence = _evidence()
        hit = dict(evidence["hits"][0])
        hit.update(
            {
                "character_id": 1004,
                "character_name": "安魂曲",
                "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage",
                "damage": 100,
                "follow_up_damage": 0,
                "target_max_hp": 1_000,
                "target_hp_before": 500,
            }
        )
        evidence["hits"] = [hit]
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "observed_name": "安魂曲",
                    "profile": {
                        "awakening_selection_initialized": True,
                        "selected_awaken_effect_ids": ["Effect5"],
                    },
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

        self.assertEqual(100.0, analysis.total_damage)
        self.assertEqual(100.0, analysis.effective_damage)
        self.assertEqual(100.0, analysis.estimated_max_hp_reduction_damage)
        self.assertEqual(1, len(analysis.estimated_max_hp_events))
        self.assertEqual(
            100.0,
            analysis.targets[0].estimated_max_hp_reduction_damage,
        )


if __name__ == "__main__":
    unittest.main()
