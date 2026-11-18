# 验证历史战报行内操作完整显示，并保留长文案和战报身份绑定。
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton

from src.app.theme import apply_app_theme, current_theme_name
from src.features.battle_report.history.dialog import BattleReportHistoryDialog
from src.integrations.preferences.locale_files import bundled_text_catalog
from tests.battle_report.history.test_battle_report_history_scene_ui import _entry


@pytest.mark.parametrize("theme", ["black"])
@pytest.mark.parametrize("long_text", [False, True])
def test_history_actions_keep_full_text_and_record_binding(tmp_path, theme, long_text):
    app = QApplication.instance() or QApplication([])
    previous = current_theme_name()
    apply_app_theme(app, theme)
    catalog = bundled_text_catalog()
    if long_text:
        catalog = replace(catalog, messages={**catalog.messages,
            "ui.battle_report.actions_widget.view": "读取所选历史战报详情",
            "warehouse.management.rules.delete": "删除这一条历史战报",
            "ui.battle_report.actions_widget.toggle": "取消这条战报的手动保存",
        })
    dialog = BattleReportHistoryDialog(game_ui_asset_root=tmp_path, text_catalog=catalog)
    viewed, deleted, toggled = [], [], []
    dialog.view_requested.connect(viewed.append)
    dialog.delete_requested.connect(deleted.append)
    dialog.retention_toggle_requested.connect(lambda record, kind: toggled.append((record, kind)))
    try:
        dialog.set_entries((_entry(battle_record_id=21), _entry(battle_record_id=22, retention_kind="manual")))
        dialog.show()
        app.processEvents()
        for row, retention in ((0, "auto"), (1, "manual")):
            cell = dialog.table.cellWidget(row, 7)
            actions = cell.findChildren(QPushButton)
            expected = [catalog.render("ui.battle_report.actions_widget.view"),
                        catalog.render("ui.battle_report.actions_widget.toggle" if retention == "manual" else "common.save"),
                        catalog.render("warehouse.management.rules.delete")]
            assert [button.text() for button in actions] == expected
            assert all(button.fontMetrics().horizontalAdvance(text) <= button.contentsRect().width()
                       for button, text in zip(actions, expected))
            assert all(button.geometry().right() < cell.width() for button in actions)
            for button in actions:
                button.click()
        assert viewed == [21, 22] and deleted == [21, 22]
        assert toggled == [(21, "auto"), (22, "manual")]
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        apply_app_theme(app, previous)
