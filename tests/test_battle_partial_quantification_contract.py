# 验证边际与配装反事实领域值不再暴露未知即零的旧数值字段。
from __future__ import annotations

import unittest

from src.domain.battle_report.counterfactual.quantification import BattleDamageQuantification, BattleQuantificationGap
from src.domain.battle_report.counterfactual.results import BattleDamageCoverage



class BattlePartialQuantificationContractTests(unittest.TestCase):
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

    def test_damage_coverage_separates_confirmed_and_unresolved_damage(self) -> None:
        coverage = BattleDamageCoverage(
            basis_damage=1_000.0,
            covered_damage=600.0,
            unresolved_damage=250.0,
        )

        self.assertEqual(60.0, coverage.covered_percent)
        self.assertEqual(25.0, coverage.unresolved_percent)
        with self.assertRaises(ValueError):
            BattleDamageCoverage(
                basis_damage=1_000.0,
                covered_damage=800.0,
                unresolved_damage=300.0,
            )


if __name__ == "__main__":
    unittest.main()
