# 验证逐击重放保留有符号误差、暴击期望和可寻址属性来源。
from __future__ import annotations

import unittest
from math import floor
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit, BattleAnalysisSnapshot,
    BattleCharacterBaseline, BattleCharacterStat,
    BattleHitBuffProjection, BattleHitReplayFactor, BattleHitReplayResult,
    BattleSkillDamageEvidence, BattleTargetCondition,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_hit_local_crit_inference_service import (
    BattleHitLocalCritInferenceService,
)
from src.services.battle_hit_replay_support import (
    apply_observed_damage_correction,
    settle_replay_damage,
)
from src.services.battle_special_hit_replay_service import (
    BattleSpecialHitReplayService,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
)

class BattleHitReplayServiceTests(unittest.TestCase):
    def test_replay_settlement_floors_only_after_all_factors(self) -> None:
        self.assertEqual(10.0, settle_replay_damage(10.75))
        self.assertEqual(0.0, settle_replay_damage(-0.25))

    def test_overkill_uses_raw_report_for_formula_but_effective_damage_for_total(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1049:primary",
            sequence=1049,
            relative_time_us=93_785_923,
            character_id=1075,
            character_name="伊洛伊",
            skill_name="普通攻击",
            damage_name="普通攻击",
            damage_component="skill",
            attack_type="普攻",
            damage_attribute="chaos",
            target_id="target",
            target_name="目标",
            damage=1.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            raw_damage=1_896.0,
            overkill_damage=1_895.0,
            damage_correction_kind="nte_core_overkill_v3",
            damage_correction_basis="fixture",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=1.0,
            non_critical_damage=1_010.0,
            critical_damage=1_600.0,
            selected_damage=1_010.0,
            selected_error_percent=100_900.0,
            critical_state="non_critical",
            confidence="中",
            factors=(),
            expected_damage=1_200.0,
        )

        corrected = apply_observed_damage_correction(replay, hit)

        self.assertEqual(1.0, hit.damage)
        self.assertEqual(1_896.0, corrected.observed_damage)
        self.assertEqual(1.0, corrected.reported_damage)
        self.assertEqual(
            "reported_hit_before_overkill",
            corrected.observed_damage_source,
        )
        self.assertAlmostEqual(-46.7299578, corrected.signed_error_percent)

    def test_layered_damage_is_not_used_for_local_crit_pairing(self) -> None:
        baseline = BattleCharacterBaseline(
            character_id=1004,
            character_name="安魂曲",
            source="fixture",
            stats=(BattleCharacterStat("CritDamageBase", "暴击伤害", 1.14, True),),
        )
        hits = tuple(
            SimpleNamespace(
                event_id=f"nightmare:{index}",
                character_id=1004,
                gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage_LV6",
            )
            for index in range(4)
        )
        stack_factor = BattleHitReplayFactor(
            factor_id="state_coefficient",
            label="噩梦当前层数",
            value=1.0,
            evidence_basis="fixture",
        )
        results = tuple(
            BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=damage,
                non_critical_damage=565.0,
                critical_damage=1209.1,
                selected_damage=565.0,
                selected_error_percent=0.0,
                critical_state="ambiguous",
                confidence="低",
                factors=(stack_factor,),
            )
            for hit, damage in zip(hits, (565.0, 565.0, 1130.0, 1130.0))
        )
        analysis = SimpleNamespace(hits=hits, baselines=(baseline,))

        projected = BattleHitLocalCritInferenceService.apply(
            analysis,
            results,
        )

        self.assertEqual(results, projected)

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
        self.assertEqual(float(floor(expected)), result.selected_damage)
        self.assertAlmostEqual(expected, contribution.value)
        self.assertIn(f"{defense:.6f}", contribution.formula)
        self.assertIn("0.800000", contribution.formula)
        self.assertEqual("not_applicable", result.critical_state)
        self.assertEqual("disabled", result.critical_policy)
        self.assertEqual(0.0, result.critical_rate)
        self.assertEqual("达芙蒂尔·额外倾陷伤害", result.formula_type)
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
        expected = float(floor(1000.0 * (1.30 * 1.08 - 1.0)))
        self.assertEqual(expected, result.selected_damage)
        self.assertEqual("not_applicable", result.critical_state)
        self.assertIn("弱点感应", result.factors[2].evidence_basis)
        factor_ids = {factor.factor_id for factor in result.factors}
        self.assertNotIn("lingke_damage_up", factor_ids)

    def test_weave_uses_paired_damage_source_magbase_including_dot(self) -> None:
        primary = BattleAnalysisHit(
            event_id="8:primary",
            sequence=8,
            relative_time_us=3_000_000,
            character_id=1075,
            character_name="伊洛伊",
            skill_name="蚀心",
            damage_name="蚀心",
            damage_component="dot",
            attack_type="持续伤害",
            damage_attribute="nature",
            target_id="target",
            target_name="目标",
            damage=1000.0,
            direction="outgoing",
            is_follow_up=False,
            classification="dot",
        )
        weave = BattleAnalysisHit(
            event_id="8:follow_up",
            sequence=8,
            relative_time_us=3_000_000,
            character_id=999,
            character_name="错误覆纹归属",
            skill_name="清明梦",
            damage_name="覆纹追加攻击",
            damage_component="reaction",
            attack_type="follow_up",
            damage_attribute="nature",
            target_id="target",
            target_name="目标",
            damage=297.0,
            direction="outgoing",
            is_follow_up=True,
            classification="weave",
        )
        analysis = SimpleNamespace(
            hits=(primary, weave),
            baselines=(
                BattleCharacterBaseline(
                    character_id=1075,
                    character_name="伊洛伊",
                    source="fixture",
                    stats=(BattleCharacterStat(
                        "MagBase", "环合强度", 120.0, False,
                    ),),
                ),
                BattleCharacterBaseline(
                    character_id=999,
                    character_name="错误覆纹归属",
                    source="fixture",
                    stats=(BattleCharacterStat(
                        "MagBase", "环合强度", 0.0, False,
                    ),),
                ),
            ),
            buff_intervals=(),
            target_condition=None,
        )

        results = BattleHitReplayService.replay(
            analysis,
            (),
            apply_observed_refinements=False,
        )
        replay = next(row for row in results if row.event_id == weave.event_id)

        self.assertEqual(296.0, replay.selected_damage)
        strength = next(
            row for row in replay.factors if row.factor_id == "weave_strength"
        )
        self.assertIn("原伤害来源角色环合强度 120", strength.evidence_basis)

    def test_standalone_weave_reports_source_evidence_gap(self) -> None:
        weave = BattleAnalysisHit(
            event_id="9:primary",
            sequence=9,
            relative_time_us=4_000_000,
            character_id=None,
            character_name="未知角色",
            skill_name="覆纹追加攻击",
            damage_name="覆纹追加攻击",
            damage_component="reaction",
            attack_type="覆纹",
            damage_attribute="nature",
            target_id="target",
            target_name="目标",
            damage=1136.0,
            direction="outgoing",
            is_follow_up=False,
            classification="weave",
        )
        analysis = SimpleNamespace(
            hits=(weave,),
            baselines=(),
            buff_intervals=(),
            target_condition=None,
        )

        replay = BattleHitReplayService.replay(
            analysis,
            (),
            apply_observed_refinements=False,
        )[0]

        self.assertEqual("unreplayable", replay.critical_state)
        self.assertIn("被记录原伤害", replay.missing_evidence[0])
        self.assertNotIn("缺少角色面板", replay.missing_evidence)

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
            state_multiplier: float = 1.0,
            state_label: str = "",
            dot_final_multiplier: float = 1.0,
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
                state_multiplier=state_multiplier,
                state_multiplier_label=state_label,
                state_multiplier_basis="按目标逐击重放",
                state_confidence="中",
                dot_final_multiplier=dot_final_multiplier,
                dot_final_multiplier_basis=(
                    "早雾「可以吃吗？」：结算前 2 种 DOT"
                    if dot_final_multiplier != 1.0 else ""
                ),
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
        stacked_scorch = replay(
            channel_id="reaction_scorch",
            character_id=1003,
            attribute="incantation",
            observed=6203.0,
            level_multiplier=2700.0,
            static_multiplier=1.5,
            ring_strength=60.0,
            state_multiplier=3.0,
            state_label="浊燃结算前层数",
        )
        enhanced_scorch = replay(
            channel_id="reaction_scorch",
            character_id=1003,
            attribute="incantation",
            observed=3101.0,
            level_multiplier=2700.0,
            static_multiplier=1.5,
            ring_strength=60.0,
            dot_final_multiplier=1.5,
        )

        assert creation is not None and creation.selected_damage is not None
        assert scorch is not None and scorch.selected_damage is not None
        assert stacked_scorch is not None
        assert stacked_scorch.selected_damage is not None
        assert enhanced_scorch is not None
        assert enhanced_scorch.selected_damage is not None
        self.assertEqual(5198.0, creation.selected_damage)
        self.assertEqual(1378.0, scorch.selected_damage)
        self.assertEqual(4135.0, stacked_scorch.selected_damage)
        self.assertEqual(2067.0, enhanced_scorch.selected_damage)
        self.assertNotIn(
            "reaction_multiplier",
            {factor.factor_id for factor in scorch.factors},
        )
        self.assertEqual(
            3.0,
            next(
                factor.value
                for factor in stacked_scorch.factors
                if factor.factor_id == "state_coefficient"
            ),
        )
        self.assertEqual("not_applicable", creation.critical_state)
        self.assertEqual("disabled", creation.critical_policy)
        self.assertEqual("non_critical", scorch.critical_state)
        self.assertEqual(0.50, scorch.critical_rate)
        self.assertEqual("fixed", scorch.critical_policy)
        self.assertEqual(
            1.5,
            next(
                factor.value
                for factor in enhanced_scorch.factors
                if factor.factor_id == "dot_final"
            ),
        )
        defense = next(
            row for row in scorch.factors if row.factor_id == "defense"
        )
        defense_values = {
            row.property_id: row.value for row in defense.terms
        }
        self.assertEqual(80.0, defense_values["CharacterLevel"])
        self.assertEqual(1014.0, defense_values["DefBase"])

    def test_zankou_scorch_replay_uses_zankou_baseline_after_source_replacement(
        self,
    ) -> None:
        hit = BattleAnalysisHit(
            event_id="scorch:1",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1003,
            character_name="早雾",
            skill_name="浊燃",
            damage_name="浊燃",
            damage_component="浊燃",
            attack_type="浊燃",
            damage_attribute="incantation",
            target_id="target",
            target_name="目标",
            damage=300.0,
            direction="outgoing",
            is_follow_up=False,
            classification="reaction",
            gameplay_effect_id="Buff_Reaction_5_new_1036",
        )
        baselines = (
            BattleCharacterBaseline(
                character_id=1003,
                character_name="早雾",
                source="fixture",
                stats=(BattleCharacterStat("MagBase", "环合强度", 0.0, False),),
            ),
            BattleCharacterBaseline(
                character_id=1036,
                character_name="残虹",
                source="fixture",
                stats=(
                    BattleCharacterStat("MagBase", "环合强度", 600.0, False),
                    BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
                ),
            ),
        )
        evidence = BattleSkillDamageEvidence(
            event_id=hit.event_id,
            damage_id=hit.gameplay_effect_id,
            ability_id="",
            damage_attribute="incantation",
            damage_source_category="R",
            fixed_crit_rate=0.5,
            scaling_property_id="Atk",
            scaling_multiplier=1.5,
            multiplier_coefficient=1.0,
            effective_skill_level=80,
            evidence_basis="残虹专属浊燃",
            source_character_id=1036,
            formula_kind="reaction",
            level_multiplier=100.0,
            state_multiplier=1.0,
            critical_policy="fixed",
        )
        analysis = SimpleNamespace(
            hits=(hit,),
            baselines=baselines,
            buff_intervals=(),
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

        result = BattleHitReplayService.replay(analysis, (evidence,))[0]

        self.assertEqual("fixed", result.critical_policy)
        self.assertEqual(
            2.0,
            next(
                factor.value
                for factor in result.factors
                if factor.factor_id == "scaling"
            ),
        )

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
            dot_final_multiplier=1.5,
            dot_final_multiplier_basis="早雾「可以吃吗？」：结算前 2 种 DOT",
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
        dot_final = next(
            row for row in result.factors if row.factor_id == "dot_final"
        )
        self.assertEqual(1.5, dot_final.value)

if __name__ == "__main__":
    unittest.main()
