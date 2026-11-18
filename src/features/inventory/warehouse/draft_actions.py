# 撤销当前仓库全部未保存状态，保持库存事实、账号规则与已派发操作不变。
import winsound

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QMessageBox

from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.common.text_catalog import set_button_text

from src.features.inventory.warehouse.management.management_projection import _state_projection, _project_states


def has_warehouse_draft(host):
    owner = host.warehouse_read_controller
    return bool(
        host._warehouse_pending_state_changes
        or owner.manual_targets
        or owner.recommendation_targets
        or owner.recommendation_guard
        or owner.conflicts
        or owner.management_issues
    )


def _busy(host):
    manager = getattr(host, "warehouse_management_controller", None)
    lane = getattr(getattr(host, "warehouse_read_controller", None), "state_save_lane", None)
    return (
        getattr(host, "_warehouse_management_busy", False)
        or (lane is not None and lane.is_running())
        or (manager is not None and manager.is_running())
    )


def _identity(host):
    context, owner = host.app_context, host.warehouse_read_controller
    return (
        context.account.active_account_id,
        context.generation,
        host._warehouse_snapshot_id,
        owner.draft_revision,
        tuple(sorted(host._warehouse_pending_state_changes.items())),
        tuple(sorted(owner.manual_targets.items())),
        owner.recommendation_guard,
    )


def _confirm_clear(host):
    text = host.text_catalog.render
    dialog = QMessageBox(
        QMessageBox.Warning,
        text("warehouse.draft.clear_title"),
        text("warehouse.draft.clear_confirm"),
        QMessageBox.Yes | QMessageBox.Cancel,
        host,
    )
    dialog.setTextFormat(Qt.PlainText)
    dialog.setDefaultButton(QMessageBox.Cancel)
    dialog.setEscapeButton(QMessageBox.Cancel)
    set_button_text(dialog.button(QMessageBox.Yes), text("warehouse.draft.clear"))
    set_button_text(dialog.button(QMessageBox.Cancel), text("common.cancel"))
    fit_dialog_to_available_screen(dialog, QSize(520, 220))
    try:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return dialog.exec()
    finally:
        dialog.deleteLater()


def clear_warehouse_draft(host):
    if _busy(host) or not has_warehouse_draft(host):
        return
    identity = _identity(host)
    text = host.text_catalog.render
    answer = _confirm_clear(host)
    # 模态确认会处理其他 Qt 事件；不能将旧账号的确认应用到新账号或新预览。
    if answer != QMessageBox.Yes or _busy(host) or _identity(host) != identity:
        return
    projected = _state_projection(host, {})
    owner = host.warehouse_read_controller
    owner.cancel()
    owner.draft_revision += 1
    owner.manual_targets = {}
    owner.recommendation_targets = {}
    owner.recommendation_guard = None
    owner.conflicts = []
    owner.conflict_guard = None
    owner.management_issues = []
    manager = getattr(host, "warehouse_management_controller", None)
    if manager is not None:
        manager.stop()
    if owner.issue_label is not None:
        owner.issue_label.clear()
        owner.issue_label.hide()
    if owner.score_label is not None:
        label, hint = owner.base_quality or (text("warehouse.draft.quality_pending"), "")
        owner.score_label.setText(label)
        owner.score_label.setToolTip(hint)
    _project_states(host, {}, projected)
