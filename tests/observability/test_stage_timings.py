# 验证请求计时只记录数值摘要，保持返回值、异常、聚合与关闭隔离。
from concurrent.futures import CancelledError
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.observability.context import OperationContext
from src.observability.stage_timings import StageTimings, timed, timed_call, timed_context


def trace_fixture():
    ticks = [0.0]
    events = []
    trace = StageTimings(OperationContext.create("warehouse"), clock=lambda: ticks[0],
                         sink=lambda *args, **fields: events.append((args, fields)), details_enabled=lambda: True)
    return trace, ticks, events


def test_aggregation_numeric_only_and_missing_mark_is_not_zero():
    trace, ticks, events = trace_fixture()
    for _ in range(3):
        with trace.measure("solver.single"):
            ticks[0] += .01
    trace.note("solver.single", role_count=3, payload="private", uid="private")
    trace.since("absent", "delivery")
    trace.flush("worker_received")
    fields = events[0][1]
    assert fields["sample_count"] == 3
    assert fields["duration_ms"] == 30
    assert fields["max_duration_ms"] == 10
    assert fields["role_count"] == 3
    assert "private" not in repr(events)
    assert not any(e[1].get("stage") == "delivery" for e in events)


@pytest.mark.parametrize("error", [ValueError("private"), CancelledError("private")])
def test_decorator_keeps_exception_identity_and_no_exception_text(error):
    trace, _, events = trace_fixture()

    @timed("solver")
    def solve(*, timings=None):
        raise error

    with pytest.raises(type(error)) as caught:
        solve(timings=trace)
    assert caught.value is error
    trace.flush("failed")
    assert events[0][1]["cancelled_count" if isinstance(error, CancelledError) else "error_count"] == 1
    assert "private" not in repr(events)


def test_decorator_is_optional_and_returns_original_object():
    result = object()

    @timed("solver")
    def solve(*, timings=None):
        return result

    assert solve() is result
    trace, _, _ = trace_fixture()
    assert solve(timings=trace) is result
    # 包装器参数为仅位置参数，子调用仍可显式接收同名 timings。
    assert timed_call(trace, "nested", solve, timings=trace) is result


def test_context_measures_enter_and_preserves_exception_suppression():
    trace, ticks, events = trace_fixture()

    class Resource:
        def __enter__(self):
            ticks[0] += .02
            return self

        def __exit__(self, kind, error, tb):
            return kind is ValueError

    with timed_context(trace, "input.open", Resource()):
        ticks[0] += .5
        raise ValueError("private")
    trace.flush("done")
    assert events[0][1]["duration_ms"] == 20


def test_close_discards_late_work_and_flush_does_not_repeat_samples():
    trace, ticks, events = trace_fixture()
    trace.mark("worker")
    ticks[0] += .1
    trace.since("worker", "delivery")
    trace.flush("gui")
    trace.flush("paint")
    assert sum(e[1].get("stage") == "delivery" for e in events) == 1
    trace.close()
    before = len(events)
    with trace.measure("late"):
        ticks[0] += 1
    trace.flush("late")
    assert len(events) == before


def test_bounded_phases_and_logging_failure_is_not_business_failure():
    trace, _, events = trace_fixture()
    for i in range(150):
        trace.record("phase." + str(i), 1)
    trace.flush("gui")
    assert len(events) == 97
    bad_sink = lambda *a, **k: (_ for _ in ()).throw(OSError("sink"))
    trace = StageTimings(OperationContext.create("allocation"), sink=bad_sink)
    assert trace.call("solver", lambda: 42) == 42
    trace.flush("gui")


def test_shared_request_aggregates_threads_without_cross_request_state():
    trace, _, events = trace_fixture()
    other, _, other_events = trace_fixture()
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: trace.record("solver", 1), range(40)))
    trace.flush("gui")
    other.flush("gui")
    assert events[0][1]["sample_count"] == 40
    assert events[0][1]["duration_ms"] == 40
    assert not any(e[1].get("stage") == "solver" for e in other_events)
