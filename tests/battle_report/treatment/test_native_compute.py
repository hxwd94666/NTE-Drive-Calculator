# 验证整场治疗生产者、周期与消费者原生迁移保留公开事件和证据文字。
from __future__ import annotations

import math
import os
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.treatment.native import infer_treatment_batch
from src.services.battle_report.treatment.mechanisms import BattleTreatmentEventService
from src.services.battle_report.treatment.replay import BattleTreatmentReplayService
from tests.battle_report.treatment import test_events as public_treatment_tests
from tests.battle_report.treatment.test_events import (
    _action,
    _build,
    _hit,
)


class NativeTreatmentAdapterTests(unittest.TestCase):
    def test_native_failure_and_checkpoint_do_not_trigger_python_inference(self):
        checkpoint = Mock()

        class Cancelled:
            supports_battle_compute = True

            def compute_batch(inner, operation, inputs, **kwargs):
                self.assertEqual("treatment_replay_v1", operation)
                self.assertIs(checkpoint, kwargs["checkpoint"])
                raise RuntimeError("cancelled")

        with patch.object(BattleTreatmentEventService, "infer") as oracle:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                BattleTreatmentReplayService.infer(
                    build=_build(), actions=(_action(1, "E", 0),), hits=(),
                    battle_end_us=30_000_000, infer_buffs=True,
                    backend=Cancelled(), checkpoint=checkpoint,
                )
            oracle.assert_not_called()
        self.assertGreater(checkpoint.call_count, 0)


@unittest.skipUnless(
    os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
    "explicit isolated calculation executable not configured",
)
class NativeTreatmentExecutableTests(public_treatment_tests.BattleTreatmentEventServiceTests):
    """Route the existing producer tests through the native full-axis adapter."""

    @classmethod
    def setUpClass(cls):
        cls.native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-treatment-test",
        )
        if not cls.native.supports_battle_compute:
            raise AssertionError("native battle compute capability required")

    def setUp(self):
        original = BattleTreatmentEventService.infer

        def compare(**kwargs):
            expected = original(**kwargs)
            inputs = dict(
                hits=(), time_stop_intervals=(), state_buff_intervals=(),
                zankou_effect_three_recover_ratio=None,
            )
            inputs.update(kwargs)
            events, buffs = infer_treatment_batch(
                **inputs, infer_buffs=False, backend=self.native,
            )
            self.assertEqual((), buffs)
            self.assert_equivalent(tuple(map(asdict, expected)), tuple(map(asdict, events)))
            return events

        self.addCleanup(patch.stopall)
        patch.object(BattleTreatmentEventService, "infer", side_effect=compare).start()

    def assert_equivalent(self, expected, actual):
        if isinstance(expected, dict):
            self.assertEqual(expected.keys(), actual.keys())
            for key in expected:
                self.assert_equivalent(expected[key], actual[key])
        elif isinstance(expected, (tuple, list)):
            self.assertEqual(len(expected), len(actual))
            for left, right in zip(expected, actual, strict=True):
                self.assert_equivalent(left, right)
        elif isinstance(expected, float):
            self.assertTrue(math.isclose(expected, actual, rel_tol=1e-11, abs_tol=1e-11))
        else:
            self.assertEqual(expected, actual)

    def test_full_pipeline_consumers_and_amount_caps_match_frozen_inputs(self):
        # Restore the oracle entry for the baseline full pipeline.
        patch.stopall()
        build = _build()
        build["characters"][0].update(awakening_level=5)
        build["characters"][0]["stats"][0]["value"] = 5000.0
        actions = (
            _action(1, "E", 0), _action(2, "E", 1_000_000),
            _action(3, "E", 4_000_000, gesture="hold"),
            _action(4, "Q", 8_000_000),
            replace(_action(5, "E", 17_000_000), character_id=1001),
            replace(_action(6, "E", 20_000_000), character_id=1001),
            _action(7, "QTE", 25_000_000),
        )
        inputs = dict(
            build=build, actions=actions, hits=(), battle_end_us=60_000_000,
            time_stop_intervals=((9_000_000, 14_000_000), (12_000_000, 16_000_000)),
            infer_buffs=True,
        )
        expected = BattleTreatmentReplayService.infer(**inputs)
        actual = BattleTreatmentReplayService.infer(**inputs, backend=self.native)
        self.assert_equivalent(asdict(expected), asdict(actual))

    def test_combined_audited_healing_retains_zero_unknown_and_target_grouping(self):
        patch.stopall()
        characters = []
        for role, effects in ((1004, ("Effect5",)), (1021, ("Effect1",)),
                              (1076, ("Effect5",)), (1055, ("Effect2",)),
                              (1036, ("Effect3",))):
            characters.append({
                "character_id": role, "profile": {
                    "awakening_selection_initialized": True,
                    "selected_awaken_effect_ids": effects,
                },
                "stats": [
                    {"property_id": "HPMaxBase", "value": 2000.0},
                    {"property_id": "HPMaxUp", "value": 0.2},
                    {"property_id": "HPMaxAdd", "value": 100.0},
                ],
            })
        actions = (
            replace(_action(1, "E", 0, gesture="hold"), character_id=1021),
            replace(_action(2, "Q", 1_000_000), character_id=1021),
            replace(_action(3, "Q", 15_000_000), character_id=1055, end_us=16_000_000),
        )
        hits = (
            _hit("1:primary", 0, 1021, ability_id="E", damage_name="segment"),
            _hit("2:primary", 1_000_000, 1021, ability_id="Q", damage_name="field"),
            _hit("3:primary", 1_000_000, 1004, ability_id="passive", damage_name="噩梦", damage=1.0),
            _hit("4:primary", 3_000_000, 1004, ability_id="passive", damage_name="噩梦", damage=1000.0),
            _hit("5:primary", 5_000_000, 1076, ability_id="E", damage_name="shinku_skill2_rage_damage"),
            replace(_hit("6:primary", 5_000_000, 1076, ability_id="E",
                         damage_name="shinku_skill2_rage_damage"), target_id="other"),
            _hit("7:primary", 16_500_000, 1055, ability_id="Q", damage_name="kuhara_budboom_damage"),
        )
        states = (SimpleNamespace(
            interval_id="huo", source_character_id=1036, source_effect_definition_id="form:huo",
            start_us=0, end_us=10_000_000,
        ),)
        inputs = dict(
            build={"characters": characters}, actions=actions, hits=hits,
            battle_end_us=30_000_000, time_stop_intervals=((2_000_000, 4_000_000),),
            state_buff_intervals=states, zankou_effect_three_recover_ratio=0.03,
            infer_buffs=True,
        )
        expected = BattleTreatmentReplayService.infer(**inputs)
        actual = BattleTreatmentReplayService.infer(**inputs, backend=self.native)
        self.assert_equivalent(asdict(expected), asdict(actual))
