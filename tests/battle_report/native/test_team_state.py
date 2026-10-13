# 对公共角色与环境状态回归执行完整结果差分，保留时停、目标、刷新及觉醒证据。
from __future__ import annotations

import math
import os
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.characters.daffodill import BattleDaffodillAwakeningService
from src.services.battle_report.characters.linko_buffs import BattleLinkoCoattackBuffService
from src.services.battle_report.environment.outer_realm_buffs import BattleOuterRealmBuffService
from tests.battle_report.characters import test_daffodill as public_daffodill_tests
from tests.battle_report.characters import test_linko_formula as public_linko_tests
from tests.battle_report.environment import test_outer_realm_buffs as public_outer_tests
from tests.battle_report.characters.test_linko_formula import (
    _build, _hit, _inference,
)


class NativeTeamStateAdapterTests(unittest.TestCase):
    def test_each_owner_restores_its_plan_and_forwards_frozen_inputs(self):
        class Backend:
            supports_battle_compute = True
            calls = []
            def compute_batch(self, operation, inputs, *, checkpoint=None):
                payload, = inputs
                self.calls.append((operation, payload, checkpoint))
                checkpoint()
                plans = {
                    "linko": [{"index": 0, "target_id": "target-1", "element": "incantation", "start_us": 1, "end_us": 12}],
                    "outer": [{"component": 0, "kind": "stack", "ordinal": 1, "start_us": 1, "end_us": 12, "stacks": 2, "event_ids": ["1:primary"]}],
                    "daffodill": [{"kind": "qte", "stacks": 1, "index": 0, "value": .3, "start_us": 1, "end_us": 12, "action_ids": ["qte"], "event_ids": ["1:primary"]}],
                }
                return ({"intervals": plans[payload["kind"]]},)
        backend = Backend()
        seen = []
        def checkpoint():
            seen.append(True)
        hit = _hit("1:primary")
        linko, = BattleLinkoCoattackBuffService.infer(
            build=_build(), inferences=(_inference(hit.event_id),), hits=(hit,),
            battle_end_us=20, time_stop_intervals=(), compute_backend=backend, checkpoint=checkpoint,
        )
        outer, = BattleOuterRealmBuffService.infer(
            public_outer_tests._p8(), hits=(hit,), battle_end_us=20,
            compute_backend=backend, checkpoint=checkpoint,
        )
        daffodill, = BattleDaffodillAwakeningService.infer(
            build=public_daffodill_tests._build(), hits=(hit,),
            actions=(public_daffodill_tests._action("qte", "QTE", 0, 1),),
            battle_end_us=20, time_stop_intervals=(), compute_backend=backend, checkpoint=checkpoint,
        )
        self.assertEqual(-.08, linko.modifiers[0].magnitude_value)
        self.assertEqual((2, .06), (outer.stacks, outer.modifiers[0].magnitude_value))
        self.assertEqual(.3, daffodill.modifiers[0].magnitude_value)
        self.assertEqual(["linko", "outer", "daffodill"], [row[1]["kind"] for row in backend.calls])
        self.assertEqual(["team_state_v1"] * 3, [row[0] for row in backend.calls])
        self.assertEqual(3, len(seen))
        self.assertTrue(all(row[2] is checkpoint and row[1]["hits"][0]["event_id"] == hit.event_id for row in backend.calls))


class _NativeStateDifferential:
    @classmethod
    def setUpClass(cls):
        cls.native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-team-state-test",
        )
        if not cls.native.supports_battle_compute:
            raise AssertionError("native battle compute capability required")

    def setUp(self):
        original = self.service.infer

        def compare(*args, **kwargs):
            expected = original(*args, **kwargs)
            actual = original(*args, **kwargs, compute_backend=self.native)
            self.assert_equivalent(tuple(map(asdict, expected)), tuple(map(asdict, actual)))
            return actual

        patcher = patch.object(self.service, "infer", side_effect=compare)
        patcher.start()
        self.addCleanup(patcher.stop)

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


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated executable not configured")
class NativeDaffodillStateTests(_NativeStateDifferential, public_daffodill_tests.BattleDaffodillAwakeningServiceTests):
    service = BattleDaffodillAwakeningService


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated executable not configured")
class NativeOuterStateTests(_NativeStateDifferential, public_outer_tests.BattleOuterRealmBuffServiceTests):
    service = BattleOuterRealmBuffService


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated executable not configured")
class NativeLinkoStateTests(_NativeStateDifferential, public_linko_tests.BattleLinkoPrecisionTuningTests):
    service = BattleLinkoCoattackBuffService

    def test_stop_plateaus_refresh_and_missing_inference_targets(self):
        hits = (
            _hit("1:primary", relative_time_us=1_000_000),
            _hit("2:primary", relative_time_us=7_000_000),
            replace(_hit("3:primary", relative_time_us=7_000_000), target_id="second"),
            replace(_hit("4:primary", relative_time_us=8_000_000), target_id=""),
        )
        inferences = tuple(
            _inference(hit.event_id, time_us=hit.relative_time_us) for hit in hits
        )
        inferences = (
            *inferences, inferences[1],
            replace(inferences[0], event_id="missing"),
            replace(inferences[0], damage_attribute="unknown"),
        )
        rows = self.service.infer(
            build=_build(), inferences=inferences, hits=hits, battle_end_us=30_000_000,
            time_stop_intervals=((2_000_000, 5_000_000), (4_000_000, 6_000_000)),
        )
        self.assertEqual(3, len(rows))
        self.assertEqual(7_000_000, rows[0].end_us)
        self.assertEqual({"target-1", "second"}, {row.target_id for row in rows})
