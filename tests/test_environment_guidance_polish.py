# 验证同步引导、模式草稿和账号取消保持权限与账号代次边界。
import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.domain.work_mode import CheckState, FeatureCheck, WorkMode, WorkModeReport
from src.features.settings.work_mode_card import ModeReportDialog
from tests.work_modes.test_controller import controller, qt_app  # noqa: F401 - 复用无真实游戏的控制器夹具。


def report(state=CheckState.WAITING):
    return WorkModeReport(
        WorkMode.LOW, (FeatureCheck("sync", "同步", state, "等待游戏就绪"),),
        sync_enable_ready=True,
    )


def test_sync_button_only_invokes_injected_preflight_action():
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    start = Mock()
    dialog = ModeReportDialog(parent, SimpleNamespace(check=Mock()), sync_action_provider=lambda result: start)
    try:
        dialog.begin("low")
        dialog.set_report(report())
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "开启自动同步").click()
        start.assert_called_once_with()
        dialog.begin("low", preview=True)
        dialog.set_report(report())
        assert not any(button.text() == "开启自动同步" and not button.isHidden()
                       for button in dialog.findChildren(QPushButton))
    finally:
        dialog.close()
        parent.close()
        app.processEvents()


def test_detection_button_runs_fresh_preflight_before_saving_and_navigating(controller, qt_app):
    c, _, policy, _, _, _ = controller
    c._navigate = Mock()
    calls = []
    original_tick = c.runtime.tick
    c.runtime.tick = lambda **kwargs: (calls.append(kwargs), original_tick(**kwargs))[1]

    def confirm():
        policy.enable_auto_sync_after_preflight()
        return True

    c.attach_sync_enable_action(lambda: c.begin_sync_enable(confirm))
    c.sync_enable_action_for_report(report())()
    assert not policy.settings.auto_sync_enabled
    c._navigate.assert_not_called()
    c._observer.run_jobs()
    qt_app.processEvents()
    assert calls[0]["preview"] and not calls[0]["allow_connect"]
    assert policy.settings.auto_sync_enabled
    c._navigate.assert_called_once_with("home")


@pytest.mark.parametrize("state", (CheckState.FAULT, CheckState.MISSING, CheckState.CLEANUP_PENDING))
def test_sync_action_rejects_blockers_and_changed_account(controller, state):
    c, window, policy, _, _, _ = controller
    start = Mock()
    c.attach_sync_enable_action(start)
    assert c.sync_enable_action_for_report(report(state)) is None
    action = c.sync_enable_action_for_report(report())
    assert action is not None
    window.app_context.generation += 1
    action()
    start.assert_not_called()
    assert not policy.settings.auto_sync_enabled


@pytest.mark.parametrize("change", ("offline", "enabled"))
def test_sync_action_not_offered_for_offline_or_enabled_state(controller, change):
    c, _, policy, _, _, _ = controller
    c.attach_sync_enable_action(Mock())
    if change == "offline":
        policy.select_mode("offline")
    else:
        policy.set_auto_sync_enabled(True)
    assert c.sync_enable_action_for_report(report()) is None


def test_sync_navigation_waits_for_success_and_is_cancelled_with_context(controller, qt_app):
    c, window, policy, _, _, _ = controller
    c._navigate = Mock()
    c.attach_sync_enable_action(Mock())
    c.sync_enable_action_for_report(report())()
    c._navigate.assert_not_called()
    c.finish_report_sync_guidance(False)
    qt_app.processEvents()
    c._navigate.assert_not_called()
    c.sync_enable_action_for_report(report())()
    policy.set_auto_sync_enabled(True)
    c.finish_report_sync_guidance(True)
    qt_app.processEvents()
    c._navigate.assert_called_once_with("home")
    c._navigate.reset_mock()
    policy.set_auto_sync_enabled(False)
    c.sync_enable_action_for_report(report())()
    policy.set_auto_sync_enabled(True)
    c.finish_report_sync_guidance(True)
    window.app_context.generation += 1
    qt_app.processEvents()
    c._navigate.assert_not_called()


def test_incomplete_report_does_not_offer_sync(controller):
    c, _, _, _, _, _ = controller
    c.attach_sync_enable_action(Mock())
    incomplete = WorkModeReport(WorkMode.LOW, (
        FeatureCheck("sync", "同步", CheckState.WAITING, "尚未检测", facts=(("inspection_incomplete", True),)),
    ), sync_enable_ready=True)
    assert c.sync_enable_action_for_report(incomplete) is None


def test_upgrade_mode_placeholder_preserves_confirmed_mode_and_accepts_same_mode(controller):
    from PySide6.QtWidgets import QComboBox

    c, window, policy, _, _, _ = controller
    combo = QComboBox(window)
    combo.addItem("低风险", "low")
    c.attach_controls(combo, QLabel(window), QPushButton(window))
    c.open_settings = Mock()
    policy.set_paused(True)
    frozen = policy.settings
    c._upgrade_action("mode")
    assert combo.currentIndex() == -1
    assert policy.settings == frozen
    c.refresh_controls()
    assert combo.currentIndex() == -1
    c.select_mode("low")
    assert policy.settings.mode == WorkMode.LOW and not policy.settings.paused
    assert combo.currentData() == "low"


def test_upgrade_same_mode_cancel_restores_selection_without_resuming(controller, monkeypatch):
    from PySide6.QtWidgets import QComboBox
    from src.ui.controllers import work_mode_controller

    c, window, policy, _, _, _ = controller
    combo = QComboBox(window)
    combo.addItem("低风险", "low")
    c.attach_controls(combo, QLabel(window), QPushButton(window))
    c.open_settings = Mock()
    policy.set_paused(True)
    frozen = policy.settings
    c._upgrade_action("mode")
    monkeypatch.setattr(work_mode_controller, "confirm_mode", lambda *_args: False)
    c.select_mode("low")
    assert combo.currentData() == "low" and policy.settings == frozen


def test_account_manager_new_and_delete_keep_existing_cancellation_guards(monkeypatch):
    from PySide6.QtWidgets import QDialog
    from src.features.accounts import manager as account_module

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    manager = SimpleNamespace(
        read_index=lambda: {"accounts": [{"id": "test", "name": "测试"}]},
        account_meta=lambda _account: {"name": "测试"}, create_account=Mock(), delete_account=Mock(),
    )
    # 调用正式删除冻结入口：最后账号应在读索引阶段被拒绝，不伪造目标、不触文件。
    manager.prepare_account_deletion = lambda account_id: account_module.AccountManager.prepare_account_deletion(
        manager, account_id)
    switch_account = Mock()
    monkeypatch.setattr(account_module.QInputDialog, "getText", lambda *_args: ("", False))
    warning = Mock()
    monkeypatch.setattr(account_module.QMessageBox, "information", warning)

    def inspect(dialog):
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        buttons["新建"].click()
        manager.create_account.assert_not_called()
        buttons["删除"].click()
        manager.delete_account.assert_not_called()
        switch_account.assert_not_called()
        warning.assert_called_once()
        return QDialog.Rejected

    monkeypatch.setattr(account_module.QDialog, "exec", inspect)
    account_module.show_account_manager_dialog(owner, "", manager, "test", switch_account, Mock())
    owner.close()
    owner.deleteLater()
    app.processEvents()
