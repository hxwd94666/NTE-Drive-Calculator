# 同一流程的公共行为回归合并，保留各自数据、失败与回滚断言。
from __future__ import annotations

# 验证原值边界只挑选冻结身份行、隔离副本并及时取消，不在 Python 计算属性。

from concurrent.futures import CancelledError
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.allocation.test_direct_postprocess import fixture
from src.services.allocation.direct.direct_projection import freeze_direct_projection_inputs

NTE_TEST_TIER = "core"


def test_raw_projection_serializer_preserves_original_units_and_omits_page_context():
    request, _, _, raw, detail = fixture()
    detail["equipment_contexts"] = {"stale": {"items": ["not-consumed"]}}
    detail["static_database_path"] = "not-consumed"
    before = deepcopy((raw, detail))
    with patch("src.services.official_role.panel.attributes.calculate_official_role_combat_stat_components",
               side_effect=AssertionError("Python panel calculation called")), \
            patch("src.domain.equipment.properties.official_stats.calculate_official_equipment_stats",
                  side_effect=AssertionError("Python equipment calculation called")):
        result = freeze_direct_projection_inputs(request, (("A", detail),), raw)
    assert result["inventory"]["nte-core-3-30"]["main_stats"][0]["value"] == 0.10
    assert result["details"]["A"]["shape_bonus"]["properties"][0]["display_value"] == 5.0
    assert len(result["details"]["A"]["growth_rows"]) == 1
    assert "equipment_contexts" not in result["details"]["A"]
    assert "static_database_path" not in result["details"]["A"]
    assert (raw, detail) == before
    raw[0]["official"]["sub_stats"][0]["value"] = 999.0
    assert result["inventory"]["nte-module-1-10"]["sub_stats"][0]["value"] == 0.06


def test_raw_projection_serializer_cancels_or_rejects_incomplete_input_without_partial_output():
    request, _, _, raw, detail = fixture()
    def cancelled():
        raise CancelledError
    with pytest.raises(CancelledError):
        freeze_direct_projection_inputs(request, (("A", detail),), raw, checkpoint=cancelled)
    with pytest.raises(ValueError):
        freeze_direct_projection_inputs(request, (("A", detail),), raw[:-1])
    with pytest.raises(ValueError):
        freeze_direct_projection_inputs(request, (("A", detail), ("A", detail)), raw)

# 验证发行图鉴与临时完整库存经真实门面和官方养成冻结完成直伤优化。

import os
import json
from pathlib import Path
from unittest.mock import patch

from tests.allocation.direct_catalog_fixture import create_catalog_case, ROOT
from src.app.facade import NTEAppFacade
from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient


NTE_TEST_TIER = "core"


def test_actual_catalog_profile_and_complete_inventory_use_real_facade(tmp_path):
    case = create_catalog_case(tmp_path)
    client = NteAnalysisCoreClient.from_executable(Path(os.environ.get("NTE_ALLOCATION_POSTPROCESS_TEST_EXE") or (
        ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")), case["dataset_id"])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
                          user_database_path=case["user_database_path"],
                          allocation_static_database_path=case["static_database_path"])
    def check_frozen_wire(request, scorer):
        from src.integrations.allocation.native_allocation_wire import freeze
        # 真实目录中的套装件数为整数键；协议冻结后必须可无损往返且不改源配置。
        frozen = freeze(request, scorer)
        assert frozen == json.loads(json.dumps(frozen, ensure_ascii=False, allow_nan=False))
        rates = request.roles_db[case["selected_role"]]["suit_permanent_crit_rates_by_name"]
        assert any(4 in values for values in rates.values())
    # 仅显式注入隔离候选组件；官方数据、角色投影、图纸、协议和目标均运行真实路径。
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client), \
            patch.object(client, "_run", wraps=client._run) as generate, \
            patch("src.solver.orchestrator.NTEPipelineOrchestrator.solve_blueprints",
                  side_effect=AssertionError("Python blueprint search called")):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], [case["selected_role"]],
            mode="direct_priority", tape_main_filters=case["core_main_filters"],
            crit_rate_baselines=case["crit_rate_baselines"], allocation_observer=check_frozen_wire)
    batches = [json.loads(call.args[0])["batch_kind"] for call in generate.call_args_list if call.args and call.args[0]]
    assert batches.count("damage_allocation_v1") == 1
    assert not set(batches) & {"allocation_v1", "allocation_blueprints_v1", "equipment_scoring_v1"}
    result = plans[case["selected_role"]]
    assert result["valid"]
    assert sum(len(row) for row in result["blueprint"]["board"]) == 25
    assert result["damage_optimization"]["status"] == "completed", result["damage_optimization"]
    assert result["damage_optimization"]["candidate_value"] >= result["damage_optimization"]["baseline_value"]
    assert result["damage_optimization"]["source_snapshot_id"] == case["snapshot_id"]
