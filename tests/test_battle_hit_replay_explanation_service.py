# 验证逐击详情保留完整代入公式、乘区来源和证据边界。
from __future__ import annotations

from dataclasses import replace
import unittest

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_replay_explanation_service import (
    BattleHitReplayExplanationService,
    _factor_lines,
)


class BattleHitReplayExplanationServiceTests(unittest.TestCase):
    @staticmethod
    def _buff(
        interval_id: str,
        *,
        property_id: str,
        value: float | None,
        calculation: str = "",
    ) -> BattleInferredBuffInterval:
        return BattleInferredBuffInterval(
            interval_id=interval_id,
            buff_asset_path=f"/Game/Buff/{interval_id}",
            buff_name=f"测试 Buff {interval_id}",
            source_effect_definition_id=f"source:{interval_id}",
            source_kind="test",
            source_character_id=1004,
            source_character_name="安魂曲",
            target_scope="self",
            start_us=0,
            end_us=10_000_000,
            stacks=1,
            duration_policy="HasDuration",
            state_confidence="中",
            value_confidence="中" if value is not None else "低",
            inference_basis="fixture",
            trigger_event_type="BUFF_EVENT_Q_SKILL_BEGIN",
            evidence_action_ids=(),
            evidence_event_ids=(),
            modifiers=(BattleBuffModifierEvidence(
                property_id=property_id,
                modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="CustomCalculationClass" if calculation else "constant",
                magnitude_value=value,
                calculation_asset_path=calculation,
                value_confidence="中" if value is not None else "低",
            ),),
        )

    def test_direct_hit_expands_formula_substitution_and_factor_meanings(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="异界窃取",
            damage_name="异界窃取",
            damage_component="skill",
            attack_type="Skill",
            damage_attribute="COSMOS",
            target_id="boss",
            target_name="墨菲斯托",
            damage=36.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            ability_id="GA_Lacrimosa_Steal",
            gameplay_effect_id="GE_boss_018_act019_Steal_Dmg_BP",
        )
        factors = (
            BattleHitReplayFactor("skill", "倍率区", 0.5, "当前技能等级静态倍率"),
            BattleHitReplayFactor(
                "scaling",
                "Atk 乘区",
                100.0,
                "冻结角色面板",
                "基础值 × (1 + 百分比提升) + 额外固定值",
                (
                    BattleHitReplayTerm(
                        "character:AtkBase",
                        "AtkBase",
                        "基础攻击力",
                        80.0,
                        "character",
                        "人物",
                        False,
                        "冻结来源",
                    ),
                    BattleHitReplayTerm(
                        "fork:AtkBase",
                        "AtkBase",
                        "基础攻击力",
                        20.0,
                        "fork",
                        "弧盘",
                        False,
                        "冻结来源",
                    ),
                ),
            ),
            BattleHitReplayFactor("damage_up", "增伤区", 1.2, "通用与属性增伤"),
            BattleHitReplayFactor("defense", "防御区", 0.5, "DefBase/6"),
            BattleHitReplayFactor("resistance", "抗性区", 0.8, "用户确认抗性"),
            BattleHitReplayFactor("vulnerability", "易伤区", 1.0, "默认无易伤"),
            BattleHitReplayFactor("independent", "独立最终乘区", 1.0, "无最终伤害 Buff"),
            BattleHitReplayFactor("critical", "暴击伤害倍率", 1.5, "角色暴击伤害"),
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=36.0,
            non_critical_damage=24.0,
            critical_damage=36.0,
            selected_damage=36.0,
            selected_error_percent=0.0,
            critical_state="critical",
            confidence="高",
            factors=factors,
            formula_type="直伤",
            critical_rate=0.5,
            expected_damage=30.0,
            corrected_expected_damage=30.0,
            signed_error_percent=0.0,
        )

        text = BattleHitReplayExplanationService.build(hit, replay)

        self.assertIn("实际伤害：36.00", text)
        self.assertIn("逐击 ID：1:primary", text)
        self.assertIn("预计误差：+0.00%", text)
        self.assertIn("预计伤害期望：30.00", text)
        self.assertIn("伤害（未暴击） = 倍率区 × Atk 乘区 × 增伤区", text)
        self.assertIn("= (人物:基础攻击力 + 弧盘:基础攻击力)", text)
        self.assertIn("= (80.000 + 20.000)", text)
        self.assertIn("防御区 = L / (敌方有效防御 + L)", text)
        self.assertIn("防御区 = 100 / (0 + 100) = 0.500000", text)
        self.assertIn("推断暴击：是（置信度高）", text)

    def test_resistance_formula_shows_subtraction_and_negative_branch(self) -> None:
        factor = BattleHitReplayFactor(
            "resistance",
            "抗性区",
            1.036363636,
            "用户确认的目标属性包",
            "抗性分段函数(目标抗性 - 属性穿透)",
            (
                BattleHitReplayTerm(
                    "target:resistance", "Resistance:chaos", "属性抗性",
                    0.20, "target", "敌方", True, "用户确认",
                ),
                BattleHitReplayTerm(
                    "buff:diabolos", "DamagePenetrateChaos", "暗属性穿透",
                    0.24, "buff", "迪亚波罗斯", True, "命中时 Buff",
                ),
            ),
        )

        text = "\n".join(_factor_lines(factor))

        self.assertIn("属性穿透合计 = 24%", text)
        self.assertIn("有效抗性 = 目标抗性 - 属性穿透", text)
        self.assertIn("= 20% - 24% = -4%", text)
        self.assertIn("采用：1 - 有效抗性 / 1.10", text)
        self.assertIn("抗性区 = 1 - (-4% / 1.10) = 1.036364", text)
        self.assertNotIn("20% + 24%", text)

    def test_dot_formula_displays_dot_only_final_multiplier(self) -> None:
        hit = BattleAnalysisHit(
            event_id="nightmare:dot",
            sequence=1,
            relative_time_us=1_448_745,
            character_id=1004,
            character_name="安魂曲",
            skill_name="安魂曲",
            damage_name="噩梦",
            damage_component="dot",
            attack_type="Special Damage",
            damage_attribute="CHAOS",
            target_id="boss",
            target_name="争锋目标",
            damage=1_630.0,
            direction="outgoing",
            is_follow_up=False,
            classification="dot",
            gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage_LV6",
        )
        factors = (
            BattleHitReplayFactor("skill", "倍率区", 0.173, "噩梦等级倍率"),
            BattleHitReplayFactor(
                "state_coefficient",
                "噩梦当前层数",
                3.0,
                "命中前逐层重放",
            ),
            BattleHitReplayFactor("scaling", "Atk 乘区", 1_934.215, "冻结面板"),
            BattleHitReplayFactor("damage_up", "增伤区", 2.125, "本击增伤"),
            BattleHitReplayFactor("defense", "防御区", 0.553846, "目标防御"),
            BattleHitReplayFactor("resistance", "抗性区", 0.92, "目标抗性"),
            BattleHitReplayFactor("vulnerability", "易伤区", 1.0, "无易伤"),
            BattleHitReplayFactor("independent", "独立最终乘区", 1.0, "无公共最终增伤"),
            BattleHitReplayFactor(
                "dot_final",
                "DOT 专属最终乘区",
                1.5,
                "早雾：浊燃、噩梦两种 DOT",
            ),
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=1_630.0,
            non_critical_damage=1_631.0,
            critical_damage=None,
            selected_damage=1_631.0,
            selected_error_percent=0.0257,
            critical_state="non_critical",
            confidence="中",
            factors=factors,
            formula_type="DOT",
        )

        text = BattleHitReplayExplanationService.build(hit, replay)

        self.assertIn(
            "伤害（未暴击） = 倍率区 × 噩梦当前层数 × Atk 乘区 × "
            "增伤区 × 防御区 × 抗性区 × 易伤区 × 独立最终乘区 × "
            "DOT 专属最终乘区",
            text,
        )
        self.assertIn(
            "17.300% × 3.000000 × 1,934.215 × 2.125000 × 0.553846 × "
            "0.920000 × 1.000000 × 1.000000 × 1.500000",
            text,
        )
        self.assertIn("ceil(1,630.418705) = 1,631.00", text)

    def test_unreplayable_hit_explains_missing_adapter(self) -> None:
        hit = BattleAnalysisHit(
            event_id="2:primary",
            sequence=2,
            relative_time_us=2_000_000,
            character_id=1036,
            character_name="残虹",
            skill_name="鸩火",
            damage_name="鸩火",
            damage_component="special",
            attack_type="Special",
            damage_attribute="CHAOS",
            target_id="boss",
            target_name="墨菲斯托",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="special",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=100.0,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
            missing_evidence=("special 需要独立反事实适配器",),
        )

        text = BattleHitReplayExplanationService.build(hit, replay)

        self.assertIn("不能安全拼出数值等式", text)
        self.assertIn("special 需要独立反事实适配器", text)

    def test_direct_hit_distinguishes_missing_target_from_missing_adapter(self) -> None:
        hit = BattleAnalysisHit(
            event_id="target-missing",
            sequence=1,
            relative_time_us=1,
            character_id=1052,
            character_name="浔",
            skill_name="胧月流",
            damage_name="胧月流",
            damage_component="skill",
            attack_type="Melee",
            damage_attribute="cosmos",
            target_id="unknown",
            target_name="未知目标",
            damage=982.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
            missing_evidence=("尚未保存用户确认的单目标防御与抗性",),
            formula_type="直伤",
        )

        text = BattleHitReplayExplanationService.build(hit, replay)

        self.assertIn("已匹配直伤适配器", text)
        self.assertNotIn("当前类型尚无完整反事实适配器", text)
        self.assertIn("可投影（公式输入不完整）", text)

    def test_error_keeps_negative_direction_for_underestimate(self) -> None:
        hit = BattleAnalysisHit(
            event_id="3:primary",
            sequence=3,
            relative_time_us=3_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="测试技能",
            damage_name="测试伤害",
            damage_component="skill",
            attack_type="Skill",
            damage_attribute="COSMOS",
            target_id="boss",
            target_name="墨菲斯托",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=100.0,
            non_critical_damage=95.63,
            critical_damage=143.445,
            selected_damage=95.63,
            selected_error_percent=4.37,
            critical_state="non_critical",
            confidence="中",
            factors=(),
            formula_type="直伤",
            critical_rate=0.5,
            expected_damage=107.58,
            corrected_expected_damage=112.38,
            signed_error_percent=-4.37,
        )

        text = BattleHitReplayExplanationService.build(hit, replay)

        self.assertIn("预计误差：-4.37%", text)
        self.assertIn("负值为低估", text)
        self.assertIn("实际伤害期望：112.38", text)

    def test_buff_evidence_is_split_into_applied_not_applied_and_unresolved(self) -> None:
        hit = BattleAnalysisHit(
            event_id="4:primary",
            sequence=4,
            relative_time_us=4_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="测试技能",
            damage_name="测试伤害",
            damage_component="skill",
            attack_type="Skill",
            damage_attribute="chaos",
            target_id="boss",
            target_name="墨菲斯托",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=100.0,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
        )

        text = BattleHitReplayExplanationService.build(
            hit,
            replay,
            active_buffs=(
                self._buff("applied", property_id="AtkUp", value=0.15),
                self._buff("wrong", property_id="DamageUpNatureBase", value=0.10),
                self._buff(
                    "unresolved",
                    property_id="CoefModify",
                    value=None,
                    calculation="/Game/Calc/Unknown",
                ),
            ),
        )

        self.assertIn("【本击 Buff：已投影（是否被公式消费见乘区）】", text)
        self.assertIn("【本击 Buff：未采用】", text)
        self.assertIn("【本击 Buff：待确认/结构化】", text)
        self.assertIn("ID：source:applied", text)
        self.assertIn("伤害属性不匹配", text)
        self.assertIn("CoefModify 尚未映射", text)

    def test_display_identical_buff_intervals_are_merged(self) -> None:
        hit = BattleAnalysisHit(
            event_id="merge:hit",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="测试技能",
            damage_name="测试伤害",
            damage_component="skill",
            attack_type="Skill",
            damage_attribute="chaos",
            target_id="boss",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=100.0,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
        )
        base = replace(
            self._buff("stack", property_id="CritDamageBase", value=0.08),
            stacking_type="AggregateBySource",
            stack_limit_count=7,
        )
        intervals = tuple(
            replace(base, interval_id=f"stack:{index}")
            for index in range(3)
        )

        text = BattleHitReplayExplanationService.build(
            hit,
            replay,
            active_buffs=intervals,
        )

        self.assertIn("合并 3 条同类区间", text)
        self.assertIn("CritDamageBase=0.08×3=0.24", text)
        self.assertEqual(1, text.count("测试 Buff stack"))

    def test_counterfactual_detail_shows_expected_gain_without_calling_it_error(self) -> None:
        hit = BattleAnalysisHit(
            event_id="candidate:1",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="测试技能",
            damage_name="测试伤害",
            damage_component="skill",
            attack_type="Skill",
            damage_attribute="chaos",
            target_id="boss",
            target_name="墨菲斯托",
            damage=1_000.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=1_000.0,
            non_critical_damage=1_200.0,
            critical_damage=None,
            selected_damage=1_200.0,
            selected_error_percent=20.0,
            critical_state="non_critical",
            confidence="中",
            factors=(),
            formula_type="直伤",
            expected_damage=1_200.0,
        )
        projection = BattleBuildHitCounterfactual(
            event_id=hit.event_id,
            character_id=hit.character_id,
            character_name=hit.character_name,
            skill_name=hit.skill_name,
            damage_name=hit.damage_name,
            baseline_damage=1_000.0,
            known_projection_damage=1_150.0,
            candidate_damage=1_150.0,
            heuristic_projection_damage=None,
            quantification=BattleCounterfactualRatio.complete(
                1.15,
                method="component_ratio",
                confidence="中",
                dependency_scope="target_sensitive",
                included_dimension_ids=("scaling", "target_defense"),
                explanation="变化乘区已经完整量化。",
            ),
            baseline_formula_damage=900.0,
            candidate_formula_damage=1_035.0,
        )

        text = BattleHitReplayExplanationService.build(
            hit,
            replay,
            counterfactual=projection,
        )

        self.assertIn("【调整后边际】", text)
        self.assertIn("完整候选：1,150.00", text)
        self.assertIn("提升：150.00（+15.00%）", text)
        self.assertIn("变化乘区完整比值", text)
        self.assertIn("【候选配置伤害公式】", text)
        self.assertNotIn("预计误差：", text)


if __name__ == "__main__":
    unittest.main()
