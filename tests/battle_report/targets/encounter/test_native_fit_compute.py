# 验证整批目标残差与局部暴击迁移保留共同样本、顺序和弱证据语义。
from __future__ import annotations

import math
import os
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.domain.battle_report import BattleHitReplayFactor, BattleHitReplayResult
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.targets.encounter.fit import (
    BattleEncounterFitCandidate,
    BattleEncounterFitPrediction,
    BattleEncounterFitService,
)
from src.services.battle_report.replay.local_critical import (
    BattleHitLocalCritInferenceService,
)


def _candidates():
    predictions = (
        BattleEncounterFitPrediction("hit-1", 100.0, 100.0, 200.0, 125.0, "组-A"),
        BattleEncounterFitPrediction("hit-2", 200.0, 100.0, 200.0, 125.0, "组-A"),
        BattleEncounterFitPrediction("hit-3", 100.0, 100.0, 100.0, None, "ß"),
        BattleEncounterFitPrediction("hit-4", 100.0, None, None, 100.0, "SS"),
        BattleEncounterFitPrediction("invalid-probability", 100.0, 100.0, 200.0, 250.0),
        BattleEncounterFitPrediction("nonfinite", float("nan"), 100.0),
        BattleEncounterFitPrediction("weight", 100.0, 100.0, evidence_weight=-1.0),
        BattleEncounterFitPrediction("duplicate", 100.0, 100.0),
        BattleEncounterFitPrediction("duplicate", 100.0, 100.0),
        BattleEncounterFitPrediction("", 100.0, 100.0),
    )
    return (
        BattleEncounterFitCandidate("ß", predictions),
        BattleEncounterFitCandidate(
            "SS",
            tuple(
                replace(row, non_critical_damage=row.non_critical_damage * 0.7)
                if row.non_critical_damage else row
                for row in predictions
            ) + (BattleEncounterFitPrediction("missing", 100.0, 100.0),),
        ),
    )


def _local_fixture():
    hits = []
    results = []
    groups = (
        (1001, "regular", (100.0, 100.0, 200.0, 200.0, 300.0, 600.0)),
        (1001, "overlapping", (100.0, 100.0, 200.0, 200.0, 400.0, 400.0)),
        (None, "rounding", (10.0625, 10.0625, 20.125, 20.125)),
        (1002, "insufficient", (100.0, 200.0, 300.0)),
        (1004, "layered", (100.0, 100.0, 200.0, 200.0)),
        (1001, "boundary", (100.0, 200.0, 300.0, 612.0, 400.0, 816.0)),
    )
    for character, effect, values in groups:
        for damage in values:
            event = f"event-{len(hits)}"
            hits.append(SimpleNamespace(
                event_id=event, character_id=character, gameplay_effect_id=effect,
            ))
            factors = (
                (BattleHitReplayFactor("state_coefficient", "层数", 1.0, "fixture"),)
                if effect == "layered" else ()
            )
            results.append(BattleHitReplayResult(
                event_id=event, observed_damage=damage,
                non_critical_damage=100.0, critical_damage=200.0,
                selected_damage=100.0, selected_error_percent=0.0,
                critical_state="ambiguous", confidence="低", factors=factors,
                expected_damage=125.0,
            ))
    return SimpleNamespace(hits=tuple(hits), baselines=()), tuple(results)


class NativeBattleFitAdapterTests(unittest.TestCase):
    def test_page_cancellation_checkpoint_reaches_both_native_operations(self):
        checkpoint = Mock()
        operations = []

        class Capturing:
            supports_battle_compute = True

            def compute_batch(inner, operation, inputs, **kwargs):
                self.assertIs(checkpoint, kwargs["checkpoint"])
                operations.append(operation)
                raise RuntimeError("native boundary")

        with self.assertRaisesRegex(RuntimeError, "native boundary"):
            BattleEncounterFitService.select(
                _candidates(), backend=Capturing(), checkpoint=checkpoint,
            )
        analysis, results = _local_fixture()
        with self.assertRaisesRegex(RuntimeError, "native boundary"):
            BattleHitLocalCritInferenceService.apply(
                analysis, results, backend=Capturing(), checkpoint=checkpoint,
            )
        self.assertEqual(["encounter_fit_v1", "local_crit_pairs_v1"], operations)
        self.assertGreater(checkpoint.call_count, 0)

    def test_unavailable_capability_uses_public_python_oracle(self):
        candidates = _candidates()
        backend = SimpleNamespace(supports_battle_compute=False)
        with patch.object(
            BattleEncounterFitService, "select_python", return_value="oracle",
        ) as oracle:
            self.assertEqual("oracle", BattleEncounterFitService.select(candidates, backend=backend))
        oracle.assert_called_once_with(candidates)
        analysis, results = _local_fixture()
        self.assertEqual(
            BattleHitLocalCritInferenceService.apply_python(analysis, results),
            BattleHitLocalCritInferenceService.apply(analysis, results, backend=backend),
        )

    def test_native_transport_failure_never_claims_oracle_success(self):
        class Broken:
            supports_battle_compute = True

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                raise RuntimeError("cancelled")

        with patch.object(BattleEncounterFitService, "select_python") as oracle:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                BattleEncounterFitService.select(_candidates(), backend=Broken())
            oracle.assert_not_called()


@unittest.skipUnless(
    os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
    "explicit isolated calculation executable not configured",
)
class NativeBattleFitExecutableTests(unittest.TestCase):
    def client(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-fit-test",
        )
        self.assertTrue(client.supports_battle_compute)
        return client

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
            if math.isnan(expected):
                self.assertTrue(math.isnan(actual))
            else:
                self.assertTrue(math.isclose(expected, actual, rel_tol=1e-11, abs_tol=1e-11))
        else:
            self.assertEqual(expected, actual)

    def test_full_fit_audits_match_python_without_python_numeric_fallback(self):
        candidates = _candidates()
        expected = BattleEncounterFitService.select_python(candidates)
        with patch.object(
            BattleEncounterFitService, "select_python", side_effect=AssertionError("fallback"),
        ):
            actual = BattleEncounterFitService.select(candidates, backend=self.client())
        self.assert_equivalent(asdict(expected), asdict(actual))

    def test_order_ties_unique_empty_evidence_and_probability_endpoints(self):
        for predictions in (
            (),
            (BattleEncounterFitPrediction("a", 100.0, 100.0, 200.0, 100.0),),
            (BattleEncounterFitPrediction("a", 200.0, 100.0, 200.0, 200.0),),
        ):
            for refs in (("ß", "SS"), ("solo",)):
                candidates = tuple(BattleEncounterFitCandidate(ref, predictions) for ref in refs)
                self.assert_equivalent(
                    asdict(BattleEncounterFitService.select_python(candidates)),
                    asdict(BattleEncounterFitService.select(candidates, backend=self.client())),
                )

    def test_local_pair_windows_rounding_overlaps_and_insufficient_evidence(self):
        analysis, results = _local_fixture()
        expected = BattleHitLocalCritInferenceService.apply_python(analysis, results)
        with patch.object(
            BattleHitLocalCritInferenceService, "apply_python", side_effect=AssertionError("fallback"),
        ):
            actual = BattleHitLocalCritInferenceService.apply(
                analysis, results, backend=self.client(),
            )
        self.assert_equivalent(tuple(map(asdict, expected)), tuple(map(asdict, actual)))
