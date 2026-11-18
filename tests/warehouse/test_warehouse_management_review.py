# 验证管理净差异单选筛选、原状态隔离与只读冲突详情导航，不在详情中裁决装备。
import os
from copy import deepcopy
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton, QWidget

from src.domain.inventory.warehouse_filter import (
    WarehouseFilterSpec,
    build_warehouse_filter_catalog,
    filter_projected_warehouse_items,
    warehouse_management_state,
    warehouse_item_statuses,
)
from src.features.inventory.warehouse.management.conflict_dialog import WarehouseConflictDialog
from src.features.inventory.warehouse.display.filter_drawer import WarehouseFilterDrawer
from src.features.inventory.warehouse.display.item_state import warehouse_item_with_state
from src.features.inventory.warehouse.management import management_controller as module
from src.integrations.preferences.locale_files import bundled_text_catalog
from tests.warehouse.test_warehouse_management_ui import context
from tests.warehouse.test_warehouse_management_ui import editor_data
from tests.warehouse.test_warehouse_management_ui import wait
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def inventory():
    def row(uid, **extra):
        return dict(uid=uid, kind="core", quality="gold", locked=False, discarded=False, equipped=False, **extra)

    return [
        warehouse_item_with_state(row("lock"), "locked"),
        warehouse_item_with_state({**row("restore"), "discarded": True}, "normal"),
        warehouse_item_with_state(row("discard"), "discarded"),
        row("same"),
    ]


def test_management_categories_are_net_changes_and_status_remains_snapshot_fact():
    rows = inventory()
    assert [warehouse_management_state(row) for row in rows] == ["locked", "normal", "discarded", "unchanged"]
    assert warehouse_item_statuses(rows[0]) == {"other"}
    assert warehouse_item_statuses(rows[1]) == {"discarded"}
    assert warehouse_management_state(warehouse_item_with_state(rows[0], "normal")) == "unchanged"
    assert {option.value: option.count for option in build_warehouse_filter_catalog(rows).management} == {
        "locked": 1,
        "discarded": 1,
        "normal": 1,
        "unchanged": 1,
    }
    for state in ("locked", "discarded", "normal", "unchanged"):
        filtered = filter_projected_warehouse_items(rows, WarehouseFilterSpec(management=state))
        assert len(filtered) == 1 and warehouse_management_state(filtered[0]) == state
    assert len(filter_projected_warehouse_items(rows, WarehouseFilterSpec())) == 4
    assert all(option.count == 0 for option in build_warehouse_filter_catalog([]).management)


def test_management_drawer_is_at_most_one_choice_and_reset_does_not_modify_targets(app):
    parent = QWidget()
    parent.resize(900, 650)
    rows = inventory()
    original = deepcopy(rows)
    drawer = WarehouseFilterDrawer(parent, catalog=bundled_text_catalog())
    try:
        drawer.set_items(rows, WarehouseFilterSpec())
        drawer.open_for(WarehouseFilterSpec())
        options = {
            button.property("filterValue"): button
            for button in drawer.findChildren(QPushButton)
            if button.property("filterGroup") == "management"
        }
        assert set(options) == {"locked", "discarded", "normal", "unchanged"}
        assert not any(button.isChecked() for button in options.values())
        options["locked"].click()
        options["discarded"].click()
        assert drawer.draft.management == "discarded" and sum(button.isChecked() for button in options.values()) == 1
        options["discarded"].click()
        assert drawer.draft.management == "" and not any(button.isChecked() for button in options.values())
        options["normal"].click()
        drawer._reset_draft()
        assert drawer.draft.management == ""
        assert rows == original
    finally:
        drawer._animation.stop()
        parent.close()
        parent.deleteLater()
        app.processEvents()


def conflict():
    return {
        "uid": "nte-core-1-1",
        "name": "<b>装备名</b>",
        "quality": "Gold",
        "main": "生命值%",
        "sub_stats": {"攻击力%": 10},
        "item": {"locked": False, "discarded": False},
        "icon_path": None,
        "rules": [
            {
                "rule_id": identity,
                "name": "同名规则",
                "action": action,
                "conditions": {"item_type": "tape", "main_stats": ["生命值%"], "sub_stats": []},
            }
            for identity, action in [("first", "keep"), ("second", "discard")]
        ],
    }


def test_conflict_dialog_is_readonly_and_navigation_uses_ids_not_duplicate_names(app):
    dialog = WarehouseConflictDialog(bundled_text_catalog(), [conflict()])
    emitted = []
    dialog.edit_requested.connect(emitted.append)
    try:
        dialog.show()
        app.processEvents()
        assert not dialog.findChildren(QCheckBox)
        buttons = {button.accessibleName(): button for button in dialog.findChildren(QPushButton)}
        assert set(buttons) == {"关闭", "修改相关规则"}
        buttons["修改相关规则"].click()
        assert emitted == [("first", "second")]
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_conflict_navigation_closes_details_then_opens_warehouse_rules_without_deciding_items(
    app, tmp_path, monkeypatch
):
    ctx = context(tmp_path)
    data = conflict()
    settings = {
        "preserve_rules": [
            dict(**rule["conditions"], name=rule["name"], action=rule["action"], rule_id=rule["rule_id"], enabled=True)
            for rule in data["rules"]
        ]
    }
    monkeypatch.setattr(module, "load_warehouse_editor", lambda *_: editor_data(settings))
    parent, project = QWidget(), Mock()
    controller = module.WarehouseManagementController(
        ctx, bundled_text_catalog(), parent, capture=Mock(), project=project, settings_changed=Mock()
    )
    try:
        guard = Mock()
        controller.open_conflicts([data], guard)
        dialog = controller.conflict_dialog
        dialog.edit_requested.emit(("first", "second"))
        wait(
            app,
            lambda: (
                controller.conflict_dialog is None and controller._expected is not None and not controller.is_running()
            ),
        )
        assert controller.dialog.tabs.currentIndex() == 1
        assert controller.dialog.rules.focus_rule("first")
        project.assert_not_called()
    finally:
        controller.stop()
        wait(app, lambda: controller.dialog is None and not controller.is_running())
        parent.deleteLater()
