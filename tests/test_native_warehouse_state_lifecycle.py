# 验证原生一键弃置锁定的旧状态转换顺序、忙碌恢复和未知结果中止。
from unittest.mock import Mock, patch

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError
from src.integrations.warehouse_state_writer import WarehouseStateWriteError, WarehouseStateWriter
from src.services.native_game_session import NativeGameSession
from tests.test_native_equipment_session import EquipmentCore


@pytest.mark.parametrize("source,target,expected", [
    ("normal", "normal", []),
    ("normal", "locked", [("locked", True)]),
    ("normal", "discarded", [("discarded", True)]),
    ("locked", "normal", [("locked", False)]),
    ("locked", "locked", []),
    ("locked", "discarded", [("locked", False), ("discarded", True)]),
    ("discarded", "normal", [("discarded", False)]),
    ("discarded", "locked", [("discarded", False), ("locked", True)]),
    ("discarded", "discarded", []),
])
def test_native_state_transitions_keep_legacy_order_and_values(source, target, expected):
    core = EquipmentCore()
    calls = []
    epoch = [8]
    original = core.call
    def status(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "equipment.status":
            result["epoch"] = str(epoch[0])
        return result
    core.call = status
    equipment = {"slot": 8, "serial": 1}
    def record(key, **kwargs):
        assert kwargs["equipment"] == equipment
        calls.append((key, kwargs[key]))
        epoch[0] += 1  # Delivered lock/discard notifications invalidate inventory references.
        return {"status": "rpc_dispatched", "confirmed": False}
    core.set_item_discarded = lambda **kwargs: record("discarded", **kwargs)
    core.set_item_locked = lambda **kwargs: record("locked", **kwargs)
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    writer = WarehouseStateWriter(lease)
    try:
        assert lease.status()["native_snapshot_ready"]
        with writer.batch():
            core.calls.clear()
            writer.apply_one({"locked": source == "locked", "discarded": source == "discarded"}, target, equipment)
            assert not any(method.startswith("native.snapshot") for method, _ in core.calls)
        assert calls == expected
        assert not lease.snapshot_ready
        assert lease.status()["native_snapshot_ready"]
    finally:
        lease.close()
        session.close()


def test_busy_second_state_rpc_retries_only_the_rejected_rpc():
    busy = NteCoreRpcError({"code": -32000, "data": {"domain_code": "EQUIPMENT_PLUGIN_BUSY"}})
    sync = Mock()
    sync.set_item_discarded.side_effect = [busy, {"status": "rpc_dispatched"}]
    writer = WarehouseStateWriter(sync)
    with patch("src.integrations.warehouse_state_writer.sleep"):
        writer.apply_one({"locked": True, "discarded": False}, "discarded", {"slot": 8, "serial": 1})
    assert sync.set_item_locked.call_count == 1
    assert sync.set_item_discarded.call_count == 2


@pytest.mark.parametrize("error", [
    NteCoreRpcError({"code": -32000, "data": {"domain_code": "EQUIPMENT_OUTCOME_UNKNOWN"}}),
    NteCoreRpcError({"code": -32001, "message": "control_timeout"}),
])
def test_unknown_unlock_result_does_not_retry_or_discard(error):
    sync = Mock()
    sync.set_item_locked.side_effect = error
    with pytest.raises(NteCoreRpcError):
        WarehouseStateWriter(sync).apply_one({"locked": True}, "discarded", {"slot": 8, "serial": 1})
    sync.set_item_locked.assert_called_once()
    sync.set_item_discarded.assert_not_called()


def test_persistent_pre_dispatch_busy_stops_after_bounded_fast_retries():
    busy = NteCoreRpcError({"code": -32000, "data": {"domain_code": "EQUIPMENT_PLUGIN_BUSY"}})
    sync = Mock()
    sync.set_item_locked.side_effect = busy
    with patch("src.integrations.warehouse_state_writer.sleep") as sleeper:
        with pytest.raises(WarehouseStateWriteError, match="多次未派发"):
            WarehouseStateWriter(sync).apply_one({}, "locked", {"slot": 8, "serial": 1})
    assert sync.set_item_locked.call_count == 8
    assert sleeper.call_count == 7


def test_source_changed_same_context_retries_without_per_item_snapshot_read():
    core = EquipmentCore()
    core.set_item_discarded = Mock(
        side_effect=[
            NteCoreRpcError({"code": -32001, "message": "source_changed"}),
            {"status": "rpc_dispatched"},
        ]
    )
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    writer = WarehouseStateWriter(lease)
    try:
        assert lease.status()["native_snapshot_ready"]
        with writer.batch():
            core.calls.clear()
            with patch("src.integrations.warehouse_state_writer.sleep"):
                writer.apply_one({}, "discarded", {"slot": 8, "serial": 1})
            assert sum(method == "equipment.status" for method, _ in core.calls) == 1
            assert not any(method.startswith("native.snapshot") for method, _ in core.calls)
        assert core.set_item_discarded.call_count == 2
        assert lease._equipment_context is None
    finally:
        lease.close()
        session.close()


def test_source_changed_provider_switch_is_not_retried():
    core = EquipmentCore()
    original_call = core.call
    status_calls = 0

    def changing_status(method, params, **kwargs):
        nonlocal status_calls
        result = original_call(method, params, **kwargs)
        if method == "equipment.status":
            status_calls += 1
            if status_calls >= 2:
                result["providerId"] = "other-provider"
        return result

    core.call = changing_status
    core.set_item_discarded = Mock(
        side_effect=NteCoreRpcError({"code": -32001, "message": "source_changed"})
    )
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    writer = WarehouseStateWriter(lease)
    try:
        assert lease.status()["native_snapshot_ready"]
        with pytest.raises(NteCoreRpcError, match="EQUIPMENT_REQUEST_REJECTED"):
            with writer.batch():
                writer.apply_one({}, "discarded", {"slot": 8, "serial": 1})
        core.set_item_discarded.assert_called_once()
    finally:
        lease.close()
        session.close()


def test_large_batch_submits_each_target_once_without_per_item_checks():
    core = EquipmentCore()
    completed = []
    def discard(*, equipment, discarded):
        key = equipment["serial"]
        completed.append(key)
        assert discarded is True
        return {"status": "rpc_dispatched", "confirmed": False}
    core.set_item_discarded = discard
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    writer = WarehouseStateWriter(lease)
    try:
        assert lease.status()["native_snapshot_ready"]
        with writer.batch():
            core.calls.clear()
            for serial in range(1, 636):
                writer.apply_one({}, "discarded", {"slot": 8, "serial": serial})
            assert not any(method.startswith("native.snapshot") for method, _ in core.calls)
            assert not any(method == "equipment.status" for method, _ in core.calls)
        assert completed == list(range(1, 636))
        assert lease._equipment_context is None
        assert lease.status()["native_snapshot_ready"]
    finally:
        lease.close()
        session.close()


def test_apply_many_submits_one_group_at_a_time_and_preserves_item_order():
    core = EquipmentCore()
    calls = []

    def record(method, *, equipment, **state):
        calls.append((equipment["serial"], method, state))
        return {"status": "rpc_dispatched", "confirmed": False}

    core.set_item_discarded = lambda **kwargs: record("discarded", **kwargs)
    core.set_item_locked = lambda **kwargs: record("locked", **kwargs)
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    writer = WarehouseStateWriter(lease)
    try:
        assert lease.status()["native_snapshot_ready"]
        core.calls.clear()
        metrics = writer.apply_many([
            ({"discarded": True}, "locked", {"slot": 8, "serial": 1}),
            ({}, "discarded", {"slot": 8, "serial": 2}),
            ({}, "locked", {"slot": 8, "serial": 3}),
        ])
        assert metrics.group_count == 3
        assert metrics.rpc_count == 4
        assert metrics.retry_count == 0
        assert [serial for serial, _method, _state in calls] == [1, 1, 2, 3]
        serial_one = [(method, state) for serial, method, state in calls if serial == 1]
        assert serial_one == [("discarded", {"discarded": False}), ("locked", {"locked": True})]
        assert sum(method == "equipment.status" for method, _ in core.calls) == 1
    finally:
        lease.close()
        session.close()
