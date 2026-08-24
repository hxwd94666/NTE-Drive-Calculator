# 验证逐击重放保留有符号误差、暴击期望和可寻址属性来源。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterSourceStat,
    BattleCharacterStat,
    BattleHitBuffProjection,
    BattleProjectedBuffModifier,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_special_hit_replay_service import (
    BattleSpecialHitReplayService,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
)
from src.services.damage_calculation_service import calculate_resistance_multiplier


class BattleHitReplayServiceTests(unittest.TestCase):
    def test_daffodill_extra_topple_uses_her_defense_and_resistance(self) -> None:
        hit = BattleAnalysisHit(
            event_id="true:1",
            sequence=1,
            relative_time_us=1,
            character_id=1054,
            character_name="达芙蒂尔",
            skill_name="完美真相",
            damage_name="额外倾陷伤害",
            damage_component="skill",
            attack_type="Passive Damage",
            damage_attribute="true",
            target_id="target",
            target_name="目标",
            damage=2400.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            gameplay_effect_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
        )
        baseline = BattleCharacterBaseline(
            character_id=1054,
            character_name="达芙蒂尔",
            source="fixture",
            stats=(
                BattleCharacterStat("UnbalIntensityBase", "倾陷强度", 300.0, False),
                BattleCharacterStat("DefIgnore", "防御穿透", 0.15, True),
                BattleCharacterStat("DamagePenetrateChaos", "暗属性穿透", 0.10, True),
            ),
        )
        evidence = BattleSkillDamageEvidence(
            event_id=hit.event_id,
            damage_id=hit.gameplay_effect_id,
            ability_id="",
            damage_attribute="true",
            damage_source_category="TRUE",
            fixed_crit_rate=0.0,
            scaling_property_id="Atk",
            scaling_multiplier=2.0,
            multiplier_coefficient=1.0,
            effective_skill_level=1,
            evidence_basis="正式 TRUE 伤害",
            critical_policy="unknown",
        )
        condition = BattleTargetCondition(
            target_name="目标",
            enemy_level=90.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("chaos", 0.30),),
            enemy_topple_limit=50.0,
            enemy_defense_base=1080.0,
        )
        analysis = SimpleNamespace(
            hits=(hit,),
            baselines=(baseline,),
            buff_intervals=(),
            target_condition=condition,
        )

        result = BattleHitReplayService.replay(
            analysis,
            (evidence,),
            topple_character_configs={
                1054: BattleToppleCharacterConfig(1054, "chaos", 3603.0),
            },
        )[0]

        defense = 180.0 / (1080.0 / 6.0 * (1.0 - 0.15) + 180.0)
        expected = 3603.0 * 2.0 * (50.0 / 3.0) * defense * 0.80
        contribution = next(
            factor
            for factor in result.factors
            if factor.factor_id == "topple_character:1054"
        )
        self.assertAlmostEqual(expected, result.selected_damage)
        self.assertAlmostEqual(expected, contribution.value)
        self.assertIn(f"{defense:.6f}", contribution.formula)
        self.assertIn("0.800000", contribution.formula)
        self.assertEqual("not_applicable", result.critical_state)
        self.assertEqual(0.0, result.critical_rate)
        self.assertEqual("达芙蒂尔五觉·额外倾陷伤害", result.formula_type)
        self.assertIn("静态 TRUE 标签不改变", result.missing_evidence[-1])

    def test_weave_replays_from_same_event_and_applies_lingke_team_passive(self) -> None:
        primary = BattleAnalysisHit(
            event_id="7:primary",
            sequence=7,
            relative_time_us=2_000_000,
            character_id=1075,
            character_name="伊洛伊",
            skill_name="清明梦",
            damage_name="清明梦",
            damage_component="skill",
            attack_type="E技能",
            damage_attribute="nature",
            target_id="target",
            target_name="目标",
            damage=1000.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )
        weave = BattleAnalysisHit(
            event_id="7:follow_up",
            sequence=7,
            relative_time_us=2_000_000,
            character_id=1075,
            character_name="伊洛伊",
            skill_name="清明梦",
            damage_name="覆纹追加攻击",
            damage_component="reaction",
            attack_type="follow_up",
            damage_attribute="nature",
            target_id="target",
            target_name="目标",
            damage=431.0,
            direction="outgoing",
            is_follow_up=True,
            classification="weave",
        )
        baseline = BattleCharacterBaseline(
            character_id=1075,
            character_name="伊洛伊",
            source="fixture",
            stats=(),
            enabled_team_passive_ids=(
                "PASSIVE-1072-GA_Radio072_Passive_1",
            ),
        )
        projection = BattleHitBuffProjection(
            event_id=weave.event_id,
            modifiers=(),
            applied_interval_ids=(),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="高",
        )
        result = BattleSpecialHitReplayService.replay(
            channel_id="reaction_hexed",
            formula_label="覆纹",
            hit=weave,
            evidence=None,
            projection=projection,
            values={
                "MagBase": 120.0,
                "DamageUpGeneralBase": 0.30,
                "DamageUpNatureBase": 0.20,
            },
            analysis=SimpleNamespace(
                hits=(primary, weave),
                baselines=(baseline,),
            ),
        )

        assert result is not None and result.selected_damage is not None
        self.assertAlmostEqual(
            1000.0 * (1.30 * 1.08 - 1.0) * (1.60 / 1.50),
            result.selected_damage,
        )
        self.assertEqual("not_applicable", result.critical_state)
        self.assertIn("弱点感应", result.factors[2].evidence_basis)

    def test_creation_and_scorch_use_official_reaction_formulas(self) -> None:
        condition = BattleTargetCondition(
            target_name="轨外目标",
            enemy_level=79.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("cosmos", 0.30), ("incantation", 0.10)),
            enemy_defense_base=1014.0,
        )
        projection = BattleHitBuffProjection(
            event_id="reaction",
            modifiers=(),
            applied_interval_ids=(),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="高",
        )

        def replay(
            *,
            channel_id: str,
            character_id: int,
            attribute: str,
            observed: float,
            level_multiplier: float,
            static_multiplier: float,
            ring_strength: float,
        ):
            hit = BattleAnalysisHit(
                event_id=f"{channel_id}:1",
                sequence=1,
                relative_time_us=1,
                character_id=character_id,
                character_name="角色",
                skill_name="环合",
                damage_name="环合",
                damage_component="reaction",
                attack_type="环合",
                damage_attribute="",
                target_id="target",
                target_name="目标",
                damage=observed,
                direction="outgoing",
                is_follow_up=False,
                classification="reaction",
            )
            baseline = BattleCharacterBaseline(
                character_id=character_id,
                character_name="角色",
                source="fixture",
                stats=(),
                character_level=80.0,
            )
            evidence = BattleSkillDamageEvidence(
                event_id=hit.event_id,
                damage_id="reaction",
                ability_id="",
                damage_attribute=attribute,
                damage_source_category="R",
                fixed_crit_rate=0.5 if channel_id == "reaction_scorch" else 0.0,
                scaling_property_id="Atk",
                scaling_multiplier=static_multiplier,
                multiplier_coefficient=1.0,
                effective_skill_level=80,
                evidence_basis="官方 16 档",
                formula_kind="reaction",
                level_multiplier=level_multiplier,
            )
            analysis = SimpleNamespace(
                target_condition=condition,
                baselines=(baseline,),
                hits=(hit,),
            )
            return BattleSpecialHitReplayService.replay(
                channel_id=channel_id,
                formula_label="创生" if channel_id == "reaction_creation" else "浊燃",
                hit=hit,
                evidence=evidence,
                projection=projection,
                values={
                    "MagBase": ring_strength,
                    "CritDamageBase": 1.0,
                },
                analysis=analysis,
            )

        creation = replay(
            channel_id="reaction_creation",
            character_id=1051,
            attribute="cosmos",
            observed=5198.0,
            level_multiplier=9000.0,
            static_multiplier=1.5,
            ring_strength=360.0,
        )
        scorch = replay(
            channel_id="reaction_scorch",
            character_id=1003,
            attribute="incantation",
            observed=2067.0,
            level_multiplier=2700.0,
            static_multiplier=1.5,
            ring_strength=60.0,
        )

        assert creation is not None and creation.selected_damage is not None
        assert scorch is not None and scorch.selected_damage is not None
        self.assertAlmostEqual(5198.854, creation.selected_damage, places=2)
        self.assertAlmostEqual(2067.937, scorch.selected_damage, places=2)
        self.assertEqual("not_applicable", creation.critical_state)
        self.assertEqual("non_critical", scorch.critical_state)
        self.assertEqual(0.50, scorch.critical_rate)

    def test_fadia_shared_damage_has_a_specific_unreplayable_boundary(self) -> None:
        hit = BattleAnalysisHit(
            event_id="fadia:shared",
            sequence=1,
            relative_time_us=1,
            character_id=1039,
            character_name="法帝娅",
            skill_name="存在证明的体验",
            damage_name="破灭体验共享伤害",
            damage_component="skill",
            attack_type="E技能",
            damage_attribute="psyche",
            target_id="target",
            target_name="目标",
            damage=2751.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            gameplay_effect_id="GE_Player_Fadia_ZhouYin_Damage",
        )
        baseline = BattleCharacterBaseline(
            character_id=1039,
            character_name="法帝娅",
            source="fixture",
            stats=(),
        )
        analysis = SimpleNamespace(hits=(hit,), baselines=(baseline,))

        result = BattleHitReplayService.replay(analysis, ())[0]

        self.assertEqual("破灭体验共享伤害", result.formula_type)
        self.assertIn("实际承受伤害转移（基础 300%", result.missing_evidence[0])

    def test_nightmare_reuses_direct_formula_but_retains_its_public_type(self) -> None:
        hit = BattleAnalysisHit(
            event_id="nightmare:1",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="极轨终结",
            damage_name="噩梦",
            damage_component="skill",
            attack_type="Q技能",
            damage_attribute="chaos",
            target_id="target",
            target_name="墨菲斯托",
            damage=50_000.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage",
            scope_half="upper",
        )
        baseline = BattleCharacterBaseline(
            character_id=1004,
            character_name="安魂曲",
            source="fixture",
            stats=(BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),),
        )
        evidence = BattleSkillDamageEvidence(
            event_id=hit.event_id,
            damage_id=hit.gameplay_effect_id,
            ability_id="GA_Test",
            damage_attribute="chaos",
            damage_source_category="NORMAL",
            fixed_crit_rate=0.0,
            scaling_property_id="Atk",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=10,
            evidence_basis="fixture",
            state_multiplier=4.0,
            state_multiplier_label="噩梦当前层数",
            state_multiplier_basis="fixture stack",
            state_confidence="高",
        )
        analysis = BattleAnalysisSnapshot(
            battle_record_id=1,
            capability_level="formal_hit",
            axis_complete=True,
            formula_model_version="fixture",
            name_mapping_version="fixture",
            action_inference_version="fixture",
            timeline_projection_version="fixture",
            battle_start_us=0,
            battle_end_us=2_000_000,
            timeline_end_us=2_000_000,
            range_start_us=0,
            range_end_us=2_000_000,
            duration_seconds=2.0,
            total_damage=hit.damage,
            total_dps=hit.damage / 2.0,
            timeline_hits=(hit,),
            inferred_actions=(),
            inferred_inputs=(),
            timeline_damage_groups=(),
            hits=(hit,),
            roles=(),
            skills=(),
            targets=(),
            baselines=(baseline,),
            target_conditions_by_half=((
                "upper",
                BattleTargetCondition(
                    target_name="墨菲斯托",
                    enemy_level=90.0,
                    scene="outer_realm",
                    defense_reduction=0.0,
                    vulnerability=0.0,
                    resistances=(("chaos", 0.20),),
                    enemy_defense_base=1050.0,
                ),
            ),),
        )

        result = BattleHitReplayService.replay(analysis, (evidence,))[0]

        self.assertIsNotNone(result.non_critical_damage)
        self.assertTrue(result.factors)
        self.assertEqual("直伤（噩梦）", result.formula_type)
        stack = next(
            row for row in result.factors if row.factor_id == "state_coefficient"
        )
        self.assertEqual(4.0, stack.value)

    def test_direct_replay_preserves_source_terms_expected_value_and_signed_error(self) -> None:
        baseline = BattleCharacterBaseline(
            character_id=1004,
            character_name="安魂曲",
            source="frozen_v30",
            stats=(
                BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),
                BattleCharacterStat("AtkUp", "攻击力提升", 0.25, True),
                BattleCharacterStat("AtkAdd", "固定攻击力", 100.0, False),
                BattleCharacterStat("CritBase", "暴击率", 0.50, True),
                BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
            ),
            source_stats=(
                BattleCharacterSourceStat(
                    "character", "人物", "AtkBase", "基础攻击力", 800.0, False
                ),
                BattleCharacterSourceStat(
                    "fork", "弧盘", "AtkBase", "基础攻击力", 200.0, False
                ),
                BattleCharacterSourceStat(
                    "equipment", "装备", "AtkUp", "攻击力提升", 0.25, True
                ),
                BattleCharacterSourceStat(
                    "equipment", "装备", "AtkAdd", "固定攻击力", 100.0, False
                ),
                BattleCharacterSourceStat(
                    "character", "人物", "CritBase", "暴击率", 0.50, True
                ),
                BattleCharacterSourceStat(
                    "character", "人物", "CritDamageBase", "暴击伤害", 1.0, True
                ),
            ),
        )
        projection = BattleHitBuffProjection(
            event_id="1:primary",
            modifiers=(
                BattleProjectedBuffModifier(
                    property_id="AtkUp",
                    additive_value=0.16,
                    interval_ids=("buff-1",),
                    buff_names=("测试攻击 Buff",),
                    confidence="高",
                ),
                BattleProjectedBuffModifier(
                    property_id="DamageResistCosmosBase",
                    additive_value=-0.10,
                    interval_ids=("debuff-1",),
                    buff_names=("测试减抗 Debuff",),
                    confidence="中",
                    target_scope="target",
                ),
            ),
            applied_interval_ids=("buff-1", "debuff-1"),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="高",
        )
        frozen = {row.property_id: row.value for row in baseline.stats}
        values = BattleBuffAttributeProjectionService.apply_additive(
            frozen,
            projection,
        )
        hit = SimpleNamespace(
            event_id="1:primary",
            damage=1000.0,
            classification="direct",
        )
        evidence = BattleSkillDamageEvidence(
            event_id="1:primary",
            damage_id="GE_Test",
            ability_id="GA_Test",
            damage_attribute="cosmos",
            damage_source_category="NORMAL",
            fixed_crit_rate=0.0,
            scaling_property_id="Atk",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=10,
            evidence_basis="测试静态倍率",
        )
        condition = BattleTargetCondition(
            target_name="墨菲斯托",
            enemy_level=90.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("cosmos", 0.30),),
            enemy_defense_base=1050.0,
        )

        result = BattleHitReplayService._replay_direct(
            hit=hit,
            evidence=evidence,
            baseline=baseline,
            projection=projection,
            values=values,
            character_level=80.0,
            analysis=SimpleNamespace(target_condition=condition),
            applied_intervals=projection.applied_interval_ids,
            excluded_intervals=(),
        )

        scaling = next(row for row in result.factors if row.factor_id == "scaling")
        self.assertAlmostEqual(1510.0, scaling.value)
        self.assertEqual(
            ("人物", "弧盘", "装备", "装备", "Buff：测试攻击 Buff"),
            tuple(term.source_name for term in scaling.terms),
        )
        resistance = next(
            row for row in result.factors if row.factor_id == "resistance"
        )
        self.assertAlmostEqual(calculate_resistance_multiplier(0.20), resistance.value)
        self.assertIn(
            "Buff：测试减抗 Debuff",
            tuple(term.source_name for term in resistance.terms),
        )
        assert result.non_critical_damage is not None
        self.assertAlmostEqual(
            result.non_critical_damage * 1.5,
            result.expected_damage,
        )
        self.assertIsNotNone(result.signed_error_percent)
        assert result.selected_damage is not None
        self.assertAlmostEqual(
            (result.selected_damage - 1000.0) / 1000.0 * 100.0,
            result.signed_error_percent,
        )
        self.assertEqual("直伤", result.formula_type)


if __name__ == "__main__":
    unittest.main()
