# 验证普通浊燃与残虹浊燃的可见 DOT 状态投影。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_dot_stack_state_service import (
    reconstruct_dot_stack_states,
)


def _hit(event_id: str, time_us: int, effect: str, character_id: int) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=time_us,
        relative_time_us=time_us,
        character_id=character_id,
        character_name=str(character_id),
        skill_name="",
        damage_name="",
        damage_component="",
        attack_type="",
        damage_attribute="",
        target_id="boss",
        target_name="boss",
        damage=1.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id=effect,
        ability_id=(
            "GA_Lacrimosa_Melee"
            if character_id == 1004
            else "GA_Zankou_Melee"
        ),
    )


class BattleDotRecentObservationTests(unittest.TestCase):
    def test_zankou_reaction_and_observed_dot_activate_scorch(self) -> None:
        scorch_application = replace(
            _hit(
                "scorch-application",
                1_000_000,
                "GE_Player_Zankou_QTE_Damage",
                1036,
            ),
            attack_type="环合·浊燃",
        )
        hits = (
            scorch_application,
            _hit(
                "nightmare-trigger",
                1_100_000,
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                1004,
            ),
            _hit(
                "nightmare-next",
                1_200_000,
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                1004,
            ),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [
                {"character_id": 1003, "breakthrough_stage": 2, "profile": {}},
                {"character_id": 1036, "breakthrough_stage": 2, "profile": {}},
            ],
        }

        states = reconstruct_dot_stack_states(analysis, build)

        self.assertEqual(1.0, states["nightmare-trigger"].dot_final_multiplier)
        self.assertEqual(1.5, states["nightmare-next"].dot_final_multiplier)
        self.assertIn(
            "目标结算前已处于浊燃",
            states["nightmare-next"].dot_final_multiplier_basis,
        )

    def test_recent_venom_tick_restores_four_kind_sagiri_multiplier(self) -> None:
        hits = (
            _hit(
                "venom-application",
                0,
                "GE_Player_Zankou_MagicUltraSkill1_Damage",
                1036,
            ),
            _hit("scorch-first", 31_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "nightmare-application",
                31_100_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "erosion-application",
                31_200_000,
                "GE_Player_Zankou_MagicMelee1_Damage",
                1036,
            ),
            _hit(
                "venom-observed",
                31_300_000,
                "GE_Player_Zankou_DotUltraDamage",
                1036,
            ),
            _hit("scorch-after", 31_700_000, "Buff_Reaction_5_new_1036", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [
                {"character_id": 1003, "breakthrough_stage": 2, "profile": {}},
                {"character_id": 1036, "breakthrough_stage": 2, "profile": {}},
            ],
        }

        state = reconstruct_dot_stack_states(analysis, build)["scorch-after"]

        self.assertEqual(4, state.active_dot_kind_count)
        self.assertEqual(2.0, state.dot_final_multiplier)
        self.assertIn("近期正式跳伤确认", state.dot_final_multiplier_basis)


if __name__ == "__main__":
    unittest.main()
