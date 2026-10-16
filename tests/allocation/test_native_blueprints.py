# 验证 Rust 图纸协议的冻结身份、完整结果门禁与取消，不以局部候选冒充完整图纸。
from copy import deepcopy
import json
from pathlib import Path
from concurrent.futures import CancelledError
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from src.integrations.nte_analysis_core import (
    NteAnalysisCoreClient, NativeAnalysisError, NativeAnalysisCancelled,
    REQUEST_SCHEMA, RESPONSE_SCHEMA, ENGINE_VERSION,
)


def request():
    return {
        "version": 1,
        "shapes": [{"shape_id": "A", "label": "one", "area": 1, "matrix": [[1]]}],
        "roles": [{"role": "role", "board": [[0] * 5 for _ in range(4)],
                   "set_shapes": [], "set_effect_mode": "none", "extra_label": "one"}],
        "include_layout_variants": False,
        "budget": {"max_nodes": 10000, "max_blueprints": 100, "max_millis": 1000,
                   "max_combinations": 100, "max_output_bytes": 10000},
    }


def response():
    return {"schema_version": RESPONSE_SCHEMA, "engine_version": ENGINE_VERSION,
            "dataset_version": "frozen", "batch_kind": "allocation_blueprints_v1", "version": 1,
            "complete": True, "termination": "complete", "nodes": 50, "combinations": 1,
            "generated": 1, "output_bytes": 200,
            "blueprints": {"role": [{"set_pieces": [], "extra_pieces": ["A"] * 20,
                                      "set_effect_mode": "none", "board": [["A"] * 5 for _ in range(4)]}]}}


class NativeBlueprintProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.executable = Path(temporary.name) / "nte-analysis-core.exe"
        self.executable.write_bytes(b"protocol-only fixture; never executed")

    def client(self, *, capabilities=frozenset({"allocation_blueprints_v1"}), cancelled=None):
        return NteAnalysisCoreClient(self.executable, "frozen", engine_version=ENGINE_VERSION,
                                     capabilities=capabilities, cancelled=cancelled)

    def test_complete_result_preserves_request_and_uses_one_batch(self):
        client = self.client()
        original = request()
        frozen = deepcopy(original)
        with patch.object(client, "_run", return_value=json.dumps(response()).encode()) as run:
            result = client.generate_blueprints(original)
        self.assertEqual(frozen, original)
        self.assertEqual(response(), result)
        payload = json.loads(run.call_args.args[0])
        self.assertEqual(REQUEST_SCHEMA, payload["schema_version"])
        self.assertEqual("frozen", payload["dataset_version"])
        self.assertEqual(frozen, payload["request"])
        self.assertEqual(1, run.call_count)

    def test_truncation_is_returned_explicitly_not_as_completed_blueprints(self):
        client = self.client()
        truncated = response()
        truncated.update(complete=False, termination="node_budget")
        with patch.object(client, "_run", return_value=json.dumps(truncated).encode()):
            result = client.generate_blueprints(request())
        self.assertIs(False, result["complete"])
        self.assertEqual("node_budget", result["termination"])

    def test_stale_identity_unknown_shapes_or_unfilled_boards_are_rejected(self):
        for change in ("dataset", "role", "shape", "board", "completion", "count", "budget"):
            with self.subTest(change=change):
                bad = response()
                if change == "dataset":
                    bad["dataset_version"] = "other"
                elif change == "role":
                    bad["blueprints"]["other"] = []
                elif change == "shape":
                    bad["blueprints"]["role"][0]["extra_pieces"][0] = "unknown"
                elif change == "board":
                    bad["blueprints"]["role"][0]["board"][0][0] = 0
                elif change == "completion":
                    bad["complete"] = False
                elif change == "budget":
                    bad["nodes"] = 10001
                else:
                    bad["generated"] = 2
                client = self.client()
                with patch.object(client, "_run", return_value=json.dumps(bad).encode()), \
                        self.assertRaises(NativeAnalysisError):
                    client.generate_blueprints(request())

    def test_capability_and_cancel_are_checked_before_starting_process(self):
        cancelled = self.client()
        cancelled.cancelled = lambda: True
        for client, exception in ((self.client(capabilities=frozenset()), NativeAnalysisError),
                                  (cancelled, NativeAnalysisCancelled)):
            with patch.object(client, "_run") as run, self.assertRaises(exception):
                client.generate_blueprints(request())
            run.assert_not_called()

    def test_allocation_requires_complete_batch_and_converts_native_cancellation(self):
        from src.integrations.native_blueprints import create_blueprint_executor
        client = self.client()
        execute = create_blueprint_executor(client)
        with patch.object(client, "generate_blueprints", return_value=response()) as generate:
            plans = execute(request())
        self.assertEqual(response()["blueprints"], plans)
        self.assertEqual(1, generate.call_count)
        partial = response()
        partial.update(complete=False, termination="node_budget")
        with patch.object(client, "generate_blueprints", return_value=partial), \
                self.assertRaises(NativeAnalysisError):
            execute(request())
        with patch.object(client, "generate_blueprints", side_effect=NativeAnalysisCancelled("cancel")), \
                self.assertRaises(CancelledError):
            execute(request())

    def test_injected_native_blueprints_reach_allocation_without_python_search(self):
        from src.models.equipment import DriveShape
        from src.solver.orchestrator import NTEPipelineOrchestrator
        shapes = {"A": DriveShape(shape_id="A", label="one", area=1, matrix=[[1]])}
        orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
            roles_db={"role": {"default_set": "Set", "extra_shape_label": "one",
                               "board_matrix": [[0] * 5 for _ in range(4)]}},
            sets_db={"Set": {"shapes": []}}, shapes_db=shapes,
        )
        calls = []

        def blueprint_executor(frozen):
            calls.append(frozen)
            self.assertEqual(request()["roles"], frozen["roles"])
            return response()["blueprints"]

        def allocate(frozen, scorer):
            self.assertEqual(response()["blueprints"], frozen.blueprints_db)
            self.assertEqual(("role",), frozen.role_order)
            return {}

        with patch.object(orchestrator, "solve_blueprints", side_effect=AssertionError("Python search called")), \
                patch("src.solver.orchestrator.ScoringEngine", return_value=SimpleNamespace()):
            result = orchestrator.run_full_allocation(
                inventory=[], priority_list=["role"], set_effect_modes={"role": "none"},
                allocation_executor=allocate, blueprint_executor=blueprint_executor)
        self.assertEqual({}, result)
        self.assertEqual(1, len(calls))
