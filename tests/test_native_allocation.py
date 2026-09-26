# 验证计算按钮原生接线、冻结装备恢复、能力拒绝及取消生命周期。
from __future__ import annotations

import copy
import json
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from src.integrations.native_allocation import create_allocation_executor, restore
from src.integrations.native_allocation_wire import freeze
from src.integrations.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.models.equipment import Drive
from src.optimizer.allocation_kernel import AllocationKernel, AllocationKernelRequest
from src.optimizer.scoring import ScoringEngine

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    roles = {name: {"default_set": "Set", "weights": {"攻击力%": 1.0}, "board_matrix": [[1]]}
             for name in ("A", "B")}
    inventory = tuple(Drive(uid=f"public-{index}", quality="Gold", area=1, shape_id="X",
                            set_name="Set", main_stats={"攻击力": 42, "生命值": 560},
                            sub_stats={"攻击力%": value}) for index, value in enumerate((6.0, 3.0, 1.0)))
    request = AllocationKernelRequest(
        inventory=inventory, roles_db=roles, sets_db={"Set": {"shapes": ["X"]}}, shapes_db={},
        blueprints_db={name: [{"set_pieces": [], "extra_pieces": ["X"], "board": [],
                               "set_effect_mode": "none"}] for name in roles},
        role_order=tuple(roles), strategy="role_priority", module_set_targets={}, set_effect_modes={},
        core_main_filters={}, core_set_targets={}, stat_priority_configs={}, property_limits={},
        priority_groups=(("A", "B"),),
    )
    return request, ScoringEngine(config_dir=ROOT / "config", roles_db=roles)


class NativeAllocationTests(unittest.TestCase):
    def executor(self, **options):
        return create_allocation_executor(static_database_path=ROOT / "data/game_static.sqlite3", **options)

    def test_deployed_component_matches_python_score_and_preserves_inputs(self):
        request, scorer = inputs()
        before = freeze(request, scorer)
        python = AllocationKernel(scorer).execute(copy.deepcopy(request))
        plans = self.executor()(request, scorer)
        self.assertEqual(sum(plan["score"] for plan in plans.values()),
                         sum(plan["score"] for plan in python.values()))
        self.assertEqual(freeze(request, scorer), before)
        self.assertTrue(all(plan["valid"] for plan in plans.values()))
        self.assertIsInstance(plans["A"]["assigned_extra_drives"][0], Drive)

    def test_large_user_combo_limit_is_not_rejected_or_clamped(self):
        request, scorer = inputs()
        request = replace(request, blueprint_combo_limit=200001)
        self.assertEqual(freeze(request, scorer)["request"]["combo_limit"], 200001)
        self.assertTrue(all(p["valid"] for p in self.executor()(request, scorer).values()))

    def test_wire_preserves_selected_fork_crit_projection_for_native_solver(self):
        request, scorer = inputs()
        roles = {
            **request.roles_db,
            "A": {
                **request.roles_db["A"],
                "active_fork_crit_rate_bonus": 32.0,
                "fork_crit_rate": 32.0,
            },
        }
        request = replace(request, roles_db=roles)
        payload = freeze(request, scorer)
        role = next(row for row in payload["request"]["roles"] if row["name"] == "A")
        self.assertEqual(32.0, role["data"]["active_fork_crit_rate_bonus"])
        self.assertEqual(32.0, role["data"]["fork_crit_rate"])

    def test_missing_component_has_no_python_fallback(self):
        with patch("src.integrations.native_allocation.create_bundled_analysis_client", return_value=None):
            with self.assertRaisesRegex(NativeAnalysisError, "空幕分配"):
                self.executor()

    def test_manifest_and_actual_binary_must_both_support_allocation(self):
        for advertised in (False, True):
            with self.subTest(advertised=advertised):
                client = Mock(supports_allocation=advertised)
                client.version.return_value = {"capabilities": []}
                with patch("src.integrations.native_allocation.create_bundled_analysis_client", return_value=client):
                    with self.assertRaises(NativeAnalysisError):
                        self.executor()
                client.allocate.assert_not_called()

    def test_cancel_before_binding_does_not_spawn(self):
        with patch("src.integrations.native_allocation.create_bundled_analysis_client") as bind:
            with self.assertRaises(CancelledError):
                self.executor(cancel_check=lambda: True)
            bind.assert_not_called()

    def test_request_cancel_does_not_enter_calculation(self):
        request, scorer = inputs()
        execute = self.executor()
        with patch("src.integrations.nte_analysis_core.subprocess.Popen") as spawn:
            with self.assertRaises(CancelledError):
                execute(replace(request, cancel_check=lambda: True), scorer)
            spawn.assert_not_called()

    def test_wrong_response_version_and_nonfinite_score_are_rejected(self):
        client = NteAnalysisCoreClient(
            ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe", "fixture",
            capabilities=frozenset({"allocation_v1"}),
        )
        for response in ({"batch_kind": "other", "version": 1, "plans": {}},
                         {"batch_kind": "allocation_v1", "version": True, "plans": {}},
                         {"batch_kind": "allocation_v1", "version": 1, "plans": []},
                         {"batch_kind": "allocation_v1", "version": 1, "plans": {"score": float("nan")}}):
            with patch.object(client, "_run", return_value=json.dumps(response).encode()), \
                    self.assertRaises(NativeAnalysisError):
                client.allocate({"batch_kind": "allocation_v1"})

    def test_response_rejects_changed_equipment_duplicate_uid_and_role(self):
        request, scorer = inputs()
        plans = self.executor()(request, scorer)
        wire = {role: {**plan, "assigned_extra_drives": [item.model_dump() for item in plan["assigned_extra_drives"]]}
                for role, plan in plans.items()}
        changed = copy.deepcopy(wire)
        changed["A"]["assigned_extra_drives"][0]["sub_stats"]["攻击力%"] += 1
        duplicate = copy.deepcopy(wire)
        duplicate["B"]["assigned_extra_drives"] = duplicate["A"]["assigned_extra_drives"]
        for invalid in (changed, duplicate, {"unknown": wire["A"]}):
            with self.subTest(invalid=list(invalid)), self.assertRaises(NativeAnalysisError):
                restore(invalid, request)

    def test_facade_uses_native_kernel_and_original_renderer_with_locked_uid_excluded(self):
        from src.app.facade import NTEAppFacade
        from src.solver.orchestrator import NTEPipelineOrchestrator
        request, _ = inputs()
        orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
            roles_db=request.roles_db, sets_db=request.sets_db, shapes_db={}, config_dir=ROOT / "config",
        )
        facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
                              user_database_path=ROOT / "unused.sqlite3",
                              allocation_static_database_path=ROOT / "data/game_static.sqlite3")
        with patch("src.app.facade.NTEPipelineOrchestrator", return_value=orchestrator), \
                patch.object(orchestrator, "solve_blueprints", return_value=request.blueprints_db), \
                patch.object(orchestrator, "_render_results") as render, \
                patch.object(AllocationKernel, "execute", side_effect=AssertionError("Python kernel called")):
            plans, _ = facade.execute_allocation_inventory(
                [item.model_dump() for item in request.inventory], list(request.role_order),
                locked_uids={"public-0"}, priority_groups=[["A", "B"]],
            )
        render.assert_called_once()
        used = [item.uid for plan in plans.values() for item in plan["assigned_extra_drives"]]
        self.assertEqual(set(used), {"public-1", "public-2"})


if __name__ == "__main__":
    unittest.main()
