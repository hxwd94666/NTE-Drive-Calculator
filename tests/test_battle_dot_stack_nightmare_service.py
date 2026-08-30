# 验证噩梦层数的生成、过期和特殊结算边界。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.services.battle_dot_stack_state_service import (
    reconstruct_dot_stack_states,
)
from tests.test_battle_dot_stack_state_service import (
    _hit,
    _nightmare_evade_hit,
    _nightmare_q_hit,
)


class BattleDotStackNightmareServiceTests(unittest.TestCase):
    def test_nightmare_uses_prior_melee_hit_count(self) -> None:
        hits = (
            _hit("a1", 100_000, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            _hit("a2", 200_000, "GE_Player_Lacrimosa_Melee1_1_Damage", 1004),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage_LV6", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)["dot"]

        self.assertEqual(2, state.coefficient)

    def test_nightmare_extreme_evade_multihit_adds_each_layer(self) -> None:
        hits = (
            _hit("a1", 100_000, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            *(
                _nightmare_evade_hit(f"evade-{index}", 200_000 + index)
                for index in range(6)
            ),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)["dot"]

        self.assertEqual(7, state.coefficient)

    def test_nightmare_layers_expire_at_their_own_active_times(self) -> None:
        hits = (
            _hit("a1", 100_000, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            _hit("a2", 1_100_000, "GE_Player_Lacrimosa_Melee2_Damage", 1004),
            _hit("first", 2_000_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
            _hit("second", 3_500_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
            _hit("third", 4_500_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        states = reconstruct_dot_stack_states(analysis, None)

        self.assertEqual(2, states["first"].coefficient)
        self.assertEqual(1, states["second"].coefficient)
        self.assertEqual(1, states["third"].coefficient)

    def test_nightmare_skill_each_hit_adds_one_layer(self) -> None:
        hits = (
            _hit("e", 100_000, "GE_Player_Lacrimosa_Skill_Damage", 1004),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)["dot"]

        self.assertEqual(1, state.coefficient)

    def test_enhanced_zankou_skill_adds_five_after_damage(self) -> None:
        hits = (
            _hit("e", 100_000, "GE_Player_Zankou_Skill1_1_Damage", 1036),
            _hit("dot", 1_100_000, "GE_Player_Zankou_DotDamage", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)["dot"]

        self.assertEqual(5, state.coefficient)

    def test_nightmare_q_each_hit_adds_one_layer(self) -> None:
        hits = (
            _nightmare_q_hit("q1", 100_000),
            _nightmare_q_hit("q2", 200_000),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)["dot"]

        self.assertEqual(2, state.coefficient)
        self.assertIn("每个有效直伤 hit 施加 1 层", state.evidence_basis)

    def test_nightmare_third_awaken_settlement_is_not_replayed_as_normal_tick(
        self,
    ) -> None:
        hits = (
            _hit("a1", 100_000, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            _hit("a5", 200_000, "GE_Player_Lacrimosa_Melee5_Damage", 1004),
            _hit("settle", 220_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
            _hit("later", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1004,
                "profile": {"selected_awaken_effect_ids": ["Effect3"]},
            }],
        }

        states = reconstruct_dot_stack_states(analysis, build)

        self.assertEqual(0, states["settle"].coefficient)
        self.assertEqual("未解析", states["settle"].confidence)
        self.assertEqual(1, states["later"].coefficient)


if __name__ == "__main__":
    unittest.main()
