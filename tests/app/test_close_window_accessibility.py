# 验证关闭窗口的长译文、键盘动作身份与默认取消语义。

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QWidget

from tests.support.auto_sync_ui_fixture import dispose
from tests.support.theme import themed_app as themed_app
from tests.support.theme import settle
from src.domain.localization.catalog import make_catalog
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.main_window.close_choice import CloseWindowDialog

@pytest.mark.parametrize('tray', [True, False])
def test_close_window_long_translation_cancel_default_and_explicit_actions(themed_app, monkeypatch, tray):
    base = bundled_text_catalog()
    messages = {key: 'Long translated message ' + value for key, value in base.messages.items() if key.startswith('app.close.')}
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture', messages=messages), base=base)
    owner = QWidget();dialog = CloseWindowDialog(owner, tray_available=tray, text_catalog=catalog)
    try:
        dialog.show();settle(themed_app)
        assert dialog.focusWidget() is dialog
        assert set(dialog.action_buttons) == {'exit', 'minimize'}
        for button in dialog.action_buttons.values():
            assert button.isVisibleTo(dialog) and dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
        QTest.keyClick(dialog, Qt.Key_Return)  # Qt Return：默认取消，不退出主程序。
        assert dialog.isVisible() and dialog.action == 'cancel'
        dialog.close()
        assert dialog.result() == QDialog.Rejected and dialog.action == 'cancel'
    finally:
        dialog.done(QDialog.Rejected);dispose(dialog);dispose(owner)


@pytest.mark.parametrize('action', ['minimize', 'exit'])
def test_focused_enter_obeys_action_id_even_when_translated_captions_match(themed_app, action):
    base = bundled_text_catalog()
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture',
        messages={key: 'Action' for key in ('app.close.exit', 'app.close.minimize')}), base=base)
    owner = QWidget();dialog = CloseWindowDialog(owner, tray_available=True, text_catalog=catalog)
    try:
        dialog.show();settle(themed_app)
        button = dialog.action_buttons[action];button.setFocus();settle(themed_app)
        QTest.keyClick(button, Qt.Key_Return)
        assert dialog.action == action and dialog.result() == QDialog.Accepted
    finally:
        dialog.done(QDialog.Rejected);dispose(dialog);dispose(owner)


@pytest.mark.parametrize('tray', [True, False])
def test_close_standard_content_has_no_scrollbar_or_button_tooltips(themed_app, tray):
    owner = QWidget();dialog = CloseWindowDialog(owner, tray_available=tray)
    try:
        dialog.show();settle(themed_app)
        assert dialog.action_buttons['minimize'].text() == '最小化'
        for button in dialog.action_buttons.values():
            assert not button.toolTip() and button.accessibleDescription()
        assert dialog._scroll.verticalScrollBar().maximum() == 0
        assert dialog._scroll.horizontalScrollBar().maximum() == 0
    finally:
        dialog.done(QDialog.Rejected);dispose(dialog);dispose(owner)

