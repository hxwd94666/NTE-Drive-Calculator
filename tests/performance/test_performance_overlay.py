# 验证四项性能指标、显示日志独立、组件确认及本机偏好，不操作真实游戏。
import tempfile
import io
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.integrations.performance.preferences import PerformancePreferences
from src.integrations.performance.presentmon import FrameWindow, PresentMon
from src.services.performance.monitor import PerformanceMonitor
from src.services.native_session.session import NativeGameSession

NTE_TEST_TIER = "core"


class OverlayTests(unittest.TestCase):
    def test_native_stop_failure_cannot_delay_frame_teardown(self):
        calls = []
        frames = Mock()
        frames.close.side_effect = lambda: calls.append('frames_closed')
        def control(payload, key):
            calls.append('native_stop')
            self.assertEqual(calls[0], 'frames_closed')
            raise TimeoutError('native pipe unavailable')
        monitor = PerformanceMonitor(lambda: ('a', {}), control=control, frames=frames)
        monitor._quit.set()
        monitor._wake.set()
        monitor._thread.join(timeout=3)
        calls.clear()
        monitor._applied = True
        monitor._disable_overlay()
        self.assertEqual(calls, ['frames_closed', 'native_stop'])

    def test_presentmon_legacy_csv_reaches_all_three_metrics(self):
        # Header from the upstream FrameMetrics1 serializer, not hand-renamed keys.
        header = "Application,ProcessID,SwapChainAddress,Runtime,TimeInSeconds,msBetweenPresents\n"
        body = "".join(f"HTGame.exe,42,0x123,DXGI,{i * .016},16\n" for i in range(1200))
        source = PresentMon(Path("unused"), lambda: 42)
        process = Mock(stdout=io.StringIO(header + body))
        source._process = process
        source._consume(process, 42)
        values = source._window.snapshot(time.monotonic())
        self.assertEqual(values, {"fps_milli": 62500, "frame_us": 16000, "low_milli": 62500})

    def test_presentmon_reports_wrong_csv_schema_and_permission_failure(self):
        source = PresentMon(Path("unused"), lambda: 42)
        process = Mock(stdout=io.StringIO("Application,ProcessID,Unknown\nx,42,1\n"),
                       stderr=io.StringIO("error: failed to start trace session: access denied.\n"))
        source._process = process
        source._consume(process, 42)
        self.assertIn("缺少", source._stream_error)
        source._consume_errors(process)
        self.assertIn("权限不足", source._stream_error)
        self.assertIsNone(source._window.snapshot(time.monotonic())["fps_milli"])

    def test_metrics_same_stream_low_tail_and_staleness(self):
        frames = FrameWindow()
        for index in range(1200):
            frames.add({"TimeInSeconds": str(index * .016), "MsBetweenPresents": "40" if index < 12 else "16",
                        "SwapChainAddress": "game"}, 50)
        # Secondary video/UI stream must not dilute the game's slow frames.
        frames.add({"TimeInSeconds": "19.19", "MsBetweenPresents": "100", "SwapChainAddress": "video"}, 50)
        values = frames.snapshot(50)
        self.assertEqual(values["fps_milli"], 62500)
        self.assertEqual(values["frame_us"], 16000)
        self.assertEqual(values["low_milli"], 25000)
        self.assertTrue(all(value is None for value in frames.snapshot(54).values()))

    def test_unknown_invalid_and_short_window(self):
        frames = FrameWindow()
        for value in ("nan", "inf", "0", "-1", "bad"):
            frames.add({"TimeInSeconds": "0", "MsBetweenPresents": value, "SwapChainAddress": "a"}, 1)
        self.assertIsNone(frames.snapshot(1)["fps_milli"])
        frames.add({"TimeInSeconds": "1", "MsBetweenPresents": "10", "SwapChainAddress": "a"}, 1)
        self.assertEqual(frames.snapshot(1)["fps_milli"], 100000)
        self.assertIsNone(frames.snapshot(1)["low_milli"])

    def test_preferences_default_and_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PerformancePreferences(Path(folder) / "performance.json")
            self.assertFalse(store.load()["overlay"])
            store.save({"overlay": True, "linked": False})
            self.assertEqual(store.load(), {"overlay": True, "linked": False})
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_display_off_does_not_cancel_logging_and_close_stops_provider(self):
        calls = []
        def control(payload, key):
            calls.append(payload.copy())
            return {**payload, "installed": True, "rendered": payload["overlay"], "rejected": False}
        with tempfile.TemporaryDirectory() as folder:
            monitor = PerformanceMonitor(lambda: ("a", {}), control=control)
            try:
                monitor.configure(key="a", log_dir=Path(folder), raw=True, allowed=True)
                monitor.set_enabled(True)
                self.wait(lambda: any(call["overlay"] for call in calls))
                monitor.set_enabled(False)
                self.wait(lambda: calls[-1]["enabled"] and not calls[-1]["overlay"])
                self.assertTrue(monitor.snapshot()["recording_requested"])
                monitor.set_linked(False)
                self.wait(lambda: not calls[-1]["enabled"])
            finally:
                monitor.close()
            self.assertFalse(calls[-1]["enabled"])

    def test_control_old_core_and_stop_without_connection(self):
        factory = Mock()
        session = NativeGameSession(factory=factory, guard=lambda _: None)
        off = {"enabled": False, "overlay": False, "fps_milli": None, "frame_us": None, "low_milli": None}
        self.assertFalse(session.configure_performance(off, None)["enabled"])
        factory.assert_not_called()
        client = Mock(is_running=True, hello_result={"capabilities": []})
        session._client = client
        with self.assertRaises(NotImplementedError):
            session.configure_performance({**off, "enabled": True}, None)
        client.call.assert_not_called()

    @staticmethod
    def wait(predicate):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("performance transition timed out")
