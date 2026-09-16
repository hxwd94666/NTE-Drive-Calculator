# 验证战报来源和诊断授权在实际启动前重查，并保留撤权后的收尾保存。
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from tests.test_battle_capture_axis_service import _Core, _Writer, _wait_until
from tests.test_battle_native_capture_lifecycle import _NativeCore
from tests.test_battle_scene_capture_lifecycle import _WaitingCore


class BattleCaptureOperationGuardTests(TestCase):
    def service(self, factory, *, guard=None, source="native", writer=None, **kwargs):
        service = BattleCaptureService(
            client_factory=factory, operation_guard=guard, required_source=source,
            operation_context=OperationContext.create("battle_report"), summary_writer=writer,
            **kwargs,
        )
        self.addCleanup(service.close, timeout=2)
        return service

    def test_missing_guard_or_source_denies_before_factory(self):
        for source, guard, error in (("native", None, PermissionError), (None, lambda _: None, ValueError)):
            factory = Mock()
            service = self.service(factory, guard=guard, source=source)
            with self.subTest(source=source), self.assertRaises(error):
                service.start()
            factory.assert_not_called()

    def test_revocation_during_staging_denies_factory(self):
        allowed = [True]

        def guard(_):
            if not allowed[0]:
                raise PermissionError("revoked")

        writer = _Writer()
        writer.begin_capture = lambda **_: allowed.__setitem__(0, False)
        factory = Mock()
        service = self.service(factory, guard=guard, writer=writer)
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        factory.assert_not_called()
        self.assertEqual("error", service.state.phase)
        self.assertTrue(writer.discarded)

    def test_revocation_in_factory_denies_client_start(self):
        allowed = [True]
        core = _NativeCore()
        core.start = Mock()

        def guard(_):
            if not allowed[0]:
                raise PermissionError("revoked")

        def factory():
            allowed[0] = False
            return core

        service = self.service(factory, guard=guard)
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        core.start.assert_not_called()
        self.assertFalse(core.capture_started.is_set())

    def test_waiting_retry_rechecks_revoked_native_permission(self):
        allowed = [True]

        def guard(_):
            if not allowed[0]:
                raise PermissionError("revoked")

        core = _WaitingCore()
        service = self.service(lambda: core, guard=guard)
        service.start()
        self.assertTrue(core.waiting.wait(1))
        allowed[0] = False
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual(1, core.attempts)
        self.assertEqual("error", service.state.phase)

    def test_diagnostics_denied_before_raw_directory_or_client(self):
        def guard(capability):
            if capability == "diagnostics":
                raise PermissionError("diagnostics disabled")

        with TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            factory = Mock()
            service = self.service(factory, guard=guard, raw_capture_enabled=True, raw_capture_directory=raw)
            with self.assertRaises(PermissionError):
                service.start()
            factory.assert_not_called()
            self.assertFalse(raw.exists())

    def test_client_start_permission_does_not_authorize_later_capture_start(self):
        allowed = [True]
        core = _Core()
        core.start = lambda: allowed.__setitem__(0, False)

        def guard(capability):
            self.assertEqual("packet_capture", capability)
            if not allowed[0]:
                raise PermissionError("revoked after process start")

        service = self.service(lambda: core, guard=guard, source="packet")
        service.start()
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertFalse(core.capture_started.is_set())

    def test_revoke_after_start_still_stops_and_saves_accepted_native_data(self):
        allowed = [True]

        def guard(_):
            if not allowed[0]:
                raise PermissionError("revoked")

        core, writer = _NativeCore(auto_end=False), _Writer()
        service = self.service(lambda: core, guard=guard, writer=writer)
        service.start()
        self.assertTrue(core.capture_started.wait(1))
        allowed[0] = False
        service.request_stop()
        service.close(timeout=2)
        self.assertEqual("saved", service.state.persistence_status)
        self.assertIsNotNone(writer.record)
        self.assertEqual(1, core.stop_calls)
