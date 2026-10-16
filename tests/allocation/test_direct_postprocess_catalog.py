# 验证发行图鉴与临时完整库存经真实门面和官方养成冻结完成直伤优化。
from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import patch

from allocation.direct_catalog_fixture import create_catalog_case, ROOT
from src.app.facade import NTEAppFacade
from src.integrations.nte_analysis_core import NteAnalysisCoreClient


NTE_TEST_TIER = "core"


def test_actual_catalog_profile_and_complete_inventory_use_real_facade(tmp_path):
    case = create_catalog_case(tmp_path)
    client = NteAnalysisCoreClient.from_executable(Path(os.environ.get("NTE_ALLOCATION_POSTPROCESS_TEST_EXE") or (
        ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")), case["dataset_id"])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
                          user_database_path=case["user_database_path"],
                          allocation_static_database_path=case["static_database_path"])
    # 仅显式注入隔离候选组件；官方数据、角色投影、图纸、协议和目标均运行真实路径。
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client), \
            patch.object(client, "_run", wraps=client._run) as generate, \
            patch("src.solver.orchestrator.NTEPipelineOrchestrator.solve_blueprints",
                  side_effect=AssertionError("Python blueprint search called")):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], [case["selected_role"]],
            mode="direct_priority", tape_main_filters=case["core_main_filters"],
            crit_rate_baselines=case["crit_rate_baselines"])
    batches = [json.loads(call.args[0])["batch_kind"] for call in generate.call_args_list if call.args and call.args[0]]
    assert batches.count("damage_allocation_v1") == 1
    assert not set(batches) & {"allocation_v1", "allocation_blueprints_v1", "equipment_scoring_v1"}
    result = plans[case["selected_role"]]
    assert result["valid"]
    assert sum(len(row) for row in result["blueprint"]["board"]) == 25
    assert result["damage_optimization"]["status"] == "completed", result["damage_optimization"]
    assert result["damage_optimization"]["candidate_value"] >= result["damage_optimization"]["baseline_value"]
    assert result["damage_optimization"]["source_snapshot_id"] == case["snapshot_id"]
