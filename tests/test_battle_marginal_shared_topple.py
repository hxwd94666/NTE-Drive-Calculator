# 验证队友名下的团队倾陷伤害不会重复进入当前角色伤害基数。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.services.battle_marginal_calculation_support import quantify_marginal


class BattleMarginalSharedToppleTest(unittest.TestCase):
    def test_cross_owner_topple_keeps_increment_out_of_role_basis(self) -> None:
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
            role_damage=100.0,
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
        self.assertEqual(100.0, quantification.basis_damage)
        self.assertEqual(0.0, quantification.fully_quantified_damage)
        self.assertEqual(100.0, quantification.proven_unchanged_damage)
        self.assertEqual(10.0, increment)


if __name__ == "__main__":
    unittest.main()
