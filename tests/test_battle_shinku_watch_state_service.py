# 验证威慑凝视缺层数不伪造伤害、暴击或完整机制收益。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleInferredAction,
    BattleTargetCondition,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
)


class _StaticDao:
    @staticmethod
    def get_skill_damage(damage_id: str):
        return {
            "ability_id": "GA_Shinku_Melee",
            "damage_type": "cosmos",
            "damage_source_category": "NORMAL",
            "fixed_crit_rate": 0.0,
            "atk_rate_base": (0.4 if "WatchEx" in damage_id else 2.0,),
            "def_rate_base": (),
            "hp_rate_base": (),
        }

    @staticmethod
    def get_reaction_damage_curve(_damage_id: str):
        return None

    @staticmethod
    def list_character_awaken_effects(_character_id: int):
        return ()


def _hit(sequence: int, damage_id: str, damage: float) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=sequence * 1_000_000,
        character_id=1076,
        character_name="真红",
        skill_name="威慑凝视",
        damage_name="威慑凝视",
        damage_component="skill",
        attack_type="普攻",
        damage_attribute="cosmos",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Shinku_Melee",
        gameplay_effect_id=damage_id,
    )


def _analysis(hits: tuple[BattleAnalysisHit, ...]) -> BattleAnalysisSnapshot:
    baseline = BattleCharacterBaseline(
        character_id=1076,
        character_name="真红",
        source="fixture",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击", 1000.0, False),
            BattleCharacterStat("AtkUp", "攻击提升", 0.0, True),
            BattleCharacterStat("CritBase", "暴击率", 0.5, True),
            BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
        ),
        selected_awaken_effect_ids=("Effect3", "Effect4", "Effect6"),
    )
    return BattleAnalysisSnapshot(
        battle_record_id=1,
        capability_level="hit_axis",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=60_000_000,
        timeline_end_us=60_000_000,
        range_start_us=0,
        range_end_us=60_000_000,
        duration_seconds=60.0,
        total_damage=sum(hit.damage for hit in hits),
        total_dps=sum(hit.damage for hit in hits) / 60.0,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=(),
        skills=(),
        targets=(),
        baselines=(baseline,),
        target_condition=BattleTargetCondition(
            target_name="目标",
            enemy_level=80,
            scene="open_world",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("cosmos", 0.0),),
            enemy_defense_base=100.0,
        ),
    )


def _evidence(
    analysis: BattleAnalysisSnapshot, static_dao=None,
    selected=("Effect3", "Effect4", "Effect6"),
):
    return BattleSkillDamageEvidenceService.load(
        _StaticDao() if static_dao is None else static_dao,
        analysis,
        {"characters": [{
            "character_id": 1076,
            "character_level": 80,
            "skills": [{"skill_id": "GA_Shinku_Melee", "skill_level": 1}],
            "profile": {
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": list(selected),
            },
        }]},
    )


class BattleShinkuWatchStateServiceTests(unittest.TestCase):
    def test_both_watch_branches_remain_unresolved_after_replay_audit(self):
        hits = tuple(
            _hit(index, "GE_Player_Shinku_Watch_Damage", damage)
            for index, damage in enumerate((1000.0, 1000.0, 2000.0, 2000.0), 1)
        ) + (_hit(5, "GE_Player_Shinku_WatchEx_Damage", 200.0),)
        analysis = _analysis(hits)
        evidence = _evidence(analysis)

        # Repeated 1:2 damage pairs must not be mistaken for observed crits
        # when the hidden stack count could itself produce those pairs.
        replays = BattleHitReplayService.replay(analysis, evidence)

        self.assertEqual(5, len(evidence))
        self.assertEqual((2.0,) * 4 + (0.4,), tuple(
            row.scaling_multiplier for row in evidence
        ))
        for row, replay in zip(evidence, replays):
            self.assertEqual(0.0, row.state_multiplier)
            self.assertEqual("未解析", row.state_confidence)
            self.assertEqual("unreplayable", replay.critical_state)
            self.assertIsNone(replay.selected_damage)
            self.assertIsNone(replay.non_critical_damage)
            self.assertIsNone(replay.critical_damage)
            self.assertIsNone(replay.expected_damage)
            self.assertIsNone(replay.corrected_expected_damage)
            self.assertIn("威慑凝视层数未解析", "".join(
                replay.missing_evidence
            ))
        self.assertEqual(hits, analysis.hits)

    def test_non_watch_damage_retains_ordinary_skill_replay(self):
        hit = _hit(1, "GE_Player_Shinku_ReactionAOE_Damage", 1000.0)
        analysis = _analysis((hit,))
        evidence = _evidence(analysis)
        replay, = BattleHitReplayService.replay(analysis, evidence)

        self.assertEqual("", evidence[0].state_multiplier_label)
        self.assertEqual(1.0, evidence[0].state_multiplier)
        self.assertIsNotNone(replay.non_critical_damage)

    def test_fixed_axis_attack_ratio_cancels_unknown_unchanged_stack(self):
        hit = _hit(1, "GE_Player_Shinku_Watch_Damage", 1000.0)
        analysis = _analysis((hit,))
        evidence, = _evidence(analysis)
        replay, = BattleHitReplayService.replay(analysis, (evidence,))
        baseline = analysis.baselines[0]
        candidate = replace(baseline, stats=tuple(
            replace(row, value=0.2) if row.property_id == "AtkUp" else row
            for row in baseline.stats
        ))

        result = BattleHitCounterfactualRatioService.compare(
            hit=hit,
            original_baseline=baseline,
            candidate_baseline=candidate,
            skill_evidence=evidence,
            original_replay=replay,
            candidate_replay=replay,
            target_condition=analysis.target_condition,
        )

        self.assertEqual("complete", result.status)
        self.assertEqual("component_ratio", result.method)
        self.assertAlmostEqual(1.2, result.quantified_ratio)
        self.assertIsNone(replay.selected_damage)

    def test_missing_stack_does_not_supply_a_critical_branch(self):
        hit = _hit(1, "GE_Player_Shinku_Watch_Damage", 1000.0)
        analysis = _analysis((hit,))
        evidence, = _evidence(analysis)
        replay, = BattleHitReplayService.replay(analysis, (evidence,))
        baseline = analysis.baselines[0]
        candidate = replace(baseline, stats=tuple(
            replace(row, value=0.8) if row.property_id == "CritBase" else row
            for row in baseline.stats
        ))

        result = BattleHitCounterfactualRatioService.compare(
            hit=hit,
            original_baseline=baseline,
            candidate_baseline=candidate,
            skill_evidence=evidence,
            original_replay=replay,
            candidate_replay=replay,
            target_condition=analysis.target_condition,
        )

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.quantified_ratio)
        self.assertIn("critical_policy_unknown", {gap.code for gap in result.gaps})

    def test_missing_rage_awakening_curve_does_not_assume_base_multiplier(self):
        hit = _hit(1, "GE_Player_Shinku_Skill2_Rage_Damage", 1000.0)
        analysis = _analysis((hit,))
        evidence, = _evidence(analysis)
        replay, = BattleHitReplayService.replay(analysis, (evidence,))

        self.assertEqual("unreplayable", replay.critical_state)
        self.assertIsNone(replay.selected_damage)
        self.assertEqual("觉醒技能倍率（未解析）", evidence.state_multiplier_label)
        self.assertIn("Shinku_RageSkillDmgCoefL6", evidence.state_multiplier_basis)

    def test_rage_awakening_coefficient_uses_frozen_static_curve(self):
        class Dao(_StaticDao):
            @staticmethod
            def get_combat_curve(table_path: str, curve_id: str):
                if (
                    table_path.endswith("/DT_ShinkuEffectFigure")
                    and curve_id == "Shinku_RageSkillDmgCoefL6"
                ):
                    return {"points": [{"value": 0.3}]}
                return None

        hit = _hit(1, "GE_Player_Shinku_Skill2_Rage_Damage", 1000.0)
        analysis = _analysis((hit,))
        evidence, = _evidence(analysis, Dao())
        replay, = BattleHitReplayService.replay(analysis, (evidence,))

        self.assertAlmostEqual(1.3, evidence.multiplier_coefficient)
        self.assertEqual("", evidence.state_multiplier_label)
        self.assertIsNotNone(replay.non_critical_damage)

    def test_weak_estimate_uses_active_gap_and_keeps_low_confidence(self):
        anchor = _hit(1, "GE_Player_Shinku_Skill1_3_Damage", 100.0)
        watch = replace(
            _hit(2, "GE_Player_Shinku_Watch_Damage", 1000.0),
            relative_time_us=7_000_000,
        )
        trigger = replace(
            _hit(3, "GE_Player_Shinku_Melee1_Damage", 100.0),
            relative_time_us=watch.relative_time_us,
        )
        analysis = replace(
            _analysis((anchor, watch, trigger)),
            time_stop_intervals=((2_000_000, 5_000_000),),
        )
        evidence = _evidence(analysis)
        by_id = {row.event_id: row for row in evidence}
        replays = {row.event_id: row for row in BattleHitReplayService.replay(
            analysis, evidence,
        )}

        # Six wall-clock seconds minus three stopped seconds gives three
        # active seconds: the explicitly chosen weak tick convention gives 2.
        self.assertEqual(2, by_id[watch.event_id].state_multiplier)
        self.assertEqual("低", by_id[watch.event_id].state_confidence)
        self.assertIn("满两秒进入时获得首层", by_id[watch.event_id].state_multiplier_basis)
        self.assertEqual("低", replays[watch.event_id].confidence)
        self.assertEqual("ambiguous", replays[watch.event_id].critical_state)
        self.assertIsNotNone(replays[watch.event_id].non_critical_damage)
        self.assertEqual((anchor, watch, trigger), analysis.hits)

    def test_layer_estimate_does_not_read_observed_damage(self):
        anchor = _hit(1, "GE_Player_Shinku_Skill1_3_Damage", 100.0)
        watch = replace(
            _hit(2, "GE_Player_Shinku_Watch_Damage", 1000.0),
            relative_time_us=7_000_000,
        )
        original = _evidence(_analysis((anchor, watch)))[1]
        changed = _evidence(_analysis((anchor, replace(watch, damage=999999.0))))[1]
        self.assertEqual(original.state_multiplier, changed.state_multiplier)
        self.assertEqual(original.state_multiplier_basis, changed.state_multiplier_basis)

    def test_consumption_resets_growth_and_extra_hit_uses_unique_main(self):
        anchor = _hit(1, "GE_Player_Shinku_Melee1_Damage", 100.0)
        watch = replace(
            _hit(2, "GE_Player_Shinku_Watch_Damage", 1000.0),
            relative_time_us=7_000_000,
        )
        extra = replace(
            _hit(3, "GE_Player_Shinku_WatchEx_Damage", 200.0),
            relative_time_us=7_010_000,
        )
        next_watch = replace(watch, event_id="4:primary", sequence=4,
                             relative_time_us=11_500_000)
        rows = _evidence(_analysis((anchor, watch, extra, next_watch)))
        self.assertEqual((1.0, 5.0, 5.0, 3.0), tuple(
            row.state_multiplier for row in rows
        ))
        wrong_target = replace(extra, target_id="different-target")
        rows = _evidence(_analysis((anchor, watch, wrong_target)))
        self.assertEqual(0.0, rows[-1].state_multiplier)
        self.assertIn("唯一主凝视配对", rows[-1].state_multiplier_basis)

    def test_incomplete_axis_and_unanchored_opening_remain_unknown(self):
        anchor = _hit(1, "GE_Player_Shinku_Melee1_Damage", 100.0)
        watch = replace(
            _hit(2, "GE_Player_Shinku_Watch_Damage", 1000.0),
            relative_time_us=30_000_000,
        )
        incomplete = replace(_analysis((anchor, watch)), axis_complete=False)
        self.assertEqual(0.0, _evidence(incomplete)[1].state_multiplier)
        self.assertEqual(0.0, _evidence(_analysis((watch,)))[0].state_multiplier)

    def test_explicit_awakening_selection_controls_cap(self):
        anchor = _hit(1, "GE_Player_Shinku_Melee1_Damage", 100.0)
        watch = replace(
            _hit(2, "GE_Player_Shinku_Watch_Damage", 1000.0),
            relative_time_us=30_000_000,
        )
        analysis = _analysis((anchor, watch))
        self.assertEqual(8.0, _evidence(analysis, selected=("Effect3",))[1].state_multiplier)
        self.assertEqual(16.0, _evidence(analysis, selected=("Effect3", "Effect4"))[1].state_multiplier)

    def test_foreground_switch_clears_without_effect_three(self):
        anchor = _hit(1, "GE_Player_Shinku_Melee1_Damage", 100.0)
        teammate = replace(
            _hit(2, "GE_Player_Female051_Skill1_Damage", 100.0),
            character_id=1051, relative_time_us=3_000_000,
        )
        watch = replace(
            _hit(3, "GE_Player_Shinku_Watch_Damage", 1000.0),
            relative_time_us=7_000_000,
        )
        action = BattleInferredAction(
            action_id="switch-proxy", character_id=1051, character_name="队友",
            action_name="E", input_kind="E", input_sequence="E",
            start_us=2_900_000, end_us=3_100_000, hits=1, damage=100.0,
            identity_confidence="中", timing_confidence="低", inference_basis="fixture",
            evidence_event_ids=(teammate.event_id,),
            gameplay_effect_ids=(teammate.gameplay_effect_id,),
        )
        analysis = replace(_analysis((anchor, teammate, watch)), inferred_actions=(action,))
        self.assertEqual(0.0, _evidence(analysis, selected=())[-1].state_multiplier)
        self.assertEqual(5.0, _evidence(analysis, selected=("Effect3",))[-1].state_multiplier)
        # A teammate hit alone does not prove a foreground switch.
        no_switch_evidence = replace(analysis, inferred_actions=())
        self.assertEqual(5.0, _evidence(no_switch_evidence, selected=())[-1].state_multiplier)


if __name__ == "__main__":
    unittest.main()
