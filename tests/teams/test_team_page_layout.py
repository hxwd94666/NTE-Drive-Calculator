# 验证队伍卡片重排后的操作身份、未知状态和三主题窄屏可达性。
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.app.theme import apply_app_theme
from src.domain.teams.models import Team, TeamRole
from src.domain.teams.overview import MemberOverview, SourceMode, TeamOverview, TeamSourceConfig
from src.features.teams.page import TeamPage
from src.features.teams.widgets import Disclosure


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('width', [560])
def test_card_actions_fit_and_keep_team_identity(theme, width, tmp_path):
    app = application()
    original = (app.styleSheet(), app.palette(), app.property('nte_theme_preference'), app.property('nte_effective_theme'))
    apply_app_theme(app, theme)
    page = TeamPage()
    team = Team('one', '备用队', (1, 2, 3, 4), 1)
    events = []
    from PySide6.QtGui import QColor, QPixmap
    portrait = QPixmap(36, 36)
    portrait.fill(QColor('#58a6ff'))
    path = tmp_path / 'synthetic-portrait.png'
    assert portrait.save(str(path))
    page.show_teams((team,), tuple(TeamRole(i, f'角色{i}', str(path)) for i in team.character_ids))
    page.enhancement_details_requested.connect(lambda key: events.append(('history', key)))
    page.graduation_requested.connect(lambda key: events.append(('graduation', key)))
    page.edit_requested.connect(lambda key: events.append(('edit', key)))
    page.loadouts_requested.connect(lambda key: events.append(('loadouts', key)))
    page.delete_requested.connect(lambda key: events.append(('delete', key)))
    page.calculation_requested.connect(lambda key: events.append(('calculate', key)))
    try:
        page.resize(width, 760)
        page.show()
        for _ in range(6):
            app.processEvents()
        card = page._cards[0][0]
        portraits = [button for button in card.findChildren(QToolButton) if button.menu() is None]
        assert len(portraits) == 4
        assert all(button.isVisible() and not button.icon().isNull() for button in portraits)
        # 宽窄切换只重排原控件，不重新读取简报或丢失头像、焦点和 team_id。
        before = tuple(portraits)
        for resized_width in (1200, 560, width):
            page.resize(resized_width, 760)
            for _ in range(3):
                app.processEvents()
            assert tuple(button for button in card.findChildren(QToolButton) if button.menu() is None) == before
            assert card.rect().contains(card.right.mapTo(card, card.right.rect().bottomRight()))
        actions = [b for b in card.findChildren(QPushButton) if b.isVisible()]
        for button in actions + portraits:
            assert card.rect().contains(button.mapTo(card, button.rect().topLeft()))
            assert card.rect().contains(button.mapTo(card, button.rect().bottomRight()))
        for index, button in enumerate(actions):
            area = button.rect().translated(button.mapTo(card, button.rect().topLeft()))
            assert all(not area.intersects(other.rect().translated(other.mapTo(card, other.rect().topLeft())))
                       for other in actions[index + 1:])
        loadouts = next(b for b in actions if b.accessibleName().endswith('：队伍配装'))
        loadouts.setFocus()
        QTest.keyClick(loadouts, Qt.Key_Space)
        next(b for b in actions if b.accessibleName().endswith('：毕业率')).click()
        next(b for b in actions if b.accessibleName().endswith('：增强记录')).click()
        calculate = next(b for b in actions if b.accessibleName().endswith('：单独计算'))
        # 用户能从每个摘要直接定位其下方操作，最后一列的计算与更多亦同轴。
        more = next(b for b in card.findChildren(QToolButton) if b.menu())
        for label, button in zip(card.labels.values(), [loadouts,
                next(b for b in actions if b.accessibleName().endswith('：毕业率')),
                next(b for b in actions if b.accessibleName().endswith('：增强记录'))]):
            assert abs(label.mapTo(card, label.rect().center()).x() - button.mapTo(card, button.rect().center()).x()) <= 1
        assert abs(calculate.mapTo(card, calculate.rect().center()).x() - more.mapTo(card, more.rect().center()).x()) <= 1
        calculate.setFocus()
        QTest.keyClick(calculate, Qt.Key_Space)
        assert not any(b.text() in ('匹配战报', '战报增强') for b in actions)
        menu = next(b.menu() for b in card.findChildren(QToolButton) if b.menu())
        next(a for a in menu.actions() if a.text() == '删除队伍').trigger()
        portraits[0].click()
        assert events == [('loadouts', 'one'), ('graduation', 'one'), ('history', 'one'), ('calculate', 'one'),
                          ('delete', 'one'), ('edit', 'one')]
        labels = page._overview_labels[team.team_id]
        page.show_overview(TeamOverview(team, TeamSourceConfig('one', SourceMode.CALC, 1),
                           (MemberOverview(1, '角色1', issues=('缺少来源快照',)),)))
        assert not labels['source'].wordWrap() and '缺少来源快照' in labels['source'].toolTip()
        assert labels['source'].accessibleName() == '配装：计算 · 0/4'
        assert '%' not in labels['axis_graduation'].text()
        page.search.setText('beiyong')
        assert not card.isHidden()
        page.clear_account()
        assert not page._overview_labels and page._teams == ()
    finally:
        dispose(page)
        app.setStyleSheet(original[0])
        app.setPalette(original[1])
        app.setProperty('nte_theme_preference', original[2])
        app.setProperty('nte_effective_theme', original[3])


def test_optional_settings_expand_by_keyboard_without_business_action():
    app = application()
    content = QWidget()
    section = Disclosure('高级设置', content)
    try:
        section.show()
        app.processEvents()
        assert content.isHidden()
        section.toggle.setFocus()
        QTest.keyClick(section.toggle, Qt.Key_Space)
        assert content.isVisible()
        QTest.keyClick(section.toggle, Qt.Key_Space)
        assert content.isHidden()
    finally:
        dispose(section)


@pytest.mark.parametrize('theme', ["black"])
def test_target_settings_confirm_only_a_draft_and_cancel_keeps_it_empty(theme):
    from PySide6.QtWidgets import QDialogButtonBox
    from src.domain.teams.graduation.axis_graduation_configuration import (
        ConfigurationMember,
        GraduationConfiguration,
        LayoutTemplate,
    )
    from src.features.teams.graduation.axis_graduation_target_dialog import GraduationTargetDialog
    from tests.teams.graduation.test_team_axis_graduation_requests import prepared

    app = application()
    original = (app.styleSheet(), app.palette(), app.property('nte_theme_preference'), app.property('nte_effective_theme'))
    apply_app_theme(app, theme)
    frozen, values = prepared()
    target = values['target']
    preset = target.presets[0]
    config = GraduationConfiguration(frozen.account_id, frozen.generation, frozen.team, values['rules'],
        target.target_id, target.revision,
        tuple(ConfigurationMember(f'角色{m.character_id}', m, ((m.character_level, m.breakthrough_stage),),
              tuple((skill, ((level, 0, 0),)) for skill, level in m.skill_levels), n)
              for n, m in enumerate(preset.members, 1)),
        tuple(LayoutTemplate(layout.character_id, n, n, f'方案{n}', layout)
              for n, layout in enumerate(preset.layouts, 1)), target.world_bonus)
    dialog = GraduationTargetDialog(configuration=config)
    try:
        dialog.open()
        app.processEvents()
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert dialog.target is None
        dialog.open()
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        assert dialog.target is not None and not dialog.isVisible()
    finally:
        dispose(dialog)
        app.setStyleSheet(original[0])
        app.setPalette(original[1])
        app.setProperty('nte_theme_preference', original[2])
        app.setProperty('nte_effective_theme', original[3])
