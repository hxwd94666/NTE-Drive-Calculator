# 验证扩展计算按冻结操作整批传输、核对身份并保留取消和旧组件边界。
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.integrations.analysis_core.nte_analysis_core import (
    ENGINE_VERSION, NativeAnalysisCancelled, NativeAnalysisError, NteAnalysisCoreClient,
)


class NativeBattleComputeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "nte-analysis-core.exe"
        self.path.touch()
        self.client = NteAnalysisCoreClient(self.path, "frozen-dataset")

    @staticmethod
    def response():
        return {
            "schema_version": "nte-analysis-response-v1", "engine_version": ENGINE_VERSION,
            "dataset_version": "frozen-dataset", "batch_kind": "battle_compute_v1",
            "operation": "encounter_fit_v1", "compute_elapsed_ns": 25,
            "results": [{"job_id": "0", "value": {"winner_ref": "first"}}],
        }

    def test_operation_identity_and_batch_statistics(self):
        def run(payload, **_kwargs):
            request = json.loads(payload)
            self.assertEqual("battle_compute_v1", request["batch_kind"])
            self.assertEqual("encounter_fit_v1", request["operation"])
            self.assertEqual([{"job_id": "0", "input": {"candidates": []}}], request["jobs"])
            return json.dumps(self.response()).encode()

        with patch.object(self.client, "_run", side_effect=run):
            result = self.client.compute_batch("encounter_fit_v1", ({"candidates": []},))
        self.assertEqual(({"winner_ref": "first"},), result)
        self.assertEqual(1, self.client.stats["compute_batch_calls"])
        self.assertEqual(1, self.client.stats["compute_jobs"])

    def test_mismatched_or_nonfinite_response_is_never_accepted(self):
        variants = (
            {"operation": "wrong"}, {"dataset_version": "other"},
            {"engine_version": "0.2.0"}, {"compute_elapsed_ns": True},
            {"results": [{"job_id": "different", "value": {}}]},
            {"results": [{"job_id": "0", "value": {"score": float("nan")}}]},
        )
        for changed in variants:
            with self.subTest(changed=changed):
                payload = {**self.response(), **changed}
                with patch.object(self.client, "_run", return_value=json.dumps(payload).encode()):
                    with self.assertRaises(NativeAnalysisError):
                        self.client.compute_batch("encounter_fit_v1", ({},))

    def test_old_component_does_not_launch_and_cancellation_propagates(self):
        client = NteAnalysisCoreClient(self.path, "fixture", engine_version="0.2.0")
        with patch.object(client, "_run") as run:
            with self.assertRaises(NativeAnalysisError):
                client.compute_batch("encounter_fit_v1", ({},))
        run.assert_not_called()
        with patch.object(self.client, "_run", side_effect=NativeAnalysisCancelled("cancel")) as run:
            with self.assertRaises(NativeAnalysisCancelled):
                self.client.compute_batch("encounter_fit_v1", ({},))
        self.assertEqual(1, run.call_count)

    def test_json_exponent_overflow_is_rejected(self):
        raw = json.dumps(self.response()).replace('"first"', "1e400").encode()
        with patch.object(self.client, "_run", return_value=raw):
            with self.assertRaises(NativeAnalysisError):
                self.client.compute_batch("encounter_fit_v1", ({},))

    def test_empty_batch_checks_cancellation_without_starting_child(self):
        with patch.object(self.client, "_run") as run:
            self.assertEqual((), self.client.compute_batch("encounter_fit_v1", ()))
        run.assert_not_called()
        def cancelled():
            raise NativeAnalysisCancelled("cancel")
        with self.assertRaises(NativeAnalysisCancelled):
            self.client.compute_batch("encounter_fit_v1", (), checkpoint=cancelled)
