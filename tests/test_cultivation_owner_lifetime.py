# 验证养成取消的物理所有权、末次请求、页面退场与提交终态保留。
from __future__ import annotations

from threading import Event
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QEvent, QThread, QTimer
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget
from shiboken6 import isValid

from src.app.page_tasks import (
    PageCommitLane, PageRequest, PageTaskLane, close_page_tasks, delete_when_page_tasks_finish,
)
from src.features.toolbox.cultivation.batch_controller import CultivationBatchController
from src.features.toolbox.cultivation.single_controller import CultivationSingleController
from src.features.toolbox.toolbox_navigation import CultivationToolboxNavigation
from tests.cultivation_planner_fixture import PlanningFixture
from tests.test_cultivation_batch_controller import _request as batch_request
from tests.test_cultivation_single_controller import _request as single_request

NTE_TEST_TIER = "core"


def spin(predicate):
    deadline = time.monotonic() + 3
    app = QApplication.instance()
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(.002)
    assert predicate()


def setup_controller(mode, parent, service, identity):
    cls = CultivationSingleController if mode == "single" else CultivationBatchController
    return cls(service, context_identity=identity, parent=parent)


def request(mode, value):
    return single_request(value) if mode == "single" else batch_request(str(value))


@pytest.mark.parametrize("mode", ["single", "batch"])
@pytest.mark.parametrize("failure", [False, True])
def test_closed_preparation_is_retained_without_delivery_or_ui_thread_wait(mode, failure):
    app = QApplication.instance() or QApplication([])
    entered, release = Event(), Event()
    work_threads, identity_threads = [], []
    gui_thread = QThread.currentThread()
    class Service:
        def prepare(self, _request):
            work_threads.append(QThread.currentThread() == gui_thread)
            entered.set()
            assert release.wait(3)
            if failure:
                raise ValueError("fixture error")
            return object()

    def identity():
        identity_threads.append(QThread.currentThread() == gui_thread)
        return "current"

    parent = QWidget()
    controller = setup_controller(mode, parent, Service(), identity)
    results, errors, ticks = [], [], []
    controller.preparation_ready.connect(results.append)
    controller.preparation_error.connect(errors.append)
    controller.prepare(request(mode, 1), "current")
    assert entered.wait(3)
    try:
        start = time.monotonic()
        controller.close()
        assert time.monotonic() - start < .25
        assert controller.is_running()
        QTimer.singleShot(0, lambda: (ticks.append(True), release.set()))
        spin(lambda: not controller.is_running())
        assert ticks == [True] and not results and not errors
        assert work_threads == [False] and identity_threads and all(identity_threads)
    finally:
        release.set()
        spin(lambda: not controller.is_running())
        parent.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("mode", ["single", "batch"])
def test_three_requests_keep_only_latest_and_never_overlap(mode):
    app = QApplication.instance() or QApplication([])
    entered, release = Event(), Event()
    calls, running = [], []
    class Service(PlanningFixture):
        def calculate(self, target):
            assert not running
            running.append(True)
            value = target.character_id if mode == "single" else int(target.dataset_identity)
            calls.append(value)
            try:
                if value == 1:
                    entered.set()
                    assert release.wait(3)
                return value
            finally:
                running.clear()

    parent = QWidget()
    controller = setup_controller(mode, parent, Service(), lambda: "current")
    results, busy = [], []
    controller.result_ready.connect(lambda result: results.append(result.plan if mode == "single" else result))
    controller.busy_changed.connect(busy.append)
    controller.submit(request(mode, 1), "current")
    assert entered.wait(3)
    try:
        controller.submit(request(mode, 2), "current")
        controller.submit(request(mode, 3), "current")
        release.set()
        spin(lambda: not controller.is_running())
        assert calls == [1, 3] and results == [3] and busy[-1] is False
    finally:
        release.set()
        spin(lambda: not controller.is_running())
        controller.close()
        parent.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("mode", ["single", "batch"])
def test_navigation_retains_removed_page_in_application_tree_until_physical_finish(mode):
    app = QApplication.instance() or QApplication([])
    entered, release = Event(), Event()
    class Service:
        def prepare(self, _request):
            entered.set()
            assert release.wait(3)
            return object()

    class Page(QWidget):
        def shutdown(self):
            self.controller.close()

    stack = QStackedWidget()
    home = QWidget(stack)
    stack.addWidget(home)
    page = Page(stack)
    page.controller = setup_controller(mode, page, Service(), lambda: "current")
    results = []
    page.controller.preparation_ready.connect(results.append)
    stack.addWidget(page)
    stack.setCurrentWidget(page)
    navigation = CultivationToolboxNavigation(
        stack=stack, home=home,
        dependencies=SimpleNamespace(cultivation_context_identity=lambda: "current"),
        dialog_parent=stack,
    )
    navigation.page = page
    page.controller.prepare(request(mode, 1), "current")
    assert entered.wait(3)
    try:
        navigation.discard()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert navigation.page is None and stack.currentWidget() is home
        assert stack.indexOf(page) == -1 and page.parent() is stack and isValid(page)
        assert close_page_tasks(stack)  # 主窗口树仍看得见已退场页面的运行任务。
        release.set()
        spin(lambda: not isValid(page))
        assert not close_page_tasks(stack) and not results
    finally:
        release.set()
        spin(lambda: not isValid(page))
        stack.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_deferred_destruction_preserves_accepted_commit_but_discards_read(tmp_path):
    app = QApplication.instance() or QApplication([])
    entered, release = Event(), Event()
    root, parent = QWidget(), QWidget()
    parent.setParent(root)
    reads, committed, errors = [], [], []
    read_lane, commit_lane = PageTaskLane(parent), PageCommitLane(parent)
    path = tmp_path / "accepted-commit.txt"

    def commit():
        entered.set()
        assert release.wait(3)
        path.write_text("persisted", encoding="utf-8")
        return "persisted"

    read_lane.submit(PageRequest("read", lambda: (release.wait(3), "stale")[1], reads.append, errors.append))
    assert commit_lane.submit(commit, committed.append, errors.append, errors.append)
    assert entered.wait(3)
    try:
        delete_when_page_tasks_finish(parent)
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert isValid(parent) and parent.parent() is root and close_page_tasks(root)
        release.set()
        spin(lambda: not isValid(parent))
        assert path.read_text(encoding="utf-8") == "persisted"
        assert committed == ["persisted"] and not reads and not errors
    finally:
        release.set()
        spin(lambda: not isValid(parent))
        root.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
