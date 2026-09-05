# 验证未知目标时只要求实际变化的逐击乘区具备输入。
from __future__ import annotations

import unittest
from dataclasses import replace

import src.domain.battle_report as battle_report
from src.domain.battle_counterfactual import BattleBuildCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)


def _baseline(**changes: float) -> BattleCharacterBaseline:
    values = {
        "AtkBase": 1000.0,
        "AtkUp": 0.0,
        "AtkAdd": 0.0,
        "HPMaxBase": 2000.0,
        "HPMaxUp": 0.0,
        "HPMaxAdd": 0.0,
        "DefBase": 500.0,
        "DefUp": 0.0,
        "DefAdd": 0.0,
        "CritBase": 0.5,
        "CritDamageBase": 1.0,
        "DamageUpGeneralBase": 0.0,
        "DamageUpNatureBase": 0.0,
        "DamageUpChaosBase": 0.0,
        "MagBase": 100.0,
        "DefIgnore": 0.0,
        "DamagePenetrateNature": 0.0,
    }
    values.update(changes)
    return BattleCharacterBaseline(
        character_id=1072,
        character_name="灵可",
        source="fixture",
        stats=tuple(
            BattleCharacterStat(key, key, value, key != "AtkBase")
            for key, value in values.items()
        ),
    )


def _hit(*, damage_attribute: str = "nature") -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="hit:1",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=1072,
        character_name="灵可",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute=damage_attribute,
        target_id="target:1",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def _scaling_factor(scaling_id: str) -> BattleHitReplayFactor:
    base_id = {
        "Atk": "AtkBase",
        "HPMax": "HPMaxBase",
        "Def": "DefBase",
    }[scaling_id]
    return BattleHitReplayFactor(
        factor_id="scaling",
        label=f"{scaling_id} 乘区",
        value=1000.0,
        evidence_basis="fixture",
        terms=(BattleHitReplayTerm(
            term_id=f"scaling:{base_id}",
            property_id=base_id,
            label=base_id,
            value=1000.0,
            source_group="panel",
            source_name="fixture",
            is_percent=False,
            evidence_basis="fixture",
        ),),
    )


def _unknown_replay(scaling_id: str | None = "Atk") -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id="hit:1",
        observed_damage=1000.0,
        non_critical_damage=None,
        critical_damage=None,
        selected_damage=None,
        selected_error_percent=None,
        critical_state="unreplayable",
        confidence="低",
        factors=(() if scaling_id is None else (_scaling_factor(scaling_id),)),
        critical_policy="unknown",
    )


def _selected_replay(non_critical_damage: float) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id="hit:1",
        observed_damage=1000.0,
        non_critical_damage=non_critical_damage,
        critical_damage=non_critical_damage * 2.0,
        selected_damage=non_critical_damage,
        selected_error_percent=0.0,
        critical_state="non_critical",
        confidence="高",
        factors=(),
        expected_damage=non_critical_damage * 1.5,
        critical_policy="character",
    )


def _ring_replay(
    hit: BattleAnalysisHit,
    *factors: BattleHitReplayFactor,
) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=None,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state="not_applicable",
        confidence="高",
        factors=factors,
        critical_policy="disabled",
    )


def _condition() -> BattleTargetCondition:
    return BattleTargetCondition(
        target_name="测试目标",
        enemy_level=90.0,
        scene="outer_realm",
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=(("nature", 0.4),),
        enemy_defense_base=1200.0,
    )


class BattleHitCounterfactualRatioServiceTests(unittest.TestCase):
    def _compare(self, candidate, **kwargs):
        return BattleHitCounterfactualRatioService.compare(
            hit=kwargs.pop("hit", _hit()),
            original_baseline=_baseline(),
            candidate_baseline=candidate,
            original_replay=kwargs.pop("original_replay", _unknown_replay()),
            candidate_replay=kwargs.pop("candidate_replay", None),
            **kwargs,
        )

    def test_unknown_target_attack_and_damage_up_are_complete(self) -> None:
        result = self._compare(
            _baseline(AtkUp=0.1, DamageUpGeneralBase=0.2),
        )

        self.assertEqual("complete", result.status)
        self.assertAlmostEqual(1.32, result.quantified_ratio)
        self.assertEqual("character_only", result.dependency_scope)
        self.assertIn("target_defense", result.cancelled_dimension_ids)
        self.assertIn("target_resistance", result.cancelled_dimension_ids)

    def test_inferred_buff_state_downgrades_formula_complete_to_partial(self) -> None:
        projection = battle_report.BattleHitBuffProjection(
            event_id="hit:1",
            modifiers=(battle_report.BattleProjectedBuffModifier(
                property_id="AtkUp",
                additive_value=0.16,
                interval_ids=("buff:mofeikesi",),
                buff_names=("好狗狗走四方：控制额外攻击",),
                confidence="低",
                target_scope="team",
            ),),
            applied_interval_ids=("buff:mofeikesi",),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="低",
        )

        result = self._compare(
            _baseline(AtkBase=1100.0),
            original_projection=projection,
            candidate_projection=projection,
        )

        self.assertEqual("partial", result.status)
        self.assertAlmostEqual(1100.0 / 1000.0, result.quantified_ratio)
        self.assertIn("scaling", result.included_dimension_ids)
        self.assertTrue(any(
            gap.code == "buff_state_inferred"
            and gap.property_ids == ("AtkUp",)
            for gap in result.gaps
        ))

    def test_unrelated_inferred_buff_does_not_downgrade_changed_dimension(self) -> None:
        projection = battle_report.BattleHitBuffProjection(
            event_id="hit:1",
            modifiers=(battle_report.BattleProjectedBuffModifier(
                property_id="DamageUpNatureBase",
                additive_value=0.20,
                interval_ids=("buff:nature",),
                buff_names=("灵属性增伤",),
                confidence="低",
                target_scope="self",
            ),),
            applied_interval_ids=("buff:nature",),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="低",
        )

        result = self._compare(
            _baseline(CritDamageBase=1.2),
            original_projection=projection,
            candidate_projection=projection,
            original_replay=_selected_replay(1000.0),
        )

        self.assertEqual("complete", result.status)
        self.assertNotIn(
            "buff_state_inferred",
            {gap.code for gap in result.gaps},
        )

    def test_unknown_scaling_does_not_default_to_attack(self) -> None:
        result = self._compare(
            _baseline(AtkUp=0.1),
            original_replay=_unknown_replay(None),
        )

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.quantified_ratio)
        self.assertEqual("scaling_dependency_unresolved", result.gaps[0].code)

    def test_special_shared_damage_does_not_fall_back_to_direct_components(self) -> None:
        hit = _hit()
        hit = replace(
            hit,
            character_id=1039,
            character_name="法帝娅",
            skill_name="变轨技能：存在证明的体验",
            damage_name="存在证明的体验",
            ability_id="GA_Fadia_Skill",
            gameplay_effect_id="GE_Player_Fadia_ZhouYin_Damage",
        )

        result = self._compare(
            _baseline(AtkUp=0.1),
            hit=hit,
        )

        self.assertEqual("unavailable", result.status)
        self.assertEqual("unsupported_source_linkage", result.method)
        self.assertEqual("fadia_shared_source_unresolved", result.gaps[0].code)

    def test_replay_scaling_terms_identify_hp_scaling(self) -> None:
        result = self._compare(
            _baseline(HPMaxUp=0.1),
            original_replay=_unknown_replay("HPMax"),
        )

        self.assertEqual("complete", result.status)
        self.assertAlmostEqual(1.1, result.quantified_ratio)

    def test_skill_evidence_takes_priority_over_replay_scaling(self) -> None:
        evidence = BattleSkillDamageEvidence(
            event_id="hit:1",
            damage_id="damage:1",
            ability_id="ability:1",
            damage_attribute="nature",
            damage_source_category="skill",
            fixed_crit_rate=0.0,
            scaling_property_id="Def",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=1,
            evidence_basis="fixture",
        )
        result = self._compare(
            _baseline(DefUp=0.1),
            original_replay=_unknown_replay("Atk"),
            skill_evidence=evidence,
        )

        self.assertEqual("complete", result.status)
        self.assertAlmostEqual(1.1, result.quantified_ratio)

    def test_unknown_target_defense_ignore_is_unavailable(self) -> None:
        result = self._compare(_baseline(DefIgnore=0.1))

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.quantified_ratio)
        self.assertEqual(
            "target_defense_dependency_changed",
            result.gaps[0].code,
        )

    def test_psychically_defense_ignore_is_not_applicable(self) -> None:
        result = self._compare(
            _baseline(DefIgnore=0.1),
            hit=_hit(damage_attribute="psychically"),
        )

        self.assertEqual("not_applicable", result.status)
        self.assertEqual(1.0, result.quantified_ratio)

    def test_unknown_target_penetration_is_unavailable(self) -> None:
        result = self._compare(_baseline(DamagePenetrateNature=0.1))

        self.assertEqual("unavailable", result.status)
        self.assertEqual(
            "target_resistance_dependency_changed",
            result.gaps[0].code,
        )

    def test_known_target_defense_and_resistance_are_complete(self) -> None:
        result = self._compare(
            _baseline(DefIgnore=0.1, DamagePenetrateNature=0.1),
            target_condition=_condition(),
        )

        self.assertEqual("complete", result.status)
        self.assertGreater(result.quantified_ratio, 1.0)
        self.assertEqual("target_sensitive", result.dependency_scope)

    def test_character_and_unknown_target_changes_are_partial(self) -> None:
        result = self._compare(_baseline(AtkUp=0.1, DefIgnore=0.1))

        self.assertEqual("partial", result.status)
        self.assertAlmostEqual(1.1, result.quantified_ratio)
        self.assertEqual(("scaling",), result.included_dimension_ids)
        self.assertEqual(
            "target_defense_dependency_changed",
            result.gaps[0].code,
        )

    def test_unknown_critical_policy_cancels_when_unchanged(self) -> None:
        result = self._compare(_baseline(AtkUp=0.1))

        self.assertEqual("complete", result.status)
        self.assertIn("critical", result.cancelled_dimension_ids)

    def test_unknown_critical_policy_change_creates_gap(self) -> None:
        result = self._compare(_baseline(CritDamageBase=1.2))

        self.assertEqual("unavailable", result.status)
        self.assertEqual("critical_policy_unknown", result.gaps[0].code)

    def test_complete_paired_replay_takes_priority(self) -> None:
        result = self._compare(
            _baseline(AtkUp=0.5, DefIgnore=0.5),
            original_replay=_selected_replay(100.0),
            candidate_replay=_selected_replay(120.0),
        )

        self.assertEqual("complete", result.status)
        self.assertAlmostEqual(1.2, result.quantified_ratio)
        self.assertEqual("structured_expected", result.method)

    def test_standard_reaction_ring_strength_uses_shared_scaling_zone(self) -> None:
        hit = replace(
            _hit(),
            classification="reaction",
            damage_name="浊燃",
            gameplay_effect_id="Buff_Reaction_5_new_1036",
        )
        replay = _ring_replay(
            hit,
            BattleHitReplayFactor(
                factor_id="scaling",
                label="环合强度区",
                value=1.0 + 100.0 / 600.0,
                evidence_basis="正式浊燃公式",
            ),
        )

        result = self._compare(
            _baseline(MagBase=106.0),
            hit=hit,
            original_replay=replay,
        )

        self.assertEqual("complete", result.status)
        self.assertAlmostEqual(
            (1.0 + 106.0 / 600.0) / (1.0 + 100.0 / 600.0),
            result.quantified_ratio,
        )

    def test_scorch_uses_fixed_half_crit_damage_but_not_panel_crit_rate(self) -> None:
        hit = replace(
            _hit(damage_attribute="incantation"),
            classification="reaction",
            damage_name="浊燃",
            gameplay_effect_id="Buff_Reaction_5_new_1036",
        )
        replay = replace(
            _unknown_replay(),
            critical_rate=0.5,
            critical_policy="fixed",
        )

        crit_damage = self._compare(
            _baseline(CritDamageBase=1.02),
            hit=hit,
            original_replay=replay,
        )
        crit_rate = self._compare(
            _baseline(CritBase=0.6),
            hit=hit,
            original_replay=replay,
        )

        self.assertEqual("complete", crit_damage.status)
        self.assertAlmostEqual(1.51 / 1.50, crit_damage.quantified_ratio)
        self.assertEqual("not_applicable", crit_rate.status)
        self.assertEqual(1.0, crit_rate.quantified_ratio)

    def test_creation_consumes_defense_and_formal_element_resistance(self) -> None:
        hit = replace(
            _hit(damage_attribute="nature"),
            classification="reaction",
            damage_name="创生花",
            gameplay_effect_id="GE_ActorReaction_1_Damage",
        )
        replay = _ring_replay(hit, BattleHitReplayFactor(
            factor_id="scaling",
            label="环合强度区",
            value=1.0 + 100.0 / 600.0,
            evidence_basis="正式创生公式",
        ))

        result = self._compare(
            _baseline(DefIgnore=0.01, DamagePenetrateNature=0.01),
            hit=hit,
            original_replay=replay,
            target_condition=_condition(),
        )

        self.assertEqual("complete", result.status)
        self.assertIn("target_defense", result.included_dimension_ids)
        self.assertIn("target_resistance", result.included_dimension_ids)

    def test_scorch_ignores_attack_and_damage_increase_but_uses_target_formula(self) -> None:
        hit = replace(
            _hit(damage_attribute=""),
            classification="reaction",
            damage_name="浊燃",
            gameplay_effect_id="Buff_Reaction_5_new_1036",
        )
        replay = _ring_replay(hit, BattleHitReplayFactor(
            factor_id="scaling",
            label="环合强度区",
            value=1.0 + 100.0 / 600.0,
            evidence_basis="正式浊燃公式",
        ))
        ignored = self._compare(
            _baseline(
                AtkUp=0.1,
                DamageUpGeneralBase=0.1,
                DamageUpIncantationBase=0.1,
                CritBase=0.9,
            ),
            hit=hit,
            original_replay=replay,
            target_condition=_condition(),
        )
        target = self._compare(
            _baseline(DefIgnore=0.01, DamagePenetrateIncantation=0.01),
            hit=hit,
            original_replay=replay,
            target_condition=_condition(),
        )

        self.assertEqual("not_applicable", ignored.status)
        self.assertEqual("complete", target.status)
        self.assertIn("target_defense", target.included_dimension_ids)
        self.assertIn("target_resistance", target.included_dimension_ids)

    def test_nova_keeps_psyche_resistance_but_cancels_defense(self) -> None:
        hit = replace(
            _hit(damage_attribute=""),
            classification="reaction",
            damage_name="黯星",
            gameplay_effect_id="Buff_Reaction_4_new",
        )
        replay = _ring_replay(hit, BattleHitReplayFactor(
            factor_id="scaling",
            label="环合强度区",
            value=1.0 + 100.0 / 600.0,
            evidence_basis="正式黯星公式",
        ))
        defense = self._compare(
            _baseline(DefIgnore=0.01),
            hit=hit,
            original_replay=replay,
            target_condition=_condition(),
        )
        resistance = self._compare(
            _baseline(DamagePenetratePsyche=0.01),
            hit=hit,
            original_replay=replay,
            target_condition=_condition(),
        )

        self.assertEqual("not_applicable", defense.status)
        self.assertEqual("complete", resistance.status)
        self.assertEqual(("target_resistance",), resistance.included_dimension_ids)

    def test_true_direct_requires_special_attribute_override(self) -> None:
        result = self._compare(
            _baseline(DefIgnore=0.01),
            hit=_hit(damage_attribute="true"),
            target_condition=_condition(),
        )

        self.assertEqual("unavailable", result.status)
        self.assertEqual("true_attribute_override_missing", result.gaps[0].code)

    def test_nightmare_continuous_direct_uses_chaos_and_fixed_half_crit(self) -> None:
        hit = replace(
            _hit(damage_attribute="chaos"),
            classification="dot",
            damage_name="噩梦",
            gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage_LV6",
        )
        replay = _unknown_replay()

        chaos = self._compare(
            _baseline(DamageUpChaosBase=0.1),
            hit=hit,
            original_replay=replay,
        )
        crit_damage = self._compare(
            _baseline(CritDamageBase=1.02),
            hit=hit,
            original_replay=replay,
        )

        self.assertEqual("complete", chaos.status)
        self.assertAlmostEqual(1.1, chaos.quantified_ratio)
        self.assertEqual("complete", crit_damage.status)
        self.assertAlmostEqual(1.51 / 1.50, crit_damage.quantified_ratio)

    def test_weave_ring_strength_preserves_lingke_thirty_percent_branch(self) -> None:
        hit = replace(_hit(), classification="weave", damage_name="覆纹")
        strength = 1.0 + 0.20 * 100.0 / (100.0 + 180.0)
        followup = 1.30 * strength - 1.0
        replay = _ring_replay(
            hit,
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
        )

        result = self._compare(
            _baseline(MagBase=106.0),
            hit=hit,
            original_replay=replay,
        )
        candidate_strength = 1.0 + 0.20 * 106.0 / (106.0 + 180.0)

        self.assertEqual("complete", result.status)
        self.assertAlmostEqual(
            (1.30 * candidate_strength - 1.0) / followup,
            result.quantified_ratio,
        )

    def test_damage_summary_rejects_broken_bucket_invariant(self) -> None:
        with self.assertRaises(ValueError):
            BattleDamageQuantification(
                status="complete",
                basis_damage=100.0,
                fully_quantified_damage=90.0,
                partially_quantified_damage=0.0,
                unavailable_damage=0.0,
                proven_unchanged_damage=0.0,
                quantified_increment=5.0,
            )

    def test_battle_report_does_not_reexport_counterfactual_types(self) -> None:
        self.assertFalse(hasattr(battle_report, "BattleMarginalResult"))
        self.assertFalse(hasattr(battle_report, "BattleBuildCounterfactual"))
        self.assertIsNotNone(BattleBuildCounterfactual)


if __name__ == "__main__":
    unittest.main()
