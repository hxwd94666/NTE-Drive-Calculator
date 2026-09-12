# 验证装配与刷新串行、未派发忙碌重试及未知结果禁止重复执行。
from threading import Event, Thread
from unittest.mock import Mock, patch

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError, NteCoreTimeoutError
from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService
from src.services.equipment_apply_service import EquipmentApplyService
from src.services.native_game_session import NativeGameSession
from tests.test_native_equipment_session import EquipmentCore


def rpc(code, message="fixture"):
    return NteCoreRpcError({"code": -32001, "message": message, "data": {"domain_code": code}})


@pytest.mark.parametrize("error", [rpc("EQUIPMENT_PLUGIN_BUSY"), rpc("MODS_PLUGIN_BUSY")])
def test_not_dispatched_busy_retries_through_existing_service(error):
    service = EquipmentApplyService(Mock(), Mock(), operation_guard=lambda _caps: None)
    dispatch = Mock(side_effect=[error, {"status": "rpc_dispatched"}])
    with patch("src.services.equipment_apply_service.time.sleep"):
        assert service._dispatch_with_busy_retry(dispatch, operation="test")["status"] == "rpc_dispatched"
    assert dispatch.call_count == 2


@pytest.mark.parametrize("error", [rpc("EQUIPMENT_OUTCOME_UNKNOWN"),
                                   rpc(None, "control_timeout"),
                                   NteCoreTimeoutError("equipment.equip_one_key", 1)])
def test_unknown_result_never_retries_or_tries_protagonist_fallback(error):
    service = EquipmentApplyService(Mock(), Mock(), operation_guard=lambda _caps: None)
    dispatch = Mock(side_effect=error)
    with pytest.raises(type(error)):
        service._dispatch_with_busy_retry(dispatch, operation="test")
    assert dispatch.call_count == 1
    apply = Mock()
    apply.apply_plan.side_effect = error
    role = {"plan_id": 1, "character_id": 1051, "character_uid": {"slot": 1, "serial": 2},
            "fallback_targets": [{"character_id": 1046, "character_uid": {"slot": 3, "serial": 4}}]}
    with pytest.raises(type(error)):
        BulkEquipmentApplyService._apply_role(apply, role, 1)
    assert apply.apply_plan.call_count == 1


def test_command_waits_for_refresh_then_dispatches_with_recovered_snapshot():
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    started, done = Event(), Event()
    errors = []
    def apply():
        started.set()
        try:
            lease.equip_core(character={"slot": 700, "serial": 701}, equipment={"slot": 8, "serial": 1})
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    worker = Thread(target=apply)
    try:
        assert lease.status()["native_snapshot_ready"]
        with session._snapshot_lock:
            lease._snapshot_ready = False
            worker.start()
            assert started.wait(1)
            assert not done.wait(0.05)
            core.equip_core.assert_not_called()
        assert done.wait(3)
        assert not errors
        core.equip_core.assert_called_once()
    finally:
        worker.join(3)
        lease.close()
        session.close()


def test_waiting_equipment_remains_cancellable_without_game_dispatch():
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    started, done = Event(), Event()
    errors = []
    def apply():
        started.set()
        try:
            lease.equip_core(character={}, equipment={})
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    worker = Thread(target=apply)
    try:
        with session._snapshot_lock:
            worker.start()
            assert started.wait(1)
            lease.request_stop()
            assert done.wait(2)
        assert errors
        core.equip_core.assert_not_called()
    finally:
        worker.join(3)
        lease.close()
        session.close()


@pytest.mark.parametrize("fail", [False, True])
def test_whole_batch_defers_snapshot_reads_and_resumes_after_success_or_failure(fail):
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    started, done = Event(), Event()
    def refresh():
        started.set()
        lease.status()
        done.set()
    worker = Thread(target=refresh)
    command = {"character": {"slot": 700, "serial": 701}, "equipment": {"slot": 8, "serial": 1}}
    try:
        assert lease.status()["native_snapshot_ready"]
        try:
            with lease.equipment_batch():
                core.calls.clear()
                lease.equip_core(**command)
                worker.start()
                assert started.wait(1) and not done.wait(0.05)
                lease.move_core_to_character(**command)
                assert not any(method.startswith("native.snapshot") for method, _ in core.calls)
                if fail:
                    raise RuntimeError("dispatch failed")
        except RuntimeError:
            assert fail
        assert done.wait(3)
        assert any(method == "native.snapshot.refresh" for method, _ in core.calls)
        core.equip_core.assert_called_once()
        core.move_core_to_character.assert_called_once()
    finally:
        worker.join(3)
        lease.close()
        session.close()


@pytest.mark.parametrize("changed_field", ["providerId", "domainKey"])
def test_batch_aborts_when_equipment_session_changes_after_first_command(changed_field):
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    command = {"character": {"slot": 700, "serial": 701}, "equipment": {"slot": 8, "serial": 1}}
    try:
        assert lease.status()["native_snapshot_ready"]
        with lease.equipment_batch():
            lease.equip_core(**command)
            original = core.call
            def changed(method, params, **kwargs):
                result = original(method, params, **kwargs)
                if method == "equipment.status":
                    result[changed_field] = "different-session"
                return result
            core.call = changed
            with pytest.raises(NteCoreRpcError, match="source_changed"):
                lease.move_core_to_character(**command)
        core.equip_core.assert_called_once()
        core.move_core_to_character.assert_not_called()
        assert lease._equipment_context is None
    finally:
        lease.close()
        session.close()


def test_clear_advances_inventory_epoch_without_aborting_one_key_batch():
    core = EquipmentCore()
    epoch = [8]
    calls = []
    original = core.call
    def status(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "equipment.status":
            result["epoch"] = str(epoch[0])
        return result
    core.call = status
    def dispatch(method, **kwargs):
        calls.append((method, kwargs))
        epoch[0] += 1
        return {"status": "rpc_dispatched", "confirmed": False}
    core.unequip_all = lambda **kwargs: dispatch("clear", **kwargs)
    core.equip_one_key = lambda **kwargs: dispatch("one_key", **kwargs)
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    character = {"slot": 700, "serial": 701}
    try:
        assert lease.status()["native_snapshot_ready"]
        with lease.equipment_batch():
            lease.unequip_all(character=character)
            assert epoch[0] == 9
            lease.equip_one_key(character=character, core={"slot": 8, "serial": 1}, placements=[
                {"equipment": {"slot": 8, "serial": 2}, "row": 1, "column": 1}])
        assert [method for method, _ in calls] == ["clear", "one_key"]
        assert epoch[0] == 10
    finally:
        lease.close()
        session.close()
