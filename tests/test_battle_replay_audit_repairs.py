# 验证逐击审计中伤害归属、噩梦层数和残虹形态的窄修复边界。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleInferredAction,
)
from src.services.battle_dot_stack_state_service import reconstruct_dot_stack_states
from src.services.battle_hit_replay_audit_service import BattleHitReplayAuditService
from src.services.battle_zankou_form_buff_service import (
    BattleZankouFormBuffService,
    BattleZankouFormConfig,
)


def _hit(
    event_id: str,
    time_us: int,
    effect_id: str,
    *,
    character_id: int = 1004,
    damage: float = 100.0,
    classification: str = "direct",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=time_us,
        relative_time_us=time_us,
        character_id=character_id,
        character_name="安魂曲" if character_id == 1004 else "残虹",
        skill_name="测试",
        damage_name="测试",
        damage_component="skill",
        attack_type="普攻",
        damage_attribute="chaos",
        target_id="boss",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification=classification,
        ability_id="GA_Lacrimosa_Melee" if character_id == 1004 else "GA_Zankou",
        gameplay_effect_id=effect_id,
        scope_half="upper",
    )


def _replay(event_id: str, damage: float, stack: float, noncrit: float) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=event_id,
        observed_damage=damage,
        non_critical_damage=noncrit,
        critical_damage=noncrit * 2.5,
        selected_damage=noncrit,
        selected_error_percent=0.0,
        critical_state="ambiguous",
        confidence="低",
        factors=(
            BattleHitReplayFactor(
                "state_coefficient",
                "噩梦当前层数",
                stack,
                "逐击正向重放",
            ),
            BattleHitReplayFactor(
                "critical",
                "暴击伤害倍率",
                2.5,
                "命中时角色暴击伤害",
            ),
        ),
        formula_type="直伤（噩梦）",
        critical_rate=0.2,
        expected_damage=noncrit * 1.3,
        corrected_expected_damage=damage,
        signed_error_percent=0.0,
    )


class BattleReplayAuditRepairTests(unittest.TestCase):
    def test_dark_star_formula_uses_exact_hp_remainder_without_rewriting_hit(self) -> None:
        hit = replace(
            _hit(
                "75:primary",
                28_164_538,
                "Buff_Reaction_4_new",
                character_id=1039,
                damage=1_249.0,
                classification="reaction",
            ),
            character_name="法帝娅",
            damage_name="黯星",
            damage_component="黯星",
            target_hp_before=3_871_103.75,
            target_hp_after=3_812_254.75,
            target_max_hp=4_428_034.0,
            overkill_damage=0.0,
        )
        replay = replace(
            _replay(hit.event_id, hit.damage, 1.0, 57_600.0),
            critical_damage=None,
            selected_damage=57_600.0,
            selected_error_percent=4_511.69,
            critical_state="not_applicable",
            formula_type="黯星",
            critical_rate=0.0,
            expected_damage=57_600.0,
            corrected_expected_damage=1_249.0,
            signed_error_percent=4_511.69,
            critical_policy="disabled",
        )

        adjusted = BattleHitReplayAuditService.apply_dark_star_hp_remainder_observation(
            SimpleNamespace(hits=(hit,)),
            (replay,),
        )[0]

        self.assertEqual(1_249.0, hit.damage)
        self.assertEqual(57_600.0, adjusted.observed_damage)
        self.assertEqual(1_249.0, adjusted.reported_damage)
        self.assertEqual("target_hp_transition_remainder", adjusted.observed_damage_source)
        self.assertEqual(0.0, adjusted.signed_error_percent)
        self.assertEqual(0.0, adjusted.selected_error_percent)
        self.assertEqual(57_600.0, adjusted.corrected_expected_damage)
        self.assertEqual("高", adjusted.confidence)

    def test_dark_star_hp_remainder_fails_closed_when_formula_does_not_match(self) -> None:
        hit = replace(
            _hit(
                "31:primary",
                1_000_000,
                "Buff_Reaction_4_new",
                character_id=1039,
                damage=2_243.0,
                classification="reaction",
            ),
            damage_name="黯星",
            target_hp_before=500_000.0,
            target_hp_after=430_369.828125,
            overkill_damage=0.0,
        )
        replay = replace(
            _replay(hit.event_id, hit.damage, 1.0, 57_600.0),
            critical_damage=None,
            selected_damage=57_600.0,
            formula_type="黯星",
        )

        adjusted = BattleHitReplayAuditService.apply_dark_star_hp_remainder_observation(
            SimpleNamespace(hits=(hit,)),
            (replay,),
        )[0]

        self.assertIs(adjusted, replay)
        self.assertEqual(2_243.0, adjusted.observed_damage)

    def test_normal_dark_star_and_positive_overkill_are_not_reinterpreted(self) -> None:
        normal = replace(
            _hit(
                "normal",
                1_000_000,
                "Buff_Reaction_4_new",
                character_id=1039,
                damage=57_600.0,
                classification="reaction",
            ),
            damage_name="黯星",
            target_hp_before=500_000.0,
            target_hp_after=442_400.0,
            overkill_damage=0.0,
        )
        overkill = replace(
            normal,
            event_id="overkill",
            damage=1_249.0,
            target_hp_after=0.0,
            overkill_damage=10.0,
        )
        results = tuple(
            replace(
                _replay(hit.event_id, hit.damage, 1.0, 57_600.0),
                critical_damage=None,
                selected_damage=57_600.0,
                formula_type="黯星",
            )
            for hit in (normal, overkill)
        )

        adjusted = BattleHitReplayAuditService.apply_dark_star_hp_remainder_observation(
            SimpleNamespace(hits=(normal, overkill)),
            results,
        )

        self.assertEqual((57_600.0, 1_249.0), tuple(
            row.observed_damage for row in adjusted
        ))
    def test_equal_damage_overlapping_hp_intervals_mark_both_replays(self) -> None:
        first = replace(
            _hit("89:primary", 46_086_321, "GE_Player_Lacrimosa_Blood_Damage", damage=34_653.0),
            target_hp_before=4_401_322.0,
            target_hp_after=4_366_669.0,
        )
        second = replace(
            _hit(
                "90:primary",
                46_103_414,
                "Buff_Reaction_5_new_1036",
                character_id=1036,
                damage=34_653.0,
                classification="reaction",
            ),
            target_hp_before=4_407_684.0,
            target_hp_after=4_373_031.0,
        )
        analysis = SimpleNamespace(hits=(first, second))
        results = (
            _replay(first.event_id, first.damage, 1.0, 2_240.0),
            _replay(second.event_id, second.damage, 3.0, 34_653.0),
        )

        marked = BattleHitReplayAuditService.apply_damage_attribution_conflicts(
            analysis,
            results,
        )

        self.assertTrue(all(row.critical_state == "ambiguous" for row in marked))
        self.assertTrue(all(row.confidence == "低" for row in marked))
        self.assertTrue(all(
            any(
                "同一服务端 HP 结算的重复归属候选" in reason
                and "不表示伤害实际发生两次" in reason
                for reason in row.missing_evidence
            )
            for row in marked
        ))

    def test_equal_damage_same_hp_endpoint_detects_delayed_attribution(self) -> None:
        first = replace(
            _hit(
                "576:primary",
                100_000,
                "GE_Player_Zankou_DotDamage",
                damage=11_902.0,
            ),
            target_hp_before=500_000.0,
            target_hp_after=488_098.0,
        )
        second = replace(
            _hit(
                "579:primary",
                638_000,
                "Buff_Reaction_5_new_1036",
                character_id=1036,
                damage=11_902.0,
                classification="reaction",
            ),
            target_hp_before=488_098.0,
            target_hp_after=488_098.0,
        )

        conflicts = BattleHitReplayAuditService.damage_attribution_conflict_ids(
            (first, second)
        )

        self.assertEqual({first.event_id, second.event_id}, set(conflicts))

    def test_recent_application_can_backsolve_one_missing_server_nightmare_layer(self) -> None:
        reference_hit = _hit(
            "60:primary",
            31_799_000,
            "GE_Player_Lacrimosa_Blood_Damage",
            damage=3_448.0,
            classification="dot",
        )
        application = _hit(
            "72:primary",
            38_136_000,
            "GE_Player_Lacrimosa_Melee1_Damage",
        )
        current_hit = _hit(
            "73:primary",
            38_653_000,
            "GE_Player_Lacrimosa_Blood_Damage",
            damage=3_448.0,
            classification="dot",
        )
        analysis = SimpleNamespace(hits=(reference_hit, application, current_hit))
        results = (
            _replay(reference_hit.event_id, 3_448.0, 4.0, 3_072.0),
            _replay(current_hit.event_id, 3_448.0, 5.0, 4_480.0),
        )

        adjusted = BattleHitReplayAuditService.apply_nightmare_observed_layer_adjustment(
            analysis,
            results,
        )

        current = adjusted[1]
        stack = next(row for row in current.factors if row.factor_id == "state_coefficient")
        self.assertEqual(4.0, stack.value)
        self.assertEqual("non_critical", current.critical_state)
        self.assertIn("服务器少接收 1 层", stack.evidence_basis)

    def test_erosion_only_selects_single_or_formal_full_settlement(self) -> None:
        hit = _hit(
            "erosion:1",
            1_000_000,
            "GE_Player_Zankou_DotDamage",
            character_id=1036,
            damage=250.0,
            classification="dot",
        )
        replay = replace(
            _replay(hit.event_id, 250.0, 10.0, 1_000.0),
            formula_type="持续伤害（蚀心）",
        )

        adjusted = BattleHitReplayAuditService.apply_erosion_settlement_adjustment(
            SimpleNamespace(hits=(hit,)),
            (replay,),
        )[0]

        stack = next(
            row for row in adjusted.factors if row.factor_id == "state_coefficient"
        )
        self.assertEqual(1.0, stack.value)
        self.assertEqual("critical", adjusted.critical_state)
        self.assertEqual(250.0, adjusted.selected_damage)
        self.assertIn("单份", stack.evidence_basis)

    def test_reused_erosion_ge_scorch_skips_erosion_adjustment(self) -> None:
        hit = replace(
            _hit(
                "scorch:1",
                1_000_000,
                "GE_Player_Zankou_DotDamage",
                character_id=1036,
                damage=250.0,
                classification="reaction",
            ),
            damage_name="浊燃",
        )
        replay = replace(
            _replay(hit.event_id, 250.0, 3.0, 300.0),
            formula_type="浊燃",
        )

        adjusted = BattleHitReplayAuditService.apply_erosion_settlement_adjustment(
            SimpleNamespace(hits=(hit,)),
            (replay,),
        )[0]

        stack = next(
            row for row in adjusted.factors
            if row.factor_id == "state_coefficient"
        )
        self.assertEqual(3.0, stack.value)
        self.assertFalse(any(
            "蚀心结算模式" in reason
            for reason in adjusted.missing_evidence
        ))

    def test_erosion_and_nightmare_both_pause_during_time_stop(self) -> None:
        hits = (
            _hit("nightmare-a", 0, "GE_Player_Lacrimosa_Melee1_Damage"),
            _hit("erosion-e", 50_000, "GE_Player_Zankou_Skill2_1_Damage", character_id=1036),
            _hit("nightmare-b", 100_000, "GE_Player_Lacrimosa_Melee2_Damage"),
            _hit(
                "nightmare-dot",
                3_100_000,
                "GE_Player_Lacrimosa_Blood_Damage",
                classification="dot",
            ),
            _hit(
                "erosion-dot",
                30_100_001,
                "GE_Player_Zankou_DotDamage",
                character_id=1036,
                classification="dot",
            ),
        )
        analysis = SimpleNamespace(
            hits=hits,
            time_stop_intervals=((1_000_000, 11_000_000),),
        )

        states = reconstruct_dot_stack_states(analysis, None)

        self.assertEqual(2, states["nightmare-dot"].coefficient)
        self.assertEqual(5, states["erosion-dot"].coefficient)

    def test_zankou_transition_uses_last_evidence_hit_before_overlapping_swap(self) -> None:
        transition = BattleInferredAction(
            action_id="zankou-e",
            character_id=1036,
            character_name="残虹",
            action_name="绯影闪",
            input_kind="E",
            input_sequence="E",
            start_us=1_000_000,
            end_us=3_000_000,
            hits=2,
            damage=200.0,
            identity_confidence="中",
            timing_confidence="低",
            inference_basis="fixture",
            evidence_event_ids=("e:first", "e:last"),
            gameplay_effect_ids=("GE_Player_Zankou_Skill2_Damage",),
        )
        teammate = replace(
            transition,
            action_id="teammate-a",
            character_id=1004,
            character_name="安魂曲",
            start_us=2_200_000,
            end_us=2_600_000,
            evidence_event_ids=("ally:hit",),
            gameplay_effect_ids=("GE_Player_Lacrimosa_Melee1_Damage",),
        )
        hits = (
            _hit("e:first", 1_500_000, "GE_Player_Zankou_Skill2_Damage", character_id=1036),
            _hit("e:last", 2_000_000, "GE_Player_Zankou_Skill2_1_Damage", character_id=1036),
        )
        config = BattleZankouFormConfig(0.25, 0.25, 8.0, 8.0, 8.0)
        build = {"characters": [{"character_id": 1036, "awakening_level": 0, "profile": {}}]}

        intervals = BattleZankouFormBuffService.infer(
            build=build,
            actions=(transition, teammate),
            hits=hits,
            battle_end_us=20_000_000,
            config=config,
        )

        huo = next(row for row in intervals if ":huo:" in row.interval_id)
        self.assertEqual(2_000_000, huo.start_us)
        self.assertEqual(10_200_000, huo.end_us)


if __name__ == "__main__":
    unittest.main()
