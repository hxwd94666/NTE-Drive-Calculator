# 验证账号计算目录未就绪时依赖页面暂停刷新且原禁用状态不被覆盖。
from types import SimpleNamespace

from PySide6.QtWidgets import QStackedWidget, QWidget

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.ui.main_window.main_window_data_mixin import MainWindowDataMixin
from src.ui.main_window.main_window_navigation_mixin import MainWindowNavigationMixin
from src.ui.main_window.navigation import NAV_ITEMS, nav_index_map, nav_item_by_key


def test_catalog_transition_blocks_dependent_pages_then_refreshes_visible_page_once():
    application()
    window = MainWindowDataMixin()
    window.stack = QStackedWidget()
    for _item in NAV_ITEMS:
        window.stack.addWidget(QWidget())
    indexes = nav_index_map()
    window.stack.setCurrentIndex(indexes["equipment"])
    warehouse = window.stack.widget(indexes["warehouse"])
    warehouse.setEnabled(False)  # Owned by another feature, not this catalog gate.
    window._nav_key_for_index = lambda index: NAV_ITEMS[index].key
    refreshes = []
    window.refresh_current_account_page = lambda: refreshes.append(1)
    try:
        window.set_allocation_catalog_ready(False)
        window.set_allocation_catalog_ready(False)
        assert not window.stack.widget(indexes["equipment"]).isEnabled()
        assert not window.stack.widget(indexes["identify"]).isEnabled()
        window.set_allocation_catalog_ready(True)
        assert window.stack.widget(indexes["equipment"]).isEnabled()
        assert not warehouse.isEnabled() and refreshes == [1]
        window.set_allocation_catalog_ready(True)
        assert refreshes == [1] and not warehouse.isEnabled()
    finally:
        dispose(window.stack)


def test_navigation_does_not_consume_previous_account_catalog_while_loading():
    events = []
    window = SimpleNamespace(_allocation_catalog_ready=False, _refresh_execute=lambda: events.append("validate"),
                             _refresh_equip=lambda: events.append("equipment"))
    MainWindowNavigationMixin._refresh_navigation_item(window, nav_item_by_key("equipment"))
    assert events == ["validate"]
    window._allocation_catalog_ready = True
    MainWindowNavigationMixin._refresh_navigation_item(window, nav_item_by_key("equipment"))
    assert events == ["validate", "equipment"]
