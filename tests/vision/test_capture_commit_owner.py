# 用真实 Qt 提交通道验证视觉提交物理终态、取消、退场及事件循环可响应。
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace
from unittest.mock import Mock
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from src.app.page_tasks import close_page_tasks
from src.features.scanning.dependencies import ScanningDependencies
from src.features.scanning.manual_recovery import ManualRecoverySession
from src.features.scanning.vision_commit_controller import VisionCommitController
from src.services.inventory.vision_commit import VisionCommitRequest, VisionCommitResult

NTE_TEST_TIER = "core"


def spin(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("commit owner did not reach physical terminal")
        time.sleep(.002)
    app.processEvents()


@pytest.mark.parametrize("action", ["cancel", "close", "generation", "hidden"])
def test_physical_commit_owner_serializes_and_acknowledges_saved_fact(tmp_path, action):
    app = QApplication.instance() or QApplication([])
    dependencies = ScanningDependencies("fixture", 1, tmp_path, tmp_path, tmp_path/"user.sqlite3",
                                        tmp_path/"static.sqlite3", tmp_path, tmp_path, tmp_path)
    current = [True]
    entered, release = threading.Event(), threading.Event()
    ticks, results, worker_threads = [], [], []
    root = QWidget()
    timer = QTimer(root)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(2)
    def commit(request, token):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return VisionCommitResult(2, request.stats)
    owner = VisionCommitController(lambda deps: current[0], root, commit=commit)
    owner.committed.connect(lambda deps, result: results.append(result))
    request = VisionCommitRequest.freeze(Path(tmp_path/"user.sqlite3"), {}, [])
    try:
        assert owner.submit(dependencies, request)
        spin(app, entered.is_set)
        if action == "cancel": owner.cancel()
        elif action == "close": owner.close()
        elif action == "generation": current[0] = False; owner.reset_account_state()
        else: root.hide()
        assert owner.is_running() and not owner.submit(dependencies, request)
        assert close_page_tasks(root)
        spin(app, lambda: len(ticks) >= 3)
        assert not results
        release.set()
        spin(app, lambda: not owner.is_running())
        assert worker_threads != [threading.get_ident()]
        if action in {"close", "generation"}:
            assert not results and owner.last_result is None
        else:
            assert [r.snapshot_id for r in results] == [2]
        assert not close_page_tasks(root)
    finally:
        release.set(); owner.close(); spin(app, lambda: not owner.is_running())
        timer.stop(); root.deleteLater(); app.processEvents()



def test_actual_window_exit_revokes_visual_submit_before_wait_gate(tmp_path, monkeypatch):
    from unittest.mock import patch
    from PySide6.QtCore import QEvent, QThread
    from src.features.onboarding.guide import OnboardingGuide
    from src.ui.app import MainWindow
    from src.ui.application_runtime import prepare_application_runtime
    from src.ui.controllers.auto_sync_controller import AutoSyncController
    from src.ui.controllers.work_mode_controller import WorkModeController

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    workspace = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("NTE_GAME_STATIC_DB", str(workspace / "data/game_static.sqlite3"))
    entered, release = threading.Event(), threading.Event()
    token_seen, results = [], []
    def forbidden(*args, **kwargs):
        raise AssertionError("退出回归不派发外部操作")
    def commit(request, token):
        entered.set()
        assert release.wait(12)
        token_seen.append(token.is_set())
        if token.is_set():
            raise CancelledError("fixture authorization revoked")
        return VisionCommitResult(2, {})
    with patch.object(AutoSyncController, "start"), patch.object(WorkModeController, "start"), \
         patch.object(WorkModeController, "start_upgrade_guidance"), \
         patch("src.features.settings.updates.controller.CalcUpdateController.startup"), \
         patch.object(OnboardingGuide, "maybe_show"), \
         patch("src.ui.controllers.workshop_refresh_controller.start_workshop_weight_template_refresh", return_value=None), \
         patch("urllib.request.urlopen", forbidden), patch("subprocess.Popen", forbidden):
        runtime = prepare_application_runtime(package_root=workspace, app_dir=tmp_path, data_root=tmp_path / "data")
        window = MainWindow(runtime)
        owner = window.scanning_controller._vision_commit
        monkeypatch.setattr(owner, "_commit", commit)
        monkeypatch.setattr(window.global_hotkey_manager, "start", lambda **kwargs: None)
        dependencies = ScanningDependencies.from_app_context(window.app_context)
        request = VisionCommitRequest.freeze(dependencies.user_database_path, {}, [])
        owner.committed.connect(lambda *args: results.append(args))
        window.show()
        try:
            spin(app, lambda: not any(worker.isRunning() for worker in window.findChildren(QThread)))
            assert owner.submit(dependencies, request)
            spin(app, entered.is_set)
            window.close()  # 真实 MainWindow 经过持久化门禁，再撤权并等待物理提交退场。
            assert window.isVisible() and owner.is_running()
            assert not owner.submit(dependencies, request)
            app.processEvents()
            assert window.isVisible() and owner.is_running()
            release.set()
            spin(app, lambda: not owner.is_running())
            spin(app, lambda: not window.isVisible())
            assert token_seen == [True] and not results and owner.last_result is None
        finally:
            release.set()
            owner.close()
            spin(app, lambda: not any(worker.isRunning() for worker in window.findChildren(QThread)))
            window.close()
            spin(app, lambda: not window.isVisible())
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_application_failure_keeps_confirmed_result_separate_from_submit_failure(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    deps = ScanningDependencies("fixture", 1, tmp_path, tmp_path, tmp_path/"user.sqlite3",
                               tmp_path/"static.sqlite3", tmp_path, tmp_path, tmp_path)
    owner = VisionCommitController(lambda value: True, root,
        commit=lambda request, token: VisionCommitResult(2, {}))
    failures, apply_failures = [], []
    owner.failed.connect(lambda *args: failures.append(args))
    owner.application_failed.connect(lambda *args: apply_failures.append(args))
    original = owner._deliver
    def deliver(dependencies, result):
        original(dependencies, result)
        raise RuntimeError("injected application failure")
    owner._deliver = deliver
    try:
        assert owner.submit(deps, VisionCommitRequest.freeze(tmp_path/"user.sqlite3", {}, []))
        spin(app, lambda: not owner.is_running())
        assert not failures and len(apply_failures) == 1
        assert owner.last_result.snapshot_id == 2
    finally:
        owner.close(); root.deleteLater(); app.processEvents()


@pytest.mark.parametrize("action", ["normal", "cancel", "close", "generation", "new_request", "display_error"])
def test_actual_scan_completion_rechecks_delayed_continuation_and_saved_display_failure(tmp_path, monkeypatch, action):
    from src.features.scanning import workflow
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    deps = ScanningDependencies("fixture", 1, tmp_path, tmp_path, tmp_path/"user.sqlite3",
                               tmp_path/"static.sqlite3", tmp_path, tmp_path, tmp_path)
    current = [True]
    commit = Mock(side_effect=lambda request, token: VisionCommitResult(2, request.stats))
    owner = VisionCommitController(lambda value: current[0], root, commit=commit)
    window = SimpleNamespace(_vision_commit=owner, _manual_recovery=ManualRecoverySession(), btn_run=Mock(), dialog_parent=root,
        _pending_parse_only=False, _postprocess_vision_files=Mock(return_value={}),
        _refresh_home=Mock(), _start_allocation_worker=Mock())
    callback = lambda dependencies, result: workflow._on_vision_committed(window, dependencies, result)
    owner.committed.connect(callback)
    monkeypatch.setattr(workflow, "_scan_event", Mock())
    completed = Mock(side_effect=RuntimeError("injected result display failure") if action == "display_error" else None)
    monkeypatch.setattr(workflow, "show_scan_completion", completed)
    warning = Mock()
    monkeypatch.setattr(workflow.QMessageBox, "warning", warning)
    try:
        assert owner.submit(deps, VisionCommitRequest.freeze(tmp_path/"user.sqlite3", {}, []))
        spin(app, lambda: not owner.is_running())
        assert commit.call_count == 1 and owner.last_result.snapshot_id == 2
        assert completed.call_count == 1
        owner.committed.disconnect(callback)
        if action == "cancel": owner.cancel()
        elif action == "close": owner.close()
        elif action == "generation": current[0] = False; owner.reset_account_state()
        elif action == "new_request":
            assert owner.submit(deps, VisionCommitRequest.freeze(tmp_path/"user.sqlite3", {}, []))
            spin(app, lambda: not owner.is_running())
        until = time.monotonic() + .15
        spin(app, lambda: time.monotonic() >= until)
        assert window._start_allocation_worker.call_count == (1 if action == "normal" else 0)
        if action == "display_error":
            assert warning.call_count == 1
            assert "背包已保存 #2" in warning.call_args.args[2]
            assert owner.last_result.snapshot_id == 2 and commit.call_count == 1
    finally:
        owner.close(); spin(app, lambda: not owner.is_running())
        root.deleteLater(); app.processEvents()



def test_actual_cancelled_exit_keeps_owner_available_for_a_fresh_request(tmp_path):
    from src.features.scanning.controller import ScanningController
    from src.ui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    deps = ScanningDependencies("fixture", 1, tmp_path, tmp_path, tmp_path/"user.sqlite3",
                               tmp_path/"static.sqlite3", tmp_path, tmp_path, tmp_path)
    owner = VisionCommitController(lambda value: True, root,
        commit=lambda request, token: VisionCommitResult(2, {}))
    scanner = SimpleNamespace(_vision_commit=owner, _manual_recovery=ManualRecoverySession(), request_stop=owner.cancel)
    scanner.begin_close = lambda: ScanningController.begin_close(scanner)
    page = SimpleNamespace(task_owner=SimpleNamespace(is_writing=lambda: False),
        is_dirty=lambda: True, confirm_leave=lambda callback: False)
    context = SimpleNamespace(account=SimpleNamespace(active_account_id="fixture"), generation=1)
    window = SimpleNamespace(scanning_controller=scanner, close=Mock(), basic_weight_page=page,
                             app_context=context)
    event = Mock()
    try:
        MainWindow.closeEvent(window, event)  # 实际草稿转场门禁的取消分支。
        assert event.ignore.called and window._pending_page_transition is None
        assert owner.submit(deps, VisionCommitRequest.freeze(tmp_path/"user.sqlite3", {}, []))
        spin(app, lambda: not owner.is_running())
        assert owner.last_result.snapshot_id == 2
    finally:
        owner.close(); spin(app, lambda: not owner.is_running())
        root.deleteLater(); app.processEvents()



def test_parse_completion_without_vision_worker_uses_real_commit_owner(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from src.features.scanning import workflow
    from src.services.inventory.vision_commit import commit_vision_result

    app = QApplication.instance() or QApplication([])
    root = QWidget()
    deps = ScanningDependencies("fixture", 1, tmp_path, tmp_path, tmp_path/"user.sqlite3",
                               tmp_path/"static.sqlite3", tmp_path, tmp_path, tmp_path)
    entered, release = threading.Event(), threading.Event()
    threads, shown = [], []

    def commit(request, token):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return commit_vision_result(request, token)  # 真正非全量 Service 不建库、不写库存。

    owner = VisionCommitController(lambda value: value == deps, root, commit=commit)
    button = QPushButton(root)
    button.setEnabled(False)
    window = SimpleNamespace(_vision_commit=owner, _manual_recovery=ManualRecoverySession(), _vision_worker=None, _progress_dlg=None,
        _pending_parse_only=True, _scan_dependencies=deps, dialog_parent=root, btn_run=button,
        _postprocess_vision_files=Mock(return_value={}), _update_inventory_status=Mock(),
        _start_allocation_worker=Mock())
    owner.committed.connect(lambda dependencies, result: workflow._on_vision_committed(window, dependencies, result))
    owner.failed.connect(lambda dependencies, error: workflow._on_vision_commit_failed(window, dependencies, error))
    monkeypatch.setattr(workflow, '_scan_event', lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, 'show_scan_completion', lambda *args: shown.append(args))
    try:
        workflow._on_vision_done(window, {'parse_scope': 'semi', 'success_count': 0})
        spin(app, entered.is_set)
        assert owner.is_running() and not button.isEnabled()
        assert button.text() == '正在保存背包与整理截图...'
        release.set()
        spin(app, lambda: not owner.is_running() and not window._pending_parse_only)
        assert threads != [threading.get_ident()]
        assert button.isEnabled() and button.text() == '⚡  开始计算'
        assert owner.last_result.snapshot_id is None and len(shown) == 1
        window._update_inventory_status.assert_called_once()
        window._start_allocation_worker.assert_not_called()
        assert not (tmp_path/'user.sqlite3').exists()
    finally:
        release.set(); owner.close(); spin(app, lambda: not owner.is_running())
        root.deleteLater(); app.processEvents()
