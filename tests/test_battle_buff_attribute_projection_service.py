# 覆盖逐击 Buff 属性投影的去重、常驻排除和安全属性边界。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)


def _hit() -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="hit:1",
        sequence=1,
        relative_time_us=2_000_000,
        character_id=1072,
        character_name="灵可",
        skill_name="技能",
        damage_name="伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute="nature",
        target_id="target",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def _interval(
    interval_id: str,
    *,
    start_us: int,
    event_type: str = "ABILITY_EVENT|E|GA_Test|",
    property_id: str = "AtkUp",
    value: float | None = 0.15,
    calculation: str = "",
    target_scope: str = "self",
    source_kind: str = "test",
    source_require_tags: tuple[str, ...] = (),
    application_requirement_asset_path: str = "",
    target_id: str = "",
    stacks: int = 1,
    stack_limit_count: int = 1,
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path="/Game/Test/Buff_Attack",
        buff_name="攻击提升",
        source_effect_definition_id="test:buff",
        source_kind=source_kind,
        source_character_id=1072,
        source_character_name="灵可",
        target_scope=target_scope,
        start_us=start_us,
        end_us=5_000_000,
        stacks=stacks,
        duration_policy="HasDuration",
        state_confidence="低",
        value_confidence="中" if value is not None else "低",
        inference_basis="fixture",
        trigger_event_type=event_type,
        evidence_action_ids=("action:1",),
        evidence_event_ids=("hit:1",),
        modifiers=(BattleBuffModifierEvidence(
            property_id=property_id,
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="ScalableFloat",
            magnitude_value=value,
            calculation_asset_path=calculation,
            value_confidence="中" if value is not None else "低",
            source_require_tags=source_require_tags,
            application_requirement_asset_path=application_requirement_asset_path,
        ),),
        stacking_type="AggregateBySource",
        stack_limit_count=stack_limit_count,
        target_id=target_id,
    )


class BattleBuffAttributeProjectionServiceTests(unittest.TestCase):
    def test_stack_limit_caps_total_stacks_not_interval_count(self) -> None:
        older = _interval(
            "older-ten",
            start_us=0,
            value=0.01,
            stacks=10,
            stack_limit_count=20,
        )
        newer = _interval(
            "newer-fifteen",
            start_us=1_000_000,
            value=0.01,
            stacks=15,
            stack_limit_count=20,
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (older, newer),
        )

        self.assertEqual(0.20, projection.modifiers[0].additive_value)

    def test_formal_ability_source_tag_applies_outside_fork_rules(self) -> None:
        interval = replace(
            _interval(
                "nanally-cat",
                start_us=0,
                property_id="DamageUpGeneralBase",
                source_require_tags=("Ability.Player.Nanally.UltraSkillCat",),
            ),
            source_character_id=1010,
        )
        cat = replace(
            _hit(),
            character_id=1010,
            gameplay_effect_id="GE_Player_Nanally_UltraSkillCat_Damage",
        )
        ordinary = replace(
            _hit(),
            character_id=1010,
            gameplay_effect_id="GE_Player_Nanally_Skill_Damage",
        )

        self.assertEqual(
            0.15,
            BattleBuffAttributeProjectionService.project_hit(
                cat, (interval,),
            ).modifiers[0].additive_value,
        )
        excluded = BattleBuffAttributeProjectionService.project_hit(
            ordinary, (interval,),
        )
        self.assertEqual((), excluded.modifiers)
        self.assertIn("指定技能伤害标签", excluded.exclusion_reasons[0])

    def test_unobserved_target_require_tag_stays_unresolved(self) -> None:
        interval = _interval("target-tag", start_us=0)
        modifier = replace(
            interval.modifiers[0],
            target_require_tags=("Ability.ForkSkill.BlackBook.Linked",),
        )
        interval = replace(interval, modifiers=(modifier,))

        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (interval,),
        )

        self.assertEqual((), projection.modifiers)
        self.assertEqual("unresolved", projection.decisions[0].status)
        self.assertIn("目标标签状态", projection.exclusion_reasons[0])

    def test_fadia_awakening_five_only_applies_to_godslayer_hit(self) -> None:
        interval = replace(
            _interval(
                "fadia-godslayer",
                start_us=0,
                property_id="CritBase",
                value=0.50,
                application_requirement_asset_path=(
                    "battle-awakening:fadia-godslayer"
                ),
            ),
            source_character_id=1039,
        )
        godslayer = replace(
            _hit(),
            character_id=1039,
            gameplay_effect_id="GE_Player_Fadia_UltraSkillMelee_Damage",
        )
        other_q = replace(
            godslayer,
            gameplay_effect_id="GE_Player_Fadia_UltraSkill_Damage",
        )

        self.assertEqual(
            0.50,
            BattleBuffAttributeProjectionService.project_hit(
                godslayer, (interval,),
            ).modifiers[0].additive_value,
        )
        self.assertEqual(
            (),
            BattleBuffAttributeProjectionService.project_hit(
                other_q, (interval,),
            ).modifiers,
        )

    def test_mint_sixth_awakening_uses_pre_hit_target_hp_ratio(self) -> None:
        interval = _interval(
            "mint-low-hp",
            start_us=0,
            property_id="DamageUpGeneralBase",
            application_requirement_asset_path="/Game/Condition/Con_Mint_Lv6",
        )
        low_hp = replace(_hit(), target_hp_before=39.0, target_max_hp=100.0)
        high_hp = replace(_hit(), target_hp_before=40.0, target_max_hp=100.0)

        self.assertEqual(
            0.15,
            BattleBuffAttributeProjectionService.project_hit(
                low_hp, (interval,),
            ).modifiers[0].additive_value,
        )
        excluded = BattleBuffAttributeProjectionService.project_hit(
            high_hp, (interval,),
        )
        self.assertEqual((), excluded.modifiers)
        self.assertIn("不低于 40%", excluded.exclusion_reasons[0])

    def test_target_modifier_requires_the_same_concrete_target(self) -> None:
        interval = _interval(
            "target-debuff",
            start_us=0,
            property_id="DamageResistNatureBase",
            value=-0.10,
            target_scope="target",
            target_id="target-a",
        )

        matching = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), target_id="target-a"),
            (interval,),
        )
        other = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), target_id="target-b"),
            (interval,),
        )

        self.assertEqual(-0.10, matching.modifiers[0].additive_value)
        self.assertEqual((), other.modifiers)
        self.assertIn("目标实例不匹配", other.exclusion_reasons[0])

    def test_target_modifier_without_target_identity_stays_unresolved(self) -> None:
        interval = _interval(
            "unknown-target-debuff",
            start_us=0,
            property_id="DamageResistNatureBase",
            value=-0.10,
            target_scope="target",
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (interval,),
        )

        self.assertEqual((), projection.modifiers)
        self.assertEqual("unresolved", projection.decisions[0].status)
        self.assertIn("缺少目标实例", projection.exclusion_reasons[0])

    def test_zero_first_awakening_only_projects_to_its_extra_damage_ge(self) -> None:
        interval = replace(
            _interval(
                "zero-first-gaze",
                start_us=0,
                property_id="DefIgnore",
                value=0.75,
                application_requirement_asset_path=(
                    "battle-awakening:zero-first-gaze-extra-hit"
                ),
            ),
            source_character_id=1051,
            source_character_name="零",
            buff_name="初明凝视（觉醒一）",
        )
        base_hit = replace(
            _hit(),
            character_id=1051,
            character_name="零",
            damage_attribute="cosmos",
        )

        extra = BattleBuffAttributeProjectionService.project_hit(
            replace(
                base_hit,
                gameplay_effect_id=(
                    "GE_Player_Female051_Skill_Kill_Damage_lv2"
                ),
            ),
            (interval,),
        )
        ordinary = BattleBuffAttributeProjectionService.project_hit(
            replace(
                base_hit,
                gameplay_effect_id="GE_Player_Female051_Skill1_Damage",
            ),
            (interval,),
        )

        self.assertEqual(0.75, extra.modifiers[0].additive_value)
        self.assertEqual(("zero-first-gaze",), extra.applied_interval_ids)
        self.assertEqual((), ordinary.modifiers)
        self.assertIn("只作用于铭隙鉴刻的额外伤害", ordinary.exclusion_reasons[0])

    def test_zero_first_awakening_accepts_protagonist_identity_fallback(self) -> None:
        interval = replace(
            _interval(
                "zero-first-gaze",
                start_us=0,
                property_id="DefIgnore",
                value=0.75,
                application_requirement_asset_path=(
                    "battle-awakening:zero-first-gaze-extra-hit"
                ),
            ),
            source_character_id=1046,
            source_character_name="零",
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            replace(
                _hit(),
                character_id=1046,
                character_name="零",
                gameplay_effect_id="GE_Player_Female051_Skill_Kill_Damage_lv1",
            ),
            (interval,),
        )

        self.assertEqual(0.75, projection.modifiers[0].additive_value)
        self.assertEqual(("zero-first-gaze",), projection.applied_interval_ids)

    def test_refreshing_same_buff_uses_latest_interval_once(self) -> None:
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (
                _interval("old", start_us=0),
                _interval("new", start_us=1_000_000),
            ),
        )

        self.assertEqual(1, len(projection.modifiers))
        self.assertEqual(0.15, projection.modifiers[0].additive_value)
        self.assertEqual(("new",), projection.modifiers[0].interval_ids)

    def test_element_buff_only_projects_to_matching_damage_attribute(self) -> None:
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (_interval(
                "wrong-element",
                start_us=0,
                property_id="DamageUpPsychicallyBase",
            ),),
        )

        self.assertEqual((), projection.modifiers)
        self.assertTrue(any("伤害属性不匹配" in row for row in projection.exclusion_reasons))

    def test_target_resistance_reduction_is_kept_as_enemy_modifier(self) -> None:
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (_interval(
                "nature-resistance-down",
                start_us=0,
                property_id="DamageResistNatureBase",
                value=-0.10,
                target_scope="target",
                target_id="target",
            ),),
        )

        self.assertEqual(1, len(projection.modifiers))
        self.assertEqual("target", projection.modifiers[0].target_scope)
        self.assertEqual(-0.10, projection.modifiers[0].additive_value)
        self.assertNotIn(
            "DamageResistNatureBase",
            BattleBuffAttributeProjectionService.apply_additive(
                {},
                projection,
            ),
        )

    def test_explicit_character_scope_only_projects_to_that_character(self) -> None:
        interval = _interval(
            "current-field-role",
            start_us=0,
            target_scope="character:1072",
        )

        matching = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (interval,),
        )
        other = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), character_id=1004),
            (interval,),
        )

        self.assertEqual(0.15, matching.modifiers[0].additive_value)
        self.assertEqual("character:1072", matching.modifiers[0].target_scope)
        self.assertEqual(
            0.15,
            BattleBuffAttributeProjectionService.apply_additive(
                {}, matching,
            )["AtkUp"],
        )
        self.assertEqual((), other.modifiers)

    def test_confirmed_fork_attachment_tag_matches_kuhara_attachment_ge(self) -> None:
        interval = _interval(
            "shelter-attachment",
            start_us=0,
            property_id="DamageUpGeneralBase",
            source_kind="confirmed_fork_refinement",
            source_require_tags=("State.Damage.Attachment",),
        )

        attachment = BattleBuffAttributeProjectionService.project_hit(
            replace(
                _hit(),
                gameplay_effect_id="GE_Player_Kuhara_BudBoom_Damage",
            ),
            (interval,),
        )
        ordinary = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), gameplay_effect_id="GE_Player_Kuhara_Skill_Damage"),
            (interval,),
        )

        self.assertEqual(0.15, attachment.modifiers[0].additive_value)
        self.assertEqual((), ordinary.modifiers)
        self.assertIn("指定技能伤害标签", ordinary.exclusion_reasons[0])

    def test_resolved_calculation_value_can_enter_safe_projection(self) -> None:
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (_interval(
                "resolved-calculation",
                start_us=0,
                property_id="DamageUpNatureBase",
                value=0.10,
                calculation="/Game/Calculation/Cau_Resolved",
            ),),
        )

        self.assertEqual(1, len(projection.modifiers))
        self.assertEqual(0.10, projection.modifiers[0].additive_value)

    def test_static_equipped_runtime_buff_is_applied_but_custom_calculation_is_excluded(self) -> None:
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (
                _interval(
                    "equipped",
                    start_us=0,
                    event_type="STATIC_EQUIPPED_SOURCE",
                ),
                _interval(
                    "calculation",
                    start_us=0,
                    value=None,
                    calculation="/Game/Test/Calc_Attack",
                ),
            ),
        )

        self.assertEqual(1, len(projection.modifiers))
        self.assertEqual("AtkUp", projection.modifiers[0].property_id)
        self.assertEqual(("equipped",), projection.applied_interval_ids)
        self.assertEqual(("calculation",), projection.excluded_interval_ids)
        self.assertTrue(any("Calculation" in row for row in projection.exclusion_reasons))

    def test_each_active_interval_has_one_explainable_projection_decision(self) -> None:
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(),
            (
                _interval("applied", start_us=0),
                _interval(
                    "wrong-element",
                    start_us=0,
                    property_id="DamageUpPsychicallyBase",
                ),
                _interval(
                    "unresolved",
                    start_us=0,
                    value=None,
                    calculation="/Game/Test/Calc_Attack",
                ),
            ),
        )

        decisions = {row.interval_id: row for row in projection.decisions}
        self.assertEqual("applied", decisions["applied"].status)
        self.assertEqual("not_applied", decisions["wrong-element"].status)
        self.assertEqual("unresolved", decisions["unresolved"].status)
        self.assertIn("AtkUp", decisions["applied"].applied_property_ids)
        self.assertTrue(decisions["unresolved"].reasons)

    def test_special_coefficient_is_unresolved_only_for_its_bound_damage(self) -> None:
        calculation = (
            "/Game/Blueprints/Abilities/Calculation/Zankou/"
            "Calc_ZankouDotStackCoef"
        )
        interval = _interval(
            "zankou-stack",
            start_us=0,
            property_id="CoefModify",
            value=None,
            calculation=calculation,
        )

        direct = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), gameplay_effect_id="GE_Player_Zankou_Skill2_Damage"),
            (interval,),
        )
        dot = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), gameplay_effect_id="GE_Player_Zankou_DotDamage"),
            (interval,),
        )

        self.assertEqual("not_applied", direct.decisions[0].status)
        self.assertEqual("not_applied", dot.decisions[0].status)
        self.assertIn(
            "逐击重放适配器单独计算",
            dot.decisions[0].reasons[0],
        )
        self.assertIn("绑定的伤害项", direct.decisions[0].reasons[0])

    def test_generic_dot_accepts_continuous_damage_only_modifier(self) -> None:
        interval = _interval(
            "continuous-damage",
            start_us=0,
            property_id="DamageUpGeneralBase",
            application_requirement_asset_path="battle-channel:continuous-damage",
        )

        dot = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), gameplay_effect_id="GE_Player_Cang_UltraSkill_Damage"),
            (interval,),
        )
        direct = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(), gameplay_effect_id="GE_Player_Cang_Skill_Damage"),
            (interval,),
        )

        self.assertEqual(0.15, dot.modifiers[0].additive_value)
        self.assertEqual((), direct.modifiers)
        self.assertIn("持续伤害", direct.decisions[0].reasons[0])

    def test_topple_uses_only_its_per_character_formula_properties(self) -> None:
        topple = replace(
            _hit(),
            damage_name="倾陷伤害",
            attack_type="倾陷伤害",
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )
        projection = BattleBuffAttributeProjectionService.project_hit(
            topple,
            (
                _interval(
                    "strength",
                    start_us=0,
                    property_id="UnbalIntensityBase",
                    value=60.0,
                ),
                _interval(
                    "general-damage",
                    start_us=0,
                    property_id="DamageUpGeneralBase",
                    value=0.15,
                ),
            ),
        )

        self.assertEqual(
            ("UnbalIntensityBase",),
            tuple(row.property_id for row in projection.modifiers),
        )
        decisions = {row.interval_id: row for row in projection.decisions}
        self.assertEqual("applied", decisions["strength"].status)
        self.assertEqual("not_applied", decisions["general-damage"].status)
        self.assertIn("不进入倾陷", decisions["general-damage"].reasons[0])


if __name__ == "__main__":
    unittest.main()
