# 验证历史在三主题下的键盘选择、只读详情和默认取消，尺寸只断言可操作边界。
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("theme", ["dark", "black", "light"])
def test_keyboard_select_view_and_cancel_delete_in_three_themes(tmp_path, monkeypatch, theme):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QLabel, QListView, QMessageBox
    from src.app.theme import apply_app_theme, current_theme_name
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page, _wait

    application, page, service, _planner_service = _page(tmp_path)
    previous_theme = current_theme_name()
    apply_app_theme(application, theme)
    try:
        with UserDataDao(tmp_path / "user.sqlite3") as dao:
            dao.save_cultivation_history("one", "keyboard-entry", history_payload())
        page.show()
        page._show_history()
        view = page._history_view
        _wait(lambda: view._table.topLevelItemCount() == 1)
        item = view._table.topLevelItem(0)
        view._table.setCurrentItem(item)
        view._table.setFocus()
        QTest.keyClick(view._table, Qt.Key.Key_Space)
        _wait(lambda: view._current_record is not None)
        assert len(view._selected) == 1
        assert view._load.isEnabled()
        assert any("合并已知体力" in label.text() for label in view.findChildren(QLabel))
        materials = view._detail.findChild(QListView, "cultivationHistoryMaterialTotals")
        assert materials.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert not materials.model().flags(materials.model().index(0, 0)) & Qt.ItemFlag.ItemIsEditable
        assert view._delete.isEnabled()
        cancelled = []

        def cancel_with_keyboard(dialog):
            assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.Cancel
            available = dialog.screen().availableGeometry()
            assert dialog.width() <= available.width() and dialog.height() <= available.height()
            dialog.show()
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            cancelled.append(True)
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "exec", cancel_with_keyboard)
        view._delete.setFocus()
        QTest.keyClick(view._delete, Qt.Key.Key_Space)
        assert cancelled == [True]
        assert service.list().total == 1
    finally:
        page.shutdown()
        page.close()
        page.deleteLater()
        application.processEvents()
        apply_app_theme(application, previous_theme)
