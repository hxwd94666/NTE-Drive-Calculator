# 在完整 Rust 作业保留取消、数值证明及冻结约束篡改门禁，不再测试已退场的 Python 面板公式。
from __future__ import annotations

from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tests.allocation.test_axis_postprocess_component import axis_facade_case, ROOT
from src.app.facade import NTEAppFacade
from src.integrations.damage_allocation_job import _decode, execute_damage_job
from src.integrations.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError, NteAnalysisCoreClient
from src.services.allocation.direct_group_postprocess import optimize_standard_direct_groups

NTE_TEST_TIER = "core"


def test_full_job_rejects_forged_score_or_plan_and_changed_frozen_constraints(tmp_path):
    case = axis_facade_case(tmp_path)
    executable = Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")
    client = NteAnalysisCoreClient.from_executable(executable, case["dataset_id"])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    capture = {}
    run = client._run
    def record(payload=None, **kwargs):
        result = run(payload, **kwargs)
        if payload and json.loads(payload).get("batch_kind") == "damage_allocation_v1":
            capture.update(sent=json.loads(payload)["request"], result=json.loads(result))
        return result
    def record_inputs(*args, **kwargs):
        capture.update(request=args[1], scorer=args[2], frozen=args[3])
        return execute_damage_job(*args, **kwargs)
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client), \
            patch("src.app.facade.execute_damage_job", side_effect=record_inputs), patch.object(client, "_run", side_effect=record):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], case["selected_roles"], mode="direct_priority",
            custom_sets={"灵可": case["suit_name"]}, set_effect_modes={"灵可": "none"},
            tape_main_filters={"灵可": ["攻击力%"]}, crit_rate_baselines=case["crit_baselines"],
            postprocess_budget={"max_evaluations": 20_000, "max_rounds": 3})
    assert plans["灵可"]["damage_optimization"]["status"] == "completed"
    # 原生进程取消仍归计算 owner 的取消通道，不作为普通组件故障发布。
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client), \
            patch("src.app.facade.execute_damage_job", side_effect=NativeAnalysisCancelled("cancelled")):
        with pytest.raises(CancelledError):
            facade.execute_allocation_inventory(case["inventory"], case["selected_roles"], mode="direct_priority",
                custom_sets={"灵可": case["suit_name"]}, set_effect_modes={"灵可": "none"},
                tape_main_filters={"灵可": ["攻击力%"]}, crit_rate_baselines=case["crit_baselines"])

    request, scorer = capture["request"], capture["scorer"]
    terminal = deepcopy(capture["result"])
    terminal["elapsed_millis"] = capture["sent"]["max_millis"] + 1
    assert _decode(client, request, scorer, capture["sent"], terminal, axis=False)[2].response is not None
    terminal["elapsed_millis"] = capture["sent"]["max_millis"] + 1_001
    with pytest.raises(NativeAnalysisError):
        _decode(client, request, scorer, capture["sent"], terminal, axis=False)

    for change in (dict(crit_rate_caps={"灵可": 10.0}),
                   dict(stat_priority_configs={"灵可": {"crit_threshold": 90.0}}),
                   dict(stat_priority_configs={"灵可": {"blacklist": ["攻击力%"]}}),
                   dict(core_main_filters={"灵可": ("生命值%",)})):
        with pytest.raises(NativeAnalysisError):
            _decode(client, replace(request, **change), scorer, capture["sent"], capture["result"], axis=False)
    for variant in ("score", "null", "invalid"):
        forged = deepcopy(capture["result"])
        plan = forged["result"]["plans"]["灵可"]
        if variant == "score":
            plan["assigned_tape"]["role_scores"]["灵可"] += 1.0
            plan["score"] += 1.0
        elif variant == "null":
            forged["result"]["plans"]["灵可"] = None
        else:
            plan["valid"] = False
        try:
            effective, baseline, native = _decode(client, request, scorer, capture["sent"], forged, axis=False)
        except NativeAnalysisError:
            continue  # 协议层已拒绝，无预览或写入；其他变体继续验证服务恢复门禁。
        original = deepcopy(baseline)
        result = optimize_standard_direct_groups(client, effective, scorer, baseline, capture["frozen"], native_job=native)
        assert result["灵可"]["damage_optimization"]["status"] == "failed", variant
        assert result["灵可"]["score"] == original["灵可"]["score"]
        assert baseline == original


def test_cancelling_complete_job_before_dispatch_starts_no_process():
    def cancelled():
        raise NativeAnalysisCancelled("cancelled")
    client = SimpleNamespace(_checkpoint=lambda fn: fn(), _run=Mock())
    with pytest.raises(NativeAnalysisCancelled):
        execute_damage_job(client, None, None, None, None, None, checkpoint=cancelled)
    client._run.assert_not_called()


@pytest.mark.parametrize("code,text", [("damage_job_blueprints_incomplete", "图纸生成已达预算"),
                                     ("damage_job_time_budget", "分配准备已达时间预算")])
def test_preparation_budget_failure_is_actionable_without_exposing_payload(code, text):
    client = SimpleNamespace(engine_version="0.3.0")
    payload = json.dumps({"engine_version":"0.3.0", "schema_version":"nte-analysis-response-v1",
                          "error":{"code":code,"details":"private fixture payload"}}).encode("utf8")
    with pytest.raises(NativeAnalysisError, match=text) as caught:
        NteAnalysisCoreClient._check_exit(client, 2, payload)
    assert "private fixture payload" not in str(caught.value)
    assert "现有方案保持不变" in str(caught.value)

    malformed = json.loads(payload)
    malformed["error"]["code"] = []
    with pytest.raises(NativeAnalysisError, match="退出码"):
        NteAnalysisCoreClient._check_exit(client, 2, json.dumps(malformed).encode("utf8"))
