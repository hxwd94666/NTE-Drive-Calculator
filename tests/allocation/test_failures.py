# 验证同一冻结失败证据、保守短缺说明及正式facade和日志消费。
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.app.facade import NTEAppFacade
from src.domain.stat_catalog import StatCatalog
from src.models.equipment import Drive
from src.domain.allocation.failures import explain_allocation_failures, allocation_failure_text
from src.solver.orchestrator import NTEPipelineOrchestrator

NTE_TEST_TIER = "core"


RESERVATION_REASON = "前序同分驱动无法一对一回填"


def _drive(uid: str, shape: str, *, crit: bool = False) -> Drive:
    return Drive(
        uid=uid, quality="Gold", area=4 if shape == "H_4" else 2,
        shape_id=shape, main_stats={"攻击力": 1.0, "生命值": 1.0},
        sub_stats={"暴击率%": 4.0} if crit else {},
    )


def _request(*, optional_shape: bool = False, zero_weight: bool = False):
    blocked_blueprints = [
        {"set_pieces": ["H_4"], "extra_pieces": ["V_2"]},
        {"set_pieces": ["H_4"], "extra_pieces": ["H_2"]},
    ]
    if optional_shape:
        blocked_blueprints.append({"set_pieces": ["V_2"], "extra_pieces": []})
    return SimpleNamespace(
        inventory=(
            _drive("h4-a", "H_4", crit=True),
            _drive("h4-b", "H_4", crit=True),
            _drive("v2", "V_2"),
        ),
        blueprints_db={"Blocked": blocked_blueprints, "Peer": []},
        stat_priority_configs={
            "Blocked": {"blacklist": ["暴击率%"], "blacklist_zero_weight": zero_weight},
        },
        priority_groups=(("Blocked", "Peer"),),
    )


class AllocationFailureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = StatCatalog(gold_base_values={"暴击率%": 1.0})

    def test_required_shape_blacklisted_for_every_blueprint(self):
        plans = {
            "Blocked": {"valid": False, "reason": RESERVATION_REASON},
            "Peer": {"valid": False, "reason": RESERVATION_REASON},
        }
        explained = explain_allocation_failures(plans, _request(), self.catalog)
        self.assertIn("H_4", explained["Blocked"]["reason"])
        self.assertIn("黑名单", explained["Blocked"]["reason"])
        self.assertIn("合格 0", explained["Blocked"]["reason"])
        self.assertIn("同级组", explained["Peer"]["reason"])
        self.assertIn("独立可行性仍未判定", explained["Peer"]["reason"])
        self.assertNotIn("一对一回填", explained["Peer"]["reason"])
        self.assertEqual(plans["Blocked"]["reason"], RESERVATION_REASON)

    def test_zero_weight_and_optional_shape_do_not_claim_hard_shortage(self):
        plans = {"Blocked": {"valid": False, "reason": RESERVATION_REASON}}
        for request in (_request(zero_weight=True), _request(optional_shape=True)):
            with self.subTest(request=request):
                self.assertIs(explain_allocation_failures(plans, request, self.catalog), plans)

    def test_missing_mandatory_shape_is_reported_without_blacklist_attribution(self):
        request = _request()
        request.inventory = (_drive("v2", "V_2"),)
        plans = {"Blocked": {"valid": False, "reason": RESERVATION_REASON}}
        explained = explain_allocation_failures(plans, request, self.catalog)
        self.assertIn("H_4", explained["Blocked"]["reason"])
        self.assertIn("候选 0", explained["Blocked"]["reason"])
        self.assertNotIn("黑名单过滤后", explained["Blocked"]["reason"])

    def test_distinct_blueprints_each_have_a_different_proven_shape_shortage(self):
        request = _request(zero_weight=True)
        request.inventory = (_drive("v2", "V_2"),)
        request.blueprints_db["Blocked"] = [
            {"set_pieces": ["H_4"], "extra_pieces": ["V_2"]},
            {"set_pieces": ["H_2"], "extra_pieces": ["V_2"]},
        ]
        plans = {"Blocked": {
            "valid": False, "search_status": "budget_exhausted",
            "reason": "本次搜索预算耗尽",
        }}
        explained = explain_allocation_failures(plans, request, self.catalog)
        self.assertIn("全部 2 张图纸各有驱动形状缺口", explained["Blocked"]["reason"])
        self.assertEqual("constraint_proven", explained["Blocked"]["search_status"])

        request.blueprints_db["Blocked"].append(
            {"set_pieces": ["V_2"], "extra_pieces": []}
        )
        self.assertIs(explain_allocation_failures(plans, request, self.catalog), plans)

    def test_bounded_native_failure_uses_proven_shape_shortage(self):
        for status in ("bounded_unproven", "budget_exhausted", "candidate_truncated"):
            plans = {"Blocked": {
                "valid": False, "search_status": status,
                "reason": "本次有界搜索未找到完整配装",
            }}
            explained = explain_allocation_failures(plans, _request(), self.catalog)
            self.assertIn("黑名单", explained["Blocked"]["reason"])
            self.assertEqual("constraint_proven", explained["Blocked"]["search_status"])

    def test_preserves_success_and_unrelated_failure_reason(self):
        plans = {
            "Blocked": {"valid": False, "reason": RESERVATION_REASON},
            "Peer": {"valid": False, "reason": "暴击率上限未满足"},
            "Done": {"valid": True, "score": 12.0},
        }
        explained = explain_allocation_failures(plans, _request(), self.catalog)
        self.assertEqual(explained["Peer"], plans["Peer"])
        self.assertIs(explained["Done"], plans["Done"])

        explicit = {"Blocked": {"valid": False, "reason": "暴击率下限未满足"}}
        self.assertIs(explain_allocation_failures(explicit, _request(), self.catalog), explicit)

    def test_facade_uses_frozen_native_request_for_explanation(self):
        request = _request()
        scorer = Mock(stat_catalog=self.catalog)
        plans = {
            "Blocked": {"valid": False, "reason": RESERVATION_REASON},
            "Peer": {"valid": False, "reason": RESERVATION_REASON},
        }
        facade = NTEAppFacade(
            config_dir="config", user_config_dir="config",
            user_database_path="unused.sqlite3", allocation_static_database_path="data/game_static.sqlite3",
        )
        with patch("src.app.facade.create_allocation_executor", return_value=lambda *_: plans), \
             patch("src.app.facade.NTEPipelineOrchestrator") as orchestrator_type:
            orchestrator_type.return_value.run_full_allocation.side_effect = (
                lambda **kwargs: kwargs["allocation_executor"](request, scorer)
            )
            result, _ = facade.execute_allocation_inventory([], ["Blocked", "Peer"])
        self.assertIn("黑名单", result["Blocked"]["reason"])
        self.assertIn("独立可行性仍未判定", result["Peer"]["reason"])


class AllocationFailureTextTests(unittest.TestCase):
    def test_crit_evidence_and_search_uncertainty_share_one_line(self):
        plan = {
            "valid": False,
            "reason": "本次候选装备暴击率下界 74% 超过上限 70%\n；组级搜索预算耗尽，可行性未定",
            "search_status": "budget_exhausted",
            "group_search": {"members": 6, "completed": 5},
        }
        before = deepcopy(plan)
        text = allocation_failure_text(plan)
        self.assertIn("74% 超过上限 70%", text)
        self.assertIn("可行性未定", text)
        self.assertIn("同级组已完成 5/6 人", text)
        self.assertNotIn("\n", text)
        self.assertEqual(plan, before)

    def test_proven_failure_does_not_gain_an_unproven_search_claim(self):
        text = allocation_failure_text({
            "valid": False, "search_status": "constraint_proven",
            "reason": "暴击率上限 3% 低于基础暴击率 5%",
            "group_search": {"members": 6, "completed": 5},
        })
        self.assertEqual(text, "暴击率上限 3% 低于基础暴击率 5%")

    def test_missing_reason_retains_diagnostic_fallback(self):
        self.assertEqual(allocation_failure_text(None), "本次未生成可保存方案，原因待诊断")

    def test_failure_log_uses_the_same_reason_as_result_rendering(self):
        plan = {
            "valid": False, "reason": "本次候选暴击率上界 60% 低于最小值 70%",
            "search_status": "candidate_truncated",
            "group_search": {"members": 6, "completed": 5},
        }
        with patch("src.solver.orchestrator.logger") as log:
            NTEPipelineOrchestrator._render_results(
                SimpleNamespace(), {"Synthetic": plan}, Mock(), {},
            )
        log.error.assert_called_once()
        self.assertIn(allocation_failure_text(plan), log.error.call_args.args)
