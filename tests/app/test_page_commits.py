# 验证后台提交不合并不重放、终态确认和关闭期间的结果保留。
from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.app.page_tasks import PageCommitLane, close_page_tasks


class PageCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.lane = PageCommitLane(self.owner)
        self.values, self.errors, self.application_errors = [], [], []

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate())

    def tearDown(self):
        self.wait(lambda: not self.lane.is_running())
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def submit(self, work, committed=None):
        return self.lane.submit(work, committed or self.values.append, self.errors.append, self.application_errors.append)

    def test_duplicate_commit_is_rejected_not_queued(self):
        release = threading.Event()
        self.assertTrue(self.submit(lambda: (release.wait(2), 1)[1]))
        for _ in range(30):
            self.assertFalse(self.submit(lambda: 2))
        release.set()
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual([1], self.values)

    def test_commit_is_background_and_acknowledgement_is_terminal_ui_state(self):
        threads, busy = [], []
        self.submit(lambda: (threads.append(QThread.currentThread()), 1)[1],
                    lambda _v: (threads.append(QThread.currentThread()), busy.append(self.lane.is_running())))
        self.wait(lambda: len(threads) == 2)
        self.assertIsNot(threads[0], self.app.thread())
        self.assertIs(threads[1], self.app.thread())
        self.assertEqual([False], busy)

    def test_close_waits_without_cancelling_an_accepted_commit(self):
        release = threading.Event()
        self.submit(lambda: (release.wait(2), 7)[1])
        self.assertTrue(close_page_tasks(self.owner))
        release.set()
        self.wait(lambda: self.values == [7])
        self.assertFalse(close_page_tasks(self.owner))

    def test_commit_error_preserves_transaction_failure_and_does_not_replay(self):
        calls = []

        def fail():
            calls.append(1)
            raise RuntimeError("fixture failure")

        self.submit(fail)
        self.wait(lambda: len(self.errors) == 1)
        QTest.qWait(60)
        self.assertEqual([1], calls)
        self.assertEqual([], self.values)

    def test_projection_error_is_not_reported_as_commit_failure(self):
        states = []
        self.lane.settled.connect(states.append)

        def apply(_value):
            raise RuntimeError("view failed")

        self.submit(lambda: 1, apply)
        self.wait(lambda: len(states) == 1)
        self.assertEqual([], self.errors)
        self.assertEqual(["view failed"], self.application_errors)
        self.assertEqual([False], states)

    def test_success_allows_a_later_explicit_commit(self):
        self.submit(lambda: 1)
        self.wait(lambda: not self.lane.is_running())
        self.submit(lambda: 2)
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual([1, 2], self.values)


if __name__ == "__main__":
    unittest.main()
