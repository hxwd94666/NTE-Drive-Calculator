# 验证队伍候选拼音排序、紧凑摘要的未知语义和队伍选择底栏的键盘与语言回退。
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QPushButton, QToolButton

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.app.theme import apply_app_theme, current_theme_name
from src.domain.localization.catalog import make_catalog
from src.domain.teams.graduation.axis_graduation_presentation import AxisGraduationDisplay
from src.domain.teams.graduation.axis_graduation_validity import AxisResultValidity
from src.features.teams.graduation.axis_graduation_presentation import empty_axis_display, validity_display
from src.domain.teams.models import Team, TeamRole
from src.domain.teams.overview import SourceMode, TeamOverview, TeamSourceConfig
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from src.features.teams.editor import TeamEditor
from src.features.teams.page import TeamPage
from src.integrations.preferences.locale_files import bundled_text_catalog


def test_member_candidates_are_az_after_search_resize_and_do_not_reorder_saved_team():
    app = application()
    roles = (TeamRole(4, '早雾'), TeamRole(2, '安魂曲'), TeamRole(3, 'Exe'), TeamRole(1, '白藏'))
    team = Team('t', '顺序不变', (4, 2, 3), 1)
    dialog = TeamEditor(roles=roles, team=team)
    try:
        dialog.show()
        app.processEvents()
        assert tuple(dialog.role_buttons) == (2, 1, 3, 4)
        dialog.search.setText('e')
        dialog.resize(600, 600)
        app.processEvents()
        dialog.search.clear()
        app.processEvents()
        assert [dialog.grid.itemAt(i).widget() for i in range(dialog.grid.count())] == list(dialog.role_buttons.values())
        assert dialog.draft().character_ids == team.character_ids
        dialog.role_buttons[1].click()
        assert dialog.draft().character_ids == (4, 2, 3, 1)
    finally:
        dispose(dialog)


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('width', [560])
def test_team_selection_footer_is_usable_with_long_patch_and_manage_cancels_only(theme, width):
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture',
        messages={'teams.selection.manage': '<b>Manage teams</b> ' + 'long translation ' * 8}), base=bundled_text_catalog())
    dialog = TeamAxisDialog(service=None, matching_service=None, teams=(),
        choices=SimpleNamespace(role_character_ids=()), catalog=catalog)
    try:
        dialog.resize(width, 650)
        dialog.show()
        app.processEvents()
        manage = next(button for button in dialog.findChildren(QPushButton)
                      if button.accessibleName() == catalog.render('teams.selection.manage'))
        ok = dialog.buttons.button(QDialogButtonBox.Ok)
        cancel = dialog.buttons.button(QDialogButtonBox.Cancel)
        assert manage.accessibleName() == catalog.render('teams.selection.manage')
        assert manage.width() < dialog.width() / 2
        for button in (manage, ok, cancel):
            assert button.isVisible() and button.isEnabled()
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert cancel.mapTo(dialog, cancel.rect().topRight()).x() < ok.mapTo(dialog, ok.rect().topLeft()).x()
        manage.setFocus()
        QTest.keyClick(manage, Qt.Key_Space)
        app.processEvents()
        assert dialog.manage_teams and dialog.selection is None and not dialog.isVisible()
    finally:
        dialog.reject()
        dispose(dialog)
        apply_app_theme(app, previous)


@pytest.mark.parametrize('percent', [None, 0.0, 113.42])
def test_card_compact_ratio_keeps_unknown_zero_over_reference_and_stale_distinct(percent):
    application()
    team = Team('t', '测试', (1, 2, 3, 4), 1)
    page = TeamPage()
    page.show_teams((team,), ())
    labels = page._overview_labels[team.team_id]
    display = AxisGraduationDisplay('完整标题与可执行性说明', '当前轴伤', '范围', '证据', '目标', '建议', percent)
    try:
        assert tuple(labels) == ('source', 'axis_graduation', 'enhancement')
        assert not any(button.text() == '刷新' for button in page.findChildren(QPushButton))
        page.show_axis_graduation(team, display)
        assert ('未知' in labels['axis_graduation'].text()) == (percent is None)
        if percent is not None:
            assert f'{percent:.2f}%' in labels['axis_graduation'].text()
            stale = validity_display(display, AxisResultValidity('matching', 'stale', 'ready'))
            page.show_axis_graduation(team, stale)
            assert '待更新' in labels['axis_graduation'].text()
            assert f'{percent:.2f}%' in labels['axis_graduation'].text()
        page.show_axis_graduation(team, empty_axis_display())
        assert '待建基准' in labels['axis_graduation'].text() and '%' not in labels['axis_graduation'].text()
        page.show_axis_graduation(team, replace(display, historical=True))
        assert '完整标题' in labels['axis_graduation'].toolTip()
        overview = TeamOverview(team, TeamSourceConfig('t', SourceMode.CALC, 1), ())
        page.show_overview(overview)
        assert labels['enhancement'].text() == '增强：暂无记录'
        page.show_overview_error(team.team_id, '<b>读取失败</b>')
        assert '待核对' in labels['source'].text() and '待核对' in labels['enhancement'].text()
        assert '&lt;b&gt;' in labels['source'].toolTip()
    finally:
        dispose(page)




@pytest.mark.parametrize('theme', ["black"])
def test_long_team_patch_keeps_all_actions_operable_on_narrow_page(theme):
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    base = bundled_text_catalog()
    sample = make_catalog(json.loads((Path(__file__).parents[2] / 'docs/examples/locales/qps-ploc.json')
                                   .read_text(encoding='utf8')), base=base)
    page = TeamPage(catalog=sample)
    team = Team('t', '<b>纯文本队名</b>', (1, 2, 3, 4), 1)
    page.show_teams((team,), ())
    try:
        page.resize(560, 720)
        page.show()
        app.processEvents()
        card = page._cards[0][0]
        for button in card.findChildren(QPushButton) + [b for b in card.findChildren(QToolButton) if b.menu()]:
            assert button.isVisible() and button.isEnabled()
            assert page.rect().contains(button.mapTo(page, button.rect().topLeft()))
            assert page.rect().contains(button.mapTo(page, button.rect().bottomRight()))
    finally:
        dispose(page)
        apply_app_theme(app, previous)
# 验证正常队伍选择仅有一条指引，文案补丁参与实际展示。
def test_selection_has_one_normal_guidance_region_and_catalog_text():
    from PySide6.QtWidgets import QLabel
    application()
    team = Team('one', '单队', (1, 2, 3, 4), 1)
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture',
        messages={'teams.selection.hint': '<b>Choose matching reports</b>', 'teams.selection.confirm': 'Apply'}),
        base=bundled_text_catalog())
    dialog = TeamAxisDialog(service=None, matching_service=None, teams=(team,),
        choices=SimpleNamespace(role_character_ids=()), catalog=catalog)
    try:
        assert dialog.error.text() == '<b>Choose matching reports</b>'
        assert dialog.error.textFormat() == Qt.PlainText
        assert dialog.buttons.button(QDialogButtonBox.Ok).accessibleName() == 'Apply'
        assert [label for label in dialog.findChildren(QLabel) if label.text() == dialog.error.text()] == [dialog.error]
    finally:
        dialog.reject()
        dispose(dialog)
