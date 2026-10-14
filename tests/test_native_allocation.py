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
    roles = {name: {"default_set": "Set", "weights": {"攻击力%": 1.0}, "board_matrix": [[1]],
                    "crit_source_known": True, "non_equipment_crit_rate": 0.0}
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

    def test_soft_main_fallback_after_prior_consumption_preserves_frozen_request(self):
        from src.models.equipment import Tape
        request, scorer = inputs()
        cards = (Tape(quality="Gold", area=15, uid="preferred", set_name="Set", main_stats="攻击力%", sub_stats={"攻击力%": 6.0}),
                 Tape(quality="Gold", area=15, uid="fallback", set_name="Set", main_stats="生命值%", sub_stats={"攻击力%": 3.0}),
                 Tape(quality="Gold", area=15, uid="wrong-suit", set_name="Other", main_stats="攻击力%", sub_stats={"攻击力%": 30.0}))
        request = replace(request, inventory=(*request.inventory, *cards), priority_groups=(("A",), ("B",)),
                          core_main_filters={"A": ("攻击力%",), "B": ("攻击力%",)})
        hard = self.executor()(request, scorer)
        self.assertEqual("preferred", hard["A"]["assigned_tape"].uid)
        self.assertIsNone(hard["B"]["assigned_tape"])
        soft = replace(request, stat_priority_configs={"B": {"soft_main_filter": True}})
        before = freeze(soft, scorer)
        plans = self.executor()(soft, scorer)
        self.assertEqual("preferred", plans["A"]["assigned_tape"].uid)
        self.assertEqual("fallback", plans["B"]["assigned_tape"].uid)
        self.assertTrue(plans["B"]["main_filter_fallback"])
        self.assertEqual(freeze(soft, scorer), before)
        python = AllocationKernel(scorer).execute(copy.deepcopy(soft))
        self.assertEqual("fallback", python["B"]["assigned_tape"].uid)

    def test_soft_main_fallback_reallocates_whole_peer_group_and_respects_cap(self):
        from src.models.equipment import Tape
        request, scorer = inputs()
        cards = (Tape(quality="Gold", area=15, uid="preferred", set_name="Set", main_stats="攻击力%", sub_stats={"攻击力%": 6.0}),
                 Tape(quality="Gold", area=15, uid="fallback", set_name="Set", main_stats="生命值%", sub_stats={"攻击力%": 3.0}))
        request = replace(request, inventory=(*request.inventory, *cards),
                          core_main_filters={name: ("攻击力%",) for name in request.role_order},
                          stat_priority_configs={name: {"soft_main_filter": True} for name in request.role_order})
        plans = self.executor()(request, scorer)
        self.assertEqual({"preferred", "fallback"}, {p["assigned_tape"].uid for p in plans.values()})
        crit = replace(request, inventory=(*request.inventory[:-2],
            Tape(quality="Gold", area=15, uid="capped", set_name="Set", main_stats="暴击率%", sub_stats={"攻击力%": 6.0})),
            role_order=("A",), priority_groups=(("A",),), crit_rate_caps={"A": 5.0})
        capped = self.executor()(crit, scorer)
        self.assertTrue(capped["A"]["valid"])
        self.assertIsNone(capped["A"].get("assigned_tape"))
        self.assertTrue(capped["A"]["main_filter_fallback_rejected"])

    def test_soft_fallback_rounds_are_bounded_atomic_and_cancelable(self):
        from src.optimizer.allocation_kernel import execute_with_soft_main_fallback
        request, _ = inputs()
        request = replace(request, priority_groups=(("A",), ("B",)),
                          core_main_filters={"A": ("甲",), "B": ("乙",)},
                          stat_priority_configs={name: {"soft_main_filter": True} for name in request.role_order})
        calls = []
        def result_for(frozen):
            calls.append(dict(frozen.core_main_filters))
            self.assertEqual(request.inventory, frozen.inventory)
            self.assertEqual(request.priority_groups, frozen.priority_groups)
            self.assertEqual(request.crit_rate_caps, frozen.crit_rate_caps)
            # 先放宽A抢占B原卡，再单独放宽B；全部结果由完整一轮产生。
            return {"A": {"valid": True, "assigned_tape": None if "A" in frozen.core_main_filters else "a"},
                    "B": {"valid": True, "assigned_tape": "b" if "A" in frozen.core_main_filters
                           or "B" not in frozen.core_main_filters else None}}
        result = execute_with_soft_main_fallback(request, result_for)
        self.assertEqual([{"A": ("甲",), "B": ("乙",)}, {"B": ("乙",)}, {}], calls)
        self.assertTrue(all(plan["main_filter_fallback"] for plan in result.values()))
        self.assertEqual({"A": ("甲",), "B": ("乙",)}, request.core_main_filters)
        cancelled = False
        def cancel_on_retry(frozen):
            nonlocal cancelled
            cancelled = True
            return {name: {"valid": True, "assigned_tape": None} for name in frozen.role_order}
        with self.assertRaises(CancelledError):
            execute_with_soft_main_fallback(replace(request, cancel_check=lambda: cancelled), cancel_on_retry)
        def failed_retry(frozen):
            if len(frozen.core_main_filters) < 2:
                raise NativeAnalysisError("fixture retry error")
            return {name: {"valid": True, "assigned_tape": None} for name in frozen.role_order}
        with self.assertRaisesRegex(NativeAnalysisError, "fixture retry"):
            execute_with_soft_main_fallback(request, failed_retry)
        # 拒绝导致已合法角色失效的整轮后，其余组仍从原已接受请求继续。
        attempts = []
        def invalidating(frozen):
            attempts.append(dict(frozen.core_main_filters))
            return {"A": {"valid": "A" in frozen.core_main_filters, "assigned_tape": None},
                    "B": {"valid": True, "assigned_tape": None if "B" in frozen.core_main_filters else "b"}}
        accepted = execute_with_soft_main_fallback(request, invalidating)
        self.assertEqual(3, len(attempts))
        self.assertTrue(accepted["A"]["valid"])
        self.assertTrue(accepted["A"]["main_filter_fallback_rejected"])
        self.assertEqual("b", accepted["B"]["assigned_tape"])
        self.assertEqual({"A": ("甲",)}, attempts[-1])

    def test_deployed_component_matches_python_score_and_preserves_inputs(self):
        request, scorer = inputs()
        before = freeze(request, scorer)
        python = AllocationKernel(scorer).execute(copy.deepcopy(request))
        plans = self.executor()(request, scorer)
        self.assertEqual(sum(plan["score"] for plan in plans.values()),
                         sum(plan["score"] for plan in python.values()))
        self.assertEqual(freeze(request, scorer), before)
        self.assertTrue(all(plan["valid"] for plan in plans.values()))
        self.assertTrue(all(plan["search_status"] == "found_unproven_optimum"
                            for plan in plans.values()))
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
                "non_equipment_crit_rate": 32.0,
            },
        }
        request = replace(request, roles_db=roles)
        payload = freeze(request, scorer)
        self.assertEqual(2, payload["request"]["version"])
        role = next(row for row in payload["request"]["roles"] if row["name"] == "A")
        self.assertEqual(32.0, role["data"]["active_fork_crit_rate_bonus"])
        self.assertEqual(32.0, role["data"]["non_equipment_crit_rate"])
        self.assertTrue(role["data"]["crit_source_known"])

    def test_wire_omits_catalogue_roles_without_frozen_crit_evidence(self):
        request, scorer = inputs()
        roles = {**request.roles_db, "C": {"default_set": "Set", "weights": {}}}
        request = replace(request, roles_db=roles, role_order=("A",), priority_groups=(("A",),))
        payload = freeze(request, scorer)["request"]
        self.assertEqual(["A"], [row["name"] for row in payload["roles"]])
        self.assertEqual([0], payload["role_order"])
        self.assertEqual([[0]], payload["groups"])

    def test_unknown_automatic_cap_is_frozen_distinct_from_manual_zero(self):
        from src.solver.orchestrator import NTEPipelineOrchestrator

        request, _ = inputs()
        orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
            roles_db=copy.deepcopy(request.roles_db), sets_db=request.sets_db,
            shapes_db={}, config_dir=ROOT / "config",
        )
        captured = []

        def capture(frozen_request, scorer):
            captured.append(freeze(frozen_request, scorer))
            return {}

        with patch.object(orchestrator, "solve_blueprints", return_value=request.blueprints_db), \
             patch.object(orchestrator, "_render_results"):
            orchestrator.run_full_allocation(
                [], ["A"], crit_rate_caps={"A": None}, crit_rate_baselines={},
                allocation_executor=capture,
            )
        role = next(row for row in captured[0]["request"]["roles"] if row["name"] == "A")
        self.assertIsNone(role["cap"])
        self.assertFalse(role["data"]["crit_source_known"])
        self.assertTrue(role["data"]["automatic_crit_cap_source_unknown"])

    def test_selected_fork_uses_frozen_formal_id(self):
        from src.solver.orchestrator import NTEPipelineOrchestrator

        request, _ = inputs()
        roles = copy.deepcopy(request.roles_db)
        roles["A"]["default_fork_id"] = "fork-default"
        orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
            roles_db=roles, sets_db=request.sets_db, shapes_db={},
            config_dir=ROOT / "config",
            fork_ids_by_name={"纠正弧盘": "fork-corrected"},
        )
        captured = []

        def capture(frozen_request, scorer):
            captured.append(freeze(frozen_request, scorer))
            return {}

        with patch.object(orchestrator, "solve_blueprints", return_value=request.blueprints_db), \
             patch.object(orchestrator, "_render_results"):
            orchestrator.run_full_allocation(
                [], ["A"], custom_weapons={"A": "纠正弧盘"},
                crit_rate_baselines={"A": 20.0}, allocation_executor=capture,
            )
        role = captured[0]["request"]["roles"][0]
        self.assertEqual("fork-corrected", role["data"]["effective_fork_id"])
        self.assertEqual(20.0, role["data"]["non_equipment_crit_rate"])

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
            capabilities=frozenset({"allocation_v2"}),
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
