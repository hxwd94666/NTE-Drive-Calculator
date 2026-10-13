# 验证正式窗口关闭时普通计算与已接受保存先撤权再等真实线程退场。
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from src.features.allocation import runner
from src.features.allocation import save_workflow
from src.services.allocation.locks import AllocationLockSnapshot
from src.features.onboarding.guide import OnboardingGuide
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.services.allocation.filter_settings import AllocationFilterSettings
from src.ui import app as app_module
from src.ui.application_runtime import prepare_application_runtime
from src.ui.controllers.auto_sync_controller import AutoSyncController
from src.ui.controllers.work_mode_controller import WorkModeController
from src.utils.logger import logger


NTE_TEST_TIER = "core"


def spin_until(predicate, timeout_ms=12000):
    if predicate():
        return
    loop = QEventLoop()
    poll, deadline = QTimer(), QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert predicate(), "真实线程/窗口未在验证上限内达到终态"


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    qt.setStyle("Fusion")
    logger.remove()
    workspace = Path(os.environ.get("NTE_RETIREMENT_WORKSPACE", Path(__file__).resolve().parents[2]))
    monkeypatch.setenv("NTE_GAME_STATIC_DB", str(workspace / "data/game_static.sqlite3"))
    def forbidden(*args, **kwargs):
        raise AssertionError("回归不派发外部操作")
    with patch.object(AutoSyncController, "start"), patch.object(WorkModeController, "start"), \
         patch.object(WorkModeController, "start_upgrade_guidance"), \
         patch.object(app_module.MainWindow, "_maybe_check_updates_on_startup"), \
         patch.object(OnboardingGuide, "maybe_show"), \
         patch("src.ui.controllers.workshop_refresh_controller.start_workshop_weight_template_refresh", return_value=None), \
         patch("urllib.request.urlopen", forbidden), patch("subprocess.Popen", forbidden):
        runtime = prepare_application_runtime(package_root=workspace, app_dir=tmp_path, data_root=tmp_path / "data")
        window = app_module.MainWindow(runtime)
        owner = window.scanning_controller._allocation_controller
        window.scanning_controller.build_page()
        monkeypatch.setattr(window.global_hotkey_manager, "start", lambda **kwargs: None)
        window.show()
        spin_until(lambda: not any(worker.isRunning() for worker in window.findChildren(QThread)))
        yield qt, window, owner
        # 即使原行为断言失败，夹具也先物理收尾再释放正式窗口，防止掩盖崩溃。
        spin_until(lambda: not any(worker.isRunning() for worker in window.findChildren(QThread)))
        if window.isVisible():
            window.close()
        spin_until(lambda: not window.isVisible())
        window.deleteLater()
        qt.sendPostedEvents(None, QEvent.DeferredDelete)


def start(owner):
    owner.start(strategy="role_priority", selected_roles=[], custom_sets={}, tape_main_filters={},
                crit_priority_modes={}, set_effect_modes={}, priority_groups=[], crit_rate_caps={},
                crit_rate_baselines={}, custom_weapons={}, filter_settings=AllocationFilterSettings())


def test_calculation_close_waits_for_physical_exit_and_rejects_late_delivery(desktop, monkeypatch):
    qt, window, owner = desktop
    release, entered = threading.Event(), threading.Event()
    callbacks, work_calls, saved = [], [], []
    with StaticCatalogReleaseReader().read_unit(window.app_context.paths.equipment_allocation_database_path) as unit:
        static_release = unit.release
    def calculate(*args, **kwargs):
        work_calls.append(1)
        entered.set()
        assert release.wait(12), "测试释放信号未到达"
        return runner.AllocationRunResult(
            plans={}, snapshot_id=1, lock_snapshot=None, selected_locked_role_names=frozenset(),
            static_database_path=static_release.database_path, static_dataset_id=static_release.dataset_id,
            static_file_identity=(0, 0), context_identity=kwargs["expected_context_identity"],
            run_id=kwargs["expected_run_id"], static_release=static_release,
        )
    monkeypatch.setattr(owner, "_run_allocation", calculate)
    monkeypatch.setattr(owner, "_render_results", lambda result: callbacks.append(result))
    monkeypatch.setattr(owner, "_save_alloc", lambda **kwargs: saved.append(1) or True)
    start(owner)
    spin_until(entered.is_set)
    worker = owner._worker
    began = time.perf_counter()
    window.close()  # 调用正式 MainWindow.closeEvent，而非手写关闭状态。
    close_ms = (time.perf_counter() - began) * 1000
    visible_while_live = window.isVisible()
    alive_after_close = worker.isRunning()
    cancelled = owner._cancel_event.is_set()
    qt.processEvents()
    retained_before_release = window.isVisible() and worker.isRunning()
    release.set()
    spin_until(lambda: not isValid(worker) or not worker.isRunning())
    spin_until(lambda: not window.isVisible())
    qt.processEvents()
    late_callbacks = len(callbacks)
    before = len(work_calls)
    try:
        start(owner)
        rejected_start = False
    except RuntimeError:
        rejected_start = True
    if owner._worker is not None:
        spin_until(lambda: not owner.is_running())
    rejected_save = owner.save(show_message=False) is False
    evidence = dict(close_ms=close_ms, visible_while_live=visible_while_live, alive_after_close=alive_after_close,
                    retained_before_release=retained_before_release, cancelled=cancelled,
                    late_callbacks=late_callbacks, rejected_start=rejected_start, rejected_save=rejected_save,
                    new_work=len(work_calls)-before, save_dispatches=len(saved))
    print("CALCULATION_RETIREMENT", json.dumps(evidence, sort_keys=True))
    assert alive_after_close and cancelled
    assert visible_while_live and retained_before_release
    assert close_ms < 1000
    assert late_callbacks == 0
    assert rejected_start and rejected_save and len(work_calls) == before and not saved


@pytest.mark.parametrize("save_outcome", ["committed", "failed"])
def test_accepted_save_close_keeps_real_worker_and_event_loop_alive(desktop, monkeypatch, save_outcome):
    _, window, owner = desktop
    release, entered = threading.Event(), threading.Event()
    observed, deliveries, notices, receipts = {}, [], [], []
    with StaticCatalogReleaseReader().read_unit(window.app_context.paths.equipment_allocation_database_path) as unit:
        static_release = unit.release
    owner.final_plan = {"role": {"valid": True, "score": 7.0}}
    owner._pending_allocation_snapshot_id = 1
    owner._pending_allocation_static_release = static_release
    owner._pending_allocation_static_identity = (static_release.database_path, static_release.dataset_id, (0, 0))
    owner._allocation_lock_snapshot = AllocationLockSnapshot(1, frozenset(), frozenset(), ())
    monkeypatch.setattr(runner, "_select_allocation_save_slots", lambda *args: {"role": (1004, None)})
    monkeypatch.setattr(save_workflow, "selected_legacy_comparison_diffs", lambda *args: {"role": {}})
    monkeypatch.setattr(owner, "_render_results", lambda result: deliveries.append(result))
    monkeypatch.setattr(save_workflow, "show_allocation_save_success", lambda *args: notices.append("success"))
    monkeypatch.setattr(save_workflow.QMessageBox, "critical", lambda *args: notices.append("failure"))
    def accepted_write(**kwargs):
        # 仅替代 writer 耗时/回执，不替代正式 owner.save、模态 QThread 或其交付尾段。
        assert kwargs["rows"][0]["score"] == 7.0
        entered.set()
        assert release.wait(12), "已接受保存未收到显式释放"
        if save_outcome == "failed":
            raise RuntimeError("accepted writer failed before receipt")
        receipts.append(1)
        return 1
    monkeypatch.setattr(save_workflow, "save_allocation_plans", accepted_write)
    poll = QTimer()
    poll.setInterval(5)
    def inspect_then_release():
        observed["visible_before_release"] = window.isVisible()
        observed["physical_before_release"] = owner._save_worker.isRunning()
        observed["cancelled"] = owner._cancel_event.is_set()
        owner._saving = False
        observed["physical_busy_without_projection"] = owner.is_running()
        owner._saving = True
        release.set()
    def close_when_running():
        if not entered.is_set():
            return
        poll.stop()
        began = time.perf_counter()
        window.close()
        observed["close_ms"] = (time.perf_counter() - began) * 1000
        observed["visible_after_close"] = window.isVisible()
        observed["physical_after_close"] = owner._save_worker.isRunning()
        QTimer.singleShot(0, inspect_then_release)
    poll.timeout.connect(close_when_running)
    poll.start()
    try:
        result = owner.save()  # 正式公开保存入口及完整成功/失败尾段。
    finally:
        release.set()
        poll.stop()
    spin_until(lambda: not window.isVisible())
    print("SAVE_RETIREMENT", json.dumps(observed, sort_keys=True), save_outcome,
          "result", result, "deliveries", len(deliveries), "notices", notices, "receipts", receipts)
    assert result is (save_outcome == "committed") and owner._save_worker is None
    assert receipts == ([1] if save_outcome == "committed" else [])
    assert not deliveries and not notices
    assert observed["physical_after_close"] and observed["physical_before_release"] and observed["cancelled"]
    assert observed["physical_busy_without_projection"]
    assert observed["visible_after_close"] and observed["visible_before_release"]
    assert observed["close_ms"] < 1000


def test_mode_stop_rejects_late_payload_and_account_switch_stays_blocked_until_exit(desktop, monkeypatch):
    qt, window, owner = desktop
    entered, release = threading.Event(), threading.Event()
    deliveries, notices = [], []
    identity_before = (window.app_context.generation, window.app_context.account.active_account_id)
    with StaticCatalogReleaseReader().read_unit(window.app_context.paths.equipment_allocation_database_path) as unit:
        static_release = unit.release
    def calculate(*args, **kwargs):
        entered.set()
        assert release.wait(12)
        return runner.AllocationRunResult(
            plans={}, snapshot_id=1, lock_snapshot=None, selected_locked_role_names=frozenset(),
            static_database_path=static_release.database_path, static_dataset_id=static_release.dataset_id,
            static_file_identity=(0, 0), context_identity=kwargs["expected_context_identity"],
            run_id=kwargs["expected_run_id"], static_release=static_release,
        )
    monkeypatch.setattr(owner, "_run_allocation", calculate)
    monkeypatch.setattr(owner, "_render_results", lambda result: deliveries.append(result))
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *args: notices.append(1))
    start(owner)
    spin_until(entered.is_set)
    try:
        assert window._switch_account("pending-other-account") is False
        assert (window.app_context.generation, window.app_context.account.active_account_id) == identity_before
        window.work_mode_controller._stop_live_work()  # 正式模式降级使用的撤权链。
        assert owner._cancel_event.is_set() and owner.is_running()
        assert window._switch_account("pending-other-account") is False
    finally:
        release.set()
    spin_until(lambda: not owner.is_running())
    qt.processEvents()
    assert not deliveries and len(notices) == 2
    assert owner.btn_run.isEnabled()
    start(owner)  # 非终局取消允许显式的新只读计算，不永久关闭 owner。
    spin_until(lambda: not owner.is_running())
    qt.processEvents()
    assert len(deliveries) == 1
    print("MODE_ACCOUNT_RETIREMENT", json.dumps(dict(stale_deliveries=0, blocked_switches=2,
                                                      new_explicit_calculation_delivered=1)))
