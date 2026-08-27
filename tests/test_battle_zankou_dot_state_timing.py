# 验证残虹蚀心与鸩火只按正式施加点、扩散刷新和扣时停时钟重放。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_dot_stack_state_service import (
    reconstruct_dot_stack_states,
)


def _hit(event_id: str, time_us: int, effect: str) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=time_us,
        relative_time_us=time_us,
        character_id=1036,
        character_name="残虹",
        skill_name="",
        damage_name="",
        damage_component="skill",
        attack_type="普攻",
        damage_attribute="incantation",
        target_id="boss",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id=effect,
        ability_id="GA_Zankou_Melee",
        scope_half="upper",
    )


def _dot(event_id: str, time_us: int, effect: str) -> BattleAnalysisHit:
    return replace(_hit(event_id, time_us, effect), classification="dot")


class BattleZankouDotStateTimingTests(unittest.TestCase):
    def test_time_stop_pauses_erosion_and_venom_duration(self) -> None:
        hits = (
            _hit("erosion-apply", 0, "GE_Player_Zankou_Skill2_1_Damage"),
            _hit("venom-1", 100_000, "GE_Player_Zankou_MagicUltraSkill1_Damage"),
            _hit("venom-2", 200_000, "GE_Player_Zankou_MagicUltraSkill2_Damage"),
            _dot("erosion-dot", 35_000_000, "GE_Player_Zankou_DotDamage"),
            _dot("venom-dot", 35_100_000, "GE_Player_Zankou_DotUltraDamage"),
        )
        analysis = SimpleNamespace(
            hits=hits,
            time_stop_intervals=((1_000_000, 11_000_000),),
        )

        states = reconstruct_dot_stack_states(analysis, None)

        self.assertEqual(5, states["erosion-dot"].coefficient)
        self.assertEqual(5, states["venom-dot"].coefficient)

    def test_magic_melee_refreshes_existing_venom_without_adding_layers(self) -> None:
        hits = (
            _hit("q-1a", 0, "GE_Player_Zankou_MagicUltraSkill1_Damage"),
            _hit("q-1b", 100_000, "GE_Player_Zankou_MagicUltraSkill2_Damage"),
            _hit("magic-melee", 25_000_000, "GE_Player_Zankou_MagicMelee1_Damage"),
            _dot("venom-after-refresh", 45_000_000, "GE_Player_Zankou_DotUltraDamage"),
            _hit("q-2a", 50_000_000, "GE_Player_Zankou_MagicUltraSkill1_Damage"),
            _hit("q-2b", 50_100_000, "GE_Player_Zankou_MagicUltraSkill2_Damage"),
            _dot("venom-after-second-q", 51_000_000, "GE_Player_Zankou_DotUltraDamage"),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        states = reconstruct_dot_stack_states(analysis, None)

        self.assertEqual(5, states["venom-after-refresh"].coefficient)
        self.assertEqual(10, states["venom-after-second-q"].coefficient)

    def test_non_spreading_attacks_do_not_add_or_refresh_venom(self) -> None:
        hits = (
            _hit("q-a", 0, "GE_Player_Zankou_MagicUltraSkill1_Damage"),
            _hit("q-b", 100_000, "GE_Player_Zankou_MagicUltraSkill2_Damage"),
            _hit("reality-melee", 20_000_000, "GE_Player_Zankou_Melee1_Damage"),
            _hit("magic-branch", 25_000_000, "GE_Player_Zankou_MagicBranch1_Damage"),
            _dot("venom-after-expiry", 31_000_000, "GE_Player_Zankou_DotUltraDamage"),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)["venom-after-expiry"]

        self.assertEqual(1, state.coefficient)
        self.assertEqual("低", state.confidence)
        self.assertIn("至少存在 1 份", state.evidence_basis)

    def test_magic_melee_adds_erosion_but_not_venom(self) -> None:
        hits = (
            _hit("enhanced-skill", 0, "GE_Player_Zankou_Skill2_1_Damage"),
            _hit("melee-1", 100_000, "GE_Player_Zankou_MagicMelee1_Damage"),
            _hit("melee-2", 200_000, "GE_Player_Zankou_MagicMelee2_Damage"),
            _hit("melee-3", 300_000, "GE_Player_Zankou_MagicMelee2_1_Damage"),
            _dot("erosion-eight", 400_000, "GE_Player_Zankou_DotDamage"),
            _hit("melee-4", 500_000, "GE_Player_Zankou_MagicMelee2_2_Damage"),
            _hit("melee-5", 600_000, "GE_Player_Zankou_MagicMelee3_Damage"),
            _dot("erosion-ten", 700_000, "GE_Player_Zankou_DotDamage"),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        states = reconstruct_dot_stack_states(analysis, None)

        self.assertEqual(8, states["erosion-eight"].coefficient)
        self.assertEqual(10, states["erosion-ten"].coefficient)


if __name__ == "__main__":
    unittest.main()
