# 用真实发行图鉴、临时完整库存和冻结战报验证门面轴优化，不读取个人账号。
from __future__ import annotations

from copy import deepcopy
import os
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case, ROOT
from src.app.facade import NTEAppFacade
from tests.allocation.axis_team_fixture import attach_native_team, attach_fixed_builds
from src.domain.equipment.equipment_normalizer import calculate_drive_main_stats
from src.integrations.nte_analysis_core import NteAnalysisCoreClient, NativeAnalysisError
from src.integrations.static_catalog_release import static_catalog_read_unit
from src.services.allocation.axis_selection import AxisAllocationSelectionService
from src.services.allocation.inventory import SqliteAllocationInventory
from src.services.allocation.locks import build_allocation_lock_snapshot
from src.services.allocation.plan_save import save_allocation_plans, legacy_plan_tape_main_values
from src.services.allocation.postprocess_facts import allocation_role_state_from_plan
from src.services.allocation.direct_postprocess import optimization_payload
from src.services.official_role.attributes import calculate_official_role_combat_stat_components
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"


def axis_facade_case(directory, *, conditional=False, joint=False, second=False, game_locked=False):
    case = create_axis_catalog_case(directory, core_suit_id=None if conditional else "Suit1", second_character=second)
    attach_native_team(case)
    # 正常图纸生成会最大化角色额外形状，模板仅七件不足；补齐真实目录形状的临时完整库存，A仍原冻结。
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit, UserDataDao(case["dependencies"].user_database_path) as dao:
        _, originals = dao.export_inventory_snapshot(case["source_snapshot_id"])
        native = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]}
                  for row in originals]
        templates = [row for row in unit.dao.list_equipment_items() if row["kind"] == "module" and row["quality"] == "ORANGE"]
        for template in templates:
            for copy in range(8):
                intrinsic = calculate_drive_main_stats(template["grid_count"], "Gold")
                native.append({"uid": {"slot": 3, "serial": 10_000 + len(native)}, "kind": "module",
                    "item_id": template["item_id"], "suit_id": None, "geometry": template["geometry_id"],
                    "grid": template["grid_count"], "quality": "orange", "level": 0, "max_level": template["max_level"],
                    "locked": False, "discarded": False, "equipped": False, "names": {}, "suit_names": {},
                    "main_stats": [{"property_id": key, "value": value, "percent": False, "names": {}}
                                   for key, value in zip(("AtkAdd", "HPMaxAdd"), intrinsic.values())], "sub_stats": []})
        if second:
            spare_core = deepcopy(native[0])
            spare_core["uid"] = {"slot": 4, "serial": 102}
            spare_core["main_stats"][0]["value"] = .15
            native.append(spare_core)
        if game_locked:
            for row in native:
                row["locked"] = True
        case["source_snapshot_id"] = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 2, "observed_at_unix_ms": 1_787_000_000_001,
            "item_count": len(native), "items": native}})
    attach_fixed_builds(case, game_locked=game_locked)
    selection_service = AxisAllocationSelectionService(dependencies=case["dependencies"], context_is_current=lambda _: True)
    case["selected_roles"] = ["灵可", "早雾"] if second else ["灵可"]
    choices = selection_service.choices(case["selected_roles"])
    case["selection"] = selection_service.select(choices, battle_record_id=case["battle_record_id"], selected_role="__all_groups__" if joint else "灵可",
                                               detail_scope="all", anchor_mode="original")
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit, UserDataDao(case["dependencies"].user_database_path) as dao:
        case["inventory"] = list(SqliteAllocationInventory(dao, unit.dao).build(case["source_snapshot_id"]).items)
        suite = next(row for row in unit.dao.list_suits() if row["suit_id"] == ("Suit2" if conditional else "Suit1"))
        case["suit_name"] = suite["name_zh"].strip("「」")
        detail = load_official_role_detail(case["dependencies"].user_database_path, 1072, include_inventory_contexts=False,
            include_replacement_candidates=False, static_database_path=unit.release.database_path, static_read_unit=unit)
        fixed = {row.key: row.value for row in calculate_official_role_combat_stat_components(detail, [])}
        case["fixed_crit"] = round((fixed["CritBase"] - .05) * 100, 4)
        case["crit_baselines"] = {"灵可": case["fixed_crit"]}
        if second:
            other = load_official_role_detail(case["dependencies"].user_database_path, 1003, include_inventory_contexts=False,
                include_replacement_candidates=False, static_database_path=unit.release.database_path, static_read_unit=unit)
            other_fixed = {row.key: row.value for row in calculate_official_role_combat_stat_components(other, [])}
            case["crit_baselines"]["早雾"] = round((other_fixed["CritBase"] - .05) * 100, 4)
    return case


def run_axis_facade(case, *, observer=None, budget=None, groups=None):
    executable = Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")
    client = NteAnalysisCoreClient.from_executable(executable, case["dataset_id"])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client):
        plans, _ = facade.execute_allocation_inventory(case["inventory"], case["selected_roles"], mode="axis_priority",
            custom_sets={role: case["suit_name"] for role in case["selected_roles"]},
            set_effect_modes={role: "none" for role in case["selected_roles"]},
            tape_main_filters={role: ["攻击力%"] for role in case["selected_roles"]}, crit_rate_baselines=case["crit_baselines"],
            priority_groups=[case["selected_roles"]] if groups is None else groups, postprocess_budget=budget,
            axis_selection=case["selection"], allocation_observer=observer)
    return plans, client


@pytest.mark.parametrize("mode,joint", [("direct_priority", False), ("axis_priority", False), ("axis_priority", True)])
def test_game_discard_lock_does_not_protect_allocation_equipment(tmp_path, mode, joint):
    # 游戏锁只防误弃置；原始库存标志保持真实，后处理与角色优先共用可用装备。
    case = axis_facade_case(tmp_path, joint=joint, game_locked=True)
    client = NteAnalysisCoreClient.from_executable(
        Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe"),
        case["dataset_id"])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=case["dependencies"].user_database_path,
        allocation_static_database_path=case["dependencies"].static_database_path)
    options = dict(custom_sets={"灵可": case["suit_name"]}, set_effect_modes={"灵可": "none"},
        tape_main_filters={"灵可": ["攻击力%"]}, crit_rate_baselines=case["crit_baselines"], priority_groups=[["灵可"]])
    with patch("src.app.facade.create_bundled_analysis_client", return_value=client):
        baseline, _ = facade.execute_allocation_inventory(case["inventory"], ["灵可"], mode="role_priority", **options)
        plans, _ = facade.execute_allocation_inventory(case["inventory"], ["灵可"], mode=mode,
            axis_selection=case["selection"] if mode == "axis_priority" else None, **options)
    assert baseline["灵可"]["valid"] and baseline["灵可"]["assigned_tape"] is not None
    result = plans["灵可"]["damage_optimization"]
    assert result["status"] == "completed", result
    assert result["candidate_value"] >= result["baseline_value"]
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        _, actual = dao.export_inventory_snapshot(case["source_snapshot_id"])
        assert actual and all(item["locked"] for item in actual)


def test_actual_axis_facade_keeps_base_score_and_saves_distinct_frozen_objective(tmp_path):
    case = axis_facade_case(tmp_path)
    expected_reserved = {uid for cid, uids in case["fixed_equipment_by_character"].items() if cid != 1072 for uid in uids}
    def observe(request, scorer):
        assert not expected_reserved & {item.uid for item in request.inventory}
    captured = []
    original_run = NteAnalysisCoreClient._run
    def capture(client, payload=None, **kwargs):
        if payload and json.loads(payload).get("batch_kind") == "damage_allocation_v1":
            captured.append(json.loads(payload))
        return original_run(client, payload, **kwargs)
    with patch.object(NteAnalysisCoreClient, "_run", capture):
        plans, client = run_axis_facade(case, observer=observe)
    assert len(captured) == 1
    plan = plans["灵可"]
    result = plan["damage_optimization"]
    assert plan["valid"] and result["status"] == "completed", result
    assert {uid for m in result["fixed_team_members"] for uid in m["equipment_uids"]} == expected_reserved
    assert len(result["fixed_team_build_sha256"]) == 64
    assert result["candidate_value"] > result["baseline_value"]
    assert result["model_id"] == "frozen_axis_expected_v1" and result["equipment_value_mode"] == "actual_level"
    assert plan["score"] == pytest.approx(sum(item.role_scores["灵可"] for item in [plan["assigned_tape"], *plan["assigned_extra_drives"]]))
    assert result["source_snapshot_id"] == case["source_snapshot_id"]
    assert result["component_executable_sha256"] == client.component_provenance["component_executable_sha256"]
    assert "shared_build_dependencies" in result
    assert result["source_input_digest"] and result["scope"]["event_domain"] == "saved_frozen_observed_events"
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=case["source_snapshot_id"])
        dao.replace_application_setting_copy("ui", {"unrelated_background": "public-fixture"})
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit:
        release = unit.release
    row = dict(role_name="灵可", character_id=1072, snapshot_id=case["source_snapshot_id"], slot_id=None,
        name="公开轴优化方案", score=plan["score"], role_state=allocation_role_state_from_plan(plan),
        axis_preview_source=plan["axis_preview_source"], payload={"source_role_name": "灵可",
            "assignment_scores": {item.uid: item.role_scores["灵可"] for item in [plan["assigned_tape"], *plan["assigned_extra_drives"]]},
            "tape_main_values": legacy_plan_tape_main_values(plan), **optimization_payload(plan)})
    assert save_allocation_plans(database_path=case["dependencies"].user_database_path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks, rows=[row],
        checkpoint=lambda: None, progress=lambda _: None, axis_analysis_client=client) == 1
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        saved = dao.list_loadout_slots(1072)[0]["current_plan"]
    assert saved["payload"]["damage_optimization"] == row["payload"]["damage_optimization"]
    assert saved["payload"]["assignment_scores"] == row["payload"]["assignment_scores"]
    # 直接调用原生入口仍须核对证明和预留；不以Python门禁代替Rust入口约束。
    for field in ("reserved_uids", "fixed_build_sha256"):
        forged = deepcopy(captured[0])
        post = forged["request"]["postprocess_request"]
        if field == "reserved_uids":
            post[field] = []
        else:
            post["team_preflight"][field] = "0" * 64
        with patch.object(client, "_run", wraps=client._run) as dispatch:
            with pytest.raises(NativeAnalysisError):
                client._run(json.dumps(forged, ensure_ascii=False).encode("utf8"))
            dispatch.assert_called_once()


def test_actual_snapshot_rejects_forged_same_area_shape_and_core_main_label(tmp_path):
    case = axis_facade_case(tmp_path)
    original = deepcopy(case["inventory"])
    module = next(row for row in case["inventory"] if row.get("shape_id") == "H_2")
    module["shape_id"] = "V_2"
    plans, _ = run_axis_facade(case)
    assert plans["灵可"]["damage_optimization"]["status"] == "not_ready"
    case["inventory"] = original
    for row in case["inventory"]:
        if row["item_type"] == "tape":
            row["main_stats"] = "生命值%"
    plans, _ = run_axis_facade(case)
    assert plans["灵可"]["damage_optimization"]["status"] == "not_ready"


def test_actual_two_member_axis_group_is_one_shared_fteam_job(tmp_path):
    case = axis_facade_case(tmp_path, joint=True, second=True)
    assert case["selection"].selected_character_id is None and case["selection"].optimize_groups
    plans, _client = run_axis_facade(case, budget={"max_evaluations": 512, "max_rounds": 100})
    values = [plan["damage_optimization"] for plan in plans.values()]
    assert all(value["status"] == "completed" for value in values), values
    assert all(value["shared_value"] is True and value["team_event_scope"] == "all_frozen_axis_events_v1" for value in values)
    assert all(value["selection_policy"] == "whole_team_best_move_v1" for value in values)
    assert values[0]["input_identity"] == values[1]["input_identity"]
    assert values[0]["candidate_value"] == values[1]["candidate_value"] > values[0]["baseline_value"]
    assert sorted(values[0]["scope"]["selected_character_ids"]) == [1003, 1072]
    assert all(set(member) == {"role", "character_id"} for member in values[0]["group_members"])
    assert values[0]["frozen_identity"] == values[1]["frozen_identity"]
    dependencies = values[0]["shared_build_dependencies"]
    assert dependencies == values[1]["shared_build_dependencies"]
    assert dependencies["source_snapshot_id"] == case["source_snapshot_id"]
    assert dependencies["input_identity"] == values[0]["input_identity"]
    assert dependencies["source_input_digest"] == values[0]["source_input_digest"]
    assert {(build["role"], build["character_id"]) for build in dependencies["roles"]} == {("灵可", 1072), ("早雾", 1003)}
    assert all(build["basis"] == "accepted" and build["role_state"] == allocation_role_state_from_plan(plans[build["role"]])
               for build in dependencies["roles"])


@pytest.mark.parametrize("joint", [False, True])
def test_nonparticipant_unlocked_old_slot_does_not_block_axis_optimization(tmp_path, joint):
    case = axis_facade_case(tmp_path, joint=joint)
    # 联合作业的所选角色都应参与轴目标；未参战借出方留在旧槽位，而非伪装为优化成员。
    case["selected_roles"] = ["灵可"]
    service = AxisAllocationSelectionService(dependencies=case["dependencies"], context_is_current=lambda _: True)
    case["selection"] = service.select(service.choices(case["selected_roles"]), battle_record_id=case["battle_record_id"],
        selected_role="__all_groups__" if joint else "灵可", detail_scope="all", anchor_mode="original")
    assert case["selection"].participating_character_ids == (1072,)
    # 借出方仅有未锁定旧槽位，不伪装成本次优化成员；两条搜索入口均允许借用更优101。
    borrowed_serial = 101
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.save_loadout_plan(name="非优化成员原槽位", character_id=1003, source_snapshot_id=case["source_snapshot_id"],
            status="ready", is_active=True, payload={}, assignments=[{"kind": "core", "uid_slot": 4, "uid_serial": borrowed_serial, "ordinal": 0}])
    plans, _ = run_axis_facade(case, groups=[[role] for role in case["selected_roles"]])
    assert plans["灵可"]["assigned_tape"].uid == f"nte-core-4-{borrowed_serial}"
    assert plans["灵可"]["damage_optimization"]["status"] == "completed"
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        assert dao.list_loadout_slots(1003)[0]["current_plan"]["assignments"][0]["uid_serial"] == borrowed_serial


def test_first_axis_group_scan_cut_keeps_b0_and_is_not_saveable_optimization(tmp_path):
    case = axis_facade_case(tmp_path, joint=True)
    plans, _ = run_axis_facade(case, budget={"max_evaluations": 1, "max_rounds": 100})
    value = plans["灵可"]["damage_optimization"]
    assert value["status"] == "budget_limited" and value["termination"] == "evaluation_budget"
    assert value["candidate_value"] == value["baseline_value"] and not value["neighborhood_complete"]
    assert "axis_preview_source" not in plans["灵可"]
