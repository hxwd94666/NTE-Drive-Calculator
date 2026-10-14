# 验证权重页按正式身份读取编辑，并保护提交、重置和自建角色完整流程。
from __future__ import annotations

import os
import time
import threading
from types import SimpleNamespace
from pathlib import Path

from .fixture import build_basic_weight_static_fixture

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QMessageBox, QDoubleSpinBox, QInputDialog
from PySide6.QtTest import QTest

NTE_TEST_TIER = "core"


@pytest.fixture
def editor(tmp_path):
    from src.features.configuration.page_owner import BasicWeightPage

    app = QApplication.instance() or QApplication([])
    shell = QWidget()
    context = SimpleNamespace(
        generation=0,
        account=SimpleNamespace(active_account_id="first", user_database_path=tmp_path / "first.sqlite3"),
        paths=SimpleNamespace(config_dir=Path("config"), equipment_allocation_database_path=tmp_path / "static.sqlite3",
                              shared_database_path=tmp_path / "shared.sqlite3"),
    )
    navigation, changes = [], []
    editor = BasicWeightPage(context, navigation.append, lambda: changes.append(1), shell)
    yield editor, shell, context, navigation, changes
    if editor.task_owner is not None:
        editor.task_owner.close()
        deadline = 1000
        while editor.task_owner._reads.is_running() and deadline:
            QTest.qWait(5)
            time.sleep(0.001)
            deadline -= 1
        assert not editor.task_owner._reads.is_running()
    shell.close()
    shell.deleteLater()
    app.processEvents()


def test_close_during_accepted_write_keeps_owner_until_terminal_state(editor):
    page, _shell, _context, _navigation, _changes = editor
    owner = page.controller()
    release = threading.Event()
    completed = []
    try:
        assert owner.submit_change(lambda: release.wait(2), completed.append, pytest.fail, pytest.fail)
        with pytest.raises(RuntimeError, match="正在提交"):
            page.close_tasks()
        assert not owner._closed
    finally:
        release.set()
        wait(lambda: not owner.is_writing())
    assert completed == [True]
    page.close_tasks()
    assert owner._closed


def wait(predicate):
    for _ in range(600):
        if predicate():
            return
        # Qt 的嵌套等待可能长时间持有 GIL；让真实 Python worker 获得调度，不以卡住的夹具测响应性。
        QTest.qWait(5)
        time.sleep(0.001)
    assert predicate()


def use_real_sources(editor, monkeypatch):
    from src.storage.sqlite.user_data_dao import UserDataDao
    page, _shell, context, _navigation, _changes = editor
    context.paths.equipment_allocation_database_path = build_basic_weight_static_fixture(
        context.account.user_database_path.parent / "static" / "game_static.sqlite3"
    )
    with UserDataDao(context.account.user_database_path, account_id=context.account.active_account_id):
        pass
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: pytest.fail(str(_args[-1])))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    return page, context


def test_real_read_edit_save_keeps_equal_names_separate_and_updates_only_selected_id(editor, monkeypatch):
    from src.services.characters.custom_character_service import create_custom_character
    from src.storage.sqlite.user_data_dao import UserDataDao
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
    from src.domain.recommended_weights import base_weight_character_id
    page, context = use_real_sources(editor, monkeypatch)
    with StaticGameDataDao(context.paths.equipment_allocation_database_path) as static:
        official = static.get_character(1051)
    custom = create_custom_character(context.account.user_database_path, official["name_zh"])
    custom_key = str(custom["character_id"])
    page.refresh()
    wait(lambda: page._config_form_data is not None or "失败" in page.config_load_status.text())
    assert page._config_form_data is not None, page.config_load_status.text()
    roles = page._config_form_data
    assert roles["1051"]["name_zh"] == roles[custom_key]["name_zh"]
    tabs = page.config_roles_tabs
    index = next(i for i in range(tabs.count()) if tabs.widget(i).property("role_key") == custom_key)
    tabs.setCurrentIndex(index)
    assert page._config_active_role == custom_key
    spin = tabs.currentWidget().findChild(QDoubleSpinBox, "weight-weights-CritBase")
    spin.setValue(1.55)
    spin.editingFinished.emit()
    assert page.is_dirty()
    next(button for button in page.findChildren(QPushButton) if button.text() == "保存").click()
    wait(lambda: not page.task_owner.is_writing())
    assert not page.is_dirty() and editor[-1] == [1]
    with UserDataDao(context.account.user_database_path) as dao:
        assert dao.get_character_weight_preferences(custom["character_id"])["property_weights"]["CritBase"] == 1.55
        assert dao.get_character_weight_preferences(base_weight_character_id(1051)) is None


def test_real_official_reset_preserves_default_refreshability(editor, monkeypatch):
    from src.domain.recommended_weights import base_weight_character_id
    from src.services.characters.character_weight_service import save_account_character_weights
    from src.storage.sqlite.user_data_dao import UserDataDao
    page, context = use_real_sources(editor, monkeypatch)
    save_account_character_weights(context.account.user_database_path, 1051, {"CritBase": 2.0}, main_property_weights={},
                                   static_database_path=context.paths.equipment_allocation_database_path)
    page.refresh()
    wait(lambda: page._config_form_data is not None or "失败" in page.config_load_status.text())
    assert page._config_form_data is not None, page.config_load_status.text()
    tabs = page.config_roles_tabs
    tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.widget(i).property("role_key") == "1051"))
    next(button for button in page.findChildren(QPushButton) if button.text() == "重置当前").click()
    wait(lambda: not page.task_owner.is_writing() and page._config_form_data is not None)
    with UserDataDao(context.account.user_database_path) as dao:
        assert dao.get_character_weight_preferences(base_weight_character_id(1051))["source_kind"] == "default"
    assert page._config_active_role == "1051" and not page.is_dirty()


def test_real_create_and_delete_complete_without_window_forwarders(editor, monkeypatch):
    from src.storage.sqlite.user_data_dao import UserDataDao
    page, context = use_real_sources(editor, monkeypatch)
    page.refresh()
    wait(lambda: page._config_form_data is not None or "失败" in page.config_load_status.text())
    assert page._config_form_data is not None, page.config_load_status.text()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("页面闭环角色", True))
    next(button for button in page.findChildren(QPushButton) if button.text() == "新建").click()
    wait(lambda: page._config_form_data is not None and any(role["name_zh"] == "页面闭环角色" for role in page._config_form_data.values()))
    key = page._config_active_role
    assert page._config_form_data[key]["name_zh"] == "页面闭环角色"
    next(button for button in page.config_roles_tabs.currentWidget().findChildren(QPushButton) if button.text() == "删除角色").click()
    wait(lambda: not page.task_owner.is_writing())
    with UserDataDao(context.account.user_database_path) as dao:
        assert dao.list_custom_characters() == []
        assert dao.get_character_weight_preferences(int(key)) is None
    assert key not in page._config_form_data and not page.is_dirty()
