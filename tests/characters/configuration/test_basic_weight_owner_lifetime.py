# 验证权重 owner 换代有界回收，读取先失效、已接受写入先确认，最后才销毁 QObject。
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from src.features.configuration.controller import BasicWeightController
from src.features.configuration.page_owner import BasicWeightPage


NTE_TEST_TIER = "core"


class BasicWeightOwnerLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.shell = QWidget()
        self.context = SimpleNamespace(
            generation=0,
            account=SimpleNamespace(active_account_id="lifetime", user_database_path=Path("unused-user.sqlite3")),
            paths=SimpleNamespace(config_dir=Path("config"), equipment_allocation_database_path=Path("unused-static.sqlite3"),
                                  shared_database_path=Path("unused-shared.sqlite3")),
        )
        self.page = BasicWeightPage(self.context, lambda _key: None, lambda: None, self.shell)
        self.releases = []

    def pump(self):
        self.app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.001)  # 让真实 Python worker 获得 GIL，不用嵌套 Qt 等待饿死被测线程。

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            self.pump()
        self.assertTrue(predicate())

    def tearDown(self):
        for event in self.releases:
            event.set()
        for owner in self.page.findChildren(BasicWeightController):
            self.wait(lambda: not owner._reads.is_running() and not owner.is_writing())
            owner.close()
        self.shell.deleteLater()
        self.pump()

    def test_generation_replacements_leave_one_owner_instead_of_accumulating_children(self):
        self.page.controller()
        for _ in range(25):
            self.context.generation += 1
            self.page.controller()
            self.pump()
        self.assertEqual(1, len(self.page.findChildren(BasicWeightController)))

    def test_inflight_old_read_is_invalidated_but_owner_lives_until_thread_finishes(self):
        old = self.page.controller()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        applied, errors = [], []
        def read():
            entered.set()
            release.wait(3)
            return {"roles": {}}
        old.load_form_data = read
        old.request_form_data(applied.append, errors.append)
        self.wait(entered.is_set)
        self.context.generation += 1
        replacement = self.page.controller()
        self.pump()
        self.assertTrue(isValid(old))
        self.assertTrue(old._reads.is_running())
        self.assertIsNot(old, replacement)
        release.set()
        self.wait(lambda: not isValid(old))
        self.assertEqual([], applied)
        self.assertEqual([], errors)
        self.assertEqual([replacement], self.page.findChildren(BasicWeightController))

    def test_retirement_waits_for_accepted_write_and_keeps_its_terminal_ack(self):
        owner = self.page.controller()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        acknowledged, errors = [], []
        def write():
            entered.set()
            release.wait(3)
            return "committed"
        self.assertTrue(owner.submit_change(write, acknowledged.append, errors.append, errors.append))
        self.wait(entered.is_set)
        owner.retire()
        self.pump()
        self.assertTrue(isValid(owner))
        self.assertTrue(owner.is_writing())
        self.assertFalse(owner.submit_change(write, acknowledged.append, errors.append, errors.append))
        release.set()
        self.wait(lambda: not isValid(owner))
        self.assertEqual(["committed"], acknowledged)
        self.assertEqual([], errors)

    def test_simultaneous_read_and_write_require_both_terminal_states_before_delete(self):
        owner = self.page.controller()
        read_entered, read_release = threading.Event(), threading.Event()
        write_entered, write_release = threading.Event(), threading.Event()
        self.releases.extend((read_release, write_release))
        read_ack, write_ack, errors = [], [], []
        def read():
            read_entered.set()
            read_release.wait(3)
            return {}
        def write():
            write_entered.set()
            write_release.wait(3)
            return "committed"
        owner.load_form_data = read
        owner.request_form_data(read_ack.append, errors.append)
        self.wait(read_entered.is_set)
        self.assertTrue(owner.submit_change(write, write_ack.append, errors.append, errors.append))
        self.wait(write_entered.is_set)
        owner.retire()
        owner.retire()  # 退役请求幂等，不重复注册回收或接受新工作。
        read_release.set()
        self.wait(lambda: not owner._reads.is_running())
        self.pump()
        self.assertTrue(isValid(owner))
        write_release.set()
        self.wait(lambda: not isValid(owner))
        self.assertEqual([], read_ack)
        self.assertEqual(["committed"], write_ack)
        self.assertEqual([], errors)
