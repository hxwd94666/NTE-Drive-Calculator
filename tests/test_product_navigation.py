# 验证一级导航与进阶功能就近入口的产品信息结构。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from src.features.blueprints.page import BlueprintPage
from src.features.configuration.page import build_config_page
from src.features.home.page import build_home_page
from src.ui.navigation import NAV_ITEMS, sidebar_nav_items


class ProductNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sidebar_contains_only_primary_player_workflows(self):
        self.assertEqual(
            ("home", "execute", "equipment", "my_role", "warehouse", "identify", "settings"),
            tuple(item.key for item in sidebar_nav_items()),
        )

    def test_primary_identification_and_secondary_role_pages_have_expected_metadata(self):
        items = {item.key: item for item in NAV_ITEMS}

        self.assertTrue(items["identify"].sidebar)
        self.assertIsNone(items["identify"].parent_key)
        self.assertFalse(items["blueprint"].sidebar)
        self.assertEqual("my_role", items["blueprint"].parent_key)
        self.assertFalse(items["config"].sidebar)
        self.assertEqual("my_role", items["config"].parent_key)
        self.assertEqual("基础权重", items["config"].label)
        self.assertNotIn("mode", items)

    def test_role_subpages_have_direct_return_buttons(self):
        destinations: list[str] = []
        blueprint_page = BlueprintPage(
            app_context=SimpleNamespace(),
            navigate=destinations.append,
        ).build()
        blueprint_return = blueprint_page.findChild(QPushButton, "returnToRoleButton")
        self.assertIsNotNone(blueprint_return)
        blueprint_return.click()

        config_window = SimpleNamespace(
            _filter_config_roles=lambda _text: None,
            _reset_current_config_weights=lambda: None,
            _reset_all_config_weights=lambda: None,
            _save_config_form=lambda: None,
            _go=destinations.append,
        )
        config_page = build_config_page(config_window)
        config_return = config_page.findChild(QPushButton, "returnToRoleButton")
        self.assertIsNotNone(config_return)
        config_return.click()

        self.assertEqual(["my_role", "my_role"], destinations)

    def test_workbench_quick_actions_place_identification_after_warehouse(self):
        destinations: list[str] = []
        window = SimpleNamespace(
            app_context=SimpleNamespace(
                paths=SimpleNamespace(asset_dir=Path("assets"))
            ),
            _start_inventory_sync=lambda: None,
            _stop_inventory_sync=lambda: None,
            _focus_environment_configuration=lambda: None,
            _go=destinations.append,
        )
        page = build_home_page(window)
        buttons = page.findChildren(QPushButton)
        labels = [button.text() for button in buttons]
        warehouse_index = labels.index("仓库管理")
        self.assertEqual("空幕鉴定", labels[warehouse_index + 1])
        buttons[warehouse_index + 1].click()
        self.assertEqual(["identify"], destinations)


if __name__ == "__main__":
    unittest.main()
