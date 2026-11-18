# 验证四人队伍配装在常规屏幕完整展示、单行提示及保存取消入口，不读取真实账号或操作游戏。
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QAbstractButton

from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.overview.team_overview_fixture import overview_case
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.matching.team_report_fixture import wait_for
from tests.support.qt_fonts import fonts
from src.app.theme import apply_app_theme
from src.features.teams.loadouts.loadout_dialog import TeamLoadoutDialog
from src.domain.teams.overview import SourceMode

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("theme", ["black"])
@pytest.mark.parametrize("mode", [SourceMode.CALC, SourceMode.GAME])
def test_four_members_visible_without_scrolling_and_no_equipment_shortcut(tmp_path, monkeypatch, theme, mode):
    from src.app import window_geometry

    app = fonts()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _: QRect(0, 0, 1280, 960))
    service, team, _ = overview_case(tmp_path)
    dialog = TeamLoadoutDialog(service=service, team=team, roles={})
    try:
        dialog.open()
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        dialog.mode_buttons[mode].click()
        wait_for(lambda: not dialog.is_running())
        app.processEvents()
        app.processEvents()
        assert dialog.scroll.verticalScrollBar().maximum() == 0
        viewport = dialog.scroll.viewport()
        for row in dialog.rows.values():
            assert not row.description.wordWrap()
            assert row.description.accessibleName() and row.description.toolTip()
            assert viewport.rect().contains(row.mapTo(viewport, row.rect().topLeft()))
            assert viewport.rect().contains(row.mapTo(viewport, row.rect().bottomRight()))
        assert all(
            (button.accessibleName() or button.text()) != "前往配装" for button in dialog.findChildren(QAbstractButton)
        )
        assert QRect(0, 0, 1280, 960).contains(dialog.frameGeometry())
    finally:
        drain_dialog(dialog)
        dispose(dialog)




