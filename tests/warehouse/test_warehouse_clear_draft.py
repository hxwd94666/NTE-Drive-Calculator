# 验证清除只撤销当前仓库全部状态草稿，取消、账号切换与在途写入均不越界。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QMessageBox
from src.features.inventory.warehouse import draft_actions as actions
from src.features.inventory.warehouse.display.item_state import warehouse_item_with_state
from src.features.inventory.warehouse.management.management_projection import (
    capture_management_input,
    project_management_preview,
)
from src.integrations.preferences.locale_files import bundled_text_catalog

NTE_TEST_TIER = "core"


@pytest.fixture
def host():
    app = QApplication.instance() or QApplication([])
    h = QWidget()
    h.app_context = SimpleNamespace(account=SimpleNamespace(active_account_id="test"), generation=1)
    h.text_catalog = bundled_text_catalog()
    h._warehouse_snapshot_id = 1
    h._warehouse_source = "nte_core"
    h._warehouse_base_states = {"a": "locked", "b": "discarded", "c": "normal"}
    h._warehouse_all_items = [
        warehouse_item_with_state(
            {"uid": uid, "locked": state == "locked", "discarded": state == "discarded"},
            "normal" if uid != "c" else "locked",
        )
        for uid, state in h._warehouse_base_states.items()
    ]
    h._warehouse_all_items.append({"uid": "unknown", "state_known": False, "locked": None})
    h._warehouse_pending_state_changes = {"a": "normal", "b": "normal", "c": "locked"}
    h.warehouse_read_controller = SimpleNamespace(
        manual_targets={"a": "normal"},
        recommendation_targets={"b": "normal", "c": "locked"},
        recommendation_guard=object(),
        conflicts=[{"uid": "a"}],
        conflict_guard=object(),
        management_issues=[{"uid": "b"}],
        draft_revision=0,
        cancel=Mock(),
        score_label=QLabel(),
        issue_label=QLabel(),
        base_quality=("base score", "base hint"),
    )
    h._apply_warehouse_filters = Mock()
    h._update_warehouse_save_state = Mock()
    h.warehouse_management_controller = SimpleNamespace(is_running=lambda: False, stop=Mock())
    yield h
    h.close()
    h.deleteLater()
    app.processEvents()


def test_clear_restores_hidden_and_visible_items_and_invalidates_late_preview(host, monkeypatch):
    frozen = capture_management_input(host)
    monkeypatch.setattr(actions, "_confirm_clear", lambda *args: QMessageBox.Yes)
    actions.clear_warehouse_draft(host)
    owner = host.warehouse_read_controller
    assert not actions.has_warehouse_draft(host)
    assert not owner.manual_targets and not owner.conflicts and owner.conflict_guard is None
    assert [(i.get("locked"), i.get("discarded")) for i in host._warehouse_all_items[:3]] == [
        (True, False),
        (False, True),
        (False, False),
    ]
    assert host._warehouse_all_items[3] == {"uid": "unknown", "state_known": False, "locked": None}
    assert owner.score_label.text() == "base score" and owner.score_label.toolTip() == "base hint"
    owner.cancel.assert_called_once()
    host._apply_warehouse_filters.assert_called_once()
    with pytest.raises(ValueError, match="草稿"):
        project_management_preview(host, frozen, {}, SimpleNamespace(snapshot_id=1))


@pytest.mark.parametrize("change", ["cancel", "account", "generation", "snapshot", "draft", "busy"])
def test_confirmation_cancel_or_changed_context_preserves_drafts(host, monkeypatch, change):
    def confirm(*args):
        if change == "account":
            host.app_context.account.active_account_id = "other"
        if change == "generation":
            host.app_context.generation += 1
        if change == "snapshot":
            host._warehouse_snapshot_id = 2
        if change == "draft":
            host._warehouse_pending_state_changes["a"] = "discarded"
        if change == "busy":
            host._warehouse_management_busy = True
        return QMessageBox.Cancel if change == "cancel" else QMessageBox.Yes

    monkeypatch.setattr(actions, "_confirm_clear", confirm)
    actions.clear_warehouse_draft(host)
    assert actions.has_warehouse_draft(host)
    host.warehouse_read_controller.cancel.assert_not_called()


@pytest.mark.parametrize("busy", ["state", "management"])
def test_inflight_operation_does_not_prompt_or_clear(host, monkeypatch, busy):
    if busy == "state":
        host.warehouse_read_controller.state_save_lane = SimpleNamespace(is_running=lambda: True)
    else:
        host.warehouse_management_controller.is_running = lambda: True
    confirm = Mock()
    monkeypatch.setattr(actions, "_confirm_clear", confirm)
    actions.clear_warehouse_draft(host)
    confirm.assert_not_called()
    assert actions.has_warehouse_draft(host)


def test_clear_confirmation_defaults_to_cancel_and_uses_plain_text(host, monkeypatch):
    from PySide6.QtCore import Qt

    seen = []

    def inspect(dialog):
        assert dialog.textFormat() == Qt.PlainText
        assert dialog.defaultButton() == dialog.button(QMessageBox.Cancel)
        assert dialog.escapeButton() == dialog.button(QMessageBox.Cancel)
        assert dialog.button(QMessageBox.Yes).accessibleName() == "清除"
        seen.append(True)
        return QMessageBox.Cancel

    monkeypatch.setattr(actions.QMessageBox, "exec", inspect)
    monkeypatch.setattr(actions.winsound, "MessageBeep", lambda *_: None)
    actions.clear_warehouse_draft(host)
    assert seen == [True] and actions.has_warehouse_draft(host)
