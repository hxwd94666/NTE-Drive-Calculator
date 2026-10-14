# 用真实 Qt 通道验证倒带确认代次、取消、热键与物理收尾，输入仅为假后端。
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from src.app.context import AccountContext, AppContext
from src.app.page_tasks import close_page_tasks
from src.features.toolbox import page
from src.features.toolbox.rewind import execution_ui
from src.features.toolbox.rewind.execution_dialog import RewindExecutionDialog

NTE_TEST_TIER = "core"


def until(app, condition):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.002)
    assert condition(), "synthetic owner did not reach its bounded terminal state"


@pytest.fixture
def run_fixture(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    account = AccountContext("first", "synthetic", tmp_path, tmp_path / "unused.sqlite3",
                             tmp_path / "config", tmp_path / "shots", tmp_path / "logs")
    context = AppContext(SimpleNamespace(), account, settings_factory=lambda *_a, **_k: None)
    state = SimpleNamespace(app=app, context=context, original=account, entered=threading.Event(),
                            release=threading.Event(), calls=[], backends=[], outcome="success",
                            observed_stop=None, errors=[])

    class Service:
        def load_preferences(self):
            return {}

        def save_preferences(self, value):
            state.preferences = value

        def static_identity(self):
            return ()

        def list_target_roles(self, **_kwargs):
            return ()

        def load_owned_shape_counts(self):
            return ()

    class Hotkeys:
        active_owner = None
        configuration = SimpleNamespace(stop="F8")

        def start(self, *, owner, on_stop):
            self.active_owner, self.on_stop = owner, on_stop
            if state.outcome == "hotkey_error":
                raise RuntimeError("synthetic hotkey error")

        def stop(self, *, owner):
            assert owner == self.active_owner
            self.active_owner = None

    class Backend:
        def __init__(self, *, operation_guard):
            if state.outcome == "factory_error":
                raise RuntimeError("synthetic construction error")
            self.guard = operation_guard
            self.released = self.closed = False
            state.backends.append(self)

        def force_mouse_release(self):
            self.released = True

        def close(self):
            self.closed = True

    def execute(_request, *, backend, should_stop):
        backend.guard("interface_input")
        state.entered.set()
        assert state.release.wait(4)
        state.observed_stop = should_stop()
        if state.outcome == "execute_error":
            raise RuntimeError("synthetic execution error")
        # 故意返回迟到成功：即便底层没有及时观察取消，UI 也不可重开旧窗口。
        return SimpleNamespace(planned_draws=30, remaining_currency=50)

    monkeypatch.setattr(execution_ui, "PyAutoGuiMouseBackend", Backend)
    monkeypatch.setattr(execution_ui, "execute_rewind_request", execute)
    state.host = QWidget()
    state.hotkeys = state.host.global_hotkey_manager = Hotkeys()
    state.service = Service()
    state.dialog = page._RewindRecommendationDialog(
        state.service, state.host,
        operation_generation=lambda: (context.account.active_account_id, context.generation),
        operation_guard=lambda capability: state.calls.append(capability),
        operation_entry=lambda *_: True,
        operation_unavailable=lambda *_args: state.errors.append(_args),
    )
    state.dialog._rewind_foreground_settle_seconds = 0
    until(app, lambda: not state.dialog._catalog_loading)
    try:
        yield state
    finally:
        state.release.set()
        state.dialog.shutdown()
        until(app, lambda: not any(worker.isRunning() for worker in state.host.findChildren(QThread)))
        state.host.close()
        state.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("boundary", ["entry", "confirmation", "aba"])
def test_confirmation_never_rebinds_old_draft_to_new_generation(run_fixture, monkeypatch, boundary):
    state = run_fixture

    def switch():
        state.context.switch_account(replace(state.original, active_account_id="second",
                                              user_database_path=state.original.account_data_root / "other.sqlite3"))
        if boundary == "aba":
            state.context.switch_account(state.original)

    def confirmation(parent, *, initial):
        dialog = RewindExecutionDialog(parent, initial=initial)
        def accept():
            switch()
            dialog.accept()
        QTimer.singleShot(0, dialog, accept)
        return dialog

    if boundary == "entry":
        state.dialog.operation_entry = lambda *_: (switch(), True)[1]
    monkeypatch.setattr(execution_ui, "RewindExecutionDialog", confirmation)
    state.dialog._configure_rewind()
    assert state.context.generation == (2 if boundary == "aba" else 1)
    assert state.backends == [] and state.calls == []
    assert not state.dialog.is_rewind_running()
    assert not hasattr(state, "preferences")


@pytest.mark.parametrize("stop", ["reject", "shutdown", "hotkey"])
def test_cancel_retains_physical_owner_and_suppresses_late_completion(run_fixture, stop):
    state, dialog = run_fixture, run_fixture.dialog
    dialog._start_rewind_execution()
    until(state.app, state.entered.is_set)
    assert state.hotkeys.active_owner == "rewind_execution"
    if stop == "hotkey":
        state.hotkeys.on_stop()
    else:
        getattr(dialog, stop)()
    assert dialog.is_rewind_running()
    assert state.hotkeys.active_owner == "rewind_execution"
    assert close_page_tasks(state.host)
    assert not state.backends[0].closed
    state.release.set()
    until(state.app, lambda: not dialog.is_rewind_running())
    assert state.observed_stop is True
    assert state.backends[0].released and state.backends[0].closed
    assert state.hotkeys.active_owner is None
    assert "计划 30" not in dialog._start_rewind_button.text()
    assert not dialog.isVisible() if stop != "hotkey" else bool(state.errors)


@pytest.mark.parametrize("outcome", ["success", "factory_error", "execute_error", "hotkey_error", "foreground_error"])
def test_terminal_releases_owner_and_distinguishes_plan_from_confirmation(run_fixture, monkeypatch, outcome):
    state = run_fixture
    state.outcome = outcome
    if outcome == "foreground_error":
        def fail():
            raise RuntimeError("synthetic foreground error")
        monkeypatch.setattr(state.dialog, "_prepare_rewind_game_foreground", fail)
    state.release.set()
    state.dialog._start_rewind_execution()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert state.hotkeys.active_owner is None
    assert all(backend.released and backend.closed for backend in state.backends)
    if outcome == "success":
        assert state.dialog._start_rewind_button.text() == "输入已结束：计划 30 次，预计剩余 50"
        assert "尚未取得游戏后验确认" in state.dialog._start_rewind_button.toolTip()
    else:
        assert state.errors
        assert state.dialog._start_rewind_button.text() == "重新进行倒带"


def test_cancel_during_foreground_delay_creates_no_backend(run_fixture):
    state = run_fixture
    state.dialog._rewind_foreground_settle_seconds = 2
    state.dialog._start_rewind_execution()
    state.hotkeys.on_stop()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert state.calls == [] and state.backends == []
    assert state.hotkeys.active_owner is None


def test_account_change_discards_delayed_terminal_projection(run_fixture):
    state = run_fixture
    state.dialog._start_rewind_execution()
    until(state.app, state.entered.is_set)
    state.context.switch_account(replace(state.original, active_account_id="second",
                                          user_database_path=state.original.account_data_root / "other.sqlite3"))
    state.release.set()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert not state.dialog.isVisible() and not state.errors
    assert "计划 30" not in state.dialog._start_rewind_button.text()
    assert state.backends[0].released and state.backends[0].closed
    assert state.hotkeys.active_owner is None


def test_toolbox_retains_active_dialog_until_shutdown_physically_finishes(run_fixture, monkeypatch):
    state = run_fixture
    toolbox = page.ToolboxPage(dependencies=SimpleNamespace(rewind_service=lambda: state.service,
        operation_guard=None, operation_generation=None, operation_entry=None, operation_unavailable=None),
        dialog_parent=state.host)
    monkeypatch.setattr(page, "_RewindRecommendationDialog", lambda *_a, **_k: state.dialog)
    observations = []
    timer = QTimer(state.dialog)
    timer.setInterval(5)

    def stop():
        if not state.entered.is_set():
            return
        timer.stop()
        observations.append(toolbox.is_rewind_running())
        toolbox.shutdown()
        observations.append(toolbox.is_rewind_running())
        state.release.set()

    timer.timeout.connect(stop)
    timer.start()
    QTimer.singleShot(0, state.dialog, state.dialog._start_rewind_execution)
    # 测试复用既有宿主；仅拦最后的 deleteLater，让 fixture 统一释放。
    monkeypatch.setattr(state.dialog, "deleteLater", lambda: None)
    toolbox._show_rewind_recommendation()
    until(state.app, lambda: toolbox._rewind_dialog is None)
    assert observations == [True, True]
    assert not toolbox.is_rewind_running() and toolbox._rewind_dialog is None
    assert state.observed_stop is True and not state.dialog.isVisible()
