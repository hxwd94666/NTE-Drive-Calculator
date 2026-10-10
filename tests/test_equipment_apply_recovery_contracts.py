# 验证极速装配恢复使用原生观测、冻结目标和真实失败结果。
from concurrent.futures import CancelledError
from threading import Condition
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService
from src.services.bulk_equipment_apply_postcheck import (
    dispatch_retry_attempt,
    postcheck_and_repair,
    verify_complete_snapshot,
    wait_for_guarded_full_snapshot,
)
from src.services.equipment_apply_service import EquipmentApplyService
from src.integrations.nte_core_protocol import NteCoreError, NteCoreProtocolError
from src.services.inventory_observation_wait import (
    record_inventory_observation,
    wait_for_inventory_observation,
)


def observation_service():
    return SimpleNamespace(
        _state_condition=Condition(),
        _inventory_observation_cursor=0,
        _inventory_observation=None,
        _state=SimpleNamespace(last_snapshot_id=7, phase="listening", running=True, stop_reason=None),
    )


def payload(*, equipped=False):
    return {
        "complete": True,
        "items": [{"uid": {"slot": 1, "serial": 2}, "equipped": equipped}],
        "characters": [{"character_id": 1001, "uid": {"slot": 3, "serial": 4}}],
    }


def test_observation_copies_native_facts_instead_of_live_projection():
    service = observation_service()
    source = payload()
    record_inventory_observation(service, source)
    source["items"][0]["equipped"] = True
    observed = wait_for_inventory_observation(service, after_cursor=0, timeout=0)
    assert observed.last_snapshot_id == 7
    assert observed.items[0]["equipped"] is False
    observed.items[0]["equipped"] = True
    assert wait_for_inventory_observation(service, after_cursor=0, timeout=0).items[0]["equipped"] is False
    with pytest.raises(TimeoutError):
        wait_for_inventory_observation(service, after_cursor=1, timeout=0)


def test_incomplete_payload_cannot_advance_complete_observation_cursor():
    service = observation_service()
    record_inventory_observation(service, {**payload(), "complete": False})
    assert service._inventory_observation_cursor == 0
    assert service._inventory_observation is None


def test_full_postcheck_uses_new_observation_not_accepted_command_projection():
    observation = observation_service()
    record_inventory_observation(observation, payload())
    sync = SimpleNamespace(
        wait_for_snapshot=Mock(),
        wait_for_inventory_observation=lambda **options: wait_for_inventory_observation(observation, **options),
    )
    dao = SimpleNamespace(
        inventory_snapshot_summary=lambda _sid: {"source": "nte_core", "complete": True},
        list_inventory_items=Mock(side_effect=AssertionError("不得用展示投影确认")),
        mark_equipment_apply_job_item=Mock(),
    )
    sink = {}
    snapshot_id = wait_for_guarded_full_snapshot(
        sync,
        dao,
        after_snapshot_id=7,
        frozen_inventory_uids=frozenset({(1, 2)}),
        timeout=0,
        after_cursor=0,
        observation_sink=sink,
    )
    assert snapshot_id == 7
    sync.wait_for_snapshot.assert_not_called()
    apply = SimpleNamespace(verify_plan_in_snapshot=Mock(), verify_plan_in_items=Mock(return_value="目标驱动未装备"))
    row = {"plan_id": 10, "character_uid": {"slot": 3, "serial": 4}, "character_id": 1001, "job_item_id": 20}
    assert verify_complete_snapshot(dao, apply, [row], snapshot_id=7, observed_items=sink["items"]) == [row]
    assert apply.verify_plan_in_items.call_args.kwargs["items"][0]["equipped"] is False
    apply.verify_plan_in_snapshot.assert_not_called()
    dao.mark_equipment_apply_job_item.assert_not_called()


def test_freeze_plans_preserves_targets_across_saved_plan_changes():
    plan = {"plan_id": 10, "assignments": [{"uid_slot": 1, "uid_serial": 2}]}
    dao = SimpleNamespace(get_loadout_plan=Mock(return_value=plan))
    service = EquipmentApplyService(dao, object())
    service.validate_plan_for_fast_apply = Mock(return_value=plan)
    frozen = service.freeze_plans([{"plan_id": 10}], 7)
    plan["assignments"][0]["uid_serial"] = 99
    frozen[10]["assignments"].clear()
    assert service.read_plan(10)["assignments"][0]["uid_serial"] == 2
    returned = service.read_plan(10)
    returned["assignments"].clear()
    assert len(service.read_plan(10)["assignments"]) == 1
    dao.get_loadout_plan.assert_not_called()


def test_failure_return_is_logged_as_failure_not_bulk_success(monkeypatch):
    events = Mock()
    monkeypatch.setattr("src.services.bulk_equipment_apply_service.log_event", events)
    service = BulkEquipmentApplyService("unused.sqlite3", object(), operation_guard=Mock())
    service._run = Mock(
        return_value={"job_id": 10, "completed": False, "failure_kind": "recovery_exhausted", "applied": []}
    )
    service.run(["测试角色"])
    names = [call.args[1] for call in events.call_args_list]
    assert names == ["equipment_apply.bulk_started", "equipment_apply.bulk_failed"]


def test_repair_timeout_stops_dispatch_to_remaining_roles():
    apply = SimpleNamespace(apply_plan=Mock(side_effect=TimeoutError("回执超时")))
    rows = [
        {"plan_id": n, "character_id": n, "character_uid": {"slot": 3, "serial": n}, "role_name": f"测试角色{n}"}
        for n in (10, 11)
    ]
    errors = []
    assert dispatch_retry_attempt(object(), apply, rows, 7, 2, errors) == []
    assert apply.apply_plan.call_count == 1
    assert len(errors) == 1


def test_cancellation_prevents_postcheck_writes_and_repair_dispatch():
    check = Mock(side_effect=CancelledError)
    dao = SimpleNamespace(mark_equipment_apply_job_item=Mock())
    apply = SimpleNamespace(verify_plan_in_snapshot=Mock(), apply_plan=Mock())
    with pytest.raises(CancelledError):
        verify_complete_snapshot(dao, apply, [{"plan_id": 10}], snapshot_id=7, check_cancelled=check)
    with pytest.raises(CancelledError):
        dispatch_retry_attempt(object(), apply, [{"plan_id": 10}], 7, 2, [], check_cancelled=check)
    dao.mark_equipment_apply_job_item.assert_not_called()
    apply.apply_plan.assert_not_called()


def test_unknown_full_snapshot_fields_do_not_trigger_reset_repair():
    sync = SimpleNamespace(wait_for_snapshot=lambda **_request: SimpleNamespace(last_snapshot_id=8))
    dao = SimpleNamespace(
        inventory_snapshot_summary=lambda _sid: {"source": "nte_core", "complete": True},
        list_inventory_items=lambda _sid: [{"uid_slot": 1, "uid_serial": 2}],
        mark_equipment_apply_job_item=Mock(),
    )
    apply = SimpleNamespace(
        verify_plan_in_snapshot=Mock(side_effect=KeyError("equipped_character_uid")), apply_plan=Mock()
    )
    applied = [
        {
            "plan_id": 10,
            "job_item_id": 20,
            "character_id": 1001,
            "character_uid": {"slot": 3, "serial": 4},
            "role_name": "测试角色",
        }
    ]
    result = postcheck_and_repair(
        sync,
        dao,
        apply,
        applied,
        applied,
        stable_snapshot_id=7,
        frozen_inventory_uids=frozenset({(1, 2)}),
        timeout=0,
        max_attempts=3,
        report_progress=lambda *_args: None,
    )
    assert result["snapshot_wait_failure"]["kind"] == "snapshot_error"
    apply.apply_plan.assert_not_called()


@pytest.mark.parametrize(
    "error", [OSError("transport closed"), NteCoreError("connection closed"), NteCoreProtocolError("invalid reply")]
)
def test_transport_and_protocol_failure_never_try_another_actor(error):
    apply = SimpleNamespace(apply_plan=Mock(side_effect=error), dispatch_started=True)
    role = {
        "plan_id": 10,
        "character_id": 1051,
        "character_uid": {"slot": 3, "serial": 4},
        "fallback_targets": [{"character_id": 1046, "character_uid": {"slot": 5, "serial": 6}}],
    }
    with pytest.raises(type(error)):
        BulkEquipmentApplyService._apply_role(apply, role, 7)
    apply.apply_plan.assert_called_once()
