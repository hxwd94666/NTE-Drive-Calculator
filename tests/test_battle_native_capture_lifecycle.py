# 验证原生采集提前终止后自动收尾、保留战报，并继续暴露真实停止错误。
from __future__ import annotations

import unittest
from typing import Any

from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from tests.test_battle_capture_axis_service import _Core, _Writer, _summary_payload, _wait_until


class _NativeCore(_Core):
    native_capture = True

    def __init__(self, *, auto_end: bool = True, empty: bool = False) -> None:
        super().__init__()
        self.event_handlers: dict[str | None, list[Any]] = {}
        self.auto_end = auto_end
        self.empty = empty
        self.reason = "source_changed"
        self.stop_calls = 0
        self.in_callback = False

    def observe_battle_scopes(self, record, *, final=False, stop_requested=None):
        return None

    def add_event_handler(self, method, handler):
        self.event_handlers.setdefault(method, []).append(handler)

    def remove_event_handler(self, method, handler):
        self.event_handlers[method].remove(handler)

    def emit(self, method, params):
        self.in_callback = True
        try:
            for handler in tuple(self.event_handlers.get(method, ())):
                handler({"method": method, "params": params})
        finally:
            self.in_callback = False

    def end(self, reason):
        self.finalized = True
        self.reason = reason
        summary = _summary_payload()
        if self.empty:
            summary.update(total_damage=0, total_hits=0, total_dps=0, characters=[])
        self.emit("event.battle.summary", {**summary, "sequence": 2})
        self.emit("event.capture.status", {
            "operation_id": "capture-1", "profile": "combat", "status": "stopped",
            "reason": reason, "transport_complete": True, "source_complete": False,
            "source_coverage": "unknown", "integrity_reasons": ["source_coverage_unverified"],
        })

    def start_capture(self, **kwargs):
        # Reproduce ended arriving before the caller returns from start_capture.
        if self.auto_end:
            self.end(self.reason)
        return super().start_capture(**kwargs)

    def stop_capture(self):
        assert not self.in_callback, "RPC must not run inside event callback"
        self.stop_calls += 1
        if not self.finalized:
            self.end("user_stop")
        return {"stopped": True, "already_stopped": True, "reason": self.reason}

    def get_battle_record(self, **kwargs):
        assert not self.in_callback
        record = dict(super().get_battle_record(**kwargs))
        record.update(source="native_dll", native_capture={
            "reason": self.reason, "complete": False, "sourceCoverage": "unknown",
            "transportComplete": True, "sourceChangeTrigger": "player_state_replication",
        })
        if self.empty:
            record["summary"].update(total_damage=0, total_hits=0, total_dps=0, characters=[])
        return record


class BattleNativeCaptureLifecycleTests(unittest.TestCase):
    def test_comparison_requires_the_selected_source_without_fallback(self):
        for core, required in ((_Core(), "native"), (_NativeCore(), "packet")):
            with self.subTest(required=required):
                writer = _Writer()
                service = BattleCaptureService(operation_guard=lambda _: None,
                    client_factory=lambda: core, summary_writer=writer,
                    operation_context=OperationContext.create("battle_report"),
                    required_source=required,
                )
                service.start()
                self.assertTrue(_wait_until(lambda: not service.is_running))
                self.assertEqual("error", service.state.phase)
                self.assertIn("来源不符", service.state.error)
                self.assertFalse(core.capture_started.is_set())
                self.assertTrue(writer.discarded)

    def service(self, core):
        writer = _Writer()
        service = BattleCaptureService(operation_guard=lambda _: None, required_source="native",
            client_factory=lambda: core, summary_writer=writer,
            operation_context=OperationContext.create("battle_report"),
        )
        service.start()
        self.addCleanup(service.close, timeout=2)
        self.assertTrue(core.capture_started.wait(1))
        return service, writer

    def test_spontaneous_end_before_first_poll_preserves_record_and_warns(self):
        core = _NativeCore()
        service, writer = self.service(core)
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual("saved", service.state.persistence_status)
        self.assertEqual("stopped", service.state.phase)
        self.assertIn("游戏采集上下文已失效", service.state.message)
        self.assertIn("可能缺少逐击", service.state.message)
        self.assertEqual(1, core.stop_calls)
        self.assertFalse(writer.discarded)
        self.assertEqual(1, len(writer.final_pages))
        self.assertFalse(writer.record["native_capture"]["complete"])
        self.assertEqual("source_changed", writer.record["native_capture"]["reason"])
        self.assertTrue(all(not handlers for handlers in core.event_handlers.values()))

    def test_empty_spontaneous_end_reports_reason_without_saving(self):
        service, writer = self.service(_NativeCore(empty=True))
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual("skipped_empty", service.state.persistence_status)
        self.assertIn("游戏采集上下文已失效", service.state.message)
        self.assertIn("玩家状态复制通知", service.state.message)
        self.assertTrue(writer.discarded)
        self.assertIsNone(writer.record)

    def test_user_stop_keeps_ordinary_saved_message(self):
        core = _NativeCore(auto_end=False)
        service, writer = self.service(core)
        service.request_stop()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual("saved", service.state.persistence_status)
        self.assertNotIn("提前结束", service.state.message)
        self.assertFalse(writer.discarded)

    def test_unvalidated_or_inventory_status_does_not_stop_combat(self):
        core = _NativeCore(auto_end=False)
        service, _writer = self.service(core)
        for params in (
            {"profile": "inventory", "transport_complete": True},
            {"profile": "combat", "transport_complete": False},
        ):
            core.emit("event.capture.status", {
                "operation_id": "capture-1", "status": "stopped", **params,
            })
        self.assertTrue(service.is_running)
        self.assertEqual(0, core.stop_calls)
        service.request_stop()
        self.assertTrue(_wait_until(lambda: not service.is_running))

    def test_stop_failure_is_not_swallowed_as_already_ended(self):
        class FailedStop(_NativeCore):
            def stop_capture(self):
                raise RuntimeError("CAPTURE_NOT_RUNNING")

        service, writer = self.service(FailedStop(auto_end=False))
        service.request_stop()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual("error", service.state.phase)
        self.assertIn("CAPTURE_NOT_RUNNING", service.state.error)
        self.assertTrue(writer.discarded)

    def test_stop_response_precedes_delayed_events_reads_authoritative_record(self):
        class DelayedEvents(_NativeCore):
            def emit(self, method, params):
                # RPC responses run independently of the client's event dispatcher.
                pass

            def stop_capture(self):
                self.stop_calls += 1
                self.finalized = True
                return {"stopped": True, "reason": self.reason}

        for empty in (False, True):
            with self.subTest(empty=empty):
                core = DelayedEvents(auto_end=False, empty=empty)
                service, writer = self.service(core)
                service.request_stop()
                self.assertTrue(_wait_until(lambda: not service.is_running))
                self.assertGreater(core.record_requests, 0)
                self.assertEqual("skipped_empty" if empty else "saved", service.state.persistence_status)
                self.assertIn("游戏采集上下文已失效", service.state.message)
                self.assertEqual(empty, writer.discarded)


if __name__ == "__main__":
    unittest.main()


def test_ui_stop_during_live_snapshot_still_finalizes_and_saves():
    from concurrent.futures import CancelledError
    from unittest.mock import patch
    from src.services.native_game_session import NativeGameSession
    core = _NativeCore(auto_end=False)
    core.hello_result['capabilities'] = ['native_battle_scope_snapshot_v1']
    session = NativeGameSession(lambda: core, lambda _: None)
    writer = _Writer()
    ui_stopped = False
    def check_start():
        if ui_stopped:
            raise CancelledError('本次战报启动已取消。')
    def factory():
        lease = session.battle_client(check=check_start)
        def observe(record, *, final=False, stop_requested=None):
            if not final:
                lease._read_battle_snapshot(stop_requested)
        lease.observe_battle_scopes = observe
        return lease
    service = BattleCaptureService(client_factory=factory, summary_writer=writer, operation_guard=lambda _: None,
                                   required_source='native', operation_context=OperationContext.create('battle_report'))
    def snapshot(client, check, **kwargs):
        nonlocal ui_stopped
        ui_stopped = True
        # UI stop intent can precede the service stop signal during an in-flight read.
        check()
        service.request_stop()
        check()
    with patch('src.integrations.native_battle_snapshot.freeze_native_battle_snapshot', snapshot):
        try:
            service.start()
            assert _wait_until(lambda: not service.is_running)
            assert ui_stopped
            assert service.state.persistence_status == 'saved', service.state.error
            assert writer.record is not None and not writer.discarded
            assert len(writer.final_pages) == 1
        finally:
            service.close(timeout=2)
            session.close()
