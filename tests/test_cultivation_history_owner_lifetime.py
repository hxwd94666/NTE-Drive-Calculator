# 验证历史串行顺序、非阻塞关闭、账号过期和真实事务的完整提交或回滚。
from __future__ import annotations

from functools import partial
from threading import Event
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QEvent, QThread, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QStackedWidget
from shiboken6 import isValid

from src.app.page_tasks import close_page_tasks, delete_when_page_tasks_finish
from src.domain.cultivation_history import HistoryConflict, HistoryContextExpired, HistorySelection
from src.features.toolbox.cultivation.history_controller import CultivationHistoryController
from src.features.toolbox.page import ToolboxPage
from src.features.toolbox.toolbox_navigation import CultivationToolboxNavigation
from src.services.progression.history_service import CultivationHistoryService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.user_data_support import UserDataError
from tests.test_cultivation_history import history_payload

NTE_TEST_TIER = "core"


def spin(predicate):
    deadline = time.monotonic() + 3
    app = QApplication.instance()
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(.002)
    assert predicate()


@pytest.fixture
def parent():
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    yield root
    if isValid(root):
        delete_when_page_tasks_finish(root)
        spin(lambda: not isValid(root))
    app.processEvents()


class Service:
    def close(self):
        pass


@pytest.mark.parametrize("operation", ["list", "save"])
def test_close_returns_while_physical_action_remains_owned_and_drops_late_delivery(parent, operation):
    entered, release = Event(), Event()
    gui = QThread.currentThread()
    identity_threads, action_threads, results, ticks = [], [], [], []

    def identity():
        identity_threads.append(QThread.currentThread() == gui)
        return "ctx"

    def action():
        entered.set()
        action_threads.append(QThread.currentThread() == gui)
        assert release.wait(3)
        return "late"

    controller = CultivationHistoryController(Service(), context_identity=identity, parent=parent)
    controller.completed.connect(results.append)
    controller.submit(operation, action)
    assert entered.wait(3)
    try:
        started = time.monotonic()
        controller.close()
        assert time.monotonic() - started < .25
        assert controller.is_running() and close_page_tasks(parent)
        QTimer.singleShot(0, lambda: (ticks.append(True), release.set()))
        spin(lambda: not controller.is_running())
        assert ticks == [True] and not results
        assert action_threads == [False] and all(identity_threads)
        assert controller.submit("save", action) == 0
    finally:
        release.set()
        spin(lambda: not controller.is_running())


def test_queue_coalesces_only_not_started_reads_and_preserves_all_write_order(parent):
    entered, release = Event(), Event()
    calls, outcomes, active = [], [], []
    controller = CultivationHistoryController(Service(), context_identity=lambda: "ctx", parent=parent)
    controller.completed.connect(lambda value: outcomes.append(value.request_id))

    def action(label):
        assert not active
        active.append(label)
        try:
            calls.append(label)
            if label == "save-first":
                entered.set()
                assert release.wait(3)
            return label
        finally:
            active.clear()

    first = controller.submit("save", partial(action, "save-first"))
    assert entered.wait(3)
    try:
        obsolete = controller.submit("list", partial(action, "read-obsolete"))
        second = controller.submit("save", partial(action, "save-second"))
        latest = controller.submit("list", partial(action, "read-latest"))
        deleted = controller.submit("delete", partial(action, "delete-exact"))
        third = controller.submit("save", partial(action, "save-third"))
        release.set()
        spin(lambda: len(outcomes) == 5 and not controller.is_running())
        assert calls == ["save-first", "save-second", "read-latest", "delete-exact", "save-third"]
        assert outcomes == [first, second, latest, deleted, third] and obsolete not in outcomes
    finally:
        release.set()
        spin(lambda: not controller.is_running())
        controller.close()


def test_generation_change_discards_active_delivery_and_revokes_queued_write(parent):
    entered, release = Event(), Event()
    identity, calls, outcomes = ["old"], [], []
    controller = CultivationHistoryController(Service(), context_identity=lambda: identity[0], parent=parent)
    controller.completed.connect(outcomes.append)

    def read():
        entered.set()
        assert release.wait(3)
        return "old"

    controller.submit("list", read)
    assert entered.wait(3)
    try:
        controller.submit("delete", lambda: calls.append("write"))
        identity[0] = "new"
        release.set()
        spin(lambda: not controller.is_running())
        assert not outcomes and not calls
        assert controller.submit("save", lambda: None) == 0
    finally:
        release.set()
        spin(lambda: not controller.is_running())
        controller.close()


@pytest.mark.parametrize("error,code,message", [
    (HistoryContextExpired("private"), "expired", ""),
    (HistoryConflict("known conflict"), "conflict", "known conflict"),
    (ValueError("known validation"), "invalid_data", "known validation"),
    (UserDataError("private database details"), "storage_failed", "历史数据库操作失败；请检查文件占用和可用空间后重试。"),
    (RuntimeError("private payload"), "operation_failed", "历史操作未完成，请重试。"),
])
def test_error_outcome_preserves_category_without_raw_unknown_payload(parent, error, code, message):
    outcomes = []
    controller = CultivationHistoryController(Service(), context_identity=lambda: "ctx", parent=parent)
    controller.completed.connect(outcomes.append)

    def fail():
        raise error

    request_id = controller.submit("get", fail)
    spin(lambda: len(outcomes) == 1 and not controller.is_running())
    assert outcomes[0].request_id == request_id and outcomes[0].operation == "get"
    assert outcomes[0].error_code == code and outcomes[0].message == message
    controller.close()


def test_completion_submitting_next_write_does_not_lose_or_replay_queue(parent):
    controller = CultivationHistoryController(Service(), context_identity=lambda: "ctx", parent=parent)
    calls, completed = [], []

    def action(label):
        calls.append(label)
        return label

    def complete(value):
        completed.append(value.value)
        if value.value == "first":
            controller.submit("save", partial(action, "next"))

    controller.completed.connect(complete)
    controller.submit("save", partial(action, "first"))
    spin(lambda: len(completed) == 2 and not controller.is_running())
    assert calls == completed == ["first", "next"]
    controller.close()


@pytest.mark.parametrize("checkpoint", ["before_commit", "after_authorization", "after_commit"])
def test_real_save_on_close_is_fully_rolled_back_or_preserves_completed_commit(parent, tmp_path, checkpoint):
    entered, release = Event(), Event()
    path = tmp_path / "user.sqlite3"
    with UserDataDao(path, account_id="one"):
        pass

    class BarrierDao(UserDataDao):
        def save_cultivation_history(self, *args, **kwargs):
            check = kwargs["check"]
            calls = 0

            def guarded():
                nonlocal calls
                calls += 1
                # 账户校验、BEGIN 后校验、返回前校验，第三次在已写入但尚未提交时。
                if checkpoint == "before_commit" and calls == 3:
                    entered.set()
                    assert release.wait(3)
                check()
                if checkpoint == "after_authorization" and calls == 3:
                    # 最终检查已授权旧账号事务，但 SQLite 尚未退出 with 完成 COMMIT。
                    entered.set()
                    assert release.wait(3)

            value = super().save_cultivation_history(*args, **dict(kwargs, check=guarded))
            if checkpoint == "after_commit":
                entered.set()
                assert release.wait(3)
            return value

    service = CultivationHistoryService(account_id="one", user_database_path=path,
                                       context_identity=lambda: "ctx", dao_factory=BarrierDao)
    payload = history_payload()
    session = service.new_session("single")
    import json
    envelope = service.freeze(session, json.loads(payload.configuration_json), explicit_calculation=True)
    controller = CultivationHistoryController(service, context_identity=lambda: "ctx", parent=parent)
    outcomes = []
    controller.completed.connect(outcomes.append)
    controller.submit("save", partial(service.save_projected, envelope, lambda: payload))
    assert entered.wait(3)
    try:
        started = time.monotonic()
        controller.close()
        assert time.monotonic() - started < .25 and controller.is_running()
        release.set()
        spin(lambda: not controller.is_running())
        with UserDataDao(path, account_id="one") as dao:
            page = dao.list_cultivation_histories("one")
            assert page.total == (0 if checkpoint == "before_commit" else 1)
            if page.total:
                record = dao.get_cultivation_history("one", page.items[0].history_id)
                assert record.payload == payload and record.summary.revision == 1
        assert not outcomes
    finally:
        release.set()
        spin(lambda: not controller.is_running())


def test_toolbox_shutdown_revokes_queued_actions_and_keeps_retired_page_owned(parent):
    entered, release = Event(), Event()
    stack, home = QStackedWidget(parent), QWidget()
    stack.addWidget(home)

    class Page(QWidget):
        def shutdown(self):
            self.controller.close()

    page = Page(stack)
    page.controller = CultivationHistoryController(Service(), context_identity=lambda: "ctx", parent=page)
    stack.addWidget(page)
    stack.setCurrentWidget(page)
    dependencies = SimpleNamespace(cultivation_context_identity=lambda: "ctx")
    navigation = CultivationToolboxNavigation(stack=stack, home=home, dependencies=dependencies, dialog_parent=parent)
    navigation.page = page
    toolbox = ToolboxPage(dependencies=dependencies, dialog_parent=parent)
    toolbox._cultivation_navigation = navigation
    writes = []

    def read():
        entered.set()
        assert release.wait(3)

    page.controller.submit("list", read)
    assert entered.wait(3)
    try:
        page.controller.submit("delete", lambda: writes.append(True))
        toolbox.shutdown()
        toolbox.shutdown()  # 应用关闭重试时须幂等，不重复派发或销毁。
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert isValid(page) and page.parent() is stack and close_page_tasks(parent)
        release.set()
        spin(lambda: not isValid(page))
        assert not writes and not close_page_tasks(parent)
    finally:
        release.set()
        spin(lambda: not isValid(page))


def test_close_during_delete_final_identity_read_rolls_back_real_transaction(parent, tmp_path):
    entered, release, final_check = Event(), Event(), Event()
    path = tmp_path / "delete.sqlite3"
    payload = history_payload()
    with UserDataDao(path, account_id="one") as dao:
        dao.save_cultivation_history("one", "entry", payload)

    def identity():
        if final_check.is_set():
            entered.set()
            assert release.wait(3)
        return "ctx"

    class BarrierDao(UserDataDao):
        def delete_cultivation_histories(self, *args, **kwargs):
            check, calls = kwargs["check"], 0

            def guarded():
                nonlocal calls
                calls += 1
                # 正式账号、BEGIN、删前、删后的最终检查；只阻塞最后一次身份读取。
                if calls == 4:
                    final_check.set()
                check()

            return super().delete_cultivation_histories(*args, **dict(kwargs, check=guarded))

    service = CultivationHistoryService(account_id="one", user_database_path=path,
                                       context_identity=identity, dao_factory=BarrierDao)
    controller = CultivationHistoryController(service, context_identity=lambda: "ctx", parent=parent)
    outcomes = []
    controller.completed.connect(outcomes.append)
    controller.submit("delete", partial(service.delete, (HistorySelection("entry", 1),)))
    assert entered.wait(3)
    try:
        controller.close()
        release.set()
        spin(lambda: not controller.is_running())
        with UserDataDao(path, account_id="one") as dao:
            record = dao.get_cultivation_history("one", "entry")
            assert record is not None and record.payload == payload and record.summary.revision == 1
        assert not outcomes
    finally:
        release.set()
        spin(lambda: not controller.is_running())


def test_public_account_switch_closes_hidden_toolbox_before_identity_replacement(parent, tmp_path):
    from pathlib import Path
    from src.app.context import AccountContext, AppContext, ApplicationPaths
    from src.ui.toolbox_composition import build_toolbox_dependencies, register_toolbox_account_lifecycle

    source = Path(__file__).resolve().parents[1]
    static = tmp_path / "static-identity.sqlite3"
    static.write_bytes(b"synthetic dataset identity; no static queries")
    paths = ApplicationPaths.from_roots(
        root=source, app_dir=source, data_root=tmp_path, bundled_config_dir=source / "config",
        asset_dir=source / "assets", app_icon_path=source / "assets/app_icon.ico",
        static_database_path=static,
    )
    accounts = []
    for name in ("first", "second"):
        base = tmp_path / name
        account = AccountContext(name, name, base, base / "user.sqlite3", base / "config", base / "screenshots", base / "logs")
        with UserDataDao(account.user_database_path, account_id=name):
            pass
        accounts.append(account)
    ctx = AppContext(paths, accounts[0])
    deps = build_toolbox_dependencies(
        ctx, operation_entry=lambda *_args: True, operation_unavailable=lambda *_args: None,
        operation_guard=lambda *_args: None, operation_generation=lambda: ctx.generation,
        material_importer=lambda: None, navigate_static_catalog=lambda: None,
    )
    toolbox = ToolboxPage(dependencies=deps, dialog_parent=parent)
    toolbox.build().setParent(parent)
    navigation = toolbox._cultivation_navigation
    entered, release = Event(), Event()
    closed_at, writes, outcomes = [], [], []

    class ObservedService(CultivationHistoryService):
        def close(self):
            closed_at.append((ctx.account.active_account_id, ctx.generation))
            super().close()

    service = ObservedService(account_id="first", user_database_path=accounts[0].user_database_path,
                              context_identity=deps.cultivation_context_identity)

    class Page(QWidget):
        def shutdown(self):
            self.controller.close()

    page = Page(toolbox._stack)
    page.controller = CultivationHistoryController(service, context_identity=deps.cultivation_context_identity, parent=page)
    page.controller.completed.connect(outcomes.append)
    toolbox._stack.addWidget(page)
    navigation.page = page
    navigation.show_home()  # 页已隐藏，切换仍须主动撤销其 owner，不能等下次 refresh。
    unregister = register_toolbox_account_lifecycle(ctx, toolbox)

    def read():
        entered.set()
        assert release.wait(3)
        return "late"

    page.controller.submit("list", read)
    assert entered.wait(3)
    try:
        page.controller.submit("delete", lambda: writes.append(True))
        started = time.monotonic()
        ctx.switch_account(accounts[1])
        assert time.monotonic() - started < .25
        assert closed_at == [("first", 0)]
        assert ctx.account.active_account_id == "second" and ctx.generation == 1
        assert navigation.page is None and toolbox._stack.currentWidget() is toolbox._home
        assert isValid(page) and page.parent() is toolbox._stack and close_page_tasks(parent)
        with pytest.raises(HistoryContextExpired):
            service.new_session("single")
        release.set()
        spin(lambda: not isValid(page))
        assert not writes and not outcomes and not close_page_tasks(parent)
        with UserDataDao(accounts[1].user_database_path, account_id="second") as dao:
            assert dao.list_cultivation_histories("second").total == 0
    finally:
        release.set()
        spin(lambda: not isValid(page))
        unregister()
