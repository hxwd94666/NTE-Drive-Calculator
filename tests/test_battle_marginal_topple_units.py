# 验证属性单位边际按正式逐角色贡献处理团队倾陷。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from tests.test_battle_marginal_calculation_service import (
    CHARACTER_ID,
    _analysis,
    _hit,
)


class BattleMarginalToppleUnitTests(unittest.TestCase):
    def test_topple_unit_reuses_source_character_contribution(self) -> None:
        hit = _hit(classification="topple")
        strength_term = BattleHitReplayTerm(
            term_id="character:1072:UnbalIntensityBase",
            property_id="UnbalIntensityBase",
            label="倾陷强度",
            value=100.0,
            source_group="resolved",
            source_name="角色面板",
            is_percent=False,
            evidence_basis="冻结角色面板",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=1000.0,
            non_critical_damage=2000.0,
            critical_damage=None,
            selected_damage=2000.0,
            selected_error_percent=100.0,
            critical_state="not_applicable",
            confidence="低",
            factors=(
                BattleHitReplayFactor(
                    factor_id="topple_character:1072",
                    label="灵可倾陷贡献",
                    value=4000.0 / 3.0,
                    evidence_basis="逐角色倾陷公式",
                    terms=(strength_term,),
                ),
                BattleHitReplayFactor(
                    factor_id="topple_character:1001",
                    label="队友倾陷贡献",
                    value=2000.0 / 3.0,
                    evidence_basis="逐角色倾陷公式",
                ),
            ),
            critical_rate=0.0,
            expected_damage=2000.0,
            critical_policy="disabled",
        )
        analysis = _analysis(hit, replay)

        units = BattleMarginalCalculationService.default_units(
            analysis.baselines[0]
        )
        self.assertEqual(6.0, units["UnbalIntensityBase"])
        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"UnbalIntensityBase": 6.0},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(
            2000.0 / 3.0,
            result.quantification.fully_quantified_damage,
        )
        self.assertAlmostEqual(2000.0 / 3.0, result.baseline_damage)
        self.assertAlmostEqual(
            2000.0 / 3.0 + 10.0,
            result.known_projection_damage,
        )
        self.assertAlmostEqual(1.5, result.full_role_gain_percent)
        self.assertAlmostEqual(1.0, result.full_team_gain_percent)
        self.assertAlmostEqual(
            result.quantification.basis_damage,
            result.quantification.fully_quantified_damage
            + result.quantification.partially_quantified_damage
            + result.quantification.unavailable_damage
            + result.quantification.proven_unchanged_damage,
        )

    def test_topple_unit_accepts_omitted_zero_strength_term(self) -> None:
        hit = _hit(classification="topple")
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=1000.0,
            non_critical_damage=2000.0,
            critical_damage=None,
            selected_damage=2000.0,
            selected_error_percent=100.0,
            critical_state="not_applicable",
            confidence="低",
            factors=(
                BattleHitReplayFactor(
                    factor_id=f"topple_character:{CHARACTER_ID}",
                    label="灵可倾陷贡献",
                    value=1000.0,
                    evidence_basis="零倾陷强度的逐角色公式",
                ),
                BattleHitReplayFactor(
                    factor_id="topple_character:1001",
                    label="队友倾陷贡献",
                    value=1000.0,
                    evidence_basis="逐角色倾陷公式",
                ),
            ),
            critical_rate=0.0,
            expected_damage=2000.0,
            critical_policy="disabled",
        )
        analysis = _analysis(hit, replay)
        analysis.baselines = (replace(
            analysis.baselines[0],
            stats=tuple(
                replace(row, value=0.0)
                if row.property_id == "UnbalIntensityBase"
                else row
                for row in analysis.baselines[0].stats
            ),
        ),)

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"UnbalIntensityBase": 6.0},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(500.0, result.quantification.fully_quantified_damage)
        self.assertAlmostEqual(510.0, result.known_projection_damage)
        self.assertAlmostEqual(2.0, result.full_role_gain_percent)


if __name__ == "__main__":
    unittest.main()
