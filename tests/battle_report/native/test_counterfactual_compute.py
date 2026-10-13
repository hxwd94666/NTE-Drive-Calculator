# 用公共逐击反事实回归逐项比较 Rust 整批协议与 Python oracle 的完整量化状态。
from __future__ import annotations

import math
import os
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.native.counterfactual import compare_counterfactual_batch
from src.services.battle_report.native import counterfactual as native_wire
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.build.counterfactual import BattleBuildCounterfactualService
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from src.services.battle_report.damage.counterfactual import BattleHitCounterfactualRatioService
from tests.battle_report.damage import test_counterfactual as public_ratio_tests
from tests.battle_report.damage.test_counterfactual import (
    _baseline,
    _condition,
    _hit,
    _selected_replay,
    _unknown_replay,
)
from tests.battle_report.build.test_battle_build_structured_comparison import _snapshot


def _job(candidate=None, **kwargs):
    return {
        "hit": kwargs.pop("hit", _hit()),
        "original_baseline": _baseline(),
        "candidate_baseline": candidate or _baseline(),
        "original_replay": kwargs.pop("original_replay", _unknown_replay()),
        "candidate_replay": kwargs.pop("candidate_replay", None),
        **kwargs,
    }


class NativeCounterfactualAdapterTests(unittest.TestCase):
    def test_public_batch_preserves_values_and_deduplicates_equal_frozen_inputs(self):
        original, candidate = _baseline(), _baseline(AtkUp=0.2)
        replay, condition = _unknown_replay(), _condition()
        job = {**_job(), "original_baseline": original, "candidate_baseline": candidate,
               "original_replay": replay, "candidate_replay": replay,
               "target_condition": condition}
        expected = BattleHitCounterfactualRatioService.compare(**job)
        # 等值但身份不同的冻结对象也走公开批次，保护值复用而非私有容器结构。
        equal_job = {**job, "original_baseline": replace(original),
                     "candidate_baseline": replace(candidate),
                     "original_replay": replace(replay),
                     "candidate_replay": replace(replay),
                     "target_condition": replace(condition)}
        jobs = [job] * 128 + [equal_job]

        class Capture:
            supports_battle_compute = True

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                self.operation, self.inputs = operation, inputs
                return ({"results": [asdict(expected) for _ in inputs[0]["hits"]]},)

        backend = Capture()
        actual = compare_counterfactual_batch(jobs, backend=backend)
        self.assertEqual((expected,) * len(jobs), actual)
        self.assertEqual("counterfactual_ratios_shared_v1", backend.operation)
        payload, = backend.inputs
        self.assertEqual(2, len(payload["baselines"]))
        self.assertEqual(1, len(payload["replays"]))
        self.assertEqual(1, len(payload["factors"]))
        self.assertEqual(1, len(payload["targets"]))


    def test_without_backend_batch_preserves_order_and_python_results(self):
        jobs = (
            _job(_baseline(AtkUp=0.2)),
            _job(_baseline(DefIgnore=0.3)),
            _job(_baseline(DamagePenetrateNature=0.2), target_condition=_condition()),
        )
        self.assertEqual(
            tuple(BattleHitCounterfactualRatioService.compare(**job) for job in jobs),
            compare_counterfactual_batch(jobs),
        )

    def test_cancelled_native_batch_does_not_fall_back(self):
        class Broken:
            supports_battle_compute = True

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                raise RuntimeError("cancelled")

        with patch.object(BattleHitCounterfactualRatioService, "compare") as oracle:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                compare_counterfactual_batch((_job(),), backend=Broken())
            oracle.assert_not_called()


@unittest.skipUnless(
    os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
    "explicit isolated calculation executable not configured",
)
class NativeCounterfactualExecutableTests(public_ratio_tests.BattleHitCounterfactualRatioServiceTests):
    """Reuse the public component, reaction, scope and confidence regressions."""

    @classmethod
    def setUpClass(cls):
        cls.native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-counterfactual-test",
        )
        if not cls.native.supports_battle_compute:
            raise AssertionError("native battle compute capability required")

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

    def _compare(self, candidate, **kwargs):
        job = _job(candidate, **kwargs)
        expected = BattleHitCounterfactualRatioService.compare(**job)
        legacy, = self.native.compute_batch("counterfactual_ratios_v1", (native_wire._input(job),))
        with patch.object(
            BattleHitCounterfactualRatioService, "compare", side_effect=AssertionError("fallback"),
        ):
            actual, = compare_counterfactual_batch((job,), backend=self.native)
        self.assert_equivalent(asdict(expected), asdict(actual))
        self.assert_equivalent(legacy, asdict(actual))
        return actual

    def test_mixed_batch_preserves_order_and_structured_formula_priority(self):
        jobs = (
            _job(_baseline(AtkUp=0.2)),
            _job(_baseline(DefIgnore=0.3)),
            _job(_baseline(AtkUp=0.2, DefIgnore=0.3)),
            _job(_baseline(DamagePenetrateNature=0.2), target_condition=_condition()),
            _job(
                original_replay=_selected_replay(1000.0),
                candidate_replay=_selected_replay(1300.0),
            ),
        )
        expected = tuple(BattleHitCounterfactualRatioService.compare(**job) for job in jobs)
        actual = compare_counterfactual_batch(jobs, backend=self.native)
        self.assert_equivalent(tuple(map(asdict, expected)), tuple(map(asdict, actual)))

    def test_build_native_pair_does_not_request_missing_panel_or_projection(self):
        original = _snapshot()
        candidate = replace(original, hit_replays=(replace(
            original.hit_replays[0], expected_damage=260.0,
        ),))
        expected = BattleBuildCounterfactualService.compare(
            original=original, candidate=candidate,
        )
        with patch.object(
            BattleHitBuffProjectionCache, "project", side_effect=AssertionError("projection"),
        ), patch(
            "src.services.battle_report.build.comparison_batch."
            "BattleTargetInstanceMappingService.analysis_for_hit",
            side_effect=AssertionError("target routing"),
        ):
            actual = BattleBuildCounterfactualService.compare(
                original=original, candidate=candidate,
                projection_memo=BattleBuffProjectionMemo(self.native),
            )
        self.assert_equivalent(asdict(expected), asdict(actual))

    def test_nonfinite_pair_prediction_retains_unknown_component_result(self):
        job = _job(candidate_replay=replace(
            _selected_replay(100.0), expected_damage=float("inf"),
        ))
        expected = BattleHitCounterfactualRatioService.compare(**job)
        actual, = compare_counterfactual_batch((job,), backend=self.native)
        self.assert_equivalent(asdict(expected), asdict(actual))
