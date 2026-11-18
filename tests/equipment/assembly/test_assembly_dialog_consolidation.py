# 验证合并后的确认层数、无说明极速选择、后台输入避让及线程退场后的原窗结果。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QWidget

from src.features.inventory.equipment.assembly import assembly_controller as fast
from src.features.inventory.equipment.assembly import automatic_assembly_controller as automatic
from src.features.inventory.equipment.assembly import automatic_assembly_dialog as confirmation
from src.features.inventory.equipment.assembly.assembly_surface import AssemblySurface
from src.domain.equipment.assembly.assembly_progress import AssemblyTarget
from src.features.inventory.equipment.management.batch_selection_dialog import BatchSelectionDialog
from src.features.inventory.equipment.assembly.slot_selection_dialog import select_assembly_slot_ids
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test

NTE_TEST_TIER = "core"


class PendingWorker(QObject):
    result_ready = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def finish(self, *, value=None, error=None):
        if error is not None:
            self.error.emit(error)
        else:
            self.result_ready.emit(value)
        self.running = False
        self.finished.emit()


class Owner(QWidget):
    def __init__(self, path):
        super().__init__()
        self.calls = []
        self.user_database_path = path
        self.operation_entry = Mock(return_value=True)
        self.operation_guard = lambda _capability: None
        self.operation_unavailable = Mock()
        self._ui_preferences = {}
        self._save_ui_preferences = Mock()
        self._inventory_sync_service = SimpleNamespace(is_running=True)
        self.global_hotkey_manager = SimpleNamespace(
            active_owner=None, configuration=SimpleNamespace(stop="F8"), start=Mock(), stop=Mock()
        )
        self._go = lambda key: self.calls.append(key)
        self._refresh_equip = Mock()

    def showMinimized(self):
        self.calls.append("minimized")
        super().showMinimized()

    def showNormal(self):
        self.calls.append("restored")
        super().showNormal()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def owner(app, tmp_path, monkeypatch):
    window = Owner(tmp_path / "fixture.sqlite3")
    monkeypatch.setattr(fast, "WorkerThread", PendingWorker)
    monkeypatch.setattr(automatic, "WorkerThread", PendingWorker)
    monkeypatch.setattr(fast, "BulkEquipmentApplyService", Mock())
    monkeypatch.setattr(
        fast, "freeze_assembly_targets", lambda *_args: (AssemblyTarget(11, 1003, "角色", "主力", 230, 1, 1),)
    )
    monkeypatch.setattr(automatic, "_sqlite_automatic_assembly_state", lambda *_args, **_kwargs: {"角色": {}})
    monkeypatch.setattr(automatic, "_assert_automatic_state_current", lambda *_args: None)
    monkeypatch.setattr(automatic, "_assembly_runtime_paths", lambda _window: (Path("roles"), Path("record")))
    monkeypatch.setattr(fast.QMessageBox, "information", Mock())
    monkeypatch.setattr(fast.QMessageBox, "warning", Mock())
    monkeypatch.setattr("src.features.inventory.equipment.assembly.assembly_surface.winsound.MessageBeep", Mock())
    yield window
    for attribute in ("_equipment_apply_worker", "_automatic_equipment_apply_worker"):
        worker = getattr(window, attribute, None)
        if worker is not None and worker.isRunning():
            worker.finish(error="fixture teardown")
    for dialog in window.findChildren(QDialog):
        dialog.reject()
        dialog.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()


def accepted_surface(window, _roles, key):
    dialog = confirmation.AutomaticAssemblyDialog(window, 1, stop_hotkey=key)
    dialog.accept()
    window._assembly_surface = dialog
    return dialog, {}


def test_native_selection_has_no_information_and_reuses_its_window_for_progress_result(owner, app, monkeypatch):
    choices = (
        SimpleNamespace(
            character_id=1003, slot_id=11, slot_key="primary", slot_name="主力", role_name="角色", plan={"score": 230}
        ),
    )
    captured = []

    def choose():
        dialog = owner.findChild(BatchSelectionDialog)
        captured.append(dialog)
        assert not any(
            label.isVisible() and ("勾选本次" in label.text() or "点击角色" in label.text())
            for label in dialog.findChildren(QLabel)
        )
        assert dialog.buttons.button(QDialogButtonBox.Ok).text() == "开始极速装配"
        dialog.set_all(True)
        dialog.buttons.button(QDialogButtonBox.Ok).click()

    QTimer.singleShot(0, choose)
    assert select_assembly_slot_ids(owner, choices, native=True) == [11]
    surface = captured[0]
    fast._start_nte_core_equipment_apply(owner, [], slot_ids=[11])
    assert owner._assembly_surface is surface and surface.running
    worker = owner._equipment_apply_worker
    worker.result_ready.emit({"applied": [{"role_name": "角色", "plan_id": 1, "verified": True}]})
    assert surface.running
    owner.global_hotkey_manager.stop.assert_not_called()
    worker.running = False
    worker.finished.emit()
    assert not surface.running and surface.isVisible()
    assert surface.windowTitle() == "装配完成"
    assert "已下发 1" in surface.status_label.text()
    assert surface.progress_model.confirmed == 1
    assert surface.progress_model.rows[0].target.score == 230
    assert len(owner.findChildren(AssemblySurface)) == 1


def test_automatic_confirmation_has_only_count_summary_and_inline_name(owner):
    dialog = confirmation.AutomaticAssemblyDialog(owner, 3, stop_hotkey="F8", needs_name=True, medium=True)
    assert dialog.summary.text() == "将自动装配 3 名角色 · 每人一套计算配装"
    assert not any(label.pixmap() is not None and not label.pixmap().isNull() for label in dialog.findChildren(QLabel))
    assert not dialog.start_button.isEnabled()
    dialog.name_edit.setText("演示主角")
    assert dialog.start_button.isEnabled()
    assert any("F8" in label.text() for label in dialog.findChildren(QLabel))
    assert any("中风险" in label.text() for label in dialog.findChildren(QLabel))
    assert any("重复驱动" in label.text() for label in dialog.findChildren(QLabel))
    dialog.reject()


def test_automatic_hides_owned_dialogs_until_worker_finished_then_reuses_confirmation(owner, app, monkeypatch):
    monkeypatch.setattr(automatic, "confirm_automatic_execution", accepted_surface)
    monkeypatch.setattr(automatic, "_assembly_report_dialog", lambda *_args: ("装配完成", "1名已确认", True))
    owner.show()
    automatic._start_automatic_equipment_assembly(owner, ["角色"])
    worker = owner._automatic_equipment_apply_worker
    surface = owner._assembly_surface
    assert owner.isMinimized()
    assert all(not dialog.isVisible() for dialog in owner.findChildren(QDialog))
    worker.result_ready.emit(object())
    assert owner.calls == ["minimized"] and not surface.isVisible()
    worker.running = False
    worker.finished.emit()
    app.processEvents()
    assert owner.calls == ["minimized", "restored", "equipment"]
    assert surface.isVisible() and surface.status_label.text() == "1名已确认"
    fast.QMessageBox.information.assert_not_called()
    fast.QMessageBox.warning.assert_not_called()
    owner.global_hotkey_manager.stop.assert_called_once_with(owner="automatic_equipment_apply")


def test_automatic_keeps_mouse_execution_and_configured_stop_key(owner, monkeypatch):
    monkeypatch.setattr(automatic, "confirm_automatic_execution", accepted_surface)
    execute = Mock(return_value=object())
    monkeypatch.setattr(automatic, "execute_selected_role_from_current_game_page", execute)
    automatic._start_automatic_equipment_assembly(owner, ["角色"])
    worker = owner._automatic_equipment_apply_worker
    worker.target()
    arguments = execute.call_args.kwargs
    assert set(arguments) == {
        "backend",
        "template_dir",
        "record_root",
        "role_name_aliases",
        "should_stop",
        "on_role_progress",
    }
    assert not arguments["should_stop"]()
    owner.global_hotkey_manager.start.call_args.kwargs["on_stop"]()
    assert arguments["should_stop"]()
    assert not owner._assembly_surface.isVisible()


@pytest.mark.parametrize("reason", ["cancel", "permission", "account_changed"])
def test_unconfirmed_or_stale_automatic_request_never_dispatches_or_saves_name(owner, monkeypatch, reason):
    owner.app_context = SimpleNamespace(
        generation=1, account=SimpleNamespace(active_account_id="fixture", user_database_path=owner.user_database_path)
    )
    if reason == "permission":
        owner.operation_entry.return_value = False

    def confirm(window, roles, key):
        if reason == "cancel":
            return None
        result = accepted_surface(window, roles, key)
        if reason == "account_changed":
            owner.app_context.generation += 1
        return result

    monkeypatch.setattr(automatic, "confirm_automatic_execution", confirm)
    automatic._start_automatic_equipment_assembly(owner, ["角色"])
    assert not hasattr(owner, "_automatic_equipment_apply_worker")
    assert "minimized" not in owner.calls
    owner.global_hotkey_manager.start.assert_not_called()
    owner._save_ui_preferences.assert_not_called()


def test_automatic_error_is_delayed_and_cannot_refresh_a_new_account(owner, monkeypatch):
    owner.app_context = SimpleNamespace(
        generation=1, account=SimpleNamespace(active_account_id="fixture", user_database_path=owner.user_database_path)
    )
    monkeypatch.setattr(automatic, "confirm_automatic_execution", accepted_surface)
    automatic._start_automatic_equipment_assembly(owner, ["角色"])
    surface = owner._assembly_surface
    owner.app_context.generation += 1
    owner._automatic_equipment_apply_worker.finish(error="执行中断")
    assert not surface.isVisible() and owner.calls == ["minimized"]
    owner._refresh_equip.assert_not_called()


@pytest.mark.parametrize("sync", [None, SimpleNamespace(is_running=False)])
def test_native_closed_sync_keeps_selection_and_never_dispatches(owner, sync, monkeypatch):
    owner._inventory_sync_service = sync
    factory = Mock()
    monkeypatch.setattr(fast, "BulkEquipmentApplyService", factory)
    fast._start_nte_core_equipment_apply(owner, [], slot_ids=[11])
    factory.assert_not_called()
    assert not hasattr(owner, "_equipment_apply_worker")
    owner.operation_unavailable.assert_called_once()
    assert not owner.findChildren(AssemblySurface)


def test_long_result_keeps_close_action_inside_available_screen(owner, app):
    from PySide6.QtCore import QRect, QSize
    from src.app.window_geometry import fit_dialog_to_available_screen

    dialog = AssemblySurface(owner)
    dialog.configure_targets((), automatic=True)
    dialog.begin_running("装配", "正在装配")
    dialog.show_result("部分装配结果", "\n".join(f"角色{index}：缺少驱动，已停止后续装配。" for index in range(80)))
    available = QRect(0, 0, 640, 480)
    fit_dialog_to_available_screen(dialog, QSize(560, 360), available_geometry=available)
    app.processEvents()
    assert available.contains(dialog.frameGeometry())
    assert dialog.rect().contains(dialog.close_button.mapTo(dialog, dialog.close_button.rect().center()))
    assert dialog.close_button.isVisible()
    dialog.reject()


def test_changed_plan_after_confirmation_does_not_start_game_input(owner, monkeypatch):
    monkeypatch.setattr(automatic, "confirm_automatic_execution", accepted_surface)
    monkeypatch.setattr(automatic, "_assert_automatic_state_current", Mock(side_effect=RuntimeError("所选配装已变化")))
    automatic._start_automatic_equipment_assembly(owner, ["角色"])
    assert not hasattr(owner, "_automatic_equipment_apply_worker")
    owner.global_hotkey_manager.start.assert_not_called()
    assert "minimized" not in owner.calls
    assert "没有派发" in owner._assembly_surface.status_label.text()


def test_back_to_selection_restores_selected_slot_draft_without_starting(owner, monkeypatch):
    dao = Mock()
    dao.__enter__ = Mock(return_value=dao)
    dao.__exit__ = Mock(return_value=None)
    service = Mock()
    service.list_current.return_value = (SimpleNamespace(role_name="角色"),)
    monkeypatch.setattr(automatic, "UserDataDao", lambda *_args: dao)
    monkeypatch.setattr(automatic, "LoadoutSlotSelectionService", lambda *_args: service)
    chooser = Mock(side_effect=[[11], [12]])
    start = Mock(side_effect=[confirmation.BACK_TO_SELECTION, None])
    monkeypatch.setattr(automatic, "select_assembly_slot_ids", chooser)
    monkeypatch.setattr(automatic, "_start_automatic_equipment_assembly", start)
    automatic._preview_automatic_assemble_all_roles(owner)
    assert chooser.call_args_list[1].kwargs["selected_slot_ids"] == (11,)
    assert start.call_args_list[1].kwargs["slot_ids"] == [12]
    owner.global_hotkey_manager.start.assert_not_called()


def test_restored_selection_keeps_alternate_slot_and_score(owner):
    from src.features.inventory.equipment.management.batch_selection_dialog import BatchChoice

    dialog = BatchSelectionDialog(
        owner,
        [
            BatchChoice(
                1003,
                "角色",
                character_id=1003,
                options=((11, "主力"), (12, "备用")),
                default_id=11,
                option_scores=((11, 230.0), (12, 240.0)),
            )
        ],
        initial_ids=(12,),
        title="选择",
        description="",
        accept_text="下一步",
    )
    assert dialog.selected_ids() == [12]
    assert dialog._cards[0].score_label.text() == "240"
    dialog.reject()


def test_back_and_cancel_do_not_save_protagonist_name(owner, monkeypatch):
    monkeypatch.setattr(confirmation.AutomaticAssemblyDialog, "exec", lambda _dialog: 2)
    assert confirmation.confirm_automatic_execution(owner, ["零"], "F8") is confirmation.BACK_TO_SELECTION
    owner._save_ui_preferences.assert_not_called()


def test_native_role_progress_distinguishes_dispatch_then_unknown_result(owner, app):
    from PySide6.QtTest import QTest
    from time import monotonic

    fast._start_nte_core_equipment_apply(owner, [], slot_ids=[11])
    worker = owner._equipment_apply_worker
    worker.target()
    publish = fast.BulkEquipmentApplyService.return_value.run.call_args.kwargs["progress_callback"]
    publish({"plan_id": 1, "state": "waiting", "dispatched": True, "phase": "verifying"})
    surface = owner._assembly_surface
    # 检查排队事件最终交付，不把系统繁忙下的定时器延迟固化为 100ms 精确时序。
    deadline = monotonic() + 2
    while surface.progress_model.dispatched != 1 and monotonic() < deadline:
        QTest.qWait(20)
    assert surface.progress_model.dispatched == 1 and surface.progress_model.confirmed == 0
    worker.finish(value={"applied": [{"plan_id": 1, "verified": False}]})
    assert surface.isVisible() and surface.windowTitle() == "装配结果待核对"
    assert surface.progress_model.rows[0].state == "unknown"
    fast.QMessageBox.warning.assert_not_called()


def test_dismissed_native_window_does_not_reopen_for_error_or_late_progress(owner, app):
    fast._start_nte_core_equipment_apply(owner, [], slot_ids=[11])
    worker = owner._equipment_apply_worker
    worker.target()
    publish = fast.BulkEquipmentApplyService.return_value.run.call_args.kwargs["progress_callback"]
    surface = owner._assembly_surface
    surface.reject()
    worker.finish(error="interrupted")
    publish({"plan_id": 1, "state": "confirmed", "verified": True, "scope": "full"})
    app.processEvents()
    assert not surface.isVisible() and surface.progress_model.confirmed == 0
    fast.QMessageBox.warning.assert_not_called()
    fast.QMessageBox.information.assert_not_called()


def test_mixed_sources_never_silently_dispatch_only_native_subset(owner, monkeypatch):
    from contextlib import nullcontext

    selections = (
        SimpleNamespace(slot_id=11, role_name="原生角色", plan={"source_snapshot_id": 1}),
        SimpleNamespace(slot_id=12, role_name="视觉角色", plan={"source_snapshot_id": 2}),
    )
    dao = SimpleNamespace(
        inventory_snapshot_summary=lambda identity: {"source": "nte_core" if identity == 1 else "vision"}
    )
    monkeypatch.setattr(fast, "UserDataDao", lambda *_args: nullcontext(dao))
    monkeypatch.setattr(
        fast,
        "LoadoutSlotSelectionService",
        lambda _dao: SimpleNamespace(list_current=lambda: selections, resolve=lambda *_args, **_kwargs: selections),
    )
    monkeypatch.setattr(fast, "select_assembly_slot_ids", lambda *_args, **_kwargs: [11, 12])
    fast._preview_nte_core_assemble_all_roles(owner)
    fast.BulkEquipmentApplyService.assert_not_called()
    assert not hasattr(owner, "_equipment_apply_worker")
    assert "视觉角色" in owner._assembly_surface.status_label.text()


def test_automatic_partial_stop_retains_rows_after_worker_and_input_exit(owner, app, monkeypatch):
    from dataclasses import asdict
    from src.features.drive_assembly.input_backends import (
        ActionExecutionReport,
        AssemblyExecutionReport,
        AssemblyExecutionStopped,
    )

    targets = (AssemblyTarget(11, 1003, "A", "主力", 230, 1, 1), AssemblyTarget(12, 1004, "B", "备用", 240, 2, 1))
    state = {target.role_name: {"_assembly_target": asdict(target)} for target in targets}
    monkeypatch.setattr(automatic, "_sqlite_automatic_assembly_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(automatic, "confirm_automatic_execution", accepted_surface)

    def execute(_state, **kwargs):
        kwargs["on_role_progress"](
            {"role_name": "A", "state": "confirmed", "verified": True, "scope": "full", "dispatched": True}
        )
        kwargs["on_role_progress"]({"role_name": "B", "state": "operating"})
        raise AssemblyExecutionStopped(
            "stopped",
            report=AssemblyExecutionReport(
                role_reports=[ActionExecutionReport("A", 3)], verified_roles=["A"], current_role="B", stopped=True
            ),
        )

    monkeypatch.setattr(automatic, "execute_all_roles_from_current_game_page", execute)
    owner.show()
    automatic._start_automatic_equipment_assembly(owner, [], slot_ids=[11, 12])
    worker = owner._automatic_equipment_apply_worker
    surface = owner._assembly_surface
    report = worker.target()
    assert not surface.isVisible() and owner.isMinimized()
    worker.finish(value=report)
    app.processEvents()
    assert surface.isVisible() and not surface.running
    assert [row.state for row in surface.progress_model.rows] == ["confirmed", "unknown"]
    assert [row.target.score for row in surface.progress_model.rows] == [230, 240]
    assert "图纸校验通过 1/2" in surface.status_view.facts.text()




@pytest.mark.parametrize("outcome", ["success", "preflight", "unknown", "cancelled", "error"])
def test_fast_window_chain_has_no_transient_unowned_windows(owner, app, monkeypatch, outcome):
    from PySide6.QtCore import QEvent

    shown = []

    class Shows(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
                shown.append((isinstance(watched, AssemblySurface), watched.parentWidget() is owner))
            return False

    probe = Shows()
    owner.show()
    app.processEvents()
    app.installEventFilter(probe)
    try:
        choices = (
            SimpleNamespace(
                character_id=1003,
                slot_id=11,
                slot_key="primary",
                slot_name="主力",
                role_name="角色",
                plan={"score": 230},
            ),
        )

        def choose():
            dialog = owner.findChild(BatchSelectionDialog)
            dialog.set_all(True)
            dialog.accept()

        QTimer.singleShot(0, choose)
        assert select_assembly_slot_ids(owner, choices, native=True) == [11]
        fast._start_nte_core_equipment_apply(owner, [], slot_ids=[11])
        surface = owner._assembly_surface
        worker = owner._equipment_apply_worker
        if outcome == "error":
            worker.finish(error="preflight interrupted")
        elif outcome == "preflight":
            worker.finish(value={"preflight_errors": [{"role_name": "角色", "error": "缺少方案输入"}]})
        elif outcome == "cancelled":
            surface.close()
            worker.finish(value={"failed_role": "角色", "failure_kind": "cancelled"})
        else:
            worker.finish(value={"applied": [{"plan_id": 1, "role_name": "角色", "verified": outcome == "success"}]})
        assert surface.isVisible() == (outcome != "cancelled") and not surface.running
        surface.close()
        app.processEvents()
        # 构造、执行、停止与结果中，只允许本 owner 的正式装配窗口显示。
        assert shown and all(is_surface and owned for is_surface, owned in shown)
        fast.QMessageBox.warning.assert_not_called()
        fast.QMessageBox.information.assert_not_called()
    finally:
        app.removeEventFilter(probe)


def test_native_explicit_database_switch_discards_old_callback(owner, tmp_path):
    fast._start_nte_core_equipment_apply(owner, [], slot_ids=[11])
    surface = owner._assembly_surface
    owner.user_database_path = tmp_path / "other-account.sqlite3"
    owner._equipment_apply_worker.finish(value={"applied": [{"role_name": "角色", "plan_id": 1, "verified": True}]})
    assert not surface.isVisible()
    assert surface.progress_model.confirmed == 0
    owner._refresh_equip.assert_not_called()
