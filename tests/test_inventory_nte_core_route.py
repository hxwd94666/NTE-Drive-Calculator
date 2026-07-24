# 测试配装页只通过本地组件装配官方 SQLite 方案。
"""配装页本地组件路由测试。"""

from __future__ import annotations

import unittest

import src.features.inventory.equipment_assembly_controller as page_module


class InventoryNteCoreRouteTests(unittest.TestCase):
    def test_single_role_routes_to_fast_mode_when_selected(self) -> None:
        calls = []

        class FakeWindow:
            pass

        original_select = page_module._select_single_role_assembly_mode
        original_preview = page_module._preview_nte_core_assemble_role
        try:
            page_module._select_single_role_assembly_mode = lambda _self, _role_name: "fast"
            page_module._preview_nte_core_assemble_role = (
                lambda _self, role_name, *, confirmed=False: calls.append((role_name, confirmed))
            )
            page_module._preview_assemble_role(FakeWindow(), "测试角色")
        finally:
            page_module._select_single_role_assembly_mode = original_select
            page_module._preview_nte_core_assemble_role = original_preview

        self.assertEqual([("测试角色", True)], calls)

    def test_single_role_routes_to_automatic_mode_when_selected(self) -> None:
        calls = []

        class FakeWindow:
            pass

        original_select = page_module._select_single_role_assembly_mode
        original_preview = page_module._preview_automatic_assemble_role
        try:
            page_module._select_single_role_assembly_mode = lambda _self, _role_name: "automatic"
            page_module._preview_automatic_assemble_role = (
                lambda _self, role_name, *, confirmed=False: calls.append((role_name, confirmed))
            )
            page_module._preview_assemble_role(FakeWindow(), "测试角色")
        finally:
            page_module._select_single_role_assembly_mode = original_select
            page_module._preview_automatic_assemble_role = original_preview

        self.assertEqual([("测试角色", True)], calls)

    def test_identifies_plugin_unavailable_as_not_immediately_retryable(self) -> None:
        self.assertTrue(page_module._is_equipment_plugin_unavailable_error(
            "nte-core RPC error -32000 [EQUIPMENT_PLUGIN_UNAVAILABLE]: Core error"
        ))
        self.assertFalse(page_module._is_equipment_plugin_unavailable_error(
            "nte-core RPC error -32000 [EQUIPMENT_PLUGIN_BUSY]: Core error"
        ))

    def test_automatic_assembly_uses_duplicate_warning_after_mode_choice(self) -> None:
        calls = []

        class FakeWindow:
            pass

        original_warning = page_module._confirm_automatic_assembly_duplicate_warning
        original_start = page_module._start_automatic_equipment_assembly
        try:
            page_module._confirm_automatic_assembly_duplicate_warning = lambda _self: calls.append("warning") or True
            page_module._start_automatic_equipment_assembly = lambda _self, roles: calls.append(list(roles))
            page_module._preview_automatic_assemble_role(FakeWindow(), "测试角色", confirmed=True)
        finally:
            page_module._confirm_automatic_assembly_duplicate_warning = original_warning
            page_module._start_automatic_equipment_assembly = original_start

        self.assertEqual(["warning", ["测试角色"]], calls)

    def test_duplicate_warning_preference_skips_dialog(self) -> None:
        class FakeWindow:
            _ui_preferences = {"skip_automatic_assembly_duplicate_warning": True}

        self.assertTrue(page_module._confirm_automatic_assembly_duplicate_warning(FakeWindow()))


if __name__ == "__main__":
    unittest.main()
