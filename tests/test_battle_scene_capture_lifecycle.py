# 验证明确场景分场、无伤害丢弃及使用同一原生客户端等待场景就绪的取消边界。
import threading
import unittest

from src.integrations.nte_core_protocol import NteCoreRpcError
from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from tests.test_battle_capture_axis_service import _Core, _Writer, _wait_until
from tests.test_battle_native_capture_lifecycle import _NativeCore


class _WaitingCore(_NativeCore):
    def __init__(self, *, failures=100, error=None):
        super().__init__(auto_end=False)
        self.attempts = 0
        self.failures = failures
        self.waiting = threading.Event()
        self.error = error or NteCoreRpcError({'code': -32001, 'message': 'not_ready',
                                              'data': {'reason': 'scene_transition', 'sceneKind': 'unknown'}})
        self.closed = False

    def start_capture(self, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            self.waiting.set()
            raise self.error
        return super().start_capture(**kwargs)

    def close(self):
        self.closed = True


class SceneCaptureLifecycleTests(unittest.TestCase):
    def make_service(self, core):
        writer = _Writer()
        service = BattleCaptureService(operation_guard=lambda _: None,
            client_factory=lambda: core, summary_writer=writer,
            operation_context=OperationContext.create('battle_report'),
            required_source='native',
        )
        self.addCleanup(service.close, timeout=2)
        return service, writer

    def test_scene_transition_publishes_machine_reason_before_final_reads_and_saves_damage(self):
        core = _NativeCore()
        core.reason = 'scene_transition'
        service, writer = self.make_service(core)
        events = []
        service.add_state_handler(lambda state: events.append((state, core.record_requests)))
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        stopping = next((state, reads) for state, reads in events if state.phase == 'stopping')
        self.assertEqual('scene_transition', stopping[0].end_reason)
        self.assertEqual(0, stopping[1])
        self.assertEqual('scene_transition', service.state.end_reason)
        self.assertEqual('saved', service.state.persistence_status)
        self.assertNotIn('提前结束', service.state.message)
        self.assertIsNotNone(writer.record)

    def test_scene_transition_with_incoming_only_is_not_saved(self):
        class IncomingOnly(_NativeCore):
            def get_battle_record(self, **kwargs):
                record = super().get_battle_record(**kwargs)
                record['summary'].update(total_damage=0, total_hits=3, total_damage_taken=100)
                return record

        core = IncomingOnly()
        core.reason = 'scene_transition'
        service, writer = self.make_service(core)
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual('skipped_empty', service.state.persistence_status)
        self.assertEqual('scene_transition', service.state.end_reason)
        self.assertIsNone(writer.record)
        self.assertTrue(writer.discarded)

    def test_source_changed_keeps_distinct_hard_end_reason(self):
        service, _ = self.make_service(_NativeCore())
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual('source_changed', service.state.end_reason)
        self.assertIn('提前结束', service.state.message)

    def test_packet_scene_intent_discards_incoming_only_without_fabricating_core_reason(self):
        class IncomingPacket(_Core):
            def get_battle_record(self, **kwargs):
                record = super().get_battle_record(**kwargs)
                record['summary'].update(total_damage=0, total_hits=3, total_damage_taken=100)
                return record

        for scene in (False, True):
            with self.subTest(scene=scene):
                core, writer = IncomingPacket(), _Writer()
                service = BattleCaptureService(operation_guard=lambda _: None,
                    client_factory=lambda: core, summary_writer=writer,
                    operation_context=OperationContext.create('battle_report'), required_source='packet',
                )
                service.start()
                self.addCleanup(service.close, timeout=2)
                self.assertTrue(_wait_until(lambda: core.record_requests > 0))
                service.request_stop(end_reason='scene_transition' if scene else None)
                service.request_stop()  # A subsequent manual stop must not erase the scene boundary.
                self.assertTrue(_wait_until(lambda: not service.is_running))
                self.assertEqual('skipped_empty' if scene else 'saved', service.state.persistence_status)
                self.assertEqual('scene_transition' if scene else None, service.state.end_reason)
                self.assertEqual(scene, writer.discarded)
                self.assertNotIn('native_capture', core.get_battle_record())
                if not scene:
                    self.assertNotIn('native_capture', writer.record)

    def test_known_not_ready_retries_same_client_until_ready(self):
        core = _WaitingCore(failures=2)
        service, writer = self.make_service(core)
        service.start()
        self.assertTrue(core.waiting.wait(1))
        self.assertTrue(_wait_until(lambda: '等待新场景' in service.state.message))
        self.assertEqual('starting', service.state.phase)
        self.assertTrue(service.state.running)
        self.assertTrue(core.capture_started.wait(2))
        self.assertEqual(3, core.attempts)
        service.request_stop()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual('saved', service.state.persistence_status)
        self.assertIsNotNone(writer.record)

    def test_initial_native_start_also_waits_for_declared_transient_reason(self):
        core = _WaitingCore(failures=1)
        service, writer = self.make_service(core)
        service.start()
        self.assertTrue(core.capture_started.wait(2))
        self.assertEqual(2, core.attempts)
        service.request_stop()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual('saved', service.state.persistence_status)
        self.assertIsNotNone(writer.record)

    def test_stop_discard_and_close_cancel_wait_without_stop_rpc_or_final_queries(self):
        for method in ('request_stop', 'request_discard', 'close'):
            with self.subTest(method=method):
                core = _WaitingCore()
                service, writer = self.make_service(core)
                service.start()
                self.assertTrue(core.waiting.wait(1))
                getattr(service, method)()
                self.assertTrue(_wait_until(lambda: not service.is_running))
                self.assertEqual('stopped', service.state.phase)
                self.assertEqual('discarded_restart' if method == 'request_discard' else 'skipped_empty',
                                 service.state.persistence_status)
                self.assertEqual(0, core.stop_calls)
                self.assertEqual(0, core.record_requests)
                self.assertEqual([], core.axis_requests)
                self.assertTrue(core.closed)
                self.assertTrue(writer.discarded)
                self.assertIsNone(writer.record)

    def test_missing_reason_and_other_errors_are_not_retried(self):
        for error in (
            NteCoreRpcError({'code': -32001, 'message': 'not_ready'}),
            NteCoreRpcError({'code': -32603, 'message': 'not_ready'}),
            NteCoreRpcError({'code': -32001, 'message': 'permission_denied'}),
            RuntimeError('not_ready'),
        ):
            with self.subTest(error=type(error).__name__):
                core = _WaitingCore(error=error)
                service, writer = self.make_service(core)
                service.start()
                self.assertTrue(_wait_until(lambda: not service.is_running))
                self.assertEqual('error', service.state.phase)
                self.assertEqual(1, core.attempts)
                self.assertEqual(0, core.record_requests)
                self.assertTrue(writer.discarded)


if __name__ == '__main__':
    unittest.main()
