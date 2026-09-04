# 验证团队倾陷按结构化公式贡献归入当前角色伤害基数。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.services.battle_marginal_calculation_support import quantify_marginal


class BattleMarginalSharedToppleTest(unittest.TestCase):
    def test_zero_damage_partial_marker_does_not_create_empty_partial_bucket(
        self,
    ) -> None:
        hit = SimpleNamespace(event_id="marker:1", character_id=1008)
        gap = BattleQuantificationGap(
            code="buff_state_inferred",
            dimension_id="buff_state_evidence",
            dependency_scope="character_only",
            property_ids=("AtkUp",),
            explanation="零伤害事件上的状态推断。",
        )
        ratio = BattleCounterfactualRatio.partial(
            1.1,
            method="fixture",
            confidence="低",
            dependency_scope="character_only",
            included_dimension_ids=("scaling",),
            cancelled_dimension_ids=(),
            gaps=(gap,),
            explanation="仅用于零伤害标记回归。",
        )

        quantification, increment = quantify_marginal(
            role_damage=0.0,
            relevant_hits=(hit,),
            hit_ratios={hit.event_id: ratio},
            vital_projections=(),
            topple_hits=(),
            topple_ratios={},
            replays={},
            anchor_damage=lambda _hit: 0.0,
            anchor_quantification=lambda _hit: None,
            character_id=1008,
        )

        self.assertEqual("not_applicable", quantification.status)
        self.assertEqual(0.0, increment)
        self.assertEqual(0.0, quantification.basis_damage)

    def test_cross_owner_topple_uses_formula_contribution_in_role_basis(self) -> None:
        character_id = 1008
        hit = SimpleNamespace(event_id="topple:1", character_id=1020)
        replay = SimpleNamespace(
            critical_state="not_applicable",
            factors=(
                SimpleNamespace(
                    factor_id=f"topple_character:{character_id}", value=600.0
                ),
                SimpleNamespace(factor_id="topple_character:1020", value=400.0),
            ),
        )

        quantification, increment = quantify_marginal(
            role_damage=600.0,
            relevant_hits=(),
            hit_ratios={},
            vital_projections=(),
            topple_hits=(hit,),
            topple_ratios={hit.event_id: 1.01},
            replays={hit.event_id: replay},
            anchor_damage=lambda _hit: 1000.0,
            anchor_quantification=lambda _hit: None,
            character_id=character_id,
        )

        self.assertEqual("complete", quantification.status)
        self.assertEqual(600.0, quantification.basis_damage)
        self.assertEqual(600.0, quantification.fully_quantified_damage)
        self.assertEqual(0.0, quantification.proven_unchanged_damage)
        self.assertAlmostEqual(10.0, increment)


if __name__ == "__main__":
    unittest.main()
