# 验证性能监控需求、来源计数、账号隔离及日志失败，不连接真实游戏。
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NTE_TESTING", "1")

from src.integrations.performance_diagnostics import performance_counters, PerformanceJournal
from src.services.performance_monitor import PerformanceMonitor
from src.services.native_session.session import NativeGameSession

NTE_TEST_TIER = "core"


def status(calls=1, total=1000):
    return {"native_status": {"runtimePerformance": {"version": 1,
            "snapshot_pulse": {"calls": calls, "total_us": total, "max_us": 1000}}}}


def wait_for(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def monitor(self, reader=None):
        monitor = PerformanceMonitor(reader or (lambda: ("session", status())))
        self.addCleanup(monitor.close)
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=False, allowed=True)
        return monitor

    def test_default_does_not_read_or_create_files(self):
        read = Mock()
        monitor = self.monitor(read)
        self.assertFalse(monitor.snapshot()["enabled"])
        read.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_raw_link_stop_and_manual_survives(self):
        monitor = self.monitor()
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=True, allowed=True)
        self.assertTrue(monitor.snapshot()["automatic"])
        monitor.set_enabled(False)
        self.assertTrue(monitor.snapshot()["enabled"])
        self.assertFalse(monitor.snapshot()["overlay"])
        self.assertTrue(monitor.snapshot()["recording_requested"])
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=False, allowed=True)
        monitor.set_enabled(True)
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=True, allowed=True)
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=False, allowed=True)
        self.assertTrue(monitor.snapshot()["enabled"])
        self.assertFalse(monitor.snapshot()["recording_requested"])

    def test_account_change_discards_blocked_read(self):
        entered, release = Event(), Event()
        def read():
            entered.set()
            release.wait(2)
            return "old", status()
        monitor = self.monitor(read)
        monitor.configure(key=("a", 1), log_dir=self.directory / "a", raw=True, allowed=True)
        self.assertTrue(entered.wait(1))
        monitor.stop()
        monitor.configure(key=("b", 2), log_dir=self.directory / "b", raw=False, allowed=True)
        release.set()
        time.sleep(0.05)
        self.assertFalse(monitor.snapshot()["enabled"])
        self.assertEqual(monitor.snapshot()["rows"], {})
        self.assertEqual(list(self.directory.rglob("*.jsonl")), [])

    def test_window_delta_and_missing_samples(self):
        values = iter([status(1, 1000), status(3, 5000), status(3, 5000), status(1, 1000)])
        monitor = self.monitor(lambda: ("one", next(values)))
        revision = monitor._revision
        first = monitor._sample(revision)
        self.assertIsNone(first["rows"]["snapshot_pulse"]["mean_ms"])
        self.assertEqual(monitor._sample(revision)["rows"]["snapshot_pulse"]["mean_ms"], 2)
        self.assertIsNone(monitor._sample(revision)["rows"]["snapshot_pulse"]["mean_ms"])
        self.assertIsNone(monitor._sample(revision)["rows"]["snapshot_pulse"]["mean_ms"])

    def test_disabled_policy_does_not_read(self):
        read = Mock()
        monitor = self.monitor(read)
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=True, allowed=False)
        time.sleep(0.03)
        read.assert_not_called()
        self.assertEqual(monitor.snapshot()["state"], "unavailable")

    def test_log_failure_keeps_live_observation(self):
        monitor = self.monitor()
        with patch("src.services.performance_monitor.PerformanceJournal", side_effect=OSError):
            monitor.configure(key=("a", 1), log_dir=self.directory, raw=True, allowed=True)
            wait_for(lambda: monitor.snapshot()["log_error"])
            self.assertTrue(monitor.snapshot()["enabled"])
            self.assertEqual(monitor.snapshot()["state"], "collecting")

    def test_journal_no_raw_payload_and_budget(self):
        journal = PerformanceJournal(self.directory, byte_limit=2048)
        journal.write({"kind": "sample", "rows": performance_counters(status())})
        with self.assertRaises(OSError):
            journal.write({"padding": "x" * 2048})
        journal.close()
        rows = [json.loads(s) for s in journal.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["schema"], "calc.performance/2")
        self.assertFalse(rows[0]["cost_complete"])
        self.assertEqual(rows[-1]["kind"], "session_end")

    def test_counter_whitelist_rejects_invalid_values(self):
        value = status()
        value["native_status"]["runtimePerformance"]["payload"] = "private"
        self.assertEqual(set(performance_counters(value)), {"snapshot_pulse"})
        value["native_status"]["runtimePerformance"]["snapshot_pulse"]["calls"] = True
        self.assertEqual(performance_counters(value), {})
        value["native_status"]["runtimePerformance"]["version"] = True
        self.assertEqual(performance_counters(value), {})

    def test_user_diagnostics_are_separate_and_optional(self):
        value = status()
        costs = value["native_status"]["runtimePerformance"]
        outer = {"calls": 2, "total_us": 8000, "max_us": 5000}
        inner = {"calls": 4, "total_us": 4000, "max_us": 2000}
        costs["snapshot_diagnostics"] = {"version": 1, "stages": {"reader_step": outer}}
        costs["user_snapshot_diagnostics"] = {"version": 1, "stages": {
            "reader_step": inner, "skill_query": inner, "payload": "private"}}
        rows = performance_counters(value)
        self.assertEqual(rows["snapshot.reader_step"], outer)
        self.assertEqual(rows["user.snapshot.reader_step"], inner)
        self.assertEqual(rows["user.snapshot.skill_query"], inner)
        self.assertNotIn("user.snapshot.payload", rows)
        costs["user_snapshot_diagnostics"] = None
        self.assertFalse(any(k.startswith("user.") for k in performance_counters(value)))

    def test_stale_display_does_not_present_old_values_as_current(self):
        monitor = self.monitor()
        monitor.set_enabled(True)
        with monitor._lock:
            monitor._latest = {"state": "collecting", "sampled_at": time.monotonic()-4,
                               "rows": {"snapshot_pulse": {"mean_ms": 12}}}
            self.assertEqual(monitor.snapshot()["rows"], {})
            self.assertEqual(monitor.snapshot()["state"], "waiting")

    def test_linked_trace_ends_and_manual_view_does_not_write(self):
        monitor = self.monitor()
        monitor.set_enabled(True)
        wait_for(lambda: monitor.snapshot()["state"] == "collecting")
        self.assertEqual(list(self.directory.rglob("*.jsonl")), [])
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=True, allowed=True)
        wait_for(lambda: bool(monitor.snapshot()["log_path"]))
        path = Path(monitor.snapshot()["log_path"])
        monitor.configure(key=("a", 1), log_dir=self.directory, raw=False, allowed=True)
        wait_for(lambda: not monitor.snapshot()["log_path"])
        wait_for(lambda: '"session_end"' in path.read_text(encoding="utf-8"))
        self.assertTrue(monitor.snapshot()["enabled"])

    def test_native_reader_never_starts_or_refreshes(self):
        factory = Mock()
        session = NativeGameSession(factory, lambda cap: None)
        with self.assertRaises(LookupError):
            session.read_performance_status()
        factory.assert_not_called()
        client = Mock(is_running=True)
        client.call.return_value = status()
        session._client = client
        self.assertEqual(session.read_performance_status()[1], status())
        client.call.assert_called_once_with("core.status", timeout=2.0)


class PerformanceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_details_close_preserves_monitor_and_themes(self):
        from PySide6.QtCore import QObject, Signal
        from src.app.theme import apply_app_theme
        from src.features.settings.performance_card import PerformanceCard
        class Controller(QObject):
            changed = Signal()
            log_dir = Path(tempfile.gettempdir())
            def __init__(self):
                super().__init__()
                self.enabled = False
            def set_enabled(self, value):
                self.enabled = value
                self.changed.emit()
            def start_trace(self, services):
                pass
            def stop_trace(self):
                pass
            def snapshot(self):
                return {"enabled": self.enabled, "overlay": self.enabled, "state": "waiting" if self.enabled else "off",
                        "automatic": False, "rows": {}, "history": [], "log_path": "", "log_error": None}
        controller = Controller()
        card = PerformanceCard(controller)
        for theme in ("dark", "black", "light"):
            apply_app_theme(self.app, theme)
            controller.set_enabled(True)
            card.show_details()
            self.app.processEvents()
            self.assertTrue(card.toggle.isChecked())
            self.assertLessEqual(card.dialog.width(), card.dialog.screen().availableGeometry().width())
            card.dialog.close()
            self.assertTrue(controller.enabled)
        card.close()

    def test_details_keep_preference_failure_visible(self):
        from PySide6.QtCore import QObject, Signal
        from src.features.settings.performance_card import PerformanceCard

        class Controller(QObject):
            changed = Signal()
            log_dir = Path(tempfile.gettempdir())

            def set_enabled(self, value):
                pass

            def stop_trace(self):
                pass

            def snapshot(self):
                return {"enabled": False, "overlay": False, "state": "off", "overlay_state": "closing",
                        "automatic": False, "rows": {}, "history": [], "log_path": "", "log_error": None,
                        "preference_error": "性能偏好保存失败"}

        card = PerformanceCard(Controller())
        try:
            card.show_details()
            self.assertIn("性能偏好保存失败", card.dialog.status.text())
        finally:
            if card.dialog is not None:
                card.dialog.close()
            card.close()


if __name__ == "__main__":
    unittest.main()
