# 验证战报抓包协商等待、启动超时、代次隔离和可取消等待不保存旧战报。
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from src.services.battle_capture_start import BattleCaptureStartError, start_battle_capture
from tests.test_battle_capture_axis_service import _Core, _Writer, _summary_payload, _wait_until


def payload(status, operation="current", **extra):
    return {"capture_status": status, "capture_operation_id": operation, **extra}


class FakeEvent:
    def __init__(self, cancel_after=None):
        self.cancel_after = cancel_after
        self.waits = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, _seconds):
        self.waits += 1
        self.stopped = self.cancel_after is not None and self.waits >= self.cancel_after
        return self.stopped


class PacketBattleWaitTests(TestCase):
    def run_start(self, statuses, *, source="packet", event=None, times=None):
        core = SimpleNamespace(
            hello_result={"capabilities": ["capture_wait_v1"]},
            start_capture=Mock(return_value=statuses[0]),
            status=Mock(side_effect=statuses[1:]), stop_capture=Mock(return_value={}),
        )
        messages = []
        with patch("src.services.battle_capture_start.time.monotonic", side_effect=times or [0] * 30):
            result = start_battle_capture(
                core, source=source, require_start=lambda: None, stop_event=event or FakeEvent(),
                on_wait=messages.append, stop_timeout_seconds=1,
            )
        return core, result, messages

    def test_normal_wait_exceeds_initialization_deadline_without_fake_ready(self):
        core, ready, messages = self.run_start(
            [payload("waiting_game"), payload("waiting_network"), payload("running")],
            times=[0, 0, 40, 80],
        )
        self.assertTrue(ready)
        self.assertEqual(2, len(messages))
        self.assertTrue(all("尚未就绪" in message for message in messages))
        self.assertTrue(core.start_capture.call_args.kwargs["wait_for_game"])
        core.stop_capture.assert_not_called()

    def test_idle_and_starting_have_bounded_initialization(self):
        for status in ("idle", "starting"):
            with self.subTest(status=status), self.assertRaises(BattleCaptureStartError) as caught:
                self.run_start([payload(status), payload(status)], times=[0, 0, 16])
            self.assertEqual("CAPTURE_START_TIMEOUT", caught.exception.domain_code)

    def test_error_code_is_preserved(self):
        with self.assertRaises(BattleCaptureStartError) as caught:
            self.run_start([payload("failed", capture_error_code="NPCAP_NOT_FOUND")])
        self.assertEqual("NPCAP_NOT_FOUND", caught.exception.domain_code)

    def test_different_operation_cannot_publish_running(self):
        with self.assertRaises(BattleCaptureStartError) as caught:
            self.run_start([payload("waiting_game"), payload("running", operation="old")])
        self.assertEqual("CAPTURE_OPERATION_CHANGED", caught.exception.domain_code)

    def test_wait_cancel_stops_core_without_claiming_ready(self):
        core, ready, _ = self.run_start([payload("waiting_game")], event=FakeEvent(cancel_after=1))
        self.assertFalse(ready)
        core.stop_capture.assert_called_once()

    def test_stopped_during_wait_does_not_claim_ready(self):
        core, ready, _ = self.run_start([payload("waiting_game"), payload("stopped")])
        self.assertFalse(ready)
        core.stop_capture.assert_called_once()

    def test_native_never_requests_packet_wait(self):
        core, ready, _ = self.run_start([{"started": True}], source="native")
        self.assertTrue(ready)
        self.assertNotIn("wait_for_game", core.start_capture.call_args.kwargs)
        core.status.assert_not_called()

    def test_wait_requires_a_start_operation_identity(self):
        with self.assertRaises(BattleCaptureStartError) as caught:
            self.run_start([{"capture_status": "running"}])
        self.assertEqual("CAPTURE_OPERATION_ID_MISSING", caught.exception.domain_code)


class WaitingPacketCore(_Core):
    def __init__(self):
        super().__init__()
        self.hello_result["capabilities"] = ["capture_wait_v1"]
        self.requested = threading.Event()
        self.capture_state = "waiting_game"
        self.stop_calls = 0

    def start_capture(self, **kwargs):
        self.capture_params = kwargs
        for callback in tuple(self.handlers):
            callback({"params": {**_summary_payload(), "sequence": 99}})
        self.requested.set()
        return payload(self.capture_state)

    def status(self):
        return payload(self.capture_state)

    def stop_capture(self):
        self.stop_calls += 1
        return super().stop_capture()


class PacketBattleWaitServiceTests(TestCase):
    def service(self):
        core, writer = WaitingPacketCore(), _Writer()
        service = BattleCaptureService(
            client_factory=lambda: core, summary_writer=writer, required_source="packet",
            operation_guard=lambda _: None, operation_context=OperationContext.create("battle_report"),
        )
        self.addCleanup(service.close, timeout=2)
        return core, writer, service

    def test_cancel_wait_never_reads_or_saves_old_summary(self):
        core, writer, service = self.service()
        service.start()
        self.assertTrue(core.requested.wait(1))
        self.assertTrue(_wait_until(lambda: "等待启动游戏" in service.state.message))
        self.assertEqual("starting", service.state.phase)
        self.assertIsNone(service.state.summary)
        service.request_stop()
        service.close(timeout=2)
        self.assertEqual(1, core.stop_calls)
        self.assertEqual(0, core.record_requests)
        self.assertEqual("skipped_empty", service.state.persistence_status)
        self.assertIsNone(writer.record)
        self.assertTrue(writer.discarded)

    def test_same_operation_running_releases_wait_and_publishes_ready(self):
        core, _, service = self.service()
        service.start()
        self.assertTrue(core.requested.wait(1))
        self.assertNotEqual("running", service.state.phase)
        core.capture_state = "running"
        self.assertTrue(_wait_until(lambda: service.state.phase == "running"))
        self.assertTrue(core.capture_params["wait_for_game"])

    def test_failed_wait_exposes_machine_error_in_service_state(self):
        core, writer, service = self.service()
        core.status = lambda: payload("failed", capture_error_code="GAME_PATH_INVALID")
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual("GAME_PATH_INVALID", service.state.error_code)
        self.assertEqual(1, core.stop_calls)
        self.assertIsNone(writer.record)
