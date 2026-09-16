# 验证原生一键弃置锁定的旧状态转换顺序、忙碌恢复和未知结果中止。
from unittest.mock import Mock, patch

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError
from src.integrations.warehouse_state_writer import WarehouseStateWriter
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


def test_busy_second_state_rpc_retries_only_that_rpc():
    busy = NteCoreRpcError({"code": -32000, "data": {"domain_code": "EQUIPMENT_PLUGIN_BUSY"}})
    sync = Mock()
    sync.set_item_discarded.side_effect = [busy, {"status": "rpc_dispatched"}]
    writer = WarehouseStateWriter(sync)
    with patch("src.integrations.warehouse_state_writer.time.sleep"):
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


@pytest.mark.parametrize("changed", ["epoch", "domainKey", "providerId", "none"])
def test_dispatch_revision_race_retries_only_unexecuted_same_session_command(changed):
    core = EquipmentCore()
    state = {"epoch": "8", "domainKey": "inventory", "providerId": "fixture"}
    original = core.call
    def status(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "equipment.status":
            result.update(state)
        return result
    core.call = status
    calls = []
    def discard(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if changed != "none":
                state[changed] = "9" if changed == "epoch" else "other-session"
            raise NteCoreRpcError({"code": -32001, "message": "source_changed"})
        return {"status": "rpc_dispatched", "confirmed": False}
    core.set_item_discarded = discard
    core.set_item_locked = Mock(return_value={"status": "rpc_dispatched", "confirmed": False})
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    writer = WarehouseStateWriter(lease)
    try:
        assert lease.status()["native_snapshot_ready"]
        with writer.batch(), patch("src.integrations.warehouse_state_writer.time.sleep"):
            if changed == "epoch":
                writer.apply_one({"locked": True}, "discarded", {"slot": 8, "serial": 1})
                assert len(calls) == 2
            else:
                with pytest.raises(NteCoreRpcError, match="source_changed"):
                    writer.apply_one({"locked": True}, "discarded", {"slot": 8, "serial": 1})
                assert len(calls) == 1
        # A retry of discard must never repeat its successful unlock prerequisite.
        core.set_item_locked.assert_called_once()
        assert lease._equipment_context is None
    finally:
        lease.close()
        session.close()


def test_large_batch_recovers_revision_races_without_repeating_completed_writes():
    core = EquipmentCore()
    epoch = [8]
    attempts, completed = {}, []
    original = core.call
    def status(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "equipment.status":
            result["epoch"] = str(epoch[0])
        return result
    core.call = status
    def discard(*, equipment, discarded):
        key = equipment["serial"]
        attempts[key] = attempts.get(key, 0) + 1
        epoch[0] += 1
        if attempts[key] == 1:
            raise NteCoreRpcError({"code": -32001, "message": "source_changed"})
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
        with writer.batch(), patch("src.integrations.warehouse_state_writer.time.sleep"):
            core.calls.clear()
            for serial in range(1, 636):
                writer.apply_one({}, "discarded", {"slot": 8, "serial": serial})
            assert not any(method.startswith("native.snapshot") for method, _ in core.calls)
        assert completed == list(range(1, 636))
        assert all(count == 2 for count in attempts.values())
        assert lease._equipment_context is None
        assert lease.status()["native_snapshot_ready"]
    finally:
        lease.close()
        session.close()
