# 验证页面后台任务的线程边界、请求合并和关闭后丢弃旧结果。
"""Behavior tests for bounded asynchronous page work."""

from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.app.page_tasks import PageRequest, PageTaskLane, close_page_tasks


class PageTasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.owner = QWidget()
        self.lane = PageTaskLane(self.owner)
        self.errors: list[str] = []

    def tearDown(self) -> None:
        self.lane.close()
        self.wait(lambda: not self.lane.is_running())
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def wait(self, predicate, timeout: float = 3) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), "task did not reach expected state")

    def test_repeated_same_request_reads_once_and_delivers_to_latest_receiver(self) -> None:
        release = threading.Event()
        reads: list[int] = []
        applied: list[int] = []

        def read():
            release.wait(2)
            reads.append(1)
            return 10

        for index in range(30):
            self.lane.submit(PageRequest("same", read, lambda _value, i=index: applied.append(i), self.errors.append))
        release.set()
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual([1], reads)
        self.assertEqual([29], applied)
        self.assertEqual([], self.errors)

    def test_only_latest_different_request_runs_after_active_finishes(self) -> None:
        release = threading.Event()
        reads: list[str] = []
        applied: list[str] = []

        def first():
            release.wait(2)
            reads.append("first")
            return "first"

        self.lane.submit(PageRequest("first", first, applied.append, self.errors.append))
        for key in ("middle", "latest"):
            self.lane.submit(PageRequest(key, lambda k=key: (reads.append(k), k)[1], applied.append, self.errors.append))
        release.set()
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual(["first", "latest"], reads)
        self.assertEqual(["latest"], applied)

    def test_read_is_background_and_callbacks_are_main_thread(self) -> None:
        threads: list[QThread] = []

        def read():
            threads.append(QThread.currentThread())
            return 1

        self.lane.submit(PageRequest(1, read, lambda _v: threads.append(QThread.currentThread()), self.errors.append))
        self.wait(lambda: not self.lane.is_running())
        self.assertIsNot(self.app.thread(), threads[0])
        self.assertIs(self.app.thread(), threads[1])

    def test_close_invalidates_result_and_reports_unfinished_owner(self) -> None:
        release = threading.Event()
        applied: list[int] = []
        self.lane.submit(PageRequest(1, lambda: (release.wait(2), 1)[1], applied.append, self.errors.append))
        self.assertTrue(close_page_tasks(self.owner))
        self.lane.submit(PageRequest(2, lambda: 2, applied.append, self.errors.append))
        release.set()
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual([], applied)
        self.assertFalse(close_page_tasks(self.owner))

    def test_failure_has_feedback_and_next_request_can_retry(self) -> None:
        def fail():
            raise ValueError("fixture failure")

        self.lane.submit(PageRequest(1, fail, lambda _v: None, self.errors.append))
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual(["fixture failure"], self.errors)
        applied: list[int] = []
        self.lane.submit(PageRequest(2, lambda: 2, applied.append, self.errors.append))
        self.wait(lambda: not self.lane.is_running())
        self.assertEqual([2], applied)

    def test_superseded_requests_release_their_loading_state(self) -> None:
        release = threading.Event()
        discarded: list[int] = []
        for key in (1, 2, 3):
            self.lane.submit(PageRequest(
                key, lambda: release.wait(2), lambda _v: None, self.errors.append,
                lambda k=key: discarded.append(k),
            ))
        self.assertEqual([1, 2], discarded)
        release.set()
        self.wait(lambda: not self.lane.is_running())


if __name__ == "__main__":
    unittest.main()
