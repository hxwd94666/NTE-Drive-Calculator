# 验证失败或截断后保存本次B0、冻结内容拒改和原有锁保护，不启动游戏或搜索组件。
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.features.allocation.results.damage_result_summary import damage_optimization_presentation
from src.services.allocation.execution.baseline_fallback import attach_baseline_fallbacks, baseline_save_available
from src.services.allocation.direct.direct_postprocess import optimization_payload
from src.services.allocation.results.plan_save import save_allocation_plans
from src.services.allocation.results.postprocess_facts import allocation_save_projection
from src.services.allocation.execution.baseline_fallback import verify_baseline_save
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.test_direct_postprocess_save import setup_case

NTE_TEST_TIER = "core"


def fallback_case(tmp_path, objective, status):
    path, snapshot, payload, kwargs = setup_case(tmp_path)
    row = kwargs["rows"][0]
    role = row["role_name"]
    metadata = payload["damage_optimization"]
    metadata.update(objective=objective, status=status, reason="axis_baseline_not_ready")
    gear = lambda uid, **extra: SimpleNamespace(uid=uid, role_scores={role: payload["assignment_scores"][uid]}, **extra)
    plan = {"valid": True, "score": row["score"], "blueprint": {"board": row["role_state"]["blueprint_layout"]},
            "assigned_tape": gear("nte-core-51-510", main_value=0.0),
            "assigned_set_drives": [gear("nte-module-41-410", shape_id="L_3_TL")], "assigned_extra_drives": [],
            "damage_optimization": deepcopy(metadata)}
    baseline = deepcopy(plan)
    frozen = SimpleNamespace(role_character_ids=((role, row["character_id"]),), snapshot_id=snapshot,
                             owners_sha256=metadata["owners_sha256"])
    attach_baseline_fallbacks({role: plan}, {role: baseline}, frozen,
                             dataset_id=metadata["dataset_id"], input_identity=metadata["input_identity"], database_path=path)
    projection = allocation_save_projection(role, plan, snapshot, row["character_id"])
    row.update({key: projection[key] for key in ("role_state", "score")})
    payload.update({key: projection[key] for key in ("assignment_scores", "tape_main_values")})
    payload.update(optimization_payload(plan))
    row["baseline_preview_source"] = plan.get("baseline_preview_source")
    return path, plan, baseline, frozen, kwargs


@pytest.mark.parametrize("objective", ["standard_direct_v1", "frozen_axis_expected_v1"])
@pytest.mark.parametrize("status", ["not_ready", "budget_limited", "failed"])
def test_available_baseline_saves_without_rerun_or_axis_model(tmp_path, objective, status):
    path, plan, _, _, kwargs = fallback_case(tmp_path, objective, status)
    assert baseline_save_available(plan)
    assert save_allocation_plans(**kwargs) == 1
    with UserDataDao(path) as dao:
        saved = dao.list_loadout_slots(1003)[0]["current_plan"]
    meta = saved["payload"]["damage_optimization"]
    assert meta["status"] == "baseline_only" and meta["fallback_status"] == status
    assert "candidate_value" not in meta and "delta" not in meta
    assert saved["score"] == 65.0
    presentation = damage_optimization_presentation(meta)
    assert "基础配装" in presentation.headline and "无需重新计算" in presentation.details


@pytest.mark.parametrize("change", ["score", "equipment", "metadata", "receipt"])
def test_available_baseline_rejects_changed_frozen_content(tmp_path, change):
    path, _, _, _, kwargs = fallback_case(tmp_path, "frozen_axis_expected_v1", "not_ready")
    row = kwargs["rows"][0]
    if change == "score":
        row["score"] += 1
    elif change == "equipment":
        row["role_state"]["equipped_tape"]["uid"] = "nte-core-999-999"
    elif change == "metadata":
        row["payload"]["damage_optimization"]["delta"] = 999
    else:
        row.pop("baseline_preview_source")
    with pytest.raises(RuntimeError, match="冻结预览"):
        save_allocation_plans(**kwargs)
    with UserDataDao(path) as dao:
        assert dao.list_loadout_slots(1003) == []


def test_failed_changed_plan_cannot_be_certified_as_baseline(tmp_path):
    _, plan, baseline, frozen, kwargs = fallback_case(tmp_path, "standard_direct_v1", "not_ready")
    plan.pop("baseline_preview_source")
    plan["score"] += 1
    plan["damage_optimization"]["status"] = "not_ready"
    attach_baseline_fallbacks({"早雾": plan}, {"早雾": baseline}, frozen,
        dataset_id=kwargs["static_release"].dataset_id, input_identity={"request_id": "same"}, database_path=kwargs["database_path"])
    assert not baseline_save_available(plan)


def test_baseline_receipt_cannot_cross_account_path(tmp_path):
    path, _, _, _, kwargs = fallback_case(tmp_path, "frozen_axis_expected_v1", "not_ready")
    with UserDataDao(path) as dao:
        with pytest.raises(RuntimeError, match="冻结预览"):
            verify_baseline_save(dao, kwargs["rows"], dataset_id=kwargs["static_release"].dataset_id,
                                 database_path=tmp_path / "another-account.sqlite3")


def test_partial_blueprint_search_never_displays_full_coverage():
    value = {"objective": "standard_direct_v1", "status": "completed", "baseline_value": 100,
             "candidate_value": 110, "termination": "local_optimum", "neighborhood_complete": True,
             "blueprint_usage": {"complete": False, "termination": "blueprint_budget"}}
    shown = damage_optimization_presentation(value)
    assert shown.improved and "搜索未完成" in shown.headline
    assert "图纸枚举" in shown.details and "可保存" in shown.details


def test_seasonal_baseline_reason_identifies_missing_recipient_evidence():
    value = {"objective": "frozen_axis_expected_v1", "status": "baseline_only", "result_kind": "baseline",
             "reason": "outer_realm_recipient_evidence_missing"}
    shown = damage_optimization_presentation(value)
    assert "赛季环合" in shown.note and "受益角色" in shown.note
    assert "可保存" in shown.headline and not shown.improved


def test_baseline_fallback_still_protects_newly_locked_owner(tmp_path):
    path, _, _, _, kwargs = fallback_case(tmp_path, "frozen_axis_expected_v1", "not_ready")
    with UserDataDao(path) as dao:
        old = dao.save_loadout_plan(name="公开锁定夹具", character_id=1004,
            assignments=[{"uid_slot": 41, "uid_serial": 410, "kind": "module", "ordinal": 0,
                          "target_row": 2, "target_column": 2, "rotation": 0}],
            source_snapshot_id=kwargs["rows"][0]["snapshot_id"], is_active=True, status="ready",
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "旧角色"})
        dao.set_allocation_lock(old, True)
    with pytest.raises(RuntimeError, match="锁定"):
        save_allocation_plans(**kwargs)
    with UserDataDao(path) as dao:
        assert dao.list_loadout_slots(1003) == []
        assert dao.list_loadout_slots(1004)[0]["current_plan_id"] == old
