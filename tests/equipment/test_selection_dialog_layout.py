# 验证角色选择随可用空间重排，确认信息可滚动阅读，筛选缩放不丢失选择身份。
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from src.app import window_geometry
from src.domain.localization.catalog import make_catalog
from src.features.inventory.equipment.assembly.automatic_assembly_dialog import AutomaticAssemblyDialog
from src.features.toolbox.rewind.role_picker import _RoleSelectionDialog
from src.integrations.preferences.locale_files import bundled_text_catalog, read_catalog_file
from tests.rewind.test_slot_selection_ui import role_fixture
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test

NTE_TEST_TIER = "core"


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_rewind_resize_keeps_all_role_actions_inside_the_viewport_and_draft(app, monkeypatch):
    available = QRect(0, 0, 560, 440)
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _: available)
    roles = tuple(role_fixture(1004 + index, f"角色{index}") for index in range(8))
    references = {role.character_id: role.slots[1].reference for role in roles}
    dialog = _RoleSelectionDialog(
        None,
        title="选择",
        description="选择角色",
        roles=roles,
        selected_character_ids={1004, 1011},
        selected_slots=references,
    )
    try:
        dialog.show()
        app.processEvents()
        for size in (QSize(500, 380), QSize(450, 360), QSize(508, 388)):
            dialog.resize(size)
            app.processEvents()
            scroll = dialog.findChild(QScrollArea)
            assert scroll.horizontalScrollBar().maximum() == 0
            for role in roles:
                card = dialog._wrappers[role.character_id]
                assert card.x() >= 0 and card.geometry().right() < scroll.viewport().width()
            assert set(dialog.selected_character_ids()) == {1004, 1011}
            assert dialog.selected_slots() == references
        dialog.search_edit.setText("无匹配")
        dialog.search_edit.clear()
        assert set(dialog.selected_character_ids()) == {1004, 1011}
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("long_patch", [False, True])
def test_small_confirmation_preserves_readable_long_instructions_and_reachable_actions(app, monkeypatch, long_patch):
    available = QRect(0, 0, 560, 440)
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _: available)
    root = Path(__file__).resolve().parents[2]
    catalog = (
        make_catalog(read_catalog_file(root / "docs/examples/locales/qps-ploc.json"), base=bundled_text_catalog())
        if long_patch
        else bundled_text_catalog()
    )
    dialog = AutomaticAssemblyDialog(None, 3, stop_hotkey="F8", needs_name=True, medium=True, catalog=catalog)
    try:
        dialog.show()
        app.processEvents()
        dialog.resize(QSize(640, 430))
        app.processEvents()
        window_geometry.fit_dialog_to_available_screen(dialog)
        app.processEvents()
        assert available.contains(dialog.frameGeometry())
        for label in dialog.findChildren(QLabel):
            if label.isVisible() and label.wordWrap():
                assert label.height() >= label.heightForWidth(label.width())
        scroll = dialog.findChild(QScrollArea)
        assert scroll is not None and scroll.horizontalScrollBar().maximum() == 0
        dialog.name_edit.setText("演示主角")
        scroll.ensureWidgetVisible(dialog.name_edit)
        app.processEvents()
        assert dialog.start_button.isEnabled()
        for button in (dialog.start_button, dialog.back_button):
            assert button.isVisible()
            assert dialog.rect().contains(QRect(button.mapTo(dialog, button.rect().topLeft()), button.size()))
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()
