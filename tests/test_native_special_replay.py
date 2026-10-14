# 验证特殊伤害批处理只准备一次，并对独立原生数值核逐字段做差分。
from __future__ import annotations

import os
import unittest
from pathlib import Path

from src.integrations.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.services.battle_report.replay.special_batch import SpecialReplayBatch, PendingSpecialReplay
from src.services.battle_report.replay.special_numeric import numeric_replay, python_special_numeric


def _mitigation(resistance=0.2, profile=None):
    return {
        "character_level": 80.0, "enemy_level": 95.0, "scene": "outer_realm",
        "defense_base": profile, "defense_up": 0.1, "defense_add": 40.0,
        "defense_penetration": 0.15, "clamp_defense": True,
        "defense_reduction": 0.1, "base_resistance": resistance,
        "resistance_additions": [0.1, -0.05], "resistance_penetration": 0.1,
        "vulnerability": 0.2,
    }


def _cases():
    for observed in (0.0, 1.0, 2000.0):
        for strength in (-1.0, 0.0, 120.0, 600.0):
            yield "special_weave_v1", {
                "observed": observed, "source_damage": 1234.5,
                "ring_strength": strength, "base_ratio": 0.20,
                "extra_ratio": 0.13 if strength > 0 else 0.0, "final_multiplier": 1.0,
            }
            for resistance in (-0.5, 0.0, 0.2, 1.1):
                mitigation = _mitigation(resistance, 900.0 if strength > 0 else None)
                for scorch in (False, True):
                    yield "special_reaction_v1", {
                        "observed": observed, "level_multiplier": 2700.0,
                        "ring_strength": strength, "mitigation": mitigation, "scorch": scorch,
                        "state_multiplier": 3.0, "dot_final_multiplier": 1.1,
                        "crit_damage": 1.14,
                    }
                yield "special_nova_v1", {
                    "observed": observed, "level_multiplier": 2700.0,
                    "ring_strength": strength, "mitigation": mitigation,
                }
    # 显式 R/F=0 必须区别于缺失；其余两项检查扩展零与强度加成上限，不缩减旧矩阵。
    for reaction, final in ((0.0, 1.0), (1.5, 0.0), (1.5, 2.0), (102.0, 2.0)):
        yield "special_weave_v1", {
            "observed": 0.0, "source_damage": 1000.0, "ring_strength": 424.0,
            "base_ratio": 0.20, "extra_ratio": 0.0,
            "reaction_multiplier": reaction, "final_multiplier": final,
        }
    for limit in (0.0, 3.0, 70.0, 100.0):
        for feast in (False, True):
            yield "special_topple_v1", {
                "observed": 123456.0, "enemy_topple_limit": limit, "feast": feast,
                "cells": [
                    {"strength_base": strength, "strength_up": 0.25,
                     "strength_add": 0.0, "damage_up": 0.1, "level_multiplier": 4500.0,
                     "mitigation": _mitigation(resistance, profile)}
                    for strength, resistance, profile in ((348.0, 0.2, 1500.0), (78.0, -0.3, None))
                ],
            }


class _Backend:
    supports_battle_compute = True

    def __init__(self):
        self.calls = []

    def compute_batch(self, operation, inputs, *, checkpoint=None):
        self.calls.append((operation, inputs))
        if checkpoint:
            checkpoint()
        return tuple(python_special_numeric(operation, item) for item in inputs)


class SpecialReplayBatchTests(unittest.TestCase):
    def test_weave_current_contract_float32_and_copy_ratio_boundaries(self):
        # 文档规定逐区转 f32 后组合、最终系数再转 f32；不是双精度简式直接取整。
        for strength, passive, source, expected in (
            # f32(.20)+f32(.13) 恰在两格中点，ties-to-even 回到 .3299999833，100 原伤取整为 32。
            (0.0, False, 100.0, 20.0), (0.0, True, 100.0, 32.0),
            (180.0, False, 1000.0, 320.0), (180.0, True, 1000.0, 463.0),
            (600.0, False, 1000.0, 384.0), (-1.0, False, 100.0, 20.0),
            (0.0, False, 4.9999999, 0.0), (0.0, False, 5.0, 1.0),
        ):
            with self.subTest(strength=strength, passive=passive, source=source):
                actual = python_special_numeric("special_weave_v1", {
                    "ring_strength": strength, "base_ratio": 0.20,
                    "extra_ratio": 0.13 if passive else 0.0, "final_multiplier": 1.0,
                    "source_damage": source, "observed": 0.0,
                })
                self.assertEqual(expected, actual["selected"])
                self.assertEqual(0.13 if passive else 0.0, actual["extra_ratio"])
                self.assertEqual(0.20, actual["base_ratio"])
                self.assertIsNone(actual["signed_error"])
                self.assertIsNone(actual["absolute_error"])

        for reaction, final, expected in ((0.0, 1.0, 0.0), (1.5, 0.0, 0.0),
                                           (1.5, 2.0, 1600.0), (102.0, 2.0, 240800.0)):
            with self.subTest(reaction=reaction, final=final):
                actual = python_special_numeric("special_weave_v1", {
                    "ring_strength": 424.0, "base_ratio": 0.20, "extra_ratio": 0.0,
                    "reaction_multiplier": reaction, "final_multiplier": final,
                    "source_damage": 1000.0, "observed": 0.0,
                })
                self.assertEqual(expected, actual["selected"])
                self.assertEqual(reaction, actual["strength_multiplier"])
                self.assertEqual(final, actual["final_multiplier"])
                if expected == 0.0:
                    self.assertIsNone(actual["corrected_expected"])

    def test_preparation_runs_once_and_mixed_batches_keep_axis_order(self):
        visits = []

        @numeric_replay
        def replay(operation, inputs, marker):
            visits.append(marker)
            numbers = yield operation, inputs
            return marker, numbers

        backend = _Backend()
        batch = SpecialReplayBatch(backend)
        cases = list(_cases())[:8]
        tokens = [batch.submit(replay, operation=op, inputs=data, marker=i)
                  for i, (op, data) in enumerate(cases)]
        self.assertTrue(all(isinstance(row, PendingSpecialReplay) for row in tokens))
        self.assertEqual(list(range(len(cases))), visits)
        self.assertEqual([], backend.calls)
        results = batch.resolve(checkpoint=lambda: None)
        self.assertEqual(list(range(len(cases))), visits)
        self.assertEqual(tuple((i, python_special_numeric(op, data))
                               for i, (op, data) in enumerate(cases)), results)
        self.assertEqual(len({op for op, _ in cases}), len(backend.calls))

    def test_missing_evidence_and_cancel_do_not_run_numeric_or_render(self):
        renders = []

        @numeric_replay
        def replay(known):
            if not known:
                return "missing evidence"
            operation, inputs = next(_cases())
            numbers = yield operation, inputs
            renders.append(numbers)
            return numbers

        batch = SpecialReplayBatch(_Backend())
        self.assertEqual("missing evidence", batch.submit(replay, known=False))
        self.assertIsInstance(batch.submit(replay, known=True), PendingSpecialReplay)

        def cancel():
            raise InterruptedError
        with self.assertRaises(InterruptedError):
            batch.resolve(checkpoint=cancel)
        self.assertEqual([], renders)


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "isolated native executable not configured")
class NativeSpecialReplayDifferentialTests(unittest.TestCase):
    def test_all_supported_special_numeric_fields_match_python(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-special-test",
        )
        self.assertTrue(client.supports_battle_compute)
        groups = {}
        for operation, inputs in _cases():
            groups.setdefault(operation, []).append(inputs)
        for operation, inputs in groups.items():
            with self.subTest(operation=operation):
                self.assertEqual(tuple(python_special_numeric(operation, row) for row in inputs),
                                 client.compute_batch(operation, inputs))

        # 缺失不是业务默认；保持原节点内完整对象差分，并核对两端拒绝残缺协议输入。
        valid = next(data for operation, data in _cases() if operation == "special_weave_v1")
        invalid = []
        for field in ("base_ratio", "extra_ratio", "final_multiplier"):
            row = dict(valid)
            del row[field]
            invalid.append(("missing " + field, row))
        invalid.append(("unknown reaction", {**valid, "reaction_multiplier": None}))
        for label, row in invalid:
            with self.subTest(invalid=label):
                with self.assertRaisesRegex(ValueError, "invalid_special_input"):
                    python_special_numeric("special_weave_v1", row)
                # 进程失败由正式 client 收敛为类型化异常，不要求它暴露组件 stdout 错误细节。
                with self.assertRaises(NativeAnalysisError):
                    client.compute_batch("special_weave_v1", [row])
