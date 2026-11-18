# 验证主题不覆盖业务按钮、选队单一来源展示与新增前端文案的冻结补丁行为。
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QPushButton

from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.support.qt_fonts import fonts
from tests.allocation.test_team_axis_enhancement import choices, teams
from src.app.theme import apply_app_theme, current_theme_name, install_dialog_defaults
from src.domain.localization.catalog import make_catalog
from src.domain.teams.report_matching import ReportRange, MatchStatus, TeamReportMatch
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from src.features.inventory.warehouse.management.management_dialog import WarehouseManagementDialog
from src.features.teams.editor import TeamEditor
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, set_button_text
from src.ui.common.tooltip_policy import bind_elided_combo_tooltip

NTE_TEST_TIER = 'core'


def patch(messages):
    return make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US',
                             display_name='Fixture', messages=messages), base=bundled_text_catalog())


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('caption', ['确定', '生成预览', 'Preview pending changes for selected equipment ' * 3])
def test_real_show_filter_preserves_explicit_caption_and_footer_is_accessible(tmp_path, theme, caption):
    app = fonts()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    install_dialog_defaults(app)
    dialog = WarehouseManagementDialog(patch({'warehouse.management.preview': caption}), config_dir=tmp_path)
    try:
        dialog.show()
        app.processEvents()
        ok = dialog.buttons.button(QDialogButtonBox.Ok)
        assert ok.accessibleName() == caption
        assert bool(ok.toolTip()) == (ok.text() != caption)
        # 极长补丁可以省略，但主题不能替换成与动作无关的标准确认。
        assert ok.width() <= 260
        assert ok.width() <= ok.fontMetrics().horizontalAdvance(caption) + 36
        assert dialog.rect().contains(ok.mapTo(dialog, ok.rect().bottomRight()))
        if len(caption) < 10:
            assert ok.text() == caption
    finally:
        QDialog.done(dialog, QDialog.Rejected)
        dispose(dialog)
        apply_app_theme(app, previous)


def test_standard_button_helper_keeps_business_role_after_repeated_show():
    app = fonts()
    install_dialog_defaults(app)
    dialog = QDialog()
    box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=dialog)
    button = box.button(QDialogButtonBox.Save)
    set_button_text(button, 'Save selected team')
    try:
        for _ in range(2):
            dialog.show()
            app.processEvents()
            assert button.accessibleName() == 'Save selected team'
            assert box.standardButton(button) == QDialogButtonBox.Save
            dialog.hide()
    finally:
        dispose(dialog)


@pytest.mark.parametrize('kind', ['label', 'button', 'standard', 'combo'])
def test_caption_tooltip_only_completes_elided_text_and_clears_after_expansion(kind):
    app = fonts()
    caption = 'Select the saved plan'
    if kind == 'combo':
        widget = QComboBox()
        widget.addItem(caption)
        bind_elided_combo_tooltip(widget)
    elif kind == 'standard':
        widget = QPushButton()
        set_button_text(widget, caption)
    else:
        widget = ElidedTextLabel(caption) if kind == 'label' else TextActionButton(caption)
    try:
        widget.resize(600, 40)
        widget.show()
        app.processEvents()
        assert not widget.toolTip()
        widget.setMinimumWidth(0)
        widget.resize(65, 40)
        app.processEvents()
        assert caption in widget.toolTip()
        widget.resize(600, 40)
        app.processEvents()
        assert not widget.toolTip()
        if kind in {'label', 'button'}:
            widget.setToolTip('Meaningful help')
            widget.resize(65, 40)
            app.processEvents()
            assert widget.toolTip() == 'Meaningful help'
            widget.setText('Ready')
            widget.resize(600, 40)
            app.processEvents()
            assert not widget.toolTip()
            widget.setText(caption)
            widget.setToolTip('')
            widget.resize(75, 40)
            app.processEvents()
            assert not widget.toolTip()
            assert widget.accessibleName() == caption
    finally:
        dispose(widget)


def test_report_action_is_available_before_selection_and_source_is_only_a_choice_when_multiple():
    app = fonts()
    option = replace(choices(), active_edit_record_ids=frozenset({1}))
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams()[:1], choices=option)
    try:
        dialog.show()
        app.processEvents()
        row = dialog._rows[0]
        assert not row['check'].isChecked()
        assert row['choose'].isVisible() and row['choose'].isEnabled()
        rectangles = [button.geometry() for button in row['slots'].buttons]
        assert all(not left.intersects(right) for i, left in enumerate(rectangles) for right in rectangles[i + 1:])
        assert row['anchor'].isHidden() and row['config'].isHidden()
        assert not any(label.text() == dialog._text('teams.selection.source_original')
                       for label in row['card'].findChildren(QLabel))
        candidate = MatchingFixture(option.entries).candidate(ReportRange(1))
        dialog.set_report(row['team'].team_id, TeamReportMatch(row['team'], candidate, MatchStatus.MATCHED))
        app.processEvents()
        assert row['anchor'].isVisible() and row['config'].isVisible()
        assert row['anchor'].findData('original') >= 0 and row['anchor'].findData('effective') >= 0
        assert dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_team_editor_uses_same_patch_in_fields_buttons_and_empty_member():
    app = fonts()
    catalog = patch({'teams.editor.create': 'Create team', 'teams.editor.name': '<b>Name</b>',
                     'teams.editor.save': 'Save team', 'teams.member.empty': 'Choose member'})
    dialog = TeamEditor(roles=(), catalog=catalog)
    try:
        dialog.show()
        app.processEvents()
        assert dialog.windowTitle() == 'Create team'
        assert dialog.name_edit.accessibleName() == '<b>Name</b>'
        assert dialog.buttons.button(QDialogButtonBox.Save).accessibleName() == 'Save team'
        assert all(button.text() == 'Choose member' for button in dialog.member_buttons)
        assert any(label.text() == '<b>Name</b>' and label.textFormat() == Qt.PlainText
                   for label in dialog.findChildren(QLabel))
    finally:
        dispose(dialog)


