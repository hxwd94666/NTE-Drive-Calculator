# 验证轴策略必须明确选择本账号报告、范围、角色与A，关闭或过期不回写任何状态。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QWidget
import pytest

from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
from src.features.allocation.axis_selection_dialog import AxisAllocationSelectionDialog
from src.services.allocation.axis_selection import AxisAllocationSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"


@pytest.fixture
def selection_service(tmp_path):
    case = create_axis_catalog_case(tmp_path)
    current = {"value": True}
    service = AxisAllocationSelectionService(dependencies=case["dependencies"], context_is_current=lambda _: current["value"])
    choices = service.choices(["灵可", "未映射自建角色"])
    return case, service, choices, current


def test_choices_and_selection_use_formal_participant_and_never_default_a_or_report(selection_service):
    case, service, choices, _ = selection_service
    assert choices.role_character_ids == (("灵可", 1072),)
    for invalid in ({"battle_record_id": None}, {"selected_role": "未映射自建角色"}, {"detail_scope": None}, {"anchor_mode": None}, {"anchor_mode": "effective"}):
        arguments = dict(battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="all", anchor_mode="original")
        arguments.update(invalid)
        with pytest.raises(ValueError):
            service.select(choices, **arguments)
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="all", anchor_mode="original")
    assert selected.selected_character_id == 1072 and selected.account_id == "axis-evaluation-public"
    assert selected.battle.start_us is None and selected.battle.marginal_candidate is None


def test_range_and_generation_must_remain_frozen(selection_service):
    case, service, choices, current = selection_service
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="range",
                              anchor_mode="original", start_us=500_000, end_us=1_000_001)
    assert (selected.battle.start_us, selected.battle.end_us) == (500_000, 1_000_001)
    current["value"] = False
    with pytest.raises(CancelledError):
        service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="all", anchor_mode="original")


def test_unrelated_account_setting_does_not_cancel_explicit_report_selection(selection_service):
    case, service, choices, _ = selection_service
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.replace_application_setting_copy("ui", {"background_marker": "public-fixture"})
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可",
                              detail_scope="all", anchor_mode="original")
    assert selected.selected_character_id == 1072


def test_group_scope_is_explicit_and_does_not_silently_pick_first_role(selection_service):
    case, service, choices, _ = selection_service
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="__all_groups__",
                              detail_scope="all", anchor_mode="original")
    assert selected.optimize_groups and selected.selected_role is None and selected.selected_character_id is None
    assert selected.battle.selected_character_id is None


def test_selection_dialog_empty_defaults_reject_incomplete_choices_and_can_freeze_explicit_input(selection_service):
    case, service, choices, _ = selection_service
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    dialog = AxisAllocationSelectionDialog(service=service, choices=choices, parent=parent)
    try:
        assert dialog.report_combo.currentData() is None
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.selection is None and dialog.result() == 0
        dialog.report_combo.setCurrentIndex(dialog.report_combo.findData(case["battle_record_id"]))
        assert dialog.role_combo.currentData() is None and dialog.anchor_combo.currentData() is None
        dialog.role_combo.setCurrentIndex(dialog.role_combo.findData("__all_groups__"))
        dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData("all"))
        dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("original"))
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.selection is not None and dialog.selection.anchor_mode == "original"
    finally:
        dialog.close()
        parent.close()
        dialog.deleteLater()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_marginal_entry_prefills_only_its_explicit_selector_and_keeps_a_unselected(selection_service):
    case, service, choices, _ = selection_service
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="range",
                              anchor_mode="original", start_us=500_000, end_us=1_000_001)
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    dialog = AxisAllocationSelectionDialog(service=service, choices=choices,
        initial_request=replace(selected.battle, marginal_drive_units=(("AtkUp", 100.0),)), parent=parent)
    try:
        assert dialog.report_combo.currentData() == case["battle_record_id"]
        assert dialog.role_combo.currentData() == "__all_groups__" and dialog.scope_combo.currentData() == "range"
        assert dialog.anchor_combo.currentData() is None and dialog.selection is None
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.selection is None
        dialog.role_combo.setCurrentIndex(dialog.role_combo.findData("__all_groups__"))
        dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("original"))
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.selection.optimize_groups and dialog.selection.selected_character_id is None
        assert dialog.selection.battle.marginal_drive_units is None
        assert (dialog.selection.battle.start_us, dialog.selection.battle.end_us) == (500_000, 1_000_001)
    finally:
        dialog.close()
        parent.close()
        dialog.deleteLater()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_range_row_visibility_and_keyboard_cancel_never_selects_report(selection_service):
    case, service, choices, _ = selection_service
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    dialog = AxisAllocationSelectionDialog(service=service, choices=choices, parent=parent)
    try:
        dialog.show()
        dialog.activateWindow()
        app.processEvents()
        label = next(item for item in dialog.findChildren(QLabel) if item.text() == "指定时段")
        dialog.report_combo.setCurrentIndex(dialog.report_combo.findData(case["battle_record_id"]))
        for scope in ("all", "range", "all", "range"):
            dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData(scope))
            app.processEvents()
            assert label.isVisible() == (scope == "range")
            for control in (dialog.start_spin, dialog.end_spin):
                assert control.isVisible() == (scope == "range")
                if scope == "range":
                    assert control.isEnabled() and control.focusPolicy() != Qt.NoFocus
                    control.setFocus()
                    app.processEvents()
                    assert control.hasFocus()
        cancel = dialog.buttons.button(QDialogButtonBox.Cancel)
        assert cancel.text() == "取消"
        with patch.object(service, "select", wraps=service.select) as select:
            cancel.setFocus()
            QTest.keyClick(cancel, Qt.Key_Space)
            app.processEvents()
            select.assert_not_called()
        assert dialog.selection is None and dialog.result() == 0 and not dialog.isVisible()
    finally:
        dialog.close()
        parent.close()
        dialog.deleteLater()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
