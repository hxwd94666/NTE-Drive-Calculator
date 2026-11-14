# 用模拟状态写入核对计时不改变批次、失败停止、重试次数或异常身份。
from concurrent.futures import CancelledError
from contextlib import contextmanager

import pytest

from src.integrations.inventory.warehouse_state_writer import WarehouseStateWriter
from src.integrations.native_core.nte_core_equipment import STATE_BATCH_CAPABILITY
from src.observability.context import OperationContext
from src.observability.stage_timings import StageTimings
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact, recommend


class Sync:
    core_hello_result = {"capabilities": [STATE_BATCH_CAPABILITY]}

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    @contextmanager
    def equipment_batch(self):
        yield self

    def set_item_states(self, *, operations):
        self.calls.append(operations)
        if self.error is not None:
            raise self.error


def changes():
    return [({"locked": True}, "discarded", {"slot": 8, "serial": i + 1}) for i in range(17)]


def timing():
    events = []
    trace = StageTimings(OperationContext.create("warehouse"),
                         sink=lambda *args, **fields: events.append(fields), details_enabled=lambda: True)
    return trace, events


def test_telemetry_keeps_dispatch_batches_and_separates_gate():
    baseline, observed = Sync(), Sync()
    before = WarehouseStateWriter(baseline).apply_many(changes())
    trace, events = timing()
    guards = []
    after = WarehouseStateWriter(observed).apply_many(changes(), timings=trace,
                                                    check_current=lambda: guards.append(True))
    trace.flush("save_received")
    assert observed.calls == baseline.calls
    assert (after.group_count, after.rpc_count, after.retry_count) == (
        before.group_count, before.rpc_count, before.retry_count)
    assert len(guards) == after.rpc_count
    phases = {e.get("stage") for e in events}
    assert {"dispatch.guard", "dispatch.call", "dispatch.mode"} <= phases
    assert "serial" not in repr(events)
    assert "operations" not in repr(events)


def test_unknown_failure_is_not_retried_by_timing():
    error = RuntimeError("private fixture")
    sync = Sync(error)
    trace, events = timing()
    with pytest.raises(RuntimeError) as caught:
        WarehouseStateWriter(sync).apply_many(changes(), timings=trace)
    assert caught.value is error
    assert len(sync.calls) == 1
    trace.flush("save_failed")
    assert "private fixture" not in repr(events)
    assert not any(e.get("stage") == "dispatch.backoff" for e in events)


def test_gate_cancellation_still_prevents_dispatch():
    sync = Sync()
    trace, events = timing()

    def guard():
        raise CancelledError("private fixture")

    result = WarehouseStateWriter(sync).apply_many(changes(), timings=trace, check_current=guard)
    trace.flush("stopped")
    assert sync.calls == []
    assert result.group_count == 0
    assert result.stopped_reason
    assert any(e.get("stage") == "dispatch.guard" and e.get("cancelled_count") == 1 for e in events)


def test_bucket_diagnostics_do_not_change_recommendation():
    items = [WarehouseFact(str(i), "drive", "Suit1", "I_3", "", 3, "gold",
        (("攻击力%", 1.0),), (("role", 10.0),), ("role",)) for i in range(4)]
    options = dict(selected_roles=("role",), demands={"role": {"I_3": 1}}, baseline_uids=frozenset(),
                   policy={"current_drive": {"reserve": 1, "action": "keep"}, "future": {"reserve": 1}})
    before = recommend(items, **options)
    counts = {}
    after = recommend(items, **options, diagnostics=counts)
    assert after == before
    assert counts["max_current_bucket_count"] == 4
    assert "role" not in counts
