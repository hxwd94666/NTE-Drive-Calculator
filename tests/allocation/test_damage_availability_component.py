# 用隔离合成账号验证正式Rust作业截断后直接保存B0，保存不重新派发计算。
import os
from pathlib import Path
from unittest.mock import patch

from src.app.facade import NTEAppFacade
from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.execution.baseline_fallback import baseline_save_available
from src.services.allocation.direct.direct_postprocess import optimization_payload
from src.services.allocation.inputs.locks import build_allocation_lock_snapshot
from src.services.allocation.results.plan_save import save_allocation_plans
from src.services.allocation.results.postprocess_facts import allocation_save_projection
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.test_axis_postprocess_component import axis_facade_case, ROOT

NTE_TEST_TIER = "core"


def test_formal_budget_limited_job_saves_frozen_baseline_without_second_search(tmp_path):
    case = axis_facade_case(tmp_path)
    database = case["dependencies"].user_database_path
    static = case["dependencies"].static_database_path
    executable = Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")
    client = NteAnalysisCoreClient.from_executable(executable, case["dataset_id"])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
                          user_database_path=database, allocation_static_database_path=static)
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], case["selected_roles"], mode="direct_priority",
            custom_sets={"灵可": case["suit_name"]}, set_effect_modes={"灵可": "none"},
            tape_main_filters={"灵可": ["攻击力%"]}, crit_rate_baselines=case["crit_baselines"],
            postprocess_budget={"max_evaluations": 1, "max_rounds": 1})
    plan = plans["灵可"]
    assert baseline_save_available(plan)
    meta = plan["damage_optimization"]
    assert meta["status"] == "baseline_only" and "candidate_value" not in meta
    projected = allocation_save_projection("灵可", plan, case["source_snapshot_id"], meta["character_id"])
    row = {**{key: value for key, value in projected.items() if key not in {"assignment_scores", "tape_main_values"}},
           "slot_id": None, "name": "公开截断基础方案", "baseline_preview_source": plan["baseline_preview_source"],
           "payload": {"source_role_name": "灵可", "assignment_scores": projected["assignment_scores"],
                       "tape_main_values": projected["tape_main_values"], **optimization_payload(plan)}}
    with UserDataDao(database) as dao:
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=case["source_snapshot_id"])
    with static_catalog_read_unit(static) as unit:
        release = unit.release
    with patch.object(client, "_run", side_effect=AssertionError("保存不能重跑搜索")):
        assert save_allocation_plans(database_path=database, static_database_path=static,
            static_identity=(release.database_path, release.dataset_id), static_release=release,
            lock_snapshot=locks, rows=[row], checkpoint=lambda: None, progress=lambda _: None) == 1
    with UserDataDao(database) as dao:
        saved = dao.list_loadout_slots(meta["character_id"])[0]["current_plan"]
    assert saved["score"] == plan["score"] and saved["payload"]["damage_optimization"] == meta
