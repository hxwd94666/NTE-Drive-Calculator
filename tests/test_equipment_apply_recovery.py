# 验证极速装配仅补救明确未派发请求，并在同步恢复时保留执行边界。
from contextlib import contextmanager
from concurrent.futures import CancelledError
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError, NteCoreTimeoutError
from src.services.equipment_apply_recovery import EquipmentApplyRecovery, RecoveryStopped
from src.services.fast_equipment_execution import execute_fast_plan


def changed():
    return NteCoreRpcError({"code": -32001, "message": "source_changed"})


class Clock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


class Sync:
    def __init__(self):
        self.depth = 0
        self.identity = ("provider", "domain", "component-hash")
        self.snapshot_id = 2
        self.waits = 0

    @contextmanager
    def equipment_batch(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def equipment_identity(self, *, timeout=2.0):
        return self.identity

    def wait_for_snapshot(self, *, after_snapshot_id, timeout):
        assert self.depth == 0, "恢复同步时不得持有装配读取锁"
        self.waits += 1
        return SimpleNamespace(last_snapshot_id=self.snapshot_id)


class Dao:
    def inventory_snapshot_summary(self, _snapshot_id):
        return {"source": "nte_core", "complete": True}

    def list_inventory_items(self, _snapshot_id):
        return [{"uid_slot": 1, "uid_serial": 2}]

    def list_character_instance_mappings(self, _character_id):
        return [{"source": "snapshot", "last_seen_snapshot_id": 2, "uid_slot": 3, "uid_serial": 4}]


def recovery(**kwargs):
    clock = Clock()
    sync = Sync()
    kwargs.setdefault("operation_guard", Mock())
    runtime = EquipmentApplyRecovery(
        sync,
        Dao(),
        snapshot_id=1,
        inventory_uids=frozenset({(1, 2)}),
        clock=clock,
        sleeper=clock.sleep,
        **kwargs,
    )
    runtime.begin_role(10, 1001, {"slot": 3, "serial": 4})
    return runtime, sync, clock


def test_short_rejection_retries_only_current_command():
    runtime, sync, clock = recovery()
    accepted = Mock(return_value={"status": "accepted"})
    pending = Mock(side_effect=[changed(), changed(), {"status": "accepted"}])
    with runtime.batch():
        runtime.dispatch(accepted, step="reset")
        result = runtime.dispatch(pending, step="one_key")
    assert result == {"status": "accepted"}
    assert accepted.call_count == 1
    assert pending.call_count == 3
    assert sum(clock.delays) == pytest.approx(0.3)
    assert sync.waits == 0
    assert sync.depth == 0


def test_long_rejection_releases_batch_then_continues_current_command():
    runtime, sync, _clock = recovery()
    command = Mock(side_effect=[*[changed() for _ in range(6)], {"status": "accepted"}])
    with runtime.batch():
        runtime.dispatch(command, step="module", module_index=3)
    assert command.call_count == 7
    assert sync.waits == 1
    assert runtime.observed_snapshot_id == 2
    assert sync.depth == 0


def test_snapshot_already_satisfies_pending_step_does_not_resend():
    runtime, sync, _clock = recovery()
    command = Mock(side_effect=changed())
    with runtime.batch():
        result = runtime.dispatch(command, step="module", confirmed=lambda _rows: True)
    assert result["status"] == "confirmed_snapshot"
    assert command.call_count == 6
    assert sync.waits == 1


@pytest.mark.parametrize(
    "error",
    [
        NteCoreTimeoutError("equipment.equip_one_key", 30),
        NteCoreRpcError({"code": -32001, "message": "control_timeout"}),
        NteCoreRpcError(
            {"code": -32001, "message": "source_changed", "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"}}
        ),
        OSError("transport disconnected"),
    ],
)
def test_unknown_or_changed_context_is_never_replayed(error):
    runtime, sync, clock = recovery()
    command = Mock(side_effect=error)
    with pytest.raises(type(error)), runtime.batch():
        runtime.dispatch(command, step="one_key")
    assert command.call_count == 1
    assert sync.waits == 0
    assert clock.delays == []


@pytest.mark.parametrize(
    "identity",
    [
        ("other", "domain", "component-hash"),
        ("provider", "other-domain", "component-hash"),
        ("provider", "domain", "other-component-hash"),
        ("provider", "domain", "component-hash", "other-connection"),
    ],
)
def test_recovery_rejects_source_identity_change(identity):
    runtime, sync, _clock = recovery()

    def reject():
        sync.identity = identity
        raise changed()

    with pytest.raises(RecoveryStopped, match="source_identity_changed"), runtime.batch():
        runtime.dispatch(reject, step="one_key")
    assert sync.waits == 0
    assert sync.depth == 0


def test_recovery_rejects_changed_inventory_membership():
    runtime, sync, _clock = recovery()
    runtime.inventory_uids = frozenset({(8, 9)})
    with pytest.raises(RecoveryStopped, match="inventory_changed"), runtime.batch():
        runtime.dispatch(Mock(side_effect=changed()), step="one_key")
    assert sync.depth == 0


def test_cancellation_during_wait_does_not_send_next_request():
    cancelled = Event()
    runtime, sync, clock = recovery(cancel_event=cancelled)

    def wait(delay):
        clock.sleep(delay)
        cancelled.set()

    runtime.sleeper = wait
    command = Mock(side_effect=changed())
    with pytest.raises(CancelledError), runtime.batch():
        runtime.dispatch(command, step="one_key")
    assert command.call_count == 1
    assert sync.depth == 0


def test_recovery_limit_is_finite():
    runtime, sync, _clock = recovery()
    with pytest.raises(RecoveryStopped), runtime.batch():
        runtime.dispatch(Mock(side_effect=changed()), step="one_key")
    assert sync.waits <= 2
    assert sync.depth == 0


def test_driver_cursor_does_not_replay_earlier_accepted_driver():
    runtime, sync, _clock = recovery()
    modules = [{"uid_slot": 1, "uid_serial": n, "target_row": 1, "target_column": n} for n in (2, 3, 4)]
    rows = [{**row, "equipped": False} for row in modules]
    placements = [
        {
            "equipment": {"slot": row["uid_slot"], "serial": row["uid_serial"]},
            "row": row["target_row"],
            "column": row["target_column"],
        }
        for row in modules
    ]
    calls = []

    def equip(**request):
        serial = request["equipment"]["serial"]
        calls.append(serial)
        if serial == 3 and calls.count(3) <= 2:
            raise changed()
        return {"status": "accepted"}

    sync.equip_module = equip
    sync.move_module_to_character = equip
    sync.unequip_all = Mock(return_value={"status": "accepted"})
    service = SimpleNamespace(recovery=runtime, sync_service=sync)
    service._dispatch_with_busy_retry = lambda command, operation, **options: runtime.dispatch(command, **options)
    with runtime.batch():
        result = execute_fast_plan(
            service,
            plan={"plan_id": 10},
            snapshot_id=1,
            character_id=1001,
            character_uid={"slot": 3, "serial": 4},
            placements=placements,
            modules=modules,
            core_assignment=None,
            current_items=rows,
            timeout=30,
            reset_before_apply=True,
        )
    assert calls == [2, 3, 3, 3, 4]
    assert sync.unequip_all.call_count == 1
    assert result.verified is False


def test_cancellation_after_acceptance_never_replays_accepted_step():
    cancelled = Event()
    runtime, sync, clock = recovery(cancel_event=cancelled)
    runtime.sleeper = lambda delay: (clock.sleep(delay), cancelled.set())
    command = Mock(return_value={"status": "accepted"})
    with pytest.raises(CancelledError), runtime.batch():
        runtime.dispatch(command, step="one_key", settle_seconds=0.5)
    assert command.call_count == 1
    assert sync.depth == 0


def test_role_and_task_budgets_survive_returning_to_a_role():
    runtime, _sync, _clock = recovery()
    runtime._charge(14)
    runtime.begin_role(11, 1002, {"slot": 5, "serial": 6})
    runtime._charge(14)
    runtime.begin_role(10, 1001, {"slot": 3, "serial": 4})
    assert runtime.role_spent == 14
    assert runtime.task_spent == 28
    with pytest.raises(RecoveryStopped, match="recovery_budget_exhausted"):
        runtime._wait(1.6, charge=True)


def test_sync_wait_timeout_keeps_original_scope_released():
    runtime, sync, clock = recovery()

    def wait(**request):
        assert sync.depth == 0
        clock.sleep(request["timeout"])
        raise TimeoutError

    sync.wait_for_snapshot = wait
    with pytest.raises(RecoveryStopped, match="snapshot_recovery_timeout"), runtime.batch():
        runtime.dispatch(Mock(side_effect=changed()), step="one_key")
    assert runtime.task_spent <= 15
    assert sync.depth == 0


def test_missing_actor_in_fresh_observation_stops_recovery():
    runtime, sync, _clock = recovery()
    sync.inventory_observation_cursor = lambda: 1
    sync.wait_for_inventory_observation = lambda **_request: SimpleNamespace(
        cursor=2, last_snapshot_id=1, items=({"uid_slot": 1, "uid_serial": 2},), characters=()
    )
    with pytest.raises(RecoveryStopped, match="character_identity_unconfirmed"), runtime.batch():
        runtime.dispatch(Mock(side_effect=changed()), step="one_key")
    assert sync.depth == 0


def test_same_snapshot_id_requires_a_new_complete_observation():
    runtime, sync, _clock = recovery()
    sync.inventory_observation_cursor = lambda: 7
    sync.wait_for_inventory_observation = lambda **_request: SimpleNamespace(
        cursor=8,
        last_snapshot_id=1,
        items=({"uid_slot": 1, "uid_serial": 2},),
        characters=({"character_id": 1001, "uid": {"slot": 3, "serial": 4}},),
    )
    command = Mock(side_effect=changed())
    with runtime.batch():
        result = runtime.dispatch(command, step="one_key", confirmed=lambda _items: True)
    assert result["snapshot_id"] == 1
    assert command.call_count == 6


def test_reset_after_sync_recovery_routes_pre_reset_owner_to_equip():
    runtime, sync, _clock = recovery()
    actor = {"slot": 3, "serial": 4}
    assignment = {"uid_slot": 1, "uid_serial": 2, "target_row": 1, "target_column": 2}
    rows = [
        {
            **assignment,
            "equipped": True,
            "equipped_character_uid": actor,
            "equipped_character_id": 1001,
            "equipped_placement": {"row": 1, "column": 2},
        }
    ]
    runtime.dao.list_inventory_items = lambda _sid: rows
    sync.unequip_all = Mock(side_effect=[*[changed() for _ in range(6)], {"status": "accepted"}])
    sync.equip_module = Mock(return_value={"status": "accepted"})
    sync.move_module_to_character = Mock(side_effect=AssertionError("不能使用卸空前的归属选择移动"))
    service = SimpleNamespace(recovery=runtime, sync_service=sync)
    service._dispatch_with_busy_retry = lambda command, operation, **options: runtime.dispatch(command, **options)
    with runtime.batch():
        execute_fast_plan(
            service,
            plan={"plan_id": 10},
            snapshot_id=1,
            character_id=1001,
            character_uid=actor,
            modules=[assignment],
            core_assignment=None,
            current_items=rows,
            placements=[{"equipment": {"slot": 1, "serial": 2}, "row": 1, "column": 2}],
            timeout=30,
            reset_before_apply=True,
        )
    assert sync.unequip_all.call_count == 7
    sync.equip_module.assert_called_once()
    sync.move_module_to_character.assert_not_called()
