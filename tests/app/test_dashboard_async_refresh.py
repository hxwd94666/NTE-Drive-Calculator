# 验证工作台隐藏查询抑制、版本合并、主线程应用和账号失效。
from __future__ import annotations

import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.ui.controllers.dashboard_controller import DashboardController, DashboardDependencies


class DashboardAsyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.identity = DashboardDependencies(1, Path("fixture/user"), Path("fixture/static"))
        self.applied = []
        self.errors = []
        self.loading = []
        self.controller = DashboardController(
            dependencies=lambda: self.identity, apply=self.applied.append,
            failed=self.errors.append, loading=lambda: self.loading.append(1),
            parent=self.owner, merge_ms=35,
        )
        self.mock = patch("src.ui.controllers.dashboard_controller.DashboardService")
        self.service = self.mock.start()
        self.service.return_value.load.return_value = {"count": 1}

    def wait(self, predicate):
        until = time.monotonic() + 3
        while not predicate() and time.monotonic() < until:
            QTest.qWait(5)
        self.assertTrue(predicate())

    def tearDown(self):
        self.controller.close()
        self.wait(lambda: not self.controller._reads.is_running())
        self.mock.stop()
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def test_hidden_notifications_query_once_on_return(self):
        for i in range(30):
            self.controller.refresh(version=i)
        QTest.qWait(80)
        self.service.assert_not_called()
        self.controller.set_visible(True)
        self.wait(lambda: len(self.applied) == 1)
        self.assertEqual(1, self.service.return_value.load.call_count)

    def test_same_sync_version_is_not_requeried(self):
        self.controller.set_visible(True)
        self.controller.refresh(version=("run", 1, 2))
        self.wait(lambda: len(self.applied) == 1)
        for _ in range(30):
            self.controller.refresh(version=("run", 1, 2))
        QTest.qWait(90)
        self.assertEqual(1, self.service.return_value.load.call_count)

    def test_burst_during_active_read_only_applies_latest(self):
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def read():
            calls.append(time.monotonic())
            entered.set()
            release.wait(2)
            return {"count": len(calls)}

        self.service.return_value.load.side_effect = read
        self.controller.set_visible(True)
        self.wait(entered.is_set)
        for i in range(30):
            self.controller.refresh(version=i)
        release.set()
        self.wait(lambda: len(self.applied) == 1)
        self.assertEqual([{"count": 2}], self.applied)
        self.assertEqual(2, len(calls))
        self.assertGreaterEqual(calls[1] - calls[0], 0.025)

    def test_account_change_drops_old_result_and_reads_new_path(self):
        release = threading.Event()
        entered = threading.Event()

        def old_read():
            entered.set()
            release.wait(2)
            return {"count": 1}

        self.service.return_value.load.side_effect = old_read
        self.controller.set_visible(True)
        self.wait(entered.is_set)
        self.identity = DashboardDependencies(2, Path("fixture/other"), Path("fixture/static"))
        self.controller.refresh()
        self.service.return_value.load.side_effect = lambda: {"count": 2}
        release.set()
        self.wait(lambda: len(self.applied) == 1)
        self.assertEqual([{"count": 2}], self.applied)
        self.assertEqual(Path("fixture/other"), self.service.call_args.args[0])

    def test_failure_does_not_retry_forever_and_manual_retry_recovers(self):
        self.service.return_value.load.side_effect = RuntimeError("busy")
        self.controller.set_visible(True)
        self.wait(lambda: len(self.errors) == 1)
        QTest.qWait(120)
        self.assertEqual(1, self.service.return_value.load.call_count)
        self.service.return_value.load.side_effect = None
        self.controller.retry()
        self.wait(lambda: len(self.applied) == 1)
        self.assertEqual(2, self.service.return_value.load.call_count)

    def test_hide_during_read_discards_result_until_return(self):
        release = threading.Event()
        entered = threading.Event()

        def read():
            entered.set()
            release.wait(2)
            return {"count": 1}

        self.service.return_value.load.side_effect = read
        self.controller.set_visible(True)
        self.wait(entered.is_set)
        self.controller.set_visible(False)
        release.set()
        self.wait(lambda: not self.controller._reads.is_running())
        self.assertEqual([], self.applied)
        self.controller.set_visible(True)
        self.wait(lambda: len(self.applied) == 1)

    def test_load_is_background_but_projection_stays_on_ui_thread(self):
        threads = []
        self.service.return_value.load.side_effect = lambda: (threads.append(QThread.currentThread()), {})[1]
        self.controller._apply = lambda _model: threads.append(QThread.currentThread())
        self.controller.set_visible(True)
        self.wait(lambda: len(threads) == 2)
        self.assertIsNot(threads[0], self.app.thread())
        self.assertIs(threads[1], self.app.thread())

    def test_close_before_scheduled_read_does_no_work(self):
        self.controller.set_visible(True)
        self.controller.close()
        QTest.qWait(60)
        self.service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
