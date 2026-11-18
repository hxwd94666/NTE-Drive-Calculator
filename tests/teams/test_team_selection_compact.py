# 验证第二版选队窗口的四队密度、纯展示搜索、隐藏选项隔离和结构化战报状态。
from dataclasses import replace

import pytest
from tests.support.theme import themed_app as themed_app
from src.domain.localization.catalog import make_catalog
from src.integrations.preferences.locale_files import bundled_text_catalog
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.support.qt_fonts import fonts
from tests.allocation.test_team_axis_enhancement import choices, teams
from src.app import window_geometry
from src.app.theme import apply_app_theme, current_theme_name, install_dialog_defaults
from src.domain.teams.models import Team
from src.domain.teams.report_matching import MatchStatus, ReportCandidate, ReportRange, TeamReportMatch
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog


def settle(app):
    for _ in range(8):
        app.processEvents()


@pytest.fixture(params=["black"])
def case(request, monkeypatch):
    app = fonts()
    previous = current_theme_name()
    apply_app_theme(app, request.param)
    install_dialog_defaults(app)
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1920, 1080))
    teams = tuple(Team(f'team-{i}', f'队伍 {i + 1}', tuple(range(i * 4 + 1, i * 4 + 5)), 1) for i in range(8))
    teams = (*teams[:4], replace(teams[4], character_ids=(1, 18, 19, 20)), *teams[5:])
    option = replace(choices(), role_character_ids=tuple((f'成员 {i}', i) for i in range(1, 33)))
    matcher = MatchingFixture(option.entries)
    dialog = TeamAxisDialog(service=None, matching_service=matcher, teams=teams, choices=option)
    dialog.show();settle(app)
    yield app, dialog, matcher, option
    drain_dialog(dialog);dispose(dialog)
    apply_app_theme(app, previous)


def test_team_identity_has_only_name_without_member_count_and_keeps_four_role_actions(case):
    app, dialog, matcher, _option = case
    row = dialog._rows[0]
    card = row['card']
    caption = dialog._text('teams.selection.members_count', count=len(row['team'].character_ids))
    assert not any(label.text() == caption for label in card.findChildren(QLabel))
    assert card.title.accessibleName() == row['team'].name
    assert len(row['slots'].buttons) == 4
    QTest.mouseClick(card.title, Qt.LeftButton);settle(app)
    assert row['check'].isChecked()
    assert not dialog._rows[4]['check'].isEnabled()
    assert dialog.selection is None and matcher.calls == []


def test_four_complete_teams_fit_first_page_and_footer_stays_outside_scroll(case):
    app, dialog, _matcher, _option = case
    viewport = dialog._scroll.viewport()
    for row in dialog._rows[:4]:
        card = row['card']
        rect = card.rect().translated(card.mapTo(viewport, QPoint()))
        assert viewport.rect().contains(rect)
        assert len({button.mapTo(card, QPoint()).y() for button in row['slots'].buttons}) == 1
        assert row['choose'].mapTo(card, QPoint()).x() > row['slots'].mapTo(card, row['slots'].rect().topRight()).x()
    assert dialog._scroll.verticalScrollBar().maximum() > 0
    assert dialog.list_count.text() == dialog._text('teams.selection.visible_count', shown=4, total=8)
    size = dialog.size()
    dialog._scroll.verticalScrollBar().setValue(dialog._scroll.verticalScrollBar().maximum())
    settle(app)
    assert dialog.size() == size
    for button in dialog.buttons.buttons():
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))


def test_search_keeps_hidden_team_selection_source_match_and_shared_member_constraints(case):
    app, dialog, matcher, option = case
    first, second, _, _, overlap, *_ = dialog._rows
    dialog._choices = replace(option, active_edit_record_ids=frozenset({1}))
    result = TeamReportMatch(first['team'], ReportCandidate(option.entries[0], ReportRange(1)), MatchStatus.MATCHED)
    dialog.set_report(first['team'].team_id, result)
    first['anchor'].setCurrentIndex(first['anchor'].findData('effective'))
    first['check'].setChecked(True)
    assert not overlap['check'].isEnabled()
    dialog.search.setText('队伍 2');settle(app)
    assert first['card'].isHidden() and second['card'].isVisible()
    second['check'].setChecked(True)
    dialog.search.setFocus()
    QTest.keyClick(dialog.search, Qt.Key_Return)
    assert dialog.selection is None and matcher.calls == []
    dialog.search.setText('not-a-team');settle(app)
    assert dialog._filtered_empty.isVisible()
    assert first['check'].isChecked() and second['check'].isChecked()
    assert dialog.selection_count.text() == dialog._text('teams.selection.selected_count', count=2, limit=2)
    dialog.search.clear();settle(app)
    assert not first['card'].isHidden()
    assert first['anchor'].currentData() == 'effective' and first['match'] is result
    assert not overlap['check'].isEnabled()
    dialog.search.setText('chengyuan 8');settle(app)
    assert second['card'].isVisible() and first['card'].isHidden()
    assert matcher.calls == []


@pytest.mark.parametrize('status', list(MatchStatus))
def test_report_projection_keeps_scope_status_reason_and_change_action(case, status):
    app, dialog, matcher, option = case
    row = dialog._rows[0]
    result = TeamReportMatch(row['team'], ReportCandidate(option.entries[0], ReportRange(1, 'first')),
                             status, reason='<b>合成原因按纯文本展示</b>')
    dialog.set_report(row['team'].team_id, result)
    settle(app)
    card = row['card']
    assert '上半场' in card.report.accessibleName()
    assert card.report_status.text() == dialog._text('teams.report.status.' + status.value)
    assert result.reason in card.report.accessibleDescription()
    assert card.report.textFormat() == Qt.PlainText
    assert card.report_status.textFormat() == Qt.PlainText
    assert card.choose.text() == dialog._text('teams.selection.change_report')
    assert row['match'] is result and dialog.selection is None and matcher.calls == []


@pytest.mark.parametrize('point_size,caption', [(10, None), (12, None), (14, None),
                                             (12, '已选 {count} / {limit} 支队伍 · SELECTED TEAMS')])
def test_selected_count_is_complete_at_zero_one_and_two(themed_app, monkeypatch, point_size, caption):
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1920, 1080))
    previous = app.font()
    font = app.font();font.setPointSize(point_size);app.setFont(font)
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture',
        messages={'teams.selection.selected_count': caption} if caption else {}), base=bundled_text_catalog())
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams(), choices=choices(), catalog=catalog)
    try:
        dialog.show();settle(app)
        for count in range(3):
            if count:
                dialog._rows[count - 1]['check'].setChecked(True)
            settle(app)
            label = dialog.selection_count
            assert label.text() == dialog._text('teams.selection.selected_count', count=count, limit=2)
            assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
            assert dialog.rect().contains(label.mapTo(dialog, label.rect().bottomRight()))
    finally:
        drain_dialog(dialog);dispose(dialog);app.setFont(previous)
