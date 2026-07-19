# 测试配装页根据用户设置选择本地组件或旧手柄装配流程。
"""配装页本地组件路由测试。"""

from __future__ import annotations

import unittest

import src.features.inventory.page as page_module


class InventoryNteCoreRouteTests(unittest.TestCase):
    def test_single_role_uses_nte_core_route_when_selected(self) -> None:
        calls = []

        class FakeWindow:
            equipped_state = {"测试角色": {}}

            def _get_sync_settings(self):
                return {"equipment_apply_method": "nte_core"}

        original_reload = page_module._reload_equipped_state_from_disk
        original_preview = page_module._preview_nte_core_assemble_role
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module._preview_nte_core_assemble_role = (
                lambda _self, role_name: calls.append(role_name)
            )
            page_module._preview_assemble_role(FakeWindow(), "测试角色")
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module._preview_nte_core_assemble_role = original_preview

        self.assertEqual(["测试角色"], calls)

    def test_all_roles_use_nte_core_route_when_selected(self) -> None:
        calls = []

        class FakeWindow:
            equipped_state = {"角色甲": {}, "角色乙": {}}

            def _get_sync_settings(self):
                return {"equipment_apply_method": "nte_core"}

        original_reload = page_module._reload_equipped_state_from_disk
        original_preview = page_module._preview_nte_core_assemble_all_roles
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module._preview_nte_core_assemble_all_roles = (
                lambda _self: calls.append("nte_core")
            )
            page_module._preview_assemble_all_roles(FakeWindow())
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module._preview_nte_core_assemble_all_roles = original_preview

        self.assertEqual(["nte_core"], calls)


if __name__ == "__main__":
    unittest.main()
