# 验证特殊伤害批处理只准备一次，并对独立原生数值核逐字段做差分。
from __future__ import annotations

import os
import unittest
from pathlib import Path

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
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
                "ring_strength": strength, "lingke_passive": strength > 0,
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
