# 验证边际与配装反事实领域值不再暴露未知即零的旧数值字段。
from __future__ import annotations

import unittest
from dataclasses import fields

from src.domain.battle_counterfactual import (
    BattleBuildCounterfactual,
    BattleBuildHitCounterfactual,
    BattleBuildRoleCounterfactual,
    BattleBuildVitalCounterfactual,
    BattleMarginalResult,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)


def _field_names(value_type) -> set[str]:
    return {field.name for field in fields(value_type)}


class BattlePartialQuantificationContractTests(unittest.TestCase):
    def test_marginal_uses_nullable_known_and_full_results(self) -> None:
        names = _field_names(BattleMarginalResult)

        self.assertTrue({
            "known_projection_damage",
            "quantified_role_gain_percent",
            "quantified_team_gain_percent",
            "full_role_gain_percent",
            "full_team_gain_percent",
            "quantification",
        }.issubset(names))
        self.assertTrue({
            "predicted_damage",
            "role_gain_percent",
            "team_dps_gain_percent",
            "supported_damage",
            "unsupported_damage",
            "coverage_percent",
        }.isdisjoint(names))

    def test_build_types_separate_known_candidate_and_heuristic_values(self) -> None:
        hit_names = _field_names(BattleBuildHitCounterfactual)
        vital_names = _field_names(BattleBuildVitalCounterfactual)
        role_names = _field_names(BattleBuildRoleCounterfactual)
        build_names = _field_names(BattleBuildCounterfactual)

        projections = {
            "known_projection_damage",
            "candidate_damage",
            "heuristic_projection_damage",
            "quantification",
        }
        self.assertTrue(projections.issubset(hit_names))
        self.assertTrue(projections.issubset(vital_names))
        self.assertTrue(projections.issubset(role_names))
        self.assertTrue(projections.issubset(build_names))
        self.assertTrue({
            "predicted_damage", "ratio", "method", "confidence", "explanation",
        }.isdisjoint(hit_names))
        self.assertTrue({
            "predicted_damage", "estimated_damage",
        }.isdisjoint(role_names))
        self.assertTrue({
            "predicted_damage", "predicted_dps", "estimated_damage",
        }.isdisjoint(build_names))

    def test_partial_damage_summary_keeps_all_four_buckets(self) -> None:
        gap = BattleQuantificationGap(
            code="target_profile_missing",
            dimension_id="target_defense",
            dependency_scope="target_sensitive",
            property_ids=("DefIgnore",),
            explanation="缺少冻结敌方防御画像",
        )
        summary = BattleDamageQuantification.from_buckets(
            status="partial",
            fully_quantified_damage=40.0,
            partially_quantified_damage=20.0,
            unavailable_damage=30.0,
            proven_unchanged_damage=10.0,
            quantified_increment=5.0,
            gaps=(gap,),
        )

        self.assertEqual(100.0, summary.basis_damage)
        self.assertEqual("partial", summary.status)
        self.assertEqual(40.0, summary.fully_quantified_damage)
        self.assertEqual(20.0, summary.partially_quantified_damage)
        self.assertEqual(30.0, summary.unavailable_damage)
        self.assertEqual(10.0, summary.proven_unchanged_damage)


if __name__ == "__main__":
    unittest.main()
