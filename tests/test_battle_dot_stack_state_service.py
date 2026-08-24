# 验证噩梦、蚀心与鸩火按逐击正向重放层数且不使用观测伤害拟合。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_dot_stack_state_service import (
    BattleDotStackRules,
    load_official_dot_stack_rules,
    reconstruct_dot_stack_states,
)


OFFICIAL_RULES = BattleDotStackRules(
    nightmare_skill_application=5,
    nightmare_ultimate_application=5,
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
        damage=999999.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id=effect,
        ability_id=(
            "GA_Lacrimosa_Melee" if character_id == 1004 else "GA_Zankou_Melee"
        ),
    )


def _nightmare_q_hit(event_id: str, time_us: int) -> BattleAnalysisHit:
    hit = _hit(
        event_id,
        time_us,
        "GE_Player_Lacrimosa_UltraSkill_Damage",
        1004,
    )
    return hit.__class__(
        **{
            field: getattr(hit, field)
            for field in hit.__dataclass_fields__
            if field != "ability_id"
        },
        ability_id="GA_Lacrimosa_UltraSkill",
    )


def _nightmare_evade_hit(event_id: str, time_us: int) -> BattleAnalysisHit:
    hit = _hit(
        event_id,
        time_us,
        "GE_Player_Lacrimosa_PerfectEvadeAttack_1_Damage",
        1004,
    )
    return hit.__class__(
        **{
            field: getattr(hit, field)
            for field in hit.__dataclass_fields__
            if field != "ability_id"
        },
        ability_id="GA_Lacrimosa_ExtremEvadeAtk",
    )


class BattleDotStackStateServiceTests(unittest.TestCase):
    def test_zankou_stacks_are_isolated_by_half_and_target(self) -> None:
        upper_a = _hit(
            "upper-a",
            100_000,
            "GE_Player_Zankou_Skill1_1_Damage",
            1036,
        )
        upper_b = _hit(
            "upper-b",
            200_000,
            "GE_Player_Zankou_MagicMelee1_Damage",
            1036,
        )
        dot_a = _hit("dot-a", 1_000_000, "GE_Player_Zankou_DotDamage", 1036)
        dot_b = _hit("dot-b", 1_000_001, "GE_Player_Zankou_DotDamage", 1036)
        hits = (
            upper_a.__class__(
                **{
                    field: ("a" if field == "target_id" else getattr(upper_a, field))
                    for field in upper_a.__dataclass_fields__
                }
            ),
            upper_b.__class__(
                **{
                    field: ("b" if field == "target_id" else getattr(upper_b, field))
                    for field in upper_b.__dataclass_fields__
                }
            ),
            dot_a.__class__(
                **{
                    field: ("a" if field == "target_id" else getattr(dot_a, field))
                    for field in dot_a.__dataclass_fields__
                }
            ),
            dot_b.__class__(
                **{
                    field: ("b" if field == "target_id" else getattr(dot_b, field))
                    for field in dot_b.__dataclass_fields__
                }
            ),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        states = reconstruct_dot_stack_states(
            analysis,
            None,
            rules=OFFICIAL_RULES,
        )

        self.assertEqual(5, states["dot-a"].coefficient)
        self.assertEqual(1, states["dot-b"].coefficient)

    def test_nightmare_e_and_q_stack_amounts_load_from_official_curves(self) -> None:
        class StaticDao:
            def __init__(self) -> None:
                self.requested: list[tuple[str, str]] = []

            def get_combat_curve(self, table_path: str, curve_id: str):
                self.requested.append((table_path, curve_id))
                return {
                    "points": (
                        {"source_time": 1.0, "value": 5.0},
                        {"source_time": 13.0, "value": 5.0},
                    )
                }

        static_dao = StaticDao()

        rules = load_official_dot_stack_rules(static_dao)

        self.assertEqual(OFFICIAL_RULES, rules)
        self.assertEqual(
            [
                (
                    "/Game/DataTable/Skill/GlobalCharacterData/"
                    "DT_GlobalValueLacrimosaData",
                    "Lacrimosa_Skilldotnum_1",
                ),
                (
                    "/Game/DataTable/Skill/GlobalCharacterData/"
                    "DT_GlobalValueLacrimosaData",
                    "Lacrimosa_UltraSkilldotnum_1",
                ),
            ],
            static_dao.requested,
        )

    def test_nightmare_uses_prior_melee_hit_count(self) -> None:
        hits = (
            _hit("a1", 100_000, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            _hit("a2", 200_000, "GE_Player_Lacrimosa_Melee1_1_Damage", 1004),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage_LV6", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(
            analysis, None, rules=OFFICIAL_RULES
        )["dot"]

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

        state = reconstruct_dot_stack_states(
            analysis, None, rules=OFFICIAL_RULES
        )["dot"]

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

        states = reconstruct_dot_stack_states(
            analysis, None, rules=OFFICIAL_RULES
        )

        self.assertEqual(2, states["first"].coefficient)
        self.assertEqual(1, states["second"].coefficient)
        self.assertEqual(1, states["third"].coefficient)

    def test_nightmare_skill_uses_official_five_layer_application(self) -> None:
        hits = (
            _hit("e", 100_000, "GE_Player_Lacrimosa_Skill_Damage", 1004),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(
            analysis, None, rules=OFFICIAL_RULES
        )["dot"]

        self.assertEqual(5, state.coefficient)

    def test_enhanced_zankou_skill_adds_five_after_damage(self) -> None:
        hits = (
            _hit("e", 100_000, "GE_Player_Zankou_Skill1_1_Damage", 1036),
            _hit("dot", 1_100_000, "GE_Player_Zankou_DotDamage", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(
            analysis, None, rules=OFFICIAL_RULES
        )["dot"]

        self.assertEqual(5, state.coefficient)

    def test_nightmare_q_final_hit_adds_five_after_burst(self) -> None:
        hits = (
            _nightmare_q_hit("q1", 100_000),
            _nightmare_q_hit("q2", 200_000),
            _hit("dot", 1_100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())

        state = reconstruct_dot_stack_states(
            analysis, None, rules=OFFICIAL_RULES
        )["dot"]

        self.assertEqual(5, state.coefficient)
        self.assertIn("极轨终结按官方技能详情一次附加 5 层", state.evidence_basis)

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

        states = reconstruct_dot_stack_states(
            analysis, build, rules=OFFICIAL_RULES
        )

        self.assertEqual(0, states["settle"].coefficient)
        self.assertEqual("未解析", states["settle"].confidence)
        self.assertEqual(1, states["later"].coefficient)


if __name__ == "__main__":
    unittest.main()
