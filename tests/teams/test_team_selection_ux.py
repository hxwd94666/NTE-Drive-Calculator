# 验证紧凑选队/战报交互、就近原因、明确确认、冻结语言与三主题窄窗可操作性。
from dataclasses import replace
from pathlib import Path
import json
from threading import Event

import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.matching.team_report_fixture import wait_for
from tests.allocation.test_team_axis_enhancement import choices, teams
from src.app.theme import apply_app_theme, current_theme_name
from src.domain.localization.catalog import make_catalog
from src.domain.teams.report_matching import MatchStatus, ReportRange, TeamReportMatch
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from src.features.teams.report_matching.report_match_dialog import TeamReportMatchDialog
from src.integrations.preferences.locale_files import bundled_text_catalog


def test_card_passive_area_toggles_but_source_control_does_not_and_constraints_remain():
    application()
    option = replace(choices(), active_edit_record_ids=frozenset({1}))
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams(), choices=option)
    try:
        dialog.show()
        application().processEvents()
        a, b, overlap, third = dialog._rows
        QTest.mouseClick(a['card'].title, Qt.LeftButton)
        assert a['check'].isChecked() and not overlap['check'].isEnabled()
        assert overlap['reason'].isVisible() and '重复' in overlap['reason'].text()
        candidate = MatchingFixture(option.entries).candidate(ReportRange(1))
        dialog.set_report(a['team'].team_id, TeamReportMatch(a['team'], candidate, MatchStatus.MATCHED))
        a['anchor'].setCurrentIndex(a['anchor'].findData('effective'))
        a['anchor'].setFocus()
        QTest.keyClick(a['anchor'], Qt.Key_Return)
        assert a['check'].isChecked() and a['anchor'].currentData() == 'effective' and dialog.selection is None
        QTest.mouseClick(b['card'].title, Qt.LeftButton)
        assert b['check'].isChecked() and not third['check'].isEnabled()
        third['check'].click()
        assert not third['check'].isChecked()
        assert all(not button.icon().isNull() for button in a['slots'].buttons)
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_loaded_cards_do_not_resize_window_or_reset_choice_when_detail_expands():
    application()
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams(), choices=choices())
    try:
        dialog.show()
        application().processEvents()
        size = dialog.size()
        dialog._rows[0]['check'].setChecked(True)
        dialog._rows[1]['check'].setChecked(True)
        application().processEvents()
        assert dialog.size() == size
        assert all(row['anchor'].isHidden() and row['config'].isHidden() for row in dialog._rows)
    finally:
        drain_dialog(dialog)
        dispose(dialog)


@pytest.mark.parametrize('status', list(MatchStatus))
def test_each_match_state_explains_next_step_and_only_matched_is_selectable(status):
    application()
    team = teams()[0]
    service = MatchingFixture(choices().entries[:1], (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(lambda: dialog._results and not dialog.is_running())
        scope, old = next(iter(dialog._results.items()))
        dialog._results[scope] = replace(old, status=status, reason='合成原因 <b>保持原文</b>')
        dialog.status_filter.setCurrentIndex(dialog.status_filter.findData('all'))
        dialog._render()
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        assert '合成原因 <b>保持原文</b>' in dialog.details.text()
        assert dialog.details.textFormat() == Qt.PlainText
        assert bundled_text_catalog().render('teams.report.next.' + status.value) in dialog.details.text()
        assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled() == (status == MatchStatus.MATCHED)
        dialog.list.setFocus()
        QTest.keyClick(dialog.list, Qt.Key_Return)
        assert dialog.selection is None and not dialog.is_running()
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_filter_empty_state_is_actionable_and_does_not_turn_unknown_into_match():
    application()
    service = MatchingFixture(choices().entries[:1], (teams()[0],))
    dialog = TeamReportMatchDialog(service=service, team=teams()[0])
    try:
        dialog.show()
        wait_for(lambda: dialog._results and not dialog.is_running())
        scope, old = next(iter(dialog._results.items()))
        dialog._results[scope] = replace(old, status=MatchStatus.INSUFFICIENT, report_character_ids=())
        dialog._render()
        assert dialog.empty.isVisible() and dialog.show_all.isVisible()
        dialog.show_all.click()
        assert dialog.status_filter.currentData() == 'all' and dialog.list.topLevelItemCount() == 1
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        dialog.status_filter.setCurrentIndex(dialog.status_filter.findData('matched'))
        assert dialog.list.currentItem() is None and dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_progress_keeps_selected_scope_and_scroll_and_rechecks_before_acceptance():
    application()
    entries = tuple(replace(choices().entries[0], battle_record_id=i + 1) for i in range(30))
    service = MatchingFixture(entries, (teams()[0],))
    dialog = TeamReportMatchDialog(service=service, team=teams()[0])
    try:
        dialog.show()
        wait_for(lambda: len(dialog._results) == 30 and not dialog.is_running())
        dialog.list.setCurrentItem(dialog.list.topLevelItem(8))
        scope = dialog.list.currentItem().data(0, Qt.UserRole)
        bar = dialog.list.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        before = bar.value()
        dialog._progress(next(iter(dialog._results.values())))
        assert dialog.list.currentItem().data(0, Qt.UserRole) == scope and bar.value() == before
        service.different.add(teams()[0].team_id)
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        wait_for(lambda: not dialog.is_running())
        assert dialog.selection is None and dialog.isVisible()
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    finally:
        drain_dialog(dialog)
        dispose(dialog)


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('width', [560])
def test_long_patch_controls_fit_and_filters_keep_stable_ids(theme, width, monkeypatch):
    from src.app import window_geometry
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _dialog: QRect(0, 0, width, 800))
    base = bundled_text_catalog()
    sample = make_catalog(json.loads((Path(__file__).parents[2] / 'docs/examples/locales/qps-ploc.json').read_text(encoding='utf8')), base=base)
    dialog = TeamReportMatchDialog(service=MatchingFixture(choices().entries[:1]), team=teams()[0], catalog=sample)
    try:
        dialog.show()
        wait_for(lambda: dialog._results and not dialog.is_running())
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        dialog.manual_report.setCurrentIndex(1)
        dialog.manual_section.toggle.setChecked(True)
        dialog.manual_check.setFocus()
        dialog.manual_scroll.ensureWidgetVisible(dialog.manual_check)
        app.processEvents()
        for control in (dialog.status_filter, dialog.scene_filter, dialog.refresh_button, dialog.more_button,
                        dialog.manual_check, *dialog.buttons.buttons()):
            assert control.isVisible()
            assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
            assert not control.visibleRegion().isEmpty()
        assert dialog.rect().width() <= width
        assert dialog.list.horizontalScrollBar().maximum() == 0
        assert dialog.status_filter.currentData() == 'matched'
        assert dialog.buttons.button(QDialogButtonBox.Ok).accessibleName() == sample.render('teams.report.confirm')
    finally:
        drain_dialog(dialog)
        dispose(dialog)
        apply_app_theme(app, previous)




def test_async_team_load_keeps_initial_geometry_and_internal_picker_keeps_catalog(monkeypatch):
    from src.features.allocation.team_axis import team_axis_dialog as module
    from PySide6.QtWidgets import QDialog
    entered, released = Event(), Event()
    def load(*_):
        entered.set()
        assert released.wait(3)
        return teams(), choices()
    monkeypatch.setattr(module, 'load_team_choices', load)
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture',
        messages={'teams.report.title': 'Pick report: {team}'}), base=bundled_text_catalog())
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), catalog=catalog)
    received = []
    class Picker(QDialog):
        selection = None
        def __init__(self, **kwargs):
            received.append(kwargs['catalog'])
            super().__init__(kwargs['parent'])
        def exec(self):
            return QDialog.Rejected
    try:
        dialog.show()
        wait_for(entered.is_set)
        before = dialog.size()
        released.set()
        wait_for(lambda: dialog._rows and not dialog.is_running())
        assert dialog.size() == before
        first = dialog._rows[0]
        first['check'].setChecked(True)
        monkeypatch.setattr(module, 'TeamReportMatchDialog', Picker)
        first['choose'].click()
        assert received == [catalog] and first['check'].isChecked() and dialog.selection is None
    finally:
        released.set()
        drain_dialog(dialog)
        dispose(dialog)


@pytest.mark.parametrize('theme', ["black"])
def test_team_card_long_patch_keeps_four_portraits_and_configuration_in_small_window(theme, monkeypatch):
    from src.app import window_geometry
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _dialog: QRect(0, 0, 560, 720))
    sample = make_catalog(json.loads((Path(__file__).parents[2] / 'docs/examples/locales/qps-ploc.json').read_text(encoding='utf8')), base=bundled_text_catalog())
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams()[:1], choices=choices(), catalog=sample)
    try:
        dialog.show()
        first = dialog._rows[0]
        first['check'].setChecked(True)
        app.processEvents()
        for control in [*first['slots'].buttons, first['choose'], *dialog.buttons.buttons()]:
            assert control.isVisible() and not control.visibleRegion().isEmpty()
            assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
        assert first['anchor'].currentData() == 'original' and first['anchor'].isHidden()
        assert dialog.frameGeometry().right() < 560
    finally:
        drain_dialog(dialog)
        dispose(dialog)
        apply_app_theme(app, previous)


def test_read_failure_leaves_retry_not_a_loading_state_or_confirmation():
    class Flaky(MatchingFixture):
        failed = False
        def discover(self, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError('<b>合成读取错误</b>')
            return super().discover(**kwargs)
    application()
    dialog = TeamReportMatchDialog(service=Flaky(choices().entries[:1]), team=teams()[0])
    try:
        dialog.show()
        wait_for(lambda: dialog._warning and not dialog.is_running())
        assert '读取未完成' in dialog.empty_title.text() and '<b>合成读取错误</b>' in dialog.status.text()
        assert dialog.refresh_button.isEnabled() and not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        dialog.refresh_button.click()
        wait_for(lambda: dialog._results and not dialog.is_running())
        assert not dialog._warning and dialog.list.isVisible() and dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)
