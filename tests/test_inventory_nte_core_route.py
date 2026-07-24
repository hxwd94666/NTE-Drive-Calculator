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

    def test_visual_snapshot_confirms_before_fast_assembly_falls_back(self) -> None:
        from src.app import runtime

        class PlansDao:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def get_active_loadout_plan_for_role(self, _role_name):
                return {"source_snapshot_id": 7}
            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "gamepad"}

        messages = []
        calls = []
        old_dao = page_module.UserDataDao
        old_confirm = page_module._confirm_automatic_assembly_fallback
        old_automatic = page_module._preview_automatic_assemble_role
        old_path = getattr(runtime, "USER_DATABASE_PATH", None)
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module._confirm_automatic_assembly_fallback = lambda _window, detail: messages.append(detail) or True
            page_module._preview_automatic_assemble_role = (
                lambda _window, role_name, *, confirmed: calls.append((role_name, confirmed))
            )
            runtime.USER_DATABASE_PATH = "unused.sqlite3"
            page_module._preview_nte_core_assemble_role(
                object(), "视觉角色", confirmed=True,
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module._confirm_automatic_assembly_fallback = old_confirm
            page_module._preview_automatic_assemble_role = old_automatic
            if old_path is None:
                delattr(runtime, "USER_DATABASE_PATH")
            else:
                runtime.USER_DATABASE_PATH = old_path

        self.assertEqual([("视觉角色", True)], calls)
        self.assertIn("视觉扫描快照", messages[0])
        self.assertIn("原生 UID", messages[0])

    def test_visual_snapshot_does_not_start_automatic_assembly_when_fallback_is_cancelled(self) -> None:
        from src.app import runtime

        class PlansDao:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def get_active_loadout_plan_for_role(self, _role_name):
                return {"source_snapshot_id": 7}
            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "gamepad"}

        calls = []
        old_dao = page_module.UserDataDao
        old_confirm = page_module._confirm_automatic_assembly_fallback
        old_automatic = page_module._preview_automatic_assemble_role
        old_path = getattr(runtime, "USER_DATABASE_PATH", None)
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module._confirm_automatic_assembly_fallback = lambda *_args: False
            page_module._preview_automatic_assemble_role = lambda *_args, **_kwargs: calls.append("started")
            runtime.USER_DATABASE_PATH = "unused.sqlite3"
            page_module._preview_nte_core_assemble_role(object(), "视觉角色", confirmed=True)
        finally:
            page_module.UserDataDao = old_dao
            page_module._confirm_automatic_assembly_fallback = old_confirm
            page_module._preview_automatic_assemble_role = old_automatic
            if old_path is None:
                delattr(runtime, "USER_DATABASE_PATH")
            else:
                runtime.USER_DATABASE_PATH = old_path

        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
