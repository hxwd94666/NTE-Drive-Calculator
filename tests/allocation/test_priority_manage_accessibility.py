# 验证计算角色管理按钮长文字完整且不改变原选择。

import pytest
from PySide6.QtWidgets import QPushButton

from tests.support.auto_sync_ui_fixture import dispose
from tests.support.qt_fonts import fonts
from src.domain.localization.catalog import make_catalog
from src.features.allocation.selection.role_selector import RoleSelector
from src.integrations.preferences.locale_files import bundled_text_catalog

@pytest.mark.parametrize('caption', ['管理', 'Manage role'])
def test_priority_manage_text_and_click_are_complete_without_changing_selection(caption):
    app = fonts();base = bundled_text_catalog();calls = []
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture',
                               messages={'warehouse.management.filter.title': caption}), base=base)
    widget = RoleSelector(preference_dialog_callback=calls.append, text_catalog=catalog)
    try:
        widget.load_roles({'角色 A': {}, '角色 B': {}}, [])
        widget.selected, widget.priority_links = ['角色 A', '角色 B'], ['=']
        widget.resize(900, 320);widget._render_priority_row();widget.show();app.processEvents()
        buttons = [button for button in widget.findChildren(QPushButton, 'btnSm') if button.isVisibleTo(widget)]
        assert len(buttons) == 2
        for button in buttons:
            assert button.text() == caption
            assert button.width() >= button.fontMetrics().horizontalAdvance(caption) + 14
            assert button.parentWidget().rect().contains(button.geometry())
            assert '角色' in button.accessibleName()
        buttons[0].click()
        assert calls == ['角色 A'] and widget.selected == ['角色 A', '角色 B'] and widget.priority_links == ['=']
    finally:dispose(widget)

