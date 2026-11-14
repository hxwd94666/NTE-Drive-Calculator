# 验证常驻文件和界面不刷阶段明细，异常、终态、详细日志及原生故障仍可定位。
import pytest
from loguru import logger
from PySide6.QtCore import QObject, Signal

from src.observability.context import OperationContext
from src.observability.stage_timings import StageTimings
from src.ui.main_window.qt_log_sink import QtLogSink


NTE_TEST_TIER = 'core'


class GuiLogs(QObject):
    message = Signal(str)


def trace(*, foreground=True, slow_ms=10_000, detailed=False):
    events, ticks = [], [0.0]
    value = StageTimings(OperationContext.create('warehouse'), clock=lambda: ticks[0],
        sink=lambda *args, **fields: events.append((args, fields)),
        details_enabled=lambda: detailed, foreground=foreground, slow_ms=slow_ms)
    return value, events, ticks


@pytest.mark.parametrize('detailed', [False, True])
def test_real_file_and_qt_sinks_receive_only_one_foreground_summary(tmp_path, detailed):
    normal, detail = tmp_path / 'normal.log', tmp_path / 'detail.log'
    gui = []
    bridge = GuiLogs()
    bridge.message.connect(gui.append)
    sinks = [logger.add(str(normal), level='INFO', format='{message}'),
             logger.add(str(detail), level='DEBUG', format='{message}'),
             logger.add(QtLogSink(bridge.message), level='INFO', format='{message}')]
    try:
        timing = StageTimings(OperationContext.create('allocation'), details_enabled=lambda: detailed)
        for group in range(3):
            for index in range(10):
                timing.record(f'phase.{group}.{index}', 1)
            timing.flush(f'boundary.{group}')
        timing.finish('first_paint_dispatch')
    finally:
        for sink in sinks:
            logger.remove(sink)
    assert len(normal.read_text(encoding='utf8').splitlines()) == len(gui) == 1
    assert 'timing_summary' in gui[0] and 'timing_stage' not in gui[0]
    text = detail.read_text(encoding='utf8')
    assert ('timing_stage' in text) is detailed
    assert text.count('timing_stage') == (30 if detailed else 0)
    assert text.count('timing_summary') == 1


def test_failure_is_not_followed_by_cancel_or_returned_summary():
    timing, events, ticks = trace()
    error = ValueError('private fixture content')
    with pytest.raises(ValueError) as caught:
        timing.call('input.freeze', lambda: (_ for _ in ()).throw(error))
    assert caught.value is error
    timing.flush('worker_received')
    timing.finish('request_failed', outcome='failed')
    ticks[0] += 30
    timing.record('late', 2)
    timing.finish('request_cancelled', outcome='cancelled')
    timing.finish('dialog_retired')
    assert len(events) == 1
    args, fields = events[0]
    assert args[0] == 'WARNING' and fields['result'] == 'failed'
    assert fields['failed_stages'] == ['input.freeze']
    assert 'private' not in repr(events)


def test_repeated_fast_background_requests_are_silent_but_slow_or_failed_are_not():
    for _ in range(100):
        timing, events, _ = trace(foreground=False, slow_ms=250)
        timing.record('gui.filter', 10)
        timing.flush('card_submitted')
        timing.finish('first_paint_dispatch')
        assert events == []
    for outcome, duration in [('returned', 300), ('failed', 1)]:
        timing, events, _ = trace(foreground=False, slow_ms=250)
        timing.record('gui.filter', duration)
        timing.finish('finished', outcome=outcome)
        assert len(events) == 1 and events[0][0][0] == 'WARNING'


def test_summary_retains_phases_across_flush_without_summing_parent_durations():
    timing, events, ticks = trace()
    timing.record('preview.total', 90)
    timing.record('input.freeze', 30)
    timing.flush('worker_received')
    timing.record('gui.render', 10)
    ticks[0] = .1
    timing.finish('first_paint_dispatch')
    fields = events[0][1]
    assert fields['elapsed_ms'] == 100 and fields['stage_count'] == 3
    assert fields['slowest_stages'][0] == {'stage': 'preview.total', 'duration_ms': 90}
    assert len(fields['slowest_stages']) <= 3


def test_waiting_wall_time_does_not_alone_turn_into_a_slow_warning():
    timing, events, ticks = trace(slow_ms=1000)
    timing.record('plan.build', 10)
    ticks[0] = 60
    timing.finish('save_projected')
    assert events[0][0][0] == 'INFO' and events[0][1]['slow'] is False


def test_explicit_user_cancellation_is_not_escalated_to_a_slow_warning():
    timing, events, _ = trace()
    timing.record('solver', 20_000)
    timing.finish('request_cancelled', outcome='cancelled')
    assert events[0][0][0] == 'INFO'
    assert events[0][1]['result'] == 'cancelled' and events[0][1]['slow'] is False


def test_details_can_be_enabled_mid_request_and_closed_old_request_stays_silent():
    events, enabled = [], [False]
    timing = StageTimings(OperationContext.create('warehouse'), details_enabled=lambda: enabled[0],
        sink=lambda *args, **fields: events.append((args, fields)))
    timing.record('before', 1)
    timing.flush('first')
    assert events == []
    enabled[0] = True
    timing.record('after', 2)
    timing.finish('finished')
    assert events[0][1]['stage_count'] == 2
    assert all(args[0] == 'DEBUG' for args, fields in events[1:])
    old, old_events, _ = trace(detailed=True)
    old.close()
    old.finish('old_worker_finished')
    assert old_events == []


def test_summary_sink_failure_does_not_retry_or_change_business_result():
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError('log sink unavailable')
    timing = StageTimings(OperationContext.create('warehouse'), sink=fail, details_enabled=lambda: False)
    assert timing.call('solver', lambda: 42) == 42
    timing.finish('finished')
    timing.finish('finished')
    assert calls == [1]


def test_native_cumulative_details_are_debug_and_fault_counts_still_warn(monkeypatch):
    from src.integrations.native_core import native_hud_interaction as hud
    from src.integrations.native_core import native_snapshot_timing as snapshot
    from src.integrations.native_core import native_transport_diagnostics as transport
    from tests.native_session.test_native_snapshot_timing import payload
    events, now = [], [0.0]
    for module in (hud, snapshot, transport):
        monkeypatch.setattr(module, 'log_event', lambda *args, **fields: events.append((args, fields)))
        monkeypatch.setattr(module, 'monotonic', lambda: now[0])
    counters = {'calls': 1, 'total_us': 5, 'max_us': 5}
    interaction = dict(version=1, observed_monotonic_us=100, hud_options=0, active_mask=0,
        dropped=0, latest_sequence=0, stages={'hud': {'calls': 1, 'active': 0, 'faults': 0}})
    costs = dict(version=1, snapshot_pulse=counters, snapshot_read=counters,
                 snapshot_diagnostics=payload(), hud_interaction=interaction)
    observer = transport.RuntimeCostLog()
    observer.observe({'native_status': {'runtimePerformance': costs}})
    assert events and all(args[0] == 'DEBUG' for args, fields in events)
    events.clear()
    interaction['stages']['hud']['faults'] = 1
    now[0] = 2
    observer.observe({'native_status': {'runtimePerformance': costs}})
    assert any(args[0] == 'WARNING' and args[1] == 'native_sync.hud_interaction' for args, fields in events)


def test_long_native_callback_keeps_one_compact_rate_limited_warning(monkeypatch):
    from src.integrations.native_core import native_snapshot_timing as snapshot
    from tests.native_session.test_native_snapshot_timing import payload
    events, now = [], [0.0]
    monkeypatch.setattr(snapshot, 'log_event', lambda *args, **fields: events.append((args, fields)))
    monkeypatch.setattr(snapshot, 'monotonic', lambda: now[0])
    value = payload()
    value['recent'][0]['total_us'] = 200_000
    observer = snapshot.SnapshotTimingLog()
    observer.observe(value)
    warnings = [(args, fields) for args, fields in events if args[0] == 'WARNING']
    assert len(warnings) == 1 and warnings[0][1]['max_duration_ms'] == 200
    assert 'stages' not in warnings[0][1]
    now[0] = 30
    observer.observe(value)
    assert sum(args[0] == 'WARNING' for args, fields in events) == 1
