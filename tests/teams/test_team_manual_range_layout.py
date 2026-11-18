# 验证手动时段字段和确认按钮可同时操作，开发提示只在队伍页出现，补丁文字保持纯文本。
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtWidgets import QDialogButtonBox

from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.matching.team_report_fixture import wait_for
from src.app.theme import apply_app_theme
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.localization.catalog import make_catalog
from src.domain.teams.models import Team
from src.features.teams.page import TeamDevelopmentNotice
from src.features.teams.report_matching.report_match_dialog import TeamReportMatchDialog
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.main_window.main_window_navigation_mixin import MainWindowNavigationMixin
from src.ui.main_window.navigation import nav_index_map
from tests.allocation.test_team_axis_enhancement import choices

from tests.support.qt_fonts import fonts

NTE_TEST_TIER = "core"




@pytest.mark.parametrize("theme", ["black"])
@pytest.mark.parametrize("width,height", [(640, 600)])
def test_all_manual_inputs_and_footer_visible_without_scrolling(theme, width, height, monkeypatch):
    from src.app import window_geometry

    app = fonts()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _: QRect(0, 0, width, height))
    team = Team("team", "时段队", (1, 2, 3, 4), 1)
    service = MatchingFixture(choices().entries, (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        fit_dialog_to_available_screen(dialog, QSize(width, height), available_geometry=QRect(0, 0, width, height))
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        dialog.manual_section.toggle.setChecked(True)
        dialog.manual_report.setCurrentIndex(1)
        dialog.manual_scope.setCurrentIndex(dialog.manual_scope.findData("range"))
        dialog.start.setValue(123.456789)
        dialog.end.setValue(125.876543)
        app.processEvents()
        app.processEvents()
        viewport = dialog.manual_scroll.viewport()
        for widget in (dialog.manual_report, dialog.manual_scope, dialog.start, dialog.end, dialog.manual_check):
            assert not widget.visibleRegion().isEmpty()
            assert viewport.rect().contains(widget.mapTo(viewport, widget.rect().topLeft()))
            assert viewport.rect().contains(widget.mapTo(viewport, widget.rect().bottomRight()))
        for button in dialog.buttons.buttons():
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert dialog.start.value() == pytest.approx(123.456789)
        assert dialog.end.value() == pytest.approx(125.876543)
        assert dialog.buttons.button(QDialogButtonBox.Cancel).isEnabled()
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_notice_plain_text_fallback_and_navigation_visibility():
    app = fonts()
    notice = TeamDevelopmentNotice(
        catalog=make_catalog(
            {
                "format_version": 1,
                "catalog_id": "nte-ui",
                "locale": "en-US",
                "display_name": "old patch",
                "messages": {},
            },
            base=bundled_text_catalog(),
        )
    )
    try:
        assert notice.label.text() == "此页面功能仍在开发，切勿过多操作"
        assert notice.label.textFormat() == Qt.PlainText
        window = SimpleNamespace(
            stack=SimpleNamespace(currentIndex=lambda: 0, setCurrentIndex=Mock()),
            _nav_key_for_index=lambda _: "home",
            topbar_title=Mock(),
            topbar_team_notice=notice,
            topbar_catalog_return=Mock(),
            topbar_source_label=Mock(),
            topbar_equipment_modes=Mock(),
            _nav_buttons={},
            _refresh_navigation_item=Mock(),
        )
        MainWindowNavigationMixin._go(window, "teams")
        app.processEvents()
        assert not notice.isHidden()
        window.stack.setCurrentIndex.assert_called_with(nav_index_map()["teams"])
        MainWindowNavigationMixin._go(window, "home")
        app.processEvents()
        assert notice.isHidden()
    finally:
        dispose(notice)


def test_editing_times_from_whole_report_is_enabled_and_selects_custom_range(monkeypatch):
    from PySide6.QtTest import QTest
    app = fonts()
    team = Team('team', '时段队', (1, 2, 3, 4), 1)
    service = MatchingFixture(choices().entries, (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        dialog.manual_section.toggle.setChecked(True)
        dialog.manual_report.setCurrentIndex(1)
        dialog.manual_scope.setCurrentIndex(dialog.manual_scope.findData('all'))
        assert dialog.start.isEnabled() and dialog.end.isEnabled()
        dialog.start.setFocus()
        dialog.start.selectAll()
        QTest.keyClicks(dialog.start, '0.500000')
        dialog.end.setFocus()
        dialog.end.selectAll()
        QTest.keyClicks(dialog.end, '1.000001')
        assert dialog.manual_scope.currentData() == 'range'
        assert dialog.start.value() == pytest.approx(.5)
        assert dialog.end.value() == pytest.approx(1.000001)
        count = len(service.calls)
        dialog.manual_check.click()
        wait_for(lambda: len(service.calls) > count and not dialog.is_running())
        assert service.calls[-1][1].start_us == 500000
        assert service.calls[-1][1].end_us == 1000001
        assert dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)

