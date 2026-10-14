# 验证紧凑历史列表、旧记录名称投影与只读分区预览不改持久化事实。
from __future__ import annotations

import json
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NTE_TEST_TIER = "core"


def _multiple_payload():
    from src.domain.progression.history import HistoryPayload
    from tests.test_cultivation_history import history_payload

    base = history_payload(quantity=30)
    configuration = json.loads(base.configuration_json)
    result = json.loads(base.result_snapshot_json)
    first = configuration["targets"][0]
    first_result = result["target_summaries"][0]
    configuration["mode"] = "batch"
    configuration["targets"] = []
    result["target_summaries"] = []
    for number in range(4):
        target = json.loads(json.dumps(first))
        target.update(character_id=101 + number, line_id=f"line-{number}", name=f"角色{number + 1}")
        if number % 2:
            target["fork"] = None
        configuration["targets"].append(target)
        result["target_summaries"].append({**first_result, "character_id": 101 + number, "line_id": f"line-{number}"})
    return HistoryPayload.create(configuration, result)


def test_existing_summary_gets_fork_names_without_rewriting_row(tmp_path):
    from src.features.toolbox.cultivation.history_display import summary_text
    from src.storage.sqlite.user_data_dao import UserDataDao

    database = tmp_path / "user.sqlite3"
    payload = _multiple_payload()
    with UserDataDao(database, account_id="one") as dao:
        dao.save_cultivation_history("one", "entry", payload)
        with sqlite3.connect(database) as connection:
            before = connection.execute("SELECT * FROM cultivation_history").fetchall()
        summary = dao.list_cultivation_histories("one").items[0]
        names, stamina = summary_text(summary)
        assert names == "角色1（示例弧盘）、角色2、角色3（示例弧盘）、角色4"
        assert "目标" not in names
        assert "未完整" in stamina
        assert dao.get_cultivation_history("one", "entry").payload == payload
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT * FROM cultivation_history").fetchall() == before


def test_material_images_use_formal_ids_and_missing_image_keeps_frozen_values(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication
    from src.features.toolbox.cultivation.history_materials import HistoryMaterialGrid

    application = QApplication.instance() or QApplication([])
    icon_path = tmp_path / "material.png"
    icon = QPixmap(24, 24)
    icon.fill(QColor("red"))
    assert icon.save(str(icon_path))
    requested = []

    def lookup(item_id):
        requested.append(item_id)
        return icon_path if item_id == "known" else None

    grid = HistoryMaterialGrid(icon_lookup=lookup)
    rows = [{"item_id": item_id, "name": "历史材料", "required": 100,
             "allocated_equivalent": 30, "remaining": 70, "stamina_eligible": True}
            for item_id in ("known", "missing")]
    grid.set_materials(rows)
    model = grid.model()
    assert not model.data(model.index(0, 0), Qt.ItemDataRole.DecorationRole).isNull()
    assert model.data(model.index(1, 0), Qt.ItemDataRole.DecorationRole).isNull()
    assert requested == ["known", "missing"]
    assert model.data(model.index(1, 0), Qt.ItemDataRole.UserRole) == rows[1]
    assert "总需求 100" in model.data(model.index(1, 0))
    assert "仍需 70" in model.data(model.index(1, 0))
    grid.deleteLater()
    application.processEvents()


def test_material_grid_narrow_window_keeps_last_material_keyboard_reachable():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from src.features.toolbox.cultivation.history_materials import HistoryMaterialGrid

    application = QApplication.instance() or QApplication([])
    grid = HistoryMaterialGrid()
    materials = [{"item_id": f"material-{number}", "name": "较长的历史材料名称",
                  "required": 2**63 - 1, "allocated_equivalent": 0,
                  "remaining": 2**63 - 1, "stamina_eligible": True}
                 for number in range(24)]
    grid.set_materials(materials)
    try:
        for width in (1200, 420):
            grid.resize(width, grid.height())
            grid.show()
            application.processEvents()
            first = grid.model().index(0, 0)
            grid.setCurrentIndex(first)
            grid.setFocus()
            QTest.keyClick(grid, Qt.Key.Key_End)
            application.processEvents()
            last = grid.currentIndex()
            assert last.row() == len(materials) - 1
            assert grid.viewport().rect().contains(grid.visualRect(last))
            assert not grid.horizontalScrollBar().isVisible()
            assert grid.model().data(last, Qt.ItemDataRole.UserRole) == materials[-1]
            assert f"{2**63 - 1:,}" in grid.model().data(last)
            assert not grid.model().setData(last, 0)
    finally:
        grid.close()
        grid.deleteLater()
        application.processEvents()


def test_history_materials_expand_all_rows_and_outer_preview_owns_scroll(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QListView
    from src.domain.progression.history import HistoryPayload, HistoryRecord
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history_page import _page, _wait

    application, page, service, _planner = _page(tmp_path)
    original = _multiple_payload()
    snapshot = json.loads(original.result_snapshot_json)
    material = snapshot["materials"][0]
    snapshot["materials"] = [{**material, "item_id": f"material-{number}"} for number in range(140)]
    payload = HistoryPayload.create(json.loads(original.configuration_json), snapshot)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        saved = dao.save_cultivation_history("one", "entry", payload)
    view = page._history_view
    page.resize(1000, 800)
    page.show()
    page._header.hide()
    page._body_stack.setCurrentWidget(view)
    view._current_record = HistoryRecord(saved, payload)
    view._render_detail()
    detail = view._detail
    materials = detail.findChild(QListView, "cultivationHistoryMaterialTotals")
    application.processEvents()
    # 网格定高后，外层 QScrollArea 要在后续事件轮次接收布局请求；等可滚动状态，不假定一轮即就绪。
    _wait(lambda: detail.verticalScrollBar().maximum() > 0)
    assert materials.model().rowCount() == 140
    assert not materials.verticalScrollBar().isVisible()
    assert materials.verticalScrollBar().maximum() == 0
    assert detail.verticalScrollBar().maximum() > 0
    materials.setCurrentIndex(materials.model().index(0, 0))
    materials.setFocus()
    QTest.keyClick(materials, Qt.Key.Key_End)
    _wait(lambda: detail.verticalScrollBar().value() > 0)
    assert materials.currentIndex().row() == 139
    assert service.get("entry").payload == payload
    page.shutdown()
    page.deleteLater()


def test_readonly_preview_scope_filters_frozen_materials_and_preserves_record(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView, QLabel, QListView
    from src.domain.progression.history import HistoryPayload, HistoryRecord
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history_page import _page

    _application, page, service, _planner = _page(tmp_path)
    payload = _multiple_payload()
    snapshot = json.loads(payload.result_snapshot_json)
    snapshot["materials"][0]["stamina_eligible"] = False
    payload = HistoryPayload.create(json.loads(payload.configuration_json), snapshot)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        saved = dao.save_cultivation_history("one", "entry", payload)
    record = HistoryRecord(saved, payload)
    detail = page._history_view._detail
    detail.set_record(record, scope="all")
    materials = detail.findChild(QListView, "cultivationHistoryMaterialTotals")
    assert materials.model().rowCount() == 1
    first = materials.model().index(0, 0)
    assert materials.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert not materials.model().flags(first) & Qt.ItemFlag.ItemIsEditable
    assert materials.model().data(first, Qt.ItemDataRole.UserRole)["remaining"] == 70
    assert any("完整体力未知" in label.text() for label in detail.findChildren(QLabel))
    assert any("未参与" in label.text() for label in detail.findChildren(QLabel))
    detail.set_record(record, scope="stamina")
    assert materials.model().rowCount() == 0
    assert any(label.text() == "此显示范围没有材料条目。" and not label.isHidden()
               for label in detail.findChildren(QLabel))
    detail.set_record(record, scope="all")
    assert materials.model().rowCount() == 1
    assert detail.record == record
    detail.clear()
    assert detail.record is None
    assert service.get("entry").payload == payload
    page.shutdown()
    page.deleteLater()


def test_complete_zero_is_not_unknown_and_filter_clears_previous_total(tmp_path):
    from PySide6.QtWidgets import QLabel
    from src.domain.progression.history import HistoryPayload, HistoryRecord
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.test_cultivation_history_page import _page

    _application, page, service, _planner = _page(tmp_path)
    payload = _multiple_payload()
    result = json.loads(payload.result_snapshot_json)
    result["stamina"].update(status="complete", known_stamina=0, total_stamina=0)
    result["gaps"] = []
    payload = HistoryPayload.create(json.loads(payload.configuration_json), result)
    with UserDataDao(tmp_path / "user.sqlite3") as dao:
        saved = dao.save_cultivation_history("one", "entry", payload)
    view = page._history_view
    view._current_record = HistoryRecord(saved, payload)
    view._render_detail()
    detail = view._detail
    headline = view.findChild(QLabel, "cultivationHistoryStaminaTotal")
    assert headline.text() == "合并体力：0"
    assert not any("完整体力未知" in label.text() for label in detail.findChildren(QLabel))
    view._filter_changed()
    assert headline.text() == ""
    view.set_message("历史读取失败，请重试。")
    assert not view._status.isHidden()  # 精简常规帮助，不隐藏真正的读取失败。
    view.set_message("")
    assert view._status.isHidden()
    assert service.get("entry").payload == payload
    page.shutdown()
    page.deleteLater()
