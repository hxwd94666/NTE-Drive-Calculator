# 验证统一分析快照向分层量化属性边际提供完整逐击输入。
from __future__ import annotations

import unittest

from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from tests.test_battle_counterfactual_analysis_service import _build, _evidence


class BattleCounterfactualMarginalIntegrationTests(unittest.TestCase):
    def test_unknown_crit_policy_remains_unquantified(self) -> None:
        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=_evidence(),
            build=_build(),
            capability_level="hit_axis",
        )
        baseline = analysis.baselines[0]
        units = BattleMarginalCalculationService.default_units(baseline)
        results = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=1072,
            edited_values={},
            units=units,
        )
        crit_damage = next(
            row for row in results if row.property_id == "CritDamageBase"
        )

        self.assertIsNone(crit_damage.known_projection_damage)
        quantification = crit_damage.quantification
        self.assertEqual("unavailable", quantification.status)
        self.assertEqual(0.0, quantification.fully_quantified_damage)
        self.assertEqual(0.0, quantification.partially_quantified_damage)
        self.assertIn("不会显示为零收益", crit_damage.assumption)


if __name__ == "__main__":
    unittest.main()
