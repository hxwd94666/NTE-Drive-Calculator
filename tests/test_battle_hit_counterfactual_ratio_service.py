# 验证未知目标时只要求实际变化的逐击乘区具备输入。
from __future__ import annotations

import unittest

import src.domain.battle_report as battle_report
from src.domain.battle_counterfactual import BattleBuildCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
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
        "CritBase": 0.5,
        "CritDamageBase": 1.0,
        "DamageUpGeneralBase": 0.0,
        "DamageUpNatureBase": 0.0,
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


def _hit() -> BattleAnalysisHit:
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
        damage_attribute="nature",
        target_id="target:1",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def _unknown_replay() -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id="hit:1",
        observed_damage=1000.0,
        non_critical_damage=None,
        critical_damage=None,
        selected_damage=None,
        selected_error_percent=None,
        critical_state="unreplayable",
        confidence="低",
        factors=(),
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
        critical_policy="character",
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
            hit=_hit(),
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

    def test_unknown_target_defense_ignore_is_unavailable(self) -> None:
        result = self._compare(_baseline(DefIgnore=0.1))

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.quantified_ratio)
        self.assertEqual(
            "target_defense_dependency_changed",
            result.gaps[0].code,
        )

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
        self.assertEqual("structured_selected", result.method)

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
