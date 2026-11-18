# 用真实 Qt 通道验证倒带确认代次、取消、热键与物理收尾，输入仅为假后端。
from tests.rewind.preferences_fixture import ProfileServiceFixture
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app.context import AccountContext, AppContext
from src.domain.rewind.dual_target import RewindSourceChange, RewindSourceChanged

INVENTORY = frozenset({RewindSourceChange('inventory')})
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
                            observed_stop=None, errors=[], requests=[])

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
        def __init__(self, *, operation_guard, verify_delivery=False):
            self.verify_delivery = verify_delivery
            if state.outcome == "factory_error":
                raise RuntimeError("synthetic construction error")
            self.guard = operation_guard
            self.released = self.closed = False
            state.backends.append(self)

        def force_mouse_release(self):
            self.released = True

        def close(self):
            self.closed = True

    def execute(_request, *, backend, should_stop, capture=None):
        state.requests.append(_request)
        backend.guard("interface_input")
        state.entered.set()
        assert state.release.wait(4)
        state.observed_stop = should_stop()
        if state.outcome == "execute_error":
            raise RuntimeError("synthetic execution error")
        if state.outcome == "focus_loss":
            state.target.current = False
            backend.guard("interface_input")
            state.dispatched = True
        # 故意返回迟到成功：即便底层没有及时观察取消，UI 也不可重开旧窗口。
        return SimpleNamespace(planned_draws=30, remaining_currency=50)

    monkeypatch.setattr(execution_ui, "PyAutoGuiMouseBackend", Backend)
    monkeypatch.setattr(execution_ui, "execute_rewind_request", execute)
    state.host = QWidget()
    state.hotkeys = state.host.global_hotkey_manager = Hotkeys()
    state.service = Service()
    state.dialog = page._RewindRecommendationDialog(
        ProfileServiceFixture(state.service), state.host,
        operation_generation=lambda: (context.account.active_account_id, context.generation),
        operation_guard=lambda capability: state.calls.append(capability),
        operation_entry=lambda *_: True,
        operation_unavailable=lambda *_args: state.errors.append(_args),
    )
    class Target:
        current = True
        bound = False

        def bind_foreground(self):
            self.check()
            self.bound = True

        def check(self):
            if not self.current:
                raise RuntimeError("target_focus_lost")

        def capture(self):
            raise AssertionError("synthetic executor never captures")

    state.target = Target()
    state.dispatched = False
    state.dialog._rewind_target_factory = lambda: state.target
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

    def confirmation(parent, *, initial, text_catalog=None):
        dialog = RewindExecutionDialog(parent, initial=initial, text_catalog=text_catalog)
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


@pytest.mark.parametrize('strategy', ['balanced', 'focused', 'dual_target'])
@pytest.mark.parametrize('phase', ['entry', 'delayed'])
def test_saved_pool_source_is_checked_before_input_resource_creation(run_fixture, monkeypatch, phase, strategy):
    state, dialog = run_fixture, run_fixture.dialog
    dialog._strategy_key = strategy
    dialog._saved_rewind_shape_ids = ('EquipmentGeometry_Hen4',) * 8
    dialog._rewind_options = replace(dialog._rewind_options, drive_customization='apply_plan')
    dialog._preferences = dict(plan_origin='dual_target' if strategy == 'dual_target' else 'generated',
                               saved_guard={'fixture': 'immutable'})
    dialog._dual_preferences = dialog._preferences
    validations = []
    def validate(guard, *, checkpoint, accepted_changes=frozenset()):
        checkpoint()
        validations.append(guard)
        if phase == 'entry' or len(validations) == 2:
            raise ValueError('synthetic source changed')
    monkeypatch.setattr(state.service, 'validate_dual_target_guard', validate, raising=False)
    monkeypatch.setattr(dialog, '_dual_request_matches_saved', lambda: True)
    warnings = []
    monkeypatch.setattr(execution_ui.QMessageBox, 'warning', lambda *_args: warnings.append(_args))
    monkeypatch.setattr(dialog, '_prepare_rewind_game_foreground', lambda: None)
    dialog._start_rewind_execution()
    until(state.app, lambda: not dialog.is_rewind_running())
    assert len(validations) == (1 if phase == 'entry' else 2)
    assert state.backends == [] and state.hotkeys.active_owner is None
    assert warnings if phase == 'entry' else state.errors


def saved_pool(state, strategy):
    dialog = state.dialog
    dialog._strategy_key = strategy
    dialog._saved_rewind_shape_ids = ('EquipmentGeometry_Hen4',) * 8
    dialog._rewind_options = replace(dialog._rewind_options, drive_customization='apply_plan')
    dialog._preferences = dict(plan_origin='dual_target' if strategy == 'dual_target' else 'generated',
                               saved_guard={'fixture': 'immutable'})
    dialog._dual_preferences = dialog._preferences
    dialog._dual_request_matches_saved = lambda: True


@pytest.mark.parametrize('strategy', ['balanced', 'focused', 'dual_target'])
@pytest.mark.parametrize('phase', ['entry', 'delayed'])
@pytest.mark.parametrize('accept', [False, True])
def test_inventory_changes_require_one_confirmation_and_never_replay_input(run_fixture, monkeypatch, strategy, phase, accept):
    state = run_fixture
    saved_pool(state, strategy)
    validations, prompts = [], []

    def validate(guard, *, checkpoint, accepted_changes=frozenset()):
        checkpoint()
        validations.append((guard, accepted_changes))
        if not accepted_changes and (phase == 'entry' or len(validations) >= 2):
            raise RewindSourceChanged(INVENTORY)

    def confirm(changes):
        assert changes == INVENTORY
        assert len(state.backends) == len(prompts) and state.hotkeys.active_owner is None
        prompts.append(True)
        return accept

    monkeypatch.setattr(state.service, 'validate_dual_target_guard', validate, raising=False)
    monkeypatch.setattr(state.dialog, '_confirm_rewind_source_change', confirm)
    state.release.set()
    state.dialog._start_rewind_execution()
    until(state.app, lambda: len(prompts) == 1 and not state.dialog.is_rewind_running())
    assert len(state.backends) == int(accept)
    assert state.entered.is_set() == accept and not state.errors
    assert state.hotkeys.active_owner is None
    if accept:
        assert state.requests[0].shape_ids == ('EquipmentGeometry_Hen4',) * 8
        assert sum(accepted == INVENTORY for _, accepted in validations) == 2
        # 下次独立执行重新确认；没有持久的“忽略库存变化”状态。
        state.dialog._start_rewind_execution()
        until(state.app, lambda: len(prompts) == 2 and not state.dialog.is_rewind_running())


@pytest.mark.parametrize('change', ['account', 'plan', 'options'])
def test_confirmation_does_not_authorize_a_new_account_or_changed_plan(run_fixture, monkeypatch, change):
    state = run_fixture
    saved_pool(state, 'balanced')

    def validate(_guard, *, checkpoint, **_kwargs):
        checkpoint()
        raise RewindSourceChanged(INVENTORY)

    def confirm(_changes):
        if change == 'account':
            state.context.switch_account(replace(state.original, active_account_id='second',
                user_database_path=state.original.account_data_root / 'other.sqlite3'))
        elif change == 'plan':
            state.dialog._saved_rewind_shape_ids = ('EquipmentGeometry_Hen2',) * 8
        else:
            state.dialog._rewind_options = replace(state.dialog._rewind_options, qualities=('purple',))
        return True

    monkeypatch.setattr(state.service, 'validate_dual_target_guard', validate, raising=False)
    monkeypatch.setattr(state.dialog, '_confirm_rewind_source_change', confirm)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_args: warnings.append(_args))
    state.dialog._start_rewind_execution()
    assert state.backends == [] and state.hotkeys.active_owner is None
    assert bool(warnings) == (change != 'account')


def test_source_confirmation_lists_updates_as_plain_text_with_cancel_as_default(run_fixture, monkeypatch):
    seen = []

    def show(message):
        seen.append(message.text())
        assert message.textFormat() == Qt.PlainText
        assert message.standardButton(message.defaultButton()) == QMessageBox.Cancel
        assert message.standardButton(message.escapeButton()) == QMessageBox.Cancel
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, 'exec', show)
    monkeypatch.setattr(QApplication, 'beep', lambda: None)
    run_fixture.dialog._role_names = {1004: '<b>角色甲</b>'}
    changes = frozenset({RewindSourceChange('plan_replaced', 1004, 1), RewindSourceChange('inventory'),
                         RewindSourceChange('slot_unavailable', 1005, 2)})
    assert not run_fixture.dialog._confirm_rewind_source_change(changes)
    lines = seen[0].splitlines()
    assert '已保存' in seen[0]
    # 库存在前、角色按正式 ID 排序；未知角色以 ID 显示，名称按纯文本插入。
    assert lines[1:4] == ['· 背包库存已更新', '· <b>角色甲</b> 的所选配装已重新保存',
                          '· 1005 的所选配装槽位已归档、删除或不再属于该角色']


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
        operation_guard=None, operation_generation=None, operation_entry=None, operation_unavailable=None, rewind_target_factory=None),
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


def test_focus_loss_after_wait_stops_instead_of_clicking_calculator(run_fixture):
    state = run_fixture
    state.outcome = "focus_loss"
    state.dialog._start_rewind_execution()
    until(state.app, state.entered.is_set)
    state.release.set()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert not state.dispatched, "rewind dispatched input to the calculator after focus changed"
    assert state.errors
    assert state.target.bound and all(backend.verify_delivery for backend in state.backends)
    assert all(backend.released and backend.closed for backend in state.backends)


@pytest.mark.parametrize("stage", ["path", "activation", "focus"])
def test_window_precheck_failure_creates_no_backend_and_returns_hotkey(run_fixture, stage):
    state = run_fixture
    if stage == "path":
        state.dialog._rewind_target_factory = None
    elif stage == "activation":
        def fail():
            raise RuntimeError("target_activation_failed")
        state.target.bind_foreground = fail
    else:
        state.target.current = False
    state.dialog._start_rewind_execution()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert state.backends == [] and not state.entered.is_set()
    assert state.hotkeys.active_owner is None and state.errors
    assert "target_" not in str(state.errors)


def test_execution_cleanup_error_does_not_replace_the_focus_loss_reason(run_fixture):
    state = run_fixture
    state.outcome = "focus_loss"
    state.dialog._start_rewind_execution()
    until(state.app, state.entered.is_set)

    def rejected_release():
        raise RuntimeError("input_delivery_rejected")

    state.backends[0].force_mouse_release = rejected_release
    state.release.set()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert state.backends[0].closed and not state.dispatched
    assert any("游戏窗口失去焦点" in str(error) for error in state.errors)
    assert state.hotkeys.active_owner is None


def test_background_game_gets_the_original_settle_time_before_any_target_check(run_fixture, monkeypatch):
    state = run_fixture
    state.target.current = False
    state.dialog._rewind_foreground_settle_seconds = 0.30
    original_prepare = state.dialog._prepare_rewind_game_foreground

    def prepare():
        original_prepare()
        # 模拟宿主最小化后 Windows 稍晚才把后面的游戏变为前台。
        QTimer.singleShot(100, state.dialog, lambda: setattr(state.target, "current", True))

    monkeypatch.setattr(state.dialog, "_prepare_rewind_game_foreground", prepare)
    state.release.set()
    state.dialog._start_rewind_execution()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert state.entered.is_set(), "startup checked/activated the game before the original foreground settle delay"
    assert state.errors == [] and state.target.current
    assert all(backend.released and backend.closed for backend in state.backends)


def test_execution_request_carries_the_confirmed_ten_limits(run_fixture):
    from src.features.toolbox.rewind.execution_dialog import RewindExecutionOptions
    state = run_fixture
    state.outcome = "success"
    state.dialog._rewind_options = RewindExecutionOptions(("gold",), "none", (("gold", 3),))
    state.release.set()
    state.dialog._start_rewind_execution()
    until(state.app, lambda: not state.dialog.is_rewind_running())
    assert [request.ten_limits for request in state.requests] == [(("gold", 3),)]
