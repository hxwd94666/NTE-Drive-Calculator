# 验证历史跨页选择、筛选全选的冻结范围与删除确认的默认取消。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.cultivation_history_test_support import module_load_tests

NTE_TEST_TIER = "core"
load_tests = module_load_tests(__name__, __file__)


def test_cross_page_selection_and_filter_change(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidget
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page, _wait

    _application, page, service, _planner_service = _page(tmp_path)
    from src.storage.sqlite.user_data_dao import UserDataDao

    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        for index in range(23):
            dao.save_cultivation_history("one", f"history-{index:03d}", history_payload())
    view = page._history_view
    view.refresh()
    table = view.findChild(QTreeWidget, "cultivationHistoryList")
    _wait(lambda: table.topLevelItemCount() == 20)
    first = table.topLevelItem(0)
    first.setCheckState(0, Qt.CheckState.Checked)
    view._turn_page(1)
    _wait(lambda: table.topLevelItemCount() == 3)
    assert len(view._selected) == 1
    view._select_filtered()
    _wait(lambda: len(view._selected) == 23)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        dao.save_cultivation_history("one", "new-after-selection", history_payload())
    assert len(view._selected) == 23
    assert service.delete(tuple(view._selected.values())) == 23
    assert service.list().total == 1
    view._filter_changed()
    assert len(view._selected) == 0
    page.shutdown()
    page.deleteLater()


def test_delete_default_cancel_and_cancel_preserves_records(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page
    from src.storage.sqlite.user_data_dao import UserDataDao

    _application, page, service, _planner_service = _page(tmp_path)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        dao.save_cultivation_history("one", "a", history_payload())
    view = page._history_view
    view._selected = {item.history_id: item for item in service.select_all()}

    def cancel(dialog):
        assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.Cancel
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", cancel)
    view._delete_selected()
    assert service.list().total == 1
    page.shutdown()
    page.deleteLater()


def test_list_refresh_invalidates_late_detail(tmp_path):
    from src.features.toolbox.cultivation.history_controller import HistoryOperationResult
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page, _wait
    from src.storage.sqlite.user_data_dao import UserDataDao

    _application, page, service, _planner_service = _page(tmp_path)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        dao.save_cultivation_history("one", "entry", history_payload())
    view = page._history_view
    view._show_page(service.list())
    view._detail_request = 999
    late = HistoryOperationResult("get", 999, service.get("entry"))
    view._show_page(service.list())
    view._completed(late)
    assert view._current_record is None
    assert not view._load.isEnabled()
    assert view._detail.record is None
    page.calculator._calculate()
    _wait(lambda: service.list().total == 2)
    selected = service.select_all()
    view._selected = {item.history_id: item for item in selected}
    view._delete_request = page._history_controller.submit("delete", lambda: service.delete(selected))
    _wait(lambda: service.list().total == 0 and not page._history_controller.is_running())
    assert page.calculator._history_binding._retry is None
    assert service.saving_suppressed(page.calculator._history_binding._session)
    page.shutdown()
    page.deleteLater()


def test_manual_selection_revokes_pending_all_selection(tmp_path):
    from PySide6.QtCore import Qt
    from src.features.toolbox.cultivation.history_controller import HistoryOperationResult
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page

    _application, page, service, _planner_service = _page(tmp_path)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        dao.save_cultivation_history("one", "a", history_payload())
        dao.save_cultivation_history("one", "b", history_payload())
    view = page._history_view
    view._show_page(service.list())
    view._selection_request = 999
    view._update_selection()
    assert not view._delete.isEnabled()
    item = view._table.topLevelItem(0)
    item.setCheckState(0, Qt.CheckState.Checked)
    manually_selected = tuple(view._selected)
    view._completed(HistoryOperationResult("select_all", 999, service.select_all()))
    assert tuple(view._selected) == manually_selected
    assert len(view._selected) == 1
    assert view._delete.isEnabled()
    page.shutdown()
    page.deleteLater()


def test_delete_completion_immediately_revokes_detail_before_refresh(tmp_path, monkeypatch):
    from src.features.toolbox.cultivation.history_controller import HistoryOperationResult
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page

    _application, page, service, _planner_service = _page(tmp_path)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        dao.save_cultivation_history("one", "a", history_payload())
    view = page._history_view
    record = service.get("a")
    view._current_record = record
    view._load.setEnabled(True)
    view._render_detail()
    view._detail_request = 999
    view._delete_request = 1000
    monkeypatch.setattr(view, "refresh", lambda: None)  # Inspect before a new list response exists.
    view._completed(HistoryOperationResult("delete", 1000, 1))
    assert view._current_record is None
    assert view._detail_request == 0
    assert not view._load.isEnabled()
    assert view._detail.record is None
    view._completed(HistoryOperationResult("get", 999, record))
    assert view._current_record is None
    assert not view._load.isEnabled()
    page.shutdown()
    page.deleteLater()


def test_select_all_uses_current_mode_and_search_across_pages(tmp_path):
    import json
    from src.domain.cultivation_history import HistoryPayload
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history import history_payload
    from tests.test_cultivation_history_page import _page, _wait

    _application, page, service, _planner = _page(tmp_path)

    def payload(name, mode="single"):
        base = history_payload()
        configuration = json.loads(base.configuration_json)
        configuration["targets"][0]["name"] = name
        configuration["mode"] = mode
        return HistoryPayload.create(configuration, json.loads(base.result_snapshot_json))

    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        for number in range(23):
            dao.save_cultivation_history("one", f"matching-{number}", payload("目标角色"))
        dao.save_cultivation_history("one", "other-role", payload("其他角色"))
        dao.save_cultivation_history("one", "other-mode", payload("目标角色", "batch"))
    view = page._history_view
    view._mode.setCurrentIndex(1)
    view._search.setText("目标")
    view.refresh()
    _wait(lambda: view._table.topLevelItemCount() == 20)
    assert view._select_all.text() == "全选"
    view._select_all.click()
    _wait(lambda: len(view._selected) == 23)
    assert set(view._selected) == {f"matching-{number}" for number in range(23)}
    assert view._count.text() == "已选 23 条"
    assert service.list().total == 25
    page.shutdown()
    page.deleteLater()


def test_old_history_role_and_fork_pinyin_search_share_list_and_all_selection(tmp_path):
    import json
    import sqlite3
    from src.domain.cultivation_history import HistoryPayload
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history import history_payload

    database = tmp_path / "user.sqlite3"
    base = history_payload()
    configuration = json.loads(base.configuration_json)
    configuration["targets"][0]["name"] = "阿德勒"
    configuration["targets"][0]["fork"]["name"] = "勿忘伞"
    payload = HistoryPayload.create(configuration, json.loads(base.result_snapshot_json))
    with UserDataDao(database, account_id="one") as dao:
        for number in range(23):
            dao.save_cultivation_history("one", f"entry-{number}", payload)
        dao.save_cultivation_history("one", "other", base)
        with sqlite3.connect(database) as connection:
            # Existing data only indexed Chinese role names, not fork names or pinyin.
            connection.execute("UPDATE cultivation_history SET search_text = '阿德勒' WHERE history_id != 'other'")
            before = connection.execute("SELECT * FROM cultivation_history ORDER BY history_id").fetchall()
        for query in ("阿德勒", "adele", "ADL", "勿忘伞", "wuwangsan", "WWS"):
            page = dao.list_cultivation_histories("one", search=query)
            selected = dao.select_cultivation_histories("one", search=query)
            assert page.total == len(selected) == 23
            assert len(page.items) == 20
            assert {item.history_id for item in selected} == {f"entry-{number}" for number in range(23)}
        assert dao.list_cultivation_histories("one", search="不存在").total == 0
        assert len(dao.select_cultivation_histories("one", search="%")) == 0
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT * FROM cultivation_history ORDER BY history_id").fetchall() == before
