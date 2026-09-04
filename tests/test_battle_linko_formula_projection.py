# 验证灵可同频合击的混合公式归属、覆纹增伤与精确调频状态投影。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleLinkoCoattackInference,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
    passive_requirement_applies,
)
from src.services.battle_formula_hit_projection_service import (
    project_formula_hit,
    project_replay_formula_context,
)
from src.services.battle_hit_replay_explanation_service import (
    BattleHitReplayExplanationService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_hit_projection_preparation_service import (
    BattleHitProjectionPreparationService,
)
from src.services.battle_linko_coattack_buff_service import (
    BattleLinkoCoattackBuffService,
)
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
)


def _hit(
    event_id: str,
    *,
    character_id: int = 1036,
    is_follow_up: bool = False,
    classification: str = "direct",
    relative_time_us: int = 1_000_000,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=int(event_id.split(":", 1)[0]),
        relative_time_us=relative_time_us,
        character_id=character_id,
        character_name="残虹",
        skill_name="援护技",
        damage_name="援护伤害",
        damage_component="follow_up" if is_follow_up else "skill",
        attack_type="follow_up" if is_follow_up else "QTE",
        damage_attribute="incantation",
        target_id="target-1",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=is_follow_up,
        classification=classification,
        ability_id="GA_Zankou_QTE",
        gameplay_effect_id="GE_Player_Zankou_QTE_Damage",
    )


def _inference(event_id: str, *, time_us: int = 1_000_000):
    return BattleLinkoCoattackInference(
        event_id=event_id,
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
        inference_basis="完整灵可 E 与唯一队友 QTE 的版本化推论",
        evidence_event_ids=("linko-e", event_id),
        trigger_action_id="action:linko:e",
        qte_action_id=f"action:qte:{time_us}",
        raw_gap_us=1_000_000,
        active_gap_us=1_000_000,
    )


def _build(*, stage: int = 4) -> dict:
    return {
        "characters": [
            {
                "character_id": 1036,
                "observed_name": "残虹",
                "breakthrough_stage": 4,
                "character_level": 80,
                "skills": [{"skill_id": "GA_Zankou_QTE", "skill_level": 10}],
                "profile": {},
            },
            {
                "character_id": 1072,
                "observed_name": "灵可",
                "breakthrough_stage": stage,
                "character_level": 80,
                "skills": [{"skill_id": "GA_Radio072_QTE", "skill_level": 6}],
                "profile": {},
            },
        ]
    }


class BattleLinkoMixedFormulaEvidenceTests(unittest.TestCase):
    def test_inferred_qte_keeps_actor_definition_but_uses_linko_panel_and_level(self):
        primary = _hit("1:primary")
        weave = _hit("1:follow_up", is_follow_up=True, classification="weave")
        analysis = SimpleNamespace(
            hits=(primary,),
            timeline_hits=(primary, weave),
            inferred_actions=(),
            time_stop_intervals=(),
            linko_coattack_inferences=(_inference(primary.event_id),),
        )

        class Dao:
            @staticmethod
            def get_skill_damage(_damage_id: str):
                return {
                    "ability_id": "GA_Zankou_QTE",
                    "damage_type": "incantation",
                    "damage_source_category": "NORMAL",
                    "fixed_crit_rate": 0.0,
                    "atk_rate_base": tuple(float(index) for index in range(1, 11)),
                    "def_rate_base": (),
                    "hp_rate_base": (),
                }

            @staticmethod
            def get_reaction_damage_curve(_damage_id: str):
                return None

            @staticmethod
            def list_character_awaken_effects(_character_id: int):
                return ()

            @staticmethod
            def list_skill_damage_owner_character_ids(_damage_id: str):
                return [1036]

            @staticmethod
            def gameplay_effect_has_tag(_damage_id: str, _tag: str):
                return False

            @staticmethod
            def get_character(character_id: int):
                return {
                    "element_type": (
                        "CHARACTER_ELEMENT_TYPE_INCANTATION"
                        if character_id == 1036 else "CHARACTER_ELEMENT_TYPE_NATURE"
                    )
                }

        row = BattleSkillDamageEvidenceService.load(
            Dao(), analysis, _build(),
        )[0]

        self.assertEqual(1036, row.action_character_id)
        self.assertEqual(1036, row.definition_owner_character_id)
        self.assertEqual(1072, row.panel_character_id)
        self.assertEqual(1072, row.source_character_id)
        self.assertEqual(1072, row.skill_level_character_id)
        self.assertEqual("GA_Radio072_QTE", row.ability_id)
        self.assertEqual(6, row.effective_skill_level)
        self.assertEqual("incantation", row.damage_attribute)
        self.assertEqual("initiator_character_static_profile", row.damage_attribute_source)
        self.assertFalse(row.is_formal_follow_up)
        self.assertTrue(row.target_has_weave)
        self.assertEqual("中", row.formula_context_confidence)


class BattleLinkoFollowUpBuffTests(unittest.TestCase):
    def test_formula_projection_exposes_only_bounded_follow_up_consumer_flags(self):
        hit = _hit("1:primary")
        evidence = BattleSkillDamageEvidence(
            event_id=hit.event_id,
            damage_id=hit.gameplay_effect_id,
            ability_id="GA_Radio072_QTE",
            damage_attribute="incantation",
            damage_source_category="NORMAL",
            fixed_crit_rate=0.0,
            scaling_property_id="Atk",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=6,
            evidence_basis="测试",
            source_character_id=1072,
            action_character_id=1036,
            definition_owner_character_id=1036,
            panel_character_id=1072,
            skill_level_character_id=1072,
            is_formal_follow_up=False,
            target_has_weave=True,
            formula_context_kind="linko_coattack:skill",
            formula_context_confidence="中",
        )

        formula_hit = project_formula_hit(hit, evidence)
        matched, reason = passive_requirement_applies(
            "battle-passive|follow-up-consumer=true;target-weave=true",
            formula_hit,
        )

        self.assertEqual(1072, formula_hit.character_id)
        self.assertTrue(matched, reason)
        ordinary, _reason = passive_requirement_applies(
            "battle-passive|follow-up-consumer=true;target-weave=true",
            hit,
        )
        self.assertFalse(ordinary)

        replay = project_replay_formula_context(
            BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=100.0,
                non_critical_damage=100.0,
                critical_damage=154.0,
                selected_damage=100.0,
                selected_error_percent=0.0,
                critical_state="non_critical",
                confidence="中",
                factors=(),
            ),
            formula_hit,
            evidence,
        )
        explanation = BattleHitReplayExplanationService.build(hit, replay)
        self.assertIn("【派生公式归属】", explanation)
        self.assertIn("执行角色：1036", explanation)
        self.assertIn("面板角色：1072", explanation)
        self.assertIn("不改写 Core 原始逐击角色", explanation)
        prepared = BattleHitProjectionPreparationService.prepare(
            SimpleNamespace(hits=(hit,), buff_intervals=()),
            (evidence,),
        )
        self.assertIn(hit.event_id, prepared.formula_by_event)
        self.assertIn(hit.event_id, prepared.beneficiary_by_event)

    def test_weak_point_sensing_is_a_separate_team_general_damage_rule(self):
        rules = BattleCharacterPassiveService.rule_specs(_build(stage=2))
        rule = next(
            row
            for row in rules
            if row.passive_id == "PASSIVE-1072-GA_Radio072_Passive_1"
        )
        self.assertEqual("team", rule.target_scope)
        self.assertEqual("DamageUpGeneralBase", rule.modifiers[0].property_id)
        self.assertEqual(0.10, rule.modifiers[0].magnitude_value)
        self.assertIn(
            "follow-up-consumer=true;target-weave=true",
            rule.modifiers[0].application_requirement_asset_path,
        )

    def test_formal_tagged_follow_up_remains_an_independent_consumer(self):
        hit = replace(
            _hit("1:primary"),
            is_formal_follow_up=True,
            target_has_weave=True,
        )
        matched, reason = passive_requirement_applies(
            "battle-passive|follow-up-consumer=true;target-weave=true",
            hit,
        )

        self.assertTrue(matched, reason)

    def test_weave_settlement_never_consumes_the_extra_ten_percent(self):
        weave = replace(
            _hit("1:follow_up", is_follow_up=True, classification="weave"),
            is_formal_follow_up=True,
            target_has_weave=True,
        )
        matched, _reason = passive_requirement_applies(
            "battle-passive|follow-up-consumer=true;target-weave=true",
            weave,
        )

        self.assertFalse(matched)

    def test_ten_percent_enters_per_hit_general_damage_factor_and_linko_panel(self):
        hit = _hit("1:primary")
        evidence = BattleSkillDamageEvidence(
            event_id=hit.event_id,
            damage_id=hit.gameplay_effect_id,
            ability_id="GA_Radio072_QTE",
            damage_attribute="incantation",
            damage_source_category="NORMAL",
            fixed_crit_rate=0.0,
            scaling_property_id="Atk",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=6,
            evidence_basis="测试",
            source_character_id=1072,
            panel_character_id=1072,
            action_character_id=1036,
            definition_owner_character_id=1036,
            skill_level_character_id=1072,
            is_formal_follow_up=False,
            target_has_weave=True,
            formula_context_kind="linko_coattack:skill",
            formula_context_confidence="中",
        )
        interval = BattleInferredBuffInterval(
            interval_id="buff:linko:weak-point",
            buff_asset_path="character_passive:1072:GA_Radio072_Passive_1",
            buff_name="弱点感应",
            source_effect_definition_id="PASSIVE-1072-GA_Radio072_Passive_1",
            source_kind="confirmed_character_passive",
            source_character_id=1072,
            source_character_name="灵可",
            target_scope="team",
            start_us=0,
            end_us=2_000_000,
            stacks=1,
            duration_policy="Infinite",
            state_confidence="高",
            value_confidence="高",
            inference_basis="官方被动",
            trigger_event_type="PASSIVE_STATIC",
            evidence_action_ids=(),
            evidence_event_ids=(),
            modifiers=(BattleBuffModifierEvidence(
                property_id="DamageUpGeneralBase",
                modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="confirmed_character_passive",
                magnitude_value=0.10,
                calculation_asset_path="",
                value_confidence="高",
                application_requirement_asset_path=(
                    "battle-passive|follow-up-consumer=true;target-weave=true"
                ),
            ),),
        )
        baseline = BattleCharacterBaseline(
            character_id=1072,
            character_name="灵可",
            source="fixture",
            stats=(
                BattleCharacterStat("AtkBase", "基础攻击", 100.0, False),
                BattleCharacterStat("AtkUp", "攻击提升", 0.0, True),
                BattleCharacterStat("AtkAdd", "固定攻击", 0.0, False),
                BattleCharacterStat("CritBase", "暴击率", 0.0, True),
                BattleCharacterStat("CritDamageBase", "暴伤", 0.54, True),
                BattleCharacterStat("DamageUpGeneralBase", "通伤", 0.20, True),
                BattleCharacterStat("DamageUpIncantationBase", "咒伤", 0.0, True),
                BattleCharacterStat("DefIgnore", "无视防御", 0.0, True),
            ),
        )
        analysis = SimpleNamespace(
            hits=(hit,),
            baselines=(baseline,),
            buff_intervals=(interval,),
            target_condition=BattleTargetCondition(
                target_name="目标",
                enemy_level=80.0,
                scene="open_world",
                defense_reduction=0.0,
                vulnerability=0.0,
                resistances=(("incantation", 0.0),),
                enemy_defense_base=0.0,
            ),
        )

        replay = BattleHitReplayService.replay(
            analysis,
            (evidence,),
            apply_observed_refinements=False,
        )[0]

        damage_up = next(row for row in replay.factors if row.factor_id == "damage_up")
        self.assertAlmostEqual(1.30, damage_up.value)
        self.assertEqual(1072, replay.formula_panel_character_id)
        self.assertEqual("linko_coattack:skill", replay.formula_context_kind)
        self.assertFalse(replay.formula_is_formal_follow_up)
        self.assertTrue(replay.formula_target_has_weave)


class BattleLinkoPrecisionTuningTests(unittest.TestCase):
    def test_same_element_resistance_is_eight_percent_and_refreshes_at_twelve_seconds(self):
        first_hit = _hit("1:primary", relative_time_us=1_000_000)
        second_hit = _hit("2:primary", relative_time_us=5_000_000)
        first = replace(
            _inference(first_hit.event_id, time_us=1_000_000),
            damage_attribute="incantation",
        )
        second = replace(
            _inference(second_hit.event_id, time_us=5_000_000),
            damage_attribute="incantation",
        )

        rows = BattleLinkoCoattackBuffService.infer(
            build=_build(stage=4),
            inferences=(first, second),
            hits=(first_hit, second_hit),
            battle_end_us=30_000_000,
            time_stop_intervals=(),
        )

        self.assertEqual(2, len(rows))
        self.assertEqual(5_000_000, rows[0].end_us)
        self.assertEqual(17_000_000, rows[1].end_us)
        self.assertEqual(5_000_000, rows[1].start_us)
        self.assertEqual("target", rows[0].target_scope)
        self.assertEqual("DamageResistIncantationBase", rows[0].modifiers[0].property_id)
        self.assertEqual(-0.08, rows[0].modifiers[0].magnitude_value)
        self.assertEqual(1, rows[0].stack_limit_count)
        self.assertEqual(rows[0].end_us, rows[1].start_us)
        refreshed = BattleBuffAttributeProjectionService.project_hit(second_hit, rows)
        self.assertEqual((rows[1].interval_id,), refreshed.applied_interval_ids)
        self.assertEqual("中", rows[0].state_confidence)
        self.assertIn("不是 Core 原生状态事件", rows[0].inference_basis)

    def test_triggering_qte_hit_consumes_resistance_for_both_inference_entrances(self):
        for trigger_kind in ("skill", "qte_lte_pair"):
            with self.subTest(trigger_kind=trigger_kind):
                hit = _hit("1:primary", relative_time_us=1_000_000)
                inference = replace(
                    _inference(hit.event_id, time_us=hit.relative_time_us),
                    trigger_kind=trigger_kind,
                )
                rows = BattleLinkoCoattackBuffService.infer(
                    build=_build(stage=4),
                    inferences=(inference,),
                    hits=(hit,),
                    battle_end_us=20_000_000,
                    time_stop_intervals=(),
                )

                self.assertEqual(1, len(rows))
                self.assertEqual(hit.relative_time_us, rows[0].start_us)
                self.assertTrue(rows[0].buff_asset_path.endswith("Passive3_zhou"))
                projection = BattleBuffAttributeProjectionService.project_hit(
                    hit,
                    rows,
                )
                self.assertEqual((rows[0].interval_id,), projection.applied_interval_ids)
                self.assertEqual(
                    "DamageResistIncantationBase",
                    projection.modifiers[0].property_id,
                )
                self.assertEqual(-0.08, projection.modifiers[0].additive_value)

    def test_different_elements_each_keep_one_independent_resistance_debuff(self):
        first_hit = _hit("1:primary", relative_time_us=1_000_000)
        second_hit = _hit("2:primary", relative_time_us=2_000_000)
        first = _inference(first_hit.event_id, time_us=1_000_000)
        second = replace(
            _inference(second_hit.event_id, time_us=2_000_000),
            damage_attribute="nature",
        )

        rows = BattleLinkoCoattackBuffService.infer(
            build=_build(stage=4),
            inferences=(first, second),
            hits=(first_hit, second_hit),
            battle_end_us=20_000_000,
            time_stop_intervals=(),
        )

        properties = {row.modifiers[0].property_id for row in rows}
        self.assertEqual(
            {"DamageResistIncantationBase", "DamageResistNatureBase"},
            properties,
        )
        self.assertTrue(all(row.stack_limit_count == 1 for row in rows))

    def test_one_qte_action_projects_resistance_to_each_hit_target(self):
        first_hit = _hit("1:primary", relative_time_us=1_000_000)
        second_hit = replace(
            _hit("2:primary", relative_time_us=1_100_000),
            target_id="target-2",
        )
        first = _inference(first_hit.event_id, time_us=1_000_000)
        second = _inference(second_hit.event_id, time_us=1_000_000)

        rows = BattleLinkoCoattackBuffService.infer(
            build=_build(stage=4),
            inferences=(first, second),
            hits=(first_hit, second_hit),
            battle_end_us=20_000_000,
            time_stop_intervals=(),
        )

        self.assertEqual(2, len(rows))
        self.assertEqual({"target-1", "target-2"}, {row.target_id for row in rows})


if __name__ == "__main__":
    unittest.main()
