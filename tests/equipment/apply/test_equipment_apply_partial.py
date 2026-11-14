# 验证极速装配缺件子集的冻结、真实派发、后验确认及批量不中断边界。
from copy import deepcopy
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from src.services.equipment.assembly.apply import EquipmentApplyError, EquipmentApplyService
from src.services.equipment.assembly.bulk_apply import BulkEquipmentApplyService
from src.services.equipment.assembly.bulk_postcheck import verify_complete_snapshot
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.equipment.apply.test_equipment_apply_service import CHARACTER_UID
from tests.equipment.apply.test_equipment_apply_service import FakeSyncService
from tests.equipment.apply.test_equipment_apply_service import item
from tests.equipment.apply.test_equipment_apply_service import snapshot


def assignment(serial, kind="module"):
    return {
        "uid_slot": serial, "uid_serial": serial, "kind": kind,
        "target_row": 2 if kind == "module" else None,
        "target_column": 3 if kind == "module" else None, "rotation": 0,
    }


def virtual():
    return {**assignment(333), "uid_slot": 0, "virtual": True}


@pytest.fixture
def session(tmp_path, monkeypatch):
    path = tmp_path / "user.sqlite3"
    dao = UserDataDao(path, account_id="partial-apply-test")
    dao.import_inventory_snapshot(snapshot(1, [
        item(11, "module", equipped=True), item(22, "core"), item(99, "module"), item(88, "core"),
    ]))
    current = dao.import_inventory_snapshot(snapshot(2, [item(11, "module", equipped=True), item(22, "core")]))
    sync = FakeSyncService(dao, current)
    sync.equip_core = Mock(return_value={"status": "dispatched"})
    monkeypatch.setattr("src.services.equipment.assembly.apply.time.sleep", lambda _delay: None)
    service = EquipmentApplyService(dao, sync, operation_guard=lambda _: None)

    def save(assignments, character_id=1003):
        return dao.save_loadout_plan(
            name="部分装配测试", character_id=character_id, source_snapshot_id=1,
            status="incomplete", is_active=True, assignments=assignments,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": str(character_id)},
        )

    yield path, dao, sync, service, save
    dao.close()


def test_mixed_plan_sends_only_available_equipment_and_does_not_rewrite_saved_plan(session):
    _, dao, sync, service, save = session
    plan_id = save([assignment(11), virtual(), assignment(99), assignment(22, "core")])
    original = deepcopy(dao.get_loadout_plan(plan_id))
    result = service.apply_plan(plan_id, verify_after_dispatch=False, reset_before_apply=True)
    assert [p["equipment"]["serial"] for p in sync.params["placements"]] == [11]
    assert sync.params["core"]["serial"] == 22
    assert len(result.skipped_assignments) == 2
    assert not result.verified
    assert dao.get_loadout_plan(plan_id) == original
    assert service.plan_equipment_uid_pairs(plan_id) == frozenset({(11, 11), (22, 22)})


def test_missing_core_still_dispatches_real_modules(session):
    _, _, sync, service, save = session
    plan_id = save([assignment(11), assignment(88, "core")])
    result = service.apply_plan(plan_id, verify_after_dispatch=False, reset_before_apply=True)
    assert sync.params is None
    assert [row["equipment"]["serial"] for row in sync.module_calls] == [11]
    assert result.skipped_assignments == ({"kind": "core", "reason": "missing"},)


def test_only_remaining_core_uses_single_item_protocol(session):
    _, _, sync, service, save = session
    plan_id = save([virtual(), assignment(22, "core")])
    result = service.apply_plan(plan_id, verify_after_dispatch=False, reset_before_apply=True)
    sync.equip_core.assert_called_once_with(character=CHARACTER_UID, equipment={"slot": 22, "serial": 22})
    assert sync.params is None
    assert sync.module_calls == []
    assert not result.skipped
    assert len(result.skipped_assignments) == 1


def test_only_remaining_core_can_be_confirmed_by_new_snapshot(session):
    _, _, sync, service, save = session
    plan_id = save([virtual(), assignment(22, "core")])
    sync.equip_core.side_effect = lambda **_: sync._emit_snapshot([
        item(11, "module"), item(22, "core", equipped=True),
    ])
    result = service.apply_plan(plan_id, reset_before_apply=True)
    assert result.verified
    assert result.after_snapshot_id > result.before_snapshot_id
    assert result.skipped_assignments == ({"kind": "module", "reason": "virtual"},)


def test_no_available_equipment_does_not_resolve_identity_or_clear_role(session):
    _, _, sync, service, save = session
    plan_id = save([virtual(), assignment(99), assignment(88, "core")])
    service.resolve_character_uid = Mock(side_effect=AssertionError("空方案不需要角色实例"))
    result = service.apply_plan(plan_id, verify_after_dispatch=False, reset_before_apply=True)
    assert result.skipped and not result.verified
    assert sync.unmount_calls == sync.module_calls == []
    assert sync.params is None
    sync.equip_core.assert_not_called()
    assert service.verify_plan_in_snapshot(
        plan_id, stable_snapshot_id=2, character_uid=CHARACTER_UID,
    ) == "方案没有可用装备，本次未执行装配"


def test_later_arriving_missing_item_does_not_enter_frozen_target(session):
    _, dao, sync, service, save = session
    plan_id = save([assignment(11), assignment(99)])
    service.freeze_plans([{"plan_id": plan_id}], 2)
    newer = dao.import_inventory_snapshot(snapshot(3, [item(11, "module", equipped=True), item(99, "module")]))
    service.apply_plan(plan_id, stable_snapshot_id=newer, verify_after_dispatch=False)
    assert [row["equipment"]["serial"] for row in sync.module_calls] == [11]
    assert service.plan_equipment_uid_pairs(plan_id) == frozenset({(11, 11)})


def test_frozen_target_disappearance_is_error_not_another_skip(session):
    _, dao, sync, service, save = session
    plan_id = save([assignment(11), virtual()])
    service.freeze_plans([{"plan_id": plan_id}], 2)
    newer = dao.import_inventory_snapshot(snapshot(3, [item(22, "core")]))
    with pytest.raises(EquipmentApplyError, match="已冻结"):
        service.apply_plan(plan_id, stable_snapshot_id=newer, verify_after_dispatch=False, reset_before_apply=True)
    assert sync.unmount_calls == []
    assert service.verify_plan_in_snapshot(
        plan_id, stable_snapshot_id=newer, character_uid=CHARACTER_UID,
    ) is not None


def test_complete_observation_verifies_subset_without_requiring_skipped_items(session):
    _, dao, _, service, save = session
    plan_id = save([assignment(11), virtual(), assignment(99), assignment(22, "core")])
    service.freeze_plans([{"plan_id": plan_id}], 2)
    rows = [item(11, "module", equipped=True), item(22, "core", equipped=True)]
    rows[0]["equipped_placement"] = {"row": 2, "column": 3}
    newer = dao.import_inventory_snapshot(snapshot(3, rows))
    state = {"plan_id": plan_id, "character_uid": CHARACTER_UID, "character_id": 1003, "job_item_id": 1}
    job_dao = Mock()
    assert verify_complete_snapshot(job_dao, service, [state], snapshot_id=newer) == []
    assert state["full_snapshot_verified"]
    # 同一个冻结目标在后续完整观测中丢失时，复核必须报告不一致。
    absent = dao.import_inventory_snapshot(snapshot(4, [rows[1]]))
    assert verify_complete_snapshot(job_dao, service, [state], snapshot_id=absent) == [state]
    assert "不在复核快照" in state["last_mismatch"]


@pytest.mark.parametrize("bad", [
    {**assignment(11), "target_row": 8},
    {**assignment(11), "kind": "core", "target_row": None, "target_column": None},
])
def test_available_but_invalid_equipment_is_not_silently_skipped(session, bad):
    _, _, sync, service, save = session
    # 持久化层可能先拒绝非法格位，因此用只读方案入口提供历史损坏数据。
    plan_id = save([assignment(11), assignment(22, "core")])
    plan = service.read_plan(plan_id)
    plan["assignments"] = [bad, assignment(99)]
    service.user_dao.get_loadout_plan = Mock(return_value=plan)
    with pytest.raises(EquipmentApplyError):
        service.apply_plan(plan_id, verify_after_dispatch=False, reset_before_apply=True)
    assert sync.unmount_calls == []


def test_bulk_skips_empty_role_and_applies_partial_role(session, monkeypatch):
    path, dao, sync, _, save = session
    valid = save([assignment(11), virtual(), assignment(99), assignment(22, "core")])
    save([virtual(), assignment(99)], character_id=1004)
    monkeypatch.setattr("src.services.equipment.assembly.bulk_apply._snapshot_timeout", lambda _dao: 0)
    bulk = BulkEquipmentApplyService(path, sync, operation_guard=lambda _: None)
    result = bulk.run(["1003", "1004"])
    assert not result.get("preflight_errors")
    assert len(result["applied"]) == len(result["skipped_roles"]) == 1
    assert result["applied"][0]["plan_id"] == valid
    assert result["applied"][0]["module_count"] == 1
    assert len(result["applied"][0]["skipped_assignments"]) == 2
    assert result["skipped_roles"][0]["role_name"] == "1004"
    assert len(sync.unmount_calls) == 1
    job = dao.get_equipment_apply_job(result["job_id"])
    assert any("部分装配" in row["message"] and "跳过 2 件" in row["message"] for row in job["logs"])


def test_bulk_all_missing_never_opens_batch_or_creates_job(session):
    path, dao, sync, _, save = session
    save([virtual(), assignment(99)])
    sync.equipment_batch = Mock(side_effect=AssertionError("空任务不得开始派发"))
    result = BulkEquipmentApplyService(path, sync, operation_guard=lambda _: None).run(["1003"])
    assert not result["applied"]
    assert len(result["skipped_roles"]) == 1
    assert dao.latest_resumable_equipment_apply_job() is None


def test_resume_skips_now_empty_role_and_continues_available_role(session, monkeypatch):
    path, dao, sync, _, save = session
    valid = save([assignment(11), assignment(22, "core")])
    empty = save([assignment(99)], character_id=1004)
    job_id = dao.create_equipment_apply_job(2, [
        {"role_name": "1003", "character_id": 1003, "character_uid": CHARACTER_UID, "plan_id": valid},
        {"role_name": "1004", "character_id": 1004, "character_uid": {"slot": 8, "serial": 800}, "plan_id": empty},
    ])
    monkeypatch.setattr("src.services.equipment.assembly.bulk_apply._snapshot_timeout", lambda _dao: 0)
    result = BulkEquipmentApplyService(path, sync, operation_guard=lambda _: None).run(job_id=job_id)
    assert [row["plan_id"] for row in result["applied"]] == [valid]
    assert [row["plan_id"] for row in result["skipped_roles"]] == [empty]
    job = dao.get_equipment_apply_job(job_id)
    assert job["items"][1]["status"] == "failed"
    assert "未发送装配指令" in job["items"][1]["last_error"]
    assert len(sync.unmount_calls) == 1


@pytest.mark.parametrize("serial", [11, 99])
def test_bulk_conflict_checks_only_available_targets_before_any_rpc(session, serial):
    path, dao, sync, _, save = session
    save([assignment(serial), virtual()])
    slot_id = dao.list_loadout_slots(1003)[0]["slot_id"]
    original_slot = deepcopy(dao.get_loadout_slot(slot_id))
    save([assignment(serial), virtual()], character_id=1004)
    # 模拟旧版遗留的冲突当前槽位；正常新保存会把借出方改为占位。
    read_slot = dao.get_loadout_slot
    dao.get_loadout_slot = lambda sid: original_slot if sid == slot_id else read_slot(sid)
    bulk = BulkEquipmentApplyService(
        path, sync, operation_guard=lambda _: None, dao_factory=lambda _: nullcontext(dao),
    )
    # 角色实例来源与缺件无关，固定两个合法实例以单独检验最终子集冲突门禁。
    bulk._resolve_character_targets = lambda _service, cid, *_args: [
        {"character_id": cid, "character_uid": {"slot": cid, "serial": cid}},
    ]
    result = bulk.run(["1003", "1004"])
    if serial == 11:
        assert "冲突" in result["preflight_errors"][0]["error"]
    else:
        assert not result.get("preflight_errors")
        assert len(result["skipped_roles"]) == 2
    assert sync.unmount_calls == sync.module_calls == []
    assert sync.params is None
