# 验证批量后端保持逐击顺序、去重、解释与失败取消边界。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_report.replay.service import BattleHitReplayService
from src.domain.native_analysis import DirectFormulaBackend


from src.services.battle_report.replay.direct_numeric import calculate_python_direct_formula


def frozen_fixture():
    first = BattleAnalysisHit(
        event_id="direct:1",
        sequence=1,
        relative_time_us=1,
        character_id=1,
        character_name="角色",
        skill_name="技能",
        damage_name="伤害",
        damage_component="skill",
        attack_type="E技能",
        damage_attribute="chaos",
        target_id="target",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Test",
        gameplay_effect_id="GE_Test",
        scope_half="upper",
    )
    second = replace(first, event_id="direct:2", sequence=2, damage=200.0)
    baseline = BattleCharacterBaseline(
        character_id=1,
        character_name="角色",
        source="fixture",
        stats=(BattleCharacterStat("Atk", "攻击力", 100.0, False),),
    )
    evidence = BattleSkillDamageEvidence(
        event_id=first.event_id,
        damage_id="GE_Test",
        ability_id="GA_Test",
        damage_attribute="chaos",
        damage_source_category="skill",
        fixed_crit_rate=0.5,
        scaling_property_id="Atk",
        scaling_multiplier=1.0,
        multiplier_coefficient=1.0,
        effective_skill_level=1,
        evidence_basis="fixture",
    )
    condition = BattleTargetCondition(
        target_name="目标",
        enemy_level=80.0,
        scene="outer_realm",
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=(("chaos", 0.2),),
        enemy_defense_base=0.0,
    )
    analysis = SimpleNamespace(
        hits=(first, second),
        baselines=(baseline,),
        buff_intervals=(),
        target_condition=condition,
        target_instance_resolutions=(),
    )
    return analysis, (evidence, replace(evidence, event_id=second.event_id))


class RecordingBackend:
    def __init__(self):
        self.calls = []

    def calculate_batch(self, inputs, *, checkpoint=None):
        self.calls.append(inputs)
        if checkpoint is not None:
            checkpoint()
        return tuple(calculate_python_direct_formula(job) for job in inputs)


class NativeDirectReplayTests(unittest.TestCase):
    def test_batch_deduplicates_and_matches_complete_python_result_without_scalar_fallback(self):
        analysis, evidence = frozen_fixture()
        unsupported = replace(analysis.hits[0], event_id="missing", sequence=3)
        analysis.hits = (analysis.hits[0], unsupported, analysis.hits[1])
        expected = BattleHitReplayService.replay(analysis, evidence, apply_observed_refinements=False)
        backend = RecordingBackend()
        with patch(
            "src.services.battle_report.replay.direct_rendering.calculate_python_direct_formula",
            side_effect=AssertionError("unexpected Python fallback"),
        ):
            actual = BattleHitReplayService.replay(
                analysis,
                evidence,
                apply_observed_refinements=False,
                direct_formula_backend=backend,
            )
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(backend.calls))
        self.assertEqual(1, len(backend.calls[0]))
        self.assertEqual(["direct:1", "missing", "direct:2"], [row.event_id for row in actual])

    def test_unknown_critical_partial_retains_unknown_expected_damage(self):
        analysis, evidence = frozen_fixture()
        evidence = tuple(replace(row, critical_policy="unknown") for row in evidence)
        expected = BattleHitReplayService.replay(analysis, evidence, apply_observed_refinements=False)
        backend = RecordingBackend()
        original = backend.calculate_batch

        def partial(inputs, *, checkpoint=None):
            return tuple(
                dict(row, status="partial", gap_codes=["critical_policy_unknown"])
                for row in original(inputs, checkpoint=checkpoint)
            )

        backend.calculate_batch = partial
        actual = BattleHitReplayService.replay(
            analysis, evidence, apply_observed_refinements=False, direct_formula_backend=backend
        )
        self.assertEqual(expected, actual)
        self.assertIsNone(actual[0].expected_damage)

    def test_native_unavailable_for_true_retains_existing_evidence_gap(self):
        analysis, evidence = frozen_fixture()
        evidence = tuple(replace(row, damage_attribute="true") for row in evidence)
        expected = BattleHitReplayService.replay(analysis, evidence, apply_observed_refinements=False)
        actual = BattleHitReplayService.replay(
            analysis, evidence, apply_observed_refinements=False, direct_formula_backend=RecordingBackend()
        )
        self.assertEqual(expected, actual)
        self.assertEqual("unreplayable", actual[0].critical_state)

    def test_result_count_mismatch_and_native_errors_propagate(self):
        analysis, evidence = frozen_fixture()
        for response, error in [((), None), (None, RuntimeError("native failed"))]:
            backend = unittest.mock.Mock(spec=DirectFormulaBackend)
            backend.calculate_batch.return_value = response
            backend.calculate_batch.side_effect = error
            with self.assertRaises(RuntimeError if error else ValueError):
                BattleHitReplayService.replay(analysis, evidence, direct_formula_backend=backend)
            backend.calculate_batch.assert_called_once()

    def test_progress_cancellation_reaches_batch_checkpoint(self):
        analysis, evidence = frozen_fixture()
        backend = RecordingBackend()

        def progress(_update):
            if backend.calls:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            BattleHitReplayService.replay(
                analysis, evidence, direct_formula_backend=backend, progress_callback=progress
            )

    def test_stateful_hits_are_separate_ordered_jobs(self):
        analysis, evidence = frozen_fixture()
        evidence = tuple(
            replace(row, state_multiplier_label="状态层数", state_multiplier=float(index + 1))
            for index, row in enumerate(evidence)
        )
        expected = BattleHitReplayService.replay(analysis, evidence, apply_observed_refinements=False)
        backend = RecordingBackend()
        actual = BattleHitReplayService.replay(
            analysis, evidence, apply_observed_refinements=False, direct_formula_backend=backend
        )
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(backend.calls))
        self.assertEqual([1.0, 2.0], [job["state_multiplier"] for job in backend.calls[0]])

    def test_normal_damage_uses_general_increase_and_default_resistance(self):
        analysis, evidence = frozen_fixture()
        original = analysis.baselines[0]
        analysis.baselines = (
            replace(
                original,
                stats=original.stats
                + (
                    BattleCharacterStat("DamageUpGeneralBase", "通伤", 0.5, True),
                    BattleCharacterStat("DamageUpChaosBase", "混沌增伤", 9.0, True),
                    BattleCharacterStat("DamagePenetrateChaos", "混沌穿透", 9.0, True),
                ),
            ),
        )
        evidence = tuple(replace(row, damage_attribute="normal") for row in evidence)
        expected = BattleHitReplayService.replay(analysis, evidence, apply_observed_refinements=False)
        backend = RecordingBackend()
        actual = BattleHitReplayService.replay(
            analysis, evidence, apply_observed_refinements=False, direct_formula_backend=backend
        )
        self.assertEqual(expected, actual)
        self.assertEqual("normal", backend.calls[0][0]["damage_attribute"])
        self.assertEqual(0.2, backend.calls[0][0]["target"]["resistance"])
        self.assertEqual(120.0, actual[0].non_critical_damage)

    def test_buff_baseline_replay_receives_cancellation_callback(self):
        from src.services.battle_report.buffs.counterfactual import BattleBuffCounterfactualService

        analysis, evidence = frozen_fixture()
        analysis.buff_intervals = (SimpleNamespace(start_us=0, end_us=0),)
        analysis.hit_replays = ()
        backend = RecordingBackend()

        def progress(_update):
            raise RuntimeError("baseline cancelled")

        with self.assertRaisesRegex(RuntimeError, "baseline cancelled"):
            BattleBuffCounterfactualService.calculate(
                analysis, evidence, direct_formula_backend=backend, progress_callback=progress
            )
        self.assertEqual([], backend.calls)
