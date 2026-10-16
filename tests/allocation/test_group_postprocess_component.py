# 用正式Release和完整公共目录库存验证双成员直伤组一次作业及整组冻结保存。
from __future__ import annotations

from copy import deepcopy
import os
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.allocation.test_axis_postprocess_component import axis_facade_case, ROOT
from src.app.facade import NTEAppFacade
from src.integrations.analysis_core_release import create_bundled_analysis_client as _bundled_client
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.integrations.static_catalog_release import static_catalog_read_unit
from src.services.allocation.direct_postprocess import optimization_payload
from src.services.allocation.locks import build_allocation_lock_snapshot
from src.services.allocation.plan_save import save_allocation_plans
from src.services.allocation.postprocess_facts import allocation_save_projection
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.official_role.page import load_official_role_detail


NTE_TEST_TIER = "core"


def create_bundled_analysis_client(*, static_database_path):
    executable = os.environ.get("NTE_BATTLE_AXIS_TEST_EXE")
    if not executable:
        return _bundled_client(static_database_path=static_database_path)
    with static_catalog_read_unit(static_database_path) as unit:
        return NteAnalysisCoreClient.from_executable(Path(executable), unit.release.dataset_id)



def test_formal_two_member_direct_group_is_one_job_and_atomic_score_preserving_save(tmp_path):
    case = axis_facade_case(tmp_path, second=True)
    client = create_bundled_analysis_client(static_database_path=case["dependencies"].static_database_path)
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client), \
            patch.object(client, "_run", wraps=client._run) as job:
        plans, _ = facade.execute_allocation_inventory(case["inventory"], case["selected_roles"], mode="direct_priority",
            custom_sets={role: case["suit_name"] for role in case["selected_roles"]},
            set_effect_modes={role: "none" for role in case["selected_roles"]},
            tape_main_filters={role: ["攻击力%"] for role in case["selected_roles"]},
            crit_rate_baselines=case["crit_baselines"], priority_groups=[case["selected_roles"]])
    batches = [json.loads(call.args[0])["batch_kind"] for call in job.call_args_list if call.args and call.args[0]]
    assert batches.count("damage_allocation_v1") == 1
    assert not set(batches) & {"allocation_v1", "allocation_blueprints_v1", "equipment_scoring_v1", "allocation_group_postprocess_v1"}
    values = [plan["damage_optimization"] for plan in plans.values()]
    assert all(value["status"] == "completed" for value in values), values
    assert values[0]["input_identity"] == values[1]["input_identity"]
    assert all(member["candidate_value"] >= member["baseline_value"] for member in values[0]["group_members"])
    assert all(value["group_objective_value"] >= 0 for value in values)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=case["source_snapshot_id"])
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit:
        release = unit.release
    rows = []
    for role, plan in plans.items():
        projected = allocation_save_projection(role, plan, case["source_snapshot_id"], plan["damage_optimization"]["character_id"])
        rows.append({**{key: value for key, value in projected.items() if key not in {"assignment_scores", "tape_main_values"}},
            "slot_id": None, "name": "公开同级组方案", "direct_group_preview_source": plan["direct_group_preview_source"],
            "payload": {"source_role_name": role, "assignment_scores": projected["assignment_scores"],
                        "tape_main_values": projected["tape_main_values"], **optimization_payload(plan)}})
    assert save_allocation_plans(database_path=case["dependencies"].user_database_path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks,
        rows=rows, checkpoint=lambda: None, progress=lambda _: None) == 2
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        for row in rows:
            saved = dao.list_loadout_slots(row["character_id"])[0]["current_plan"]
            assert saved["payload"]["assignment_scores"] == row["payload"]["assignment_scores"]
            assert saved["payload"]["damage_optimization"] == row["payload"]["damage_optimization"]


def test_empty_default_groups_normalize_only_new_job_copy_and_preserve_b0_trace(tmp_path):
    case = axis_facade_case(tmp_path)
    client = create_bundled_analysis_client(static_database_path=case["dependencies"].static_database_path)
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    options = dict(custom_sets={"灵可": case["suit_name"]}, set_effect_modes={"灵可": "none"},
        tape_main_filters={"灵可": ["攻击力%"]}, crit_rate_baselines=case["crit_baselines"])
    traces = []
    from src.integrations.native_allocation_wire import freeze
    def trace(request, scorer):
        traces.append(deepcopy(freeze(request, scorer)["request"]))
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client):
        normal, _ = facade.execute_allocation_inventory(case["inventory"], ["灵可"], mode="role_priority", allocation_observer=trace, **options)
        with patch.object(client, "_run", wraps=client._run) as job:
            facade.execute_allocation_inventory(case["inventory"], ["灵可"], mode="direct_priority", allocation_observer=trace, **options)
    assert traces[0] == traces[1] and traces[1]["groups"] == []
    call = next(call for call in job.call_args_list if call.args and call.args[0]
                and json.loads(call.args[0])["batch_kind"] == "damage_allocation_v1")
    inner = json.loads(call.args[0])["request"]["postprocess_request"]
    assert inner["allocation_request"]["groups"] == [[0]]
    assert inner["baseline_plans"] == {}  # B0 只在 Rust 作业内生成，不由 Python 预计算。


def test_unready_group_does_not_protect_its_unlocked_old_slot(tmp_path):
    case = axis_facade_case(tmp_path, second=True)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.save_loadout_plan(name="未就绪成员原槽位", character_id=1003, source_snapshot_id=case["source_snapshot_id"],
            status="ready", is_active=True, payload={}, assignments=[{"kind": "core", "uid_slot": 4, "uid_serial": 100, "ordinal": 0}])
    def available(path, character_id, **kwargs):
        if character_id == 1003:
            raise RuntimeError("explicit model evidence gap")
        return load_official_role_detail(path, character_id, **kwargs)
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    with patch("src.services.allocation.direct_group_postprocess.load_official_role_detail", side_effect=available):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], case["selected_roles"], mode="direct_priority",
            custom_sets={role: case["suit_name"] for role in case["selected_roles"]},
            set_effect_modes={role: "none" for role in case["selected_roles"]},
            tape_main_filters={role: ["攻击力%"] for role in case["selected_roles"]}, crit_rate_baselines=case["crit_baselines"],
            priority_groups=[["灵可"], ["早雾"]])
    assert plans["灵可"]["assigned_tape"].uid == "nte-core-4-100"
    assert plans["灵可"]["damage_optimization"]["status"] == "completed"
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        assert dao.list_loadout_slots(1003)[0]["current_plan"]["assignments"][0]["uid_serial"] == 100


def test_budget_cut_does_not_protect_unlocked_old_slot_in_next_calculation(tmp_path):
    case = axis_facade_case(tmp_path, second=True)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.save_loadout_plan(name="预算截断成员原槽位", character_id=1003, source_snapshot_id=case["source_snapshot_id"],
            status="ready", is_active=True, payload={}, assignments=[{"kind": "core", "uid_slot": 4, "uid_serial": 100, "ordinal": 0}])
    client = create_bundled_analysis_client(static_database_path=case["dependencies"].static_database_path)
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    options = dict(mode="direct_priority", custom_sets={role: case["suit_name"] for role in case["selected_roles"]},
        set_effect_modes={role: "none" for role in case["selected_roles"]},
        tape_main_filters={role: ["攻击力%"] for role in case["selected_roles"]}, crit_rate_baselines=case["crit_baselines"])
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client):
        cut, _ = facade.execute_allocation_inventory(case["inventory"], case["selected_roles"],
            priority_groups=[["灵可"], ["早雾"]], postprocess_budget={"max_evaluations": 1, "max_rounds": 1}, **options)
        assert all(plan["damage_optimization"]["status"] != "completed" for plan in cut.values())
        assert all("direct_group_preview_source" not in plan for plan in cut.values())
        plans, _ = facade.execute_allocation_inventory(case["inventory"], ["灵可"], priority_groups=[["灵可"]],
            postprocess_budget={"max_evaluations": 128, "max_rounds": 3}, **options)
    assert plans["灵可"]["assigned_tape"].uid == "nte-core-4-100"
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        assert dao.list_loadout_slots(1003)[0]["current_plan"]["assignments"][0]["uid_serial"] == 100


@pytest.mark.parametrize("mode,joint", [("direct_priority", False), ("axis_priority", False), ("axis_priority", True)])
@pytest.mark.parametrize("locked", [False, True])
def test_old_loadout_lock_controls_borrowing_and_atomic_save(tmp_path, mode, joint, locked):
    case = axis_facade_case(tmp_path, joint=joint)
    path = case["dependencies"].user_database_path
    with UserDataDao(path) as dao:
        old = dao.save_loadout_plan(name="借出方公开夹具", character_id=1003,
            source_snapshot_id=case["source_snapshot_id"], status="ready", is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "早雾"},
            assignments=[{"kind": "core", "uid_slot": 4, "uid_serial": uid, "ordinal": i} for i, uid in enumerate((100, 101))])
        if locked:
            dao.set_allocation_lock(old, True)
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=case["source_snapshot_id"])
    client = create_bundled_analysis_client(static_database_path=case["dependencies"].static_database_path)
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config", user_database_path=path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], ["灵可"], mode=mode,
            custom_sets={"灵可": case["suit_name"]}, set_effect_modes={"灵可": "none"},
            tape_main_filters={"灵可": ["攻击力%"]}, crit_rate_baselines=case["crit_baselines"],
            locked_uids=locks.reserved_uids, axis_selection=case["selection"] if mode == "axis_priority" else None)
    plan = plans["灵可"]
    if locked:
        assert not plan.get("valid") or plan.get("assigned_tape") is None
        with UserDataDao(path) as dao:
            assert dao.list_loadout_slots(1003)[0]["current_plan_id"] == old
        return
    assert plan["damage_optimization"]["status"] == "completed", plan["damage_optimization"]
    borrowed = plan["assigned_tape"].uid
    assert borrowed in {"nte-core-4-100", "nte-core-4-101"}
    with UserDataDao(path) as dao:
        assert dao.list_loadout_slots(1003)[0]["current_plan_id"] == old  # 预览不提前借出。
    projected = allocation_save_projection("灵可", plan, case["source_snapshot_id"], 1072)
    row = {**{key: value for key, value in projected.items() if key not in {"assignment_scores", "tape_main_values"}},
        "slot_id": None, "name": "借用后优化方案",
        "payload": {"source_role_name": "灵可", "assignment_scores": projected["assignment_scores"],
                    "tape_main_values": projected["tape_main_values"], **optimization_payload(plan)}}
    # 保存投影保留冻结策略事实，不能丢掉新选择规则而破坏预览摘要。
    assert row["payload"]["damage_optimization"] == plan["damage_optimization"]
    preview_key = "axis_preview_source" if mode == "axis_priority" else "direct_group_preview_source"
    row[preview_key] = plan[preview_key]
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit:
        release = unit.release
    assert save_allocation_plans(database_path=path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks,
        rows=[row], checkpoint=lambda: None, progress=lambda _: None, axis_analysis_client=client) == 1
    with UserDataDao(path) as dao:
        borrowed_serial = int(borrowed.rsplit("-", 1)[1])
        owners = [r for r in dao.list_current_loadout_equipment_owners() if r["kind"] == "core" and r["uid_serial"] == borrowed_serial]
        assert {r["character_id"] for r in owners} == {1072}
        assert dao.list_loadout_slots(1003)[0]["current_plan_id"] != old
        assert len(dao.get_loadout_plan(old)["assignments"]) == 2
