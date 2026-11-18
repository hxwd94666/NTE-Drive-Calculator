# 验证战报增强单行入口的键盘配置、取消、账号重置与三主题窄屏可达性。
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QRadioButton, QVBoxLayout, QWidget

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.app.theme import apply_app_theme
from src.features.allocation.team_axis import team_axis_controls as controls
from src.features.allocation.execute_page import _build_strategy_card
from src.services.allocation.inputs.search_settings import load_search_mode
from src.storage.sqlite.user_data_dao import UserDataDao


def ui_case(directory):
    application()
    database = directory / 'synthetic-user.sqlite3'
    UserDataDao(database, account_id='synthetic').close()
    page = QWidget()
    layout = QVBoxLayout(page)
    def card(title):
        frame = QFrame()
        frame.setObjectName('card')
        body = QVBoxLayout(frame)
        body.setContentsMargins(20, 14, 20, 14)
        body.setSpacing(12)
        body.addWidget(QLabel(title))
        return frame
    owner = SimpleNamespace(
        app_context=SimpleNamespace(account=SimpleNamespace(user_database_path=database)),
        dialog_parent=page, _card=card, _open_allocation_filter_settings=Mock(),
        _axis_selection_service_provider=lambda: object(), _team_report_service_provider=lambda: object(),
        role_selector=SimpleNamespace(all_roles=()), navigate=Mock(), _do_exec=Mock(),
    )
    _build_strategy_card(owner, layout)
    layout.addStretch()
    return page, owner, database


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('width', [560])
def test_controls_share_a_row_below_title_and_keep_configuration_explicit(tmp_path, monkeypatch, theme, width):
    app = application()
    original = (app.styleSheet(), app.palette(), app.property('nte_theme_preference'), app.property('nte_effective_theme'))
    apply_app_theme(app, theme)
    page, owner, database = ui_case(tmp_path)
    selected = object()
    dialog = Mock(selection=selected, manage_teams=False)
    dialog.exec.return_value = QDialog.Accepted
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(controls, 'TeamAxisDialog', factory)
    try:
        page.resize(width, 420)
        page.show()
        app.processEvents()
        row = [owner.team_axis_check, owner.team_axis_configure, owner.team_axis_search]
        assert owner.team_axis_configure.text() == '进行配置'
        assert all(not widget.isHidden() for widget in row)
        assert not owner.team_axis_configure.isEnabled() and not owner.team_axis_search.isEnabled()
        assert owner.team_axis_search.currentData() == 'fast'
        tops = [widget.mapTo(page, widget.rect().topLeft()).y() for widget in row]
        bottoms = [widget.mapTo(page, widget.rect().bottomRight()).y() for widget in row]
        assert max(tops) <= min(bottoms)
        title = next(label for label in page.findChildren(QLabel) if label.text() == '第三步 · 分配策略')
        strategies = page.findChildren(QRadioButton)
        assert title.mapTo(page, title.rect().bottomRight()).y() < min(tops)
        assert max(bottoms) < min(widget.mapTo(page, widget.rect().topLeft()).y() for widget in strategies)
        for widget in row:
            assert page.rect().contains(widget.mapTo(page, widget.rect().topLeft()))
            assert page.rect().contains(widget.mapTo(page, widget.rect().bottomRight()))
        assert owner.strategy_group.checkedId() == 0
        factory.assert_not_called()
        owner.team_axis_check.setFocus()
        QTest.keyClick(owner.team_axis_check, Qt.Key_Space)
        assert factory.call_count == 1 and owner.team_axis_selection is selected
        assert owner.team_axis_configure.isEnabled() and owner.team_axis_search.isEnabled()
        owner.team_axis_search.setCurrentIndex(owner.team_axis_search.findData('deep'))
        assert load_search_mode(database) == 'deep'
        owner.team_axis_check.setChecked(False)
        assert owner.team_axis_selection is selected
        assert not owner.team_axis_configure.isEnabled() and not owner.team_axis_search.isEnabled()
        owner.team_axis_check.setChecked(True)
        assert factory.call_count == 1  # 重开沿用当前配置，不反复弹窗。
        dialog.exec.return_value = QDialog.Rejected
        owner.team_axis_configure.click()
        assert owner.team_axis_selection is selected and owner.team_axis_check.isChecked()
        assert owner.strategy_group.checkedId() == 0 and not hasattr(owner, 'team_axis_summary')
        owner._do_exec.assert_not_called()
        controls.reset_team_axis_controls(owner)
        assert owner.team_axis_selection is None and not owner.team_axis_check.isChecked()
        assert not owner.team_axis_configure.isEnabled() and not owner.team_axis_search.isEnabled()
    finally:
        dispose(page)
        app.setStyleSheet(original[0])
        app.setPalette(original[1])
        app.setProperty('nte_theme_preference', original[2])
        app.setProperty('nte_effective_theme', original[3])


def test_first_configuration_cancel_restores_disabled_state(tmp_path, monkeypatch):
    page, owner, _database = ui_case(tmp_path)
    dialog = Mock(selection=None, manage_teams=False)
    dialog.exec.return_value = QDialog.Rejected
    monkeypatch.setattr(controls, 'TeamAxisDialog', Mock(return_value=dialog))
    try:
        owner.team_axis_check.setChecked(True)
        assert not owner.team_axis_check.isChecked() and owner.team_axis_selection is None
        assert not owner.team_axis_configure.isEnabled() and not owner.team_axis_search.isEnabled()
        owner._do_exec.assert_not_called()
    finally:
        dispose(page)


def test_team_entry_can_configure_while_off_and_reset_drops_disabled_draft(tmp_path, monkeypatch):
    page, owner, _database = ui_case(tmp_path)
    selected = object()
    dialog = Mock(selection=selected, manage_teams=False)
    dialog.exec.return_value = QDialog.Accepted
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(controls, 'TeamAxisDialog', factory)
    try:
        assert controls.configure_team_axes(owner, initial_team_id='synthetic-team') is selected
        assert owner.team_axis_check.isChecked() and owner.team_axis_configure.isEnabled()
        assert factory.call_args.kwargs['initial_team_id'] == 'synthetic-team'
        owner.team_axis_check.setChecked(False)
        controls.reset_team_axis_controls(owner)
        assert owner.team_axis_selection is None and not owner.team_axis_configure.isEnabled()
        empty_owner = SimpleNamespace(team_axis_selection=selected)
        controls.reset_team_axis_controls(empty_owner)
        assert empty_owner.team_axis_selection is None
        owner._do_exec.assert_not_called()
    finally:
        dispose(page)


def test_scanning_account_reset_clears_disabled_draft_and_reloads_new_account_mode(tmp_path):
    from src.features.scanning.controller import ScanningController

    page, owner, database = ui_case(tmp_path)
    other = tmp_path / 'other-user.sqlite3'
    UserDataDao(other, account_id='other').close()
    owner.request_stop = Mock()
    owner._vision_commit = Mock()
    owner._preferences_provider = Mock(return_value=object())
    owner._reload_full_scan_preference_widgets = Mock()
    owner._reload_allocation_filter_settings = lambda: controls.reload_search_mode(owner)
    owner._allocation_controller = Mock()
    try:
        owner.team_axis_search.setCurrentIndex(owner.team_axis_search.findData('deep'))
        owner.team_axis_selection = object()  # 已关闭的草稿也属于旧账号。
        owner.app_context.account.user_database_path = other
        ScanningController.reset_account_state(owner)
        assert owner.team_axis_selection is None and not owner.team_axis_check.isChecked()
        assert not owner.team_axis_configure.isEnabled() and not owner.team_axis_search.isEnabled()
        assert owner.team_axis_search.currentData() == 'fast'
        assert load_search_mode(database) == 'deep' and load_search_mode(other) == 'fast'
        owner._allocation_controller.reset_account_state.assert_called_once()
    finally:
        dispose(page)


def test_direct_strategy_caption_is_localized_and_has_no_hover_explanation(tmp_path):
    page, owner, _ = ui_case(tmp_path)
    try:
        button = owner.strategy_group.button(2)
        assert button.text() == '直伤优先 — 仅考虑单人直伤输出，无拐，非最优解'
        assert button.toolTip() == ''
        assert owner.strategy_group.checkedId() == 0
    finally:
        dispose(page)
