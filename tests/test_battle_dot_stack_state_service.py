# 验证残虹浊燃先由反应存层，再由每个实际 DOT 跳伤激活并补层。
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
    def test_explicit_scorch_identity_overrides_reused_zankou_dot_ge(self) -> None:
        hit = replace(
            _hit(
                "reused-ge-scorch",
                1_000_000,
                "GE_Player_Zankou_DotDamage",
                1036,
            ),
            damage_name="浊燃",
            classification="reaction",
        )
        analysis = SimpleNamespace(hits=(hit,), time_stop_intervals=())

        state = reconstruct_dot_stack_states(analysis, None)[hit.event_id]

        self.assertEqual("浊燃结算前层数", state.label)
        self.assertIn("浊燃", state.evidence_basis)

    def test_visible_scorch_tick_without_prior_evidence_falls_back_to_one(self) -> None:
        nightmare_hit = _hit(
            "nightmare-1",
            500_000,
            "GE_Player_Lacrimosa_Pan_Damage",
            1004,
        )
        first_tick = _hit(
            "scorch-first",
            1_000_000,
            "Buff_Reaction_5_new_1036",
            1036,
        )
        analysis = SimpleNamespace(
            hits=(nightmare_hit, first_tick),
            time_stop_intervals=(),
        )
        build = {
            "characters": [{
                "character_id": 1036,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        state = reconstruct_dot_stack_states(
            analysis,
            build,
        )["scorch-first"]

        self.assertEqual(1, state.coefficient)

    def test_each_nightmare_tick_adds_exactly_one_scorch_layer(self) -> None:
        hits = (
            replace(
                _hit(
                    "scorch-qte",
                    500_000,
                    "GE_Player_Lacrimosa_QTE1_Damage",
                    1004,
                ),
                ability_id="GA_Lacrimosa_QTE",
                attack_type="环合·浊燃",
            ),
            _hit(
                "nightmare-tick-1",
                1_100_000,
                "GE_Player_Lacrimosa_Blood_Damage",
                1004,
            ),
            _hit("scorch-two", 2_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "nightmare-tick-2",
                2_100_000,
                "GE_Player_Lacrimosa_Blood_Damage",
                1004,
            ),
            _hit("scorch-three", 3_000_000, "Buff_Reaction_5_new_1036", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1036,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        states = reconstruct_dot_stack_states(analysis, build)

        self.assertEqual(2, states["scorch-two"].coefficient)
        self.assertEqual(3, states["scorch-three"].coefficient)

    def test_two_scorch_qtes_plus_one_nightmare_tick_reach_three_layers(
        self,
    ) -> None:
        applications = (
            replace(
                _hit(
                    "nightmare-qte",
                    0,
                    "GE_Player_Lacrimosa_QTE1_Damage",
                    1004,
                ),
                ability_id="GA_Lacrimosa_QTE",
                attack_type="环合·浊燃",
            ),
            replace(
                _hit("scorch-qte-2", 100_000, "GE_Player_Lacrimosa_QTE1_Damage", 1004),
                ability_id="GA_Lacrimosa_QTE",
                attack_type="环合·浊燃",
            ),
            _hit(
                "nightmare-a1",
                1_433_930,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "nightmare-a2",
                1_866_459,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
        )
        first_tick = _hit(
            "scorch-first",
            2_082_531,
            "Buff_Reaction_5_new_1036",
            1036,
        )
        nightmare_tick = _hit(
            "nightmare-dot",
            1_448_745,
            "GE_Player_Lacrimosa_Blood_Damage_LV6",
            1004,
        )
        second_tick = _hit(
            "scorch-second",
            4_065_710,
            "Buff_Reaction_5_new_1036",
            1036,
        )
        analysis = SimpleNamespace(
            hits=(*applications, nightmare_tick, first_tick, second_tick),
            time_stop_intervals=(),
        )
        build = {
            "characters": [
                {
                    "character_id": 1003,
                    "breakthrough_stage": 2,
                    "profile": {},
                },
                {
                    "character_id": 1036,
                    "breakthrough_stage": 2,
                    "profile": {},
                },
            ],
        }

        states = reconstruct_dot_stack_states(analysis, build)

        self.assertEqual(3, states["scorch-first"].coefficient)
        self.assertEqual(3, states["scorch-second"].coefficient)
        self.assertEqual(1, states["nightmare-dot"].coefficient)
        self.assertEqual(1.0, states["nightmare-dot"].dot_final_multiplier)
        self.assertEqual(2, states["nightmare-dot"].active_dot_kind_count)
        self.assertIn(
            "尚未确认目标处于浊燃",
            states["nightmare-dot"].dot_final_multiplier_basis,
        )
        self.assertEqual(1.5, states["scorch-first"].dot_final_multiplier)
        self.assertEqual(2, states["scorch-first"].active_dot_kind_count)
        self.assertIn(
            "目标结算前已处于浊燃",
            states["scorch-first"].dot_final_multiplier_basis,
        )

    def test_learning_e_does_not_apply_nightmare_or_scorch_layer(self) -> None:
        scorch_qte = replace(
            _hit(
                "scorch-qte",
                50_000,
                "GE_Player_Lacrimosa_QTE1_Damage",
                1004,
            ),
            ability_id="GA_Lacrimosa_QTE",
            attack_type="环合·浊燃",
        )
        valid_hit = _hit(
            "nightmare-valid",
            100_000,
            "GE_Player_Lacrimosa_Pan_Damage",
            1004,
        )
        learning_e = _hit(
            "learning-e",
            200_000,
            "GE_Boss_09_act08_Steal_Dmg_BP",
            1004,
        )
        nightmare_tick = _hit(
            "nightmare-tick",
            250_000,
            "GE_Player_Lacrimosa_Blood_Damage",
            1004,
        )
        second_scorch = _hit(
            "scorch-after-learning-e",
            300_000,
            "Buff_Reaction_5_new_1036",
            1036,
        )
        analysis = SimpleNamespace(
            hits=(
                scorch_qte,
                valid_hit,
                learning_e,
                nightmare_tick,
                second_scorch,
            ),
            time_stop_intervals=(),
        )
        build = {
            "characters": [{
                "character_id": 1036,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        states = reconstruct_dot_stack_states(analysis, build)

        self.assertEqual(1, states["nightmare-tick"].coefficient)
        self.assertEqual(2, states["scorch-after-learning-e"].coefficient)

    def test_each_visible_cang_and_adler_dot_tick_adds_scorch(self) -> None:
        hits = (
            _hit("scorch-first", 1_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "cang-dot-first",
                2_000_000,
                "GE_Player_Cang_UltraSkill_Damage",
                1023,
            ),
            _hit("scorch-after-cang", 2_500_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "cang-dot-periodic",
                4_000_000,
                "GE_Player_Cang_UltraSkill_Damage",
                1023,
            ),
            _hit("scorch-after-cang-periodic", 4_500_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "adler-dot-first",
                5_000_000,
                "GE_Player_Adler_Skill_Damage",
                1033,
            ),
            _hit("scorch-after-adler", 5_500_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "adler-dot-periodic",
                6_000_000,
                "GE_Player_Adler_Skill_Damage",
                1033,
            ),
            _hit("scorch-after-adler-periodic", 6_500_000, "Buff_Reaction_5_new_1036", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1036,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        states = reconstruct_dot_stack_states(
            analysis,
            build,
        )

        self.assertEqual(2, states["scorch-after-cang"].coefficient)
        self.assertEqual(3, states["scorch-after-cang-periodic"].coefficient)
        self.assertEqual(3, states["scorch-after-adler"].coefficient)
        self.assertEqual(3, states["scorch-after-adler-periodic"].coefficient)

    def test_only_recorded_periodic_hits_add_scorch_layers_across_gaps(
        self,
    ) -> None:
        hits = (
            _hit("scorch-first", 1_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "cang-cast",
                1_100_000,
                "GE_Player_Cang_UltraSkill2_Damage",
                1023,
            ),
            _hit(
                "cang-dot-first",
                2_000_000,
                "GE_Player_Cang_UltraSkill_Damage",
                1023,
            ),
            _hit("scorch-after-cang", 2_500_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "cang-dot-after-gap",
                8_000_000,
                "GE_Player_Cang_UltraSkill_Damage",
                1023,
            ),
            _hit("scorch-after-cang-gap", 8_500_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "adler-cast-1",
                9_000_000,
                "GE_Player_Adler_Skill2_Damage",
                1033,
            ),
            _hit(
                "adler-cast-2",
                9_200_000,
                "GE_Player_Adler_Skill3_Damage",
                1033,
            ),
            _hit(
                "adler-dot-first",
                10_000_000,
                "GE_Player_Adler_Skill_Damage",
                1033,
            ),
            _hit("scorch-after-adler", 10_500_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "adler-dot-after-gap",
                13_000_000,
                "GE_Player_Adler_Skill_Damage",
                1033,
            ),
            _hit("scorch-after-adler-gap", 13_500_000, "Buff_Reaction_5_new_1036", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1036,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        states = reconstruct_dot_stack_states(
            analysis,
            build,
        )

        self.assertEqual(2, states["scorch-after-cang"].coefficient)
        self.assertEqual(3, states["scorch-after-cang-gap"].coefficient)
        self.assertEqual(3, states["scorch-after-adler"].coefficient)
        self.assertEqual(3, states["scorch-after-adler-gap"].coefficient)

    def test_sagiri_dot_final_multiplier_uses_pre_hit_scorch_and_dot_kinds(
        self,
    ) -> None:
        hits = (
            _hit(
                "nightmare-before-scorch",
                500_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "scorch-first",
                1_000_000,
                "Buff_Reaction_5_new_1036",
                1036,
            ),
            _hit(
                "nightmare-application",
                1_200_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "nightmare",
                1_300_000,
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                1004,
            ),
            _hit(
                "erosion-application",
                1_400_000,
                "GE_Player_Zankou_MagicMelee1_Damage",
                1036,
            ),
            _hit(
                "erosion",
                1_500_000,
                "GE_Player_Zankou_DotDamage",
                1036,
            ),
            _hit("scorch-old", 2_300_000, "Buff_Reaction_5_new_1036", 1036),
            _hit("scorch-new", 5_300_001, "Buff_Reaction_5_new_1036", 1036),
            _hit("scorch-next", 6_300_000, "Buff_Reaction_5_new_1036", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1003,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        states = reconstruct_dot_stack_states(
            analysis,
            build,
        )

        self.assertEqual(1.0, states["scorch-first"].dot_final_multiplier)
        self.assertEqual(2, states["scorch-first"].active_dot_kind_count)
        self.assertEqual(1.75, states["scorch-old"].dot_final_multiplier)
        self.assertEqual(3, states["scorch-old"].active_dot_kind_count)
        self.assertEqual(1.50, states["scorch-new"].dot_final_multiplier)
        self.assertEqual(2, states["scorch-new"].active_dot_kind_count)
        self.assertEqual(1.50, states["scorch-next"].dot_final_multiplier)
        self.assertEqual(2, states["scorch-next"].active_dot_kind_count)
        self.assertEqual(1.50, states["nightmare"].dot_final_multiplier)
        self.assertEqual(2, states["nightmare"].active_dot_kind_count)
        self.assertEqual(1.75, states["erosion"].dot_final_multiplier)
        self.assertEqual(3, states["erosion"].active_dot_kind_count)
        self.assertIn(
            "结算前已处于浊燃",
            states["erosion"].dot_final_multiplier_basis,
        )

    def test_sagiri_dot_final_multiplier_counts_kinds_not_layers(self) -> None:
        hits = (
            _hit("scorch-first", 1_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "nightmare-1",
                1_100_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "nightmare-2",
                1_200_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "nightmare",
                1_300_000,
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                1004,
            ),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1003,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        state = reconstruct_dot_stack_states(
            analysis,
            build,
        )["nightmare"]

        self.assertEqual(2, state.coefficient)
        self.assertEqual(2, state.active_dot_kind_count)
        self.assertEqual(1.50, state.dot_final_multiplier)

    def test_sagiri_dot_final_multiplier_caps_at_four_kinds(self) -> None:
        hits = (
            _hit("scorch-first", 1_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "nightmare-application",
                1_100_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "erosion-application",
                1_200_000,
                "GE_Player_Zankou_MagicMelee1_Damage",
                1036,
            ),
            _hit(
                "venom-application",
                1_300_000,
                "GE_Player_Zankou_MagicUltraSkill1_Damage",
                1036,
            ),
            _hit("venom", 1_400_000, "GE_Player_Zankou_DotUltraDamage", 1036),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1003,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        state = reconstruct_dot_stack_states(
            analysis,
            build,
        )["venom"]

        self.assertEqual(4, state.active_dot_kind_count)
        self.assertEqual(2.0, state.dot_final_multiplier)

    def test_sagiri_dot_final_multiplier_requires_breakthrough_two(self) -> None:
        hits = (
            _hit("scorch-first", 1_000_000, "Buff_Reaction_5_new_1036", 1036),
            _hit(
                "nightmare-application",
                1_100_000,
                "GE_Player_Lacrimosa_Pan_Damage",
                1004,
            ),
            _hit(
                "nightmare",
                1_200_000,
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                1004,
            ),
        )
        analysis = SimpleNamespace(hits=hits, time_stop_intervals=())
        build = {
            "characters": [{
                "character_id": 1003,
                "breakthrough_stage": 1,
                "profile": {},
            }],
        }

        state = reconstruct_dot_stack_states(
            analysis,
            build,
        )["nightmare"]

        self.assertEqual(1.0, state.dot_final_multiplier)
        self.assertIn("未启用", state.dot_final_multiplier_basis)

    def test_scorch_uses_pre_hit_shared_stack_per_half_and_target(self) -> None:
        first = replace(
            _hit("scorch-first", 1_000_000, "Buff_Reaction_5_new_1036", 1003),
            scope_half="upper",
            target_id="a",
        )
        add_to_a = replace(
            _hit("erosion-a", 2_000_000, "GE_Player_Zankou_DotDamage", 1036),
            scope_half="upper",
            target_id="a",
        )
        refresh_a = replace(
            _hit("erosion-refresh", 16_500_000, "GE_Player_Zankou_DotDamage", 1036),
            scope_half="upper",
            target_id="a",
        )
        stacked = replace(
            _hit("scorch-stacked", 18_000_000, "Buff_Reaction_5_new_1036", 1003),
            scope_half="upper",
            target_id="a",
        )
        other_target = replace(
            _hit("scorch-other", 18_100_000, "Buff_Reaction_5_new_1036", 1003),
            scope_half="upper",
            target_id="b",
        )
        other_half = replace(
            _hit("scorch-lower", 18_200_000, "Buff_Reaction_5_new_1036", 1003),
            scope_half="lower",
            target_id="a",
        )
        analysis = SimpleNamespace(
            hits=(first, add_to_a, refresh_a, stacked, other_target, other_half),
            time_stop_intervals=(),
        )
        build = {
            "characters": [{
                "character_id": 1036,
                "breakthrough_stage": 2,
                "profile": {},
            }],
        }

        states = reconstruct_dot_stack_states(
            analysis,
            build,
        )

        self.assertEqual(1, states["scorch-first"].coefficient)
        self.assertEqual(3, states["scorch-stacked"].coefficient)
        self.assertEqual(1, states["scorch-other"].coefficient)
        self.assertEqual(1, states["scorch-lower"].coefficient)
        self.assertIn("不移动原轴下一跳", states["scorch-stacked"].evidence_basis)

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
        )

        self.assertEqual(5, states["dot-a"].coefficient)
        self.assertEqual(1, states["dot-b"].coefficient)

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
