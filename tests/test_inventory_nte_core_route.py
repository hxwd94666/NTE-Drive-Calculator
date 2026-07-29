# 测试配装页只通过本地组件装配官方 SQLite 方案。
"""配装页本地组件路由测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import src.features.inventory.equipment_assembly_controller as page_module
import src.features.inventory.equipment_automatic_assembly_controller as automatic_module


class InventoryNteCoreRouteTests(unittest.TestCase):
    def test_fast_apply_runs_resolved_roles_before_reporting_missing_instances(self) -> None:
        class Dao:
            def __init__(self):
                self.prepared = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def current_inventory_snapshot_id(self):
                return 10

            def get_active_loadout_plan_for_role(self, role_name):
                character_id = 1001 if role_name == "可装配" else 1002
                return {
                    "plan_id": character_id,
                    "character_id": character_id,
                    "source_snapshot_id": 10,
                    "assignments": [{"kind": "module"}],
                }

            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "nte_core"}

            def create_equipment_apply_job(self, _snapshot_id, prepared):
                self.prepared = list(prepared)
                return 99

            def get_equipment_apply_job(self, _job_id):
                return {"items": [{"job_item_id": index + 1, **entry} for index, entry in enumerate(self.prepared)]}

            def mark_equipment_apply_job_item(self, *_args, **_kwargs):
                pass

            def complete_equipment_apply_job_if_done(self, _job_id):
                return True

        class ApplyService:
            last_instance = None

            def __init__(self, *_args):
                self.apply_calls = []
                ApplyService.last_instance = self

            def validate_plan_for_fast_apply(self, *_args, **_kwargs):
                pass

            def validate_bulk_plans_for_fast_apply(self, *_args, **_kwargs):
                pass

            def resolve_fast_apply_character_ids(self, character_id, *_args, **_kwargs):
                return (character_id,)

            def resolve_character_uid(self, character_id, *_args, **_kwargs):
                if character_id == 1002:
                    raise RuntimeError("当前稳定背包和该账号的角色实例缓存均未包含该角色 UID")
                return {"slot": 1, "serial": 2}

            def require_stable_snapshot(self):
                return 10

            def apply_plan(self, plan_id, **kwargs):
                self.apply_calls.append((plan_id, kwargs))
                return SimpleNamespace(
                    before_snapshot_id=10,
                    after_snapshot_id=10,
                    character_uid=kwargs["character_uid"],
                    verified=False,
                    already_applied=False,
                )

        dao = Dao()
        old_dao = page_module.UserDataDao
        old_service = page_module.EquipmentApplyService
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: dao
            page_module.EquipmentApplyService = ApplyService
            report = page_module._run_nte_core_equipment_apply(
                SimpleNamespace(
                    _inventory_sync_service=object(),
                    user_database_path="unused.sqlite3",
                ),
                ["可装配", "实例缺失"],
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module.EquipmentApplyService = old_service

        self.assertEqual(["可装配"], [row["role_name"] for row in report["applied"]])
        self.assertEqual(["实例缺失"], [row["role_name"] for row in report["identity_requests"]])
        self.assertEqual(
            [(1001, False, True, True, True)],
            [
                (
                    plan_id,
                    kwargs["verify_after_dispatch"],
                    kwargs["exact_loadout"],
                    kwargs["force_dispatch"],
                    kwargs["reset_before_apply"],
                )
                for plan_id, kwargs in ApplyService.last_instance.apply_calls
            ],
        )

    def test_all_role_fast_apply_uses_new_snapshot_to_repair_an_omission(self) -> None:
        class Dao:
            def __init__(self):
                self.prepared = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def current_inventory_snapshot_id(self):
                return 10

            def get_active_loadout_plan_for_role(self, role_name):
                character_id = 1001 if role_name == "甲" else 1002
                return {
                    "plan_id": character_id,
                    "character_id": character_id,
                    "source_snapshot_id": 10,
                    "assignments": [{"kind": "module"}],
                }

            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "nte_core"}

            def get_sync_settings(self):
                return {"inventory_settle_seconds": 5.0}

            def create_equipment_apply_job(self, _snapshot_id, prepared):
                self.prepared = list(prepared)
                return 88

            def get_equipment_apply_job(self, _job_id):
                return {"items": [{"job_item_id": index + 1, **entry} for index, entry in enumerate(self.prepared)]}

            def mark_equipment_apply_job_item(self, *_args, **_kwargs):
                pass

            def complete_equipment_apply_job_if_done(self, _job_id):
                return True

        class ApplyService:
            def __init__(self, *_args):
                pass

            def validate_plan_for_fast_apply(self, *_args, **_kwargs):
                pass

            def validate_bulk_plans_for_fast_apply(self, *_args, **_kwargs):
                pass

            def resolve_fast_apply_character_ids(self, character_id, *_args, **_kwargs):
                return (character_id,)

            def resolve_character_uid(self, character_id, *_args, **_kwargs):
                return {"slot": character_id, "serial": character_id + 10}

            def require_stable_snapshot(self):
                return 10

            def apply_plan(self, plan_id, **kwargs):
                postcheck = kwargs["stable_snapshot_id"] == 11
                repaired = postcheck and plan_id == 1002
                return SimpleNamespace(
                    before_snapshot_id=kwargs["stable_snapshot_id"],
                    after_snapshot_id=kwargs["stable_snapshot_id"],
                    character_uid=kwargs["character_uid"],
                    verified=False,
                    already_applied=postcheck and not repaired,
                )

            def verify_plan_in_snapshot(self, plan_id, **kwargs):
                assert kwargs["stable_snapshot_id"] == 12
                assert plan_id == 1002
                return None

        class Sync:
            def __init__(self):
                self.wait_calls = []

            def wait_for_snapshot(self, **kwargs):
                self.wait_calls.append(kwargs)
                return SimpleNamespace(last_snapshot_id=10 + len(self.wait_calls))

        dao = Dao()
        sync = Sync()
        old_dao = page_module.UserDataDao
        old_service = page_module.EquipmentApplyService
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: dao
            page_module.EquipmentApplyService = ApplyService
            report = page_module._run_nte_core_equipment_apply(
                SimpleNamespace(
                    _inventory_sync_service=sync,
                    user_database_path="unused.sqlite3",
                ),
                ["甲", "乙"],
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module.EquipmentApplyService = old_service

        self.assertEqual(11, report["postcheck_snapshot_id"])
        self.assertEqual(12, report["postrepair_snapshot_id"])
        self.assertEqual([10.0, 10.0], [call["timeout"] for call in sync.wait_calls])
        self.assertEqual(
            ["乙"],
            [row["role_name"] for row in report["applied"] if row.get("repaired")],
        )
        self.assertEqual(
            ["乙"],
            [row["role_name"] for row in report["applied"] if row.get("repair_verified")],
        )

    def test_single_role_routes_to_fast_mode_when_selected(self) -> None:
        calls = []

        class FakeWindow:
            pass

        original_select = page_module._select_single_role_assembly_mode
        original_preview = page_module._preview_nte_core_assemble_role
        try:
            page_module._select_single_role_assembly_mode = lambda _self, _role_name: "fast"
            page_module._preview_nte_core_assemble_role = lambda _self, role_name, *, confirmed=False: calls.append(
                (role_name, confirmed)
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
            page_module._preview_automatic_assemble_role = lambda _self, role_name, *, confirmed=False: calls.append(
                (role_name, confirmed)
            )
            page_module._preview_assemble_role(FakeWindow(), "测试角色")
        finally:
            page_module._select_single_role_assembly_mode = original_select
            page_module._preview_automatic_assemble_role = original_preview

        self.assertEqual([("测试角色", True)], calls)

    def test_identifies_plugin_unavailable_as_not_immediately_retryable(self) -> None:
        self.assertTrue(
            page_module._is_equipment_plugin_unavailable_error(
                "nte-core RPC error -32000 [MODS_PLUGIN_UNAVAILABLE]: Core error"
            )
        )
        self.assertTrue(
            page_module._is_equipment_plugin_unavailable_error(
                "nte-core RPC error -32000 [EQUIPMENT_PLUGIN_UNAVAILABLE]: Core error"
            )
        )
        self.assertFalse(
            page_module._is_equipment_plugin_unavailable_error(
                "nte-core RPC error -32000 [MODS_PLUGIN_BUSY]: Core error"
            )
        )
        self.assertFalse(
            page_module._is_equipment_plugin_unavailable_error(
                "nte-core RPC error -32000 [EQUIPMENT_PLUGIN_BUSY]: Core error"
            )
        )

    def test_automatic_assembly_uses_duplicate_warning_after_mode_choice(self) -> None:
        calls = []

        class FakeWindow:
            pass

        original_warning = automatic_module._confirm_automatic_assembly_duplicate_warning
        original_start = automatic_module._start_automatic_equipment_assembly
        try:
            automatic_module._confirm_automatic_assembly_duplicate_warning = (
                lambda _self: calls.append("warning") or True
            )
            automatic_module._start_automatic_equipment_assembly = (
                lambda _self, roles: calls.append(list(roles))
            )
            automatic_module._preview_automatic_assemble_role(
                FakeWindow(),
                "测试角色",
                confirmed=True,
            )
        finally:
            automatic_module._confirm_automatic_assembly_duplicate_warning = original_warning
            automatic_module._start_automatic_equipment_assembly = original_start

        self.assertEqual(["warning", ["测试角色"]], calls)

    def test_duplicate_warning_preference_skips_dialog(self) -> None:
        class FakeWindow:
            _ui_preferences = {"skip_automatic_assembly_duplicate_warning": True}

        self.assertTrue(
            automatic_module._confirm_automatic_assembly_duplicate_warning(
                FakeWindow()
            )
        )

    def test_visual_snapshot_confirms_before_fast_assembly_falls_back(self) -> None:
        class PlansDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get_active_loadout_plan_for_role(self, _role_name):
                return {"source_snapshot_id": 7}

            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "gamepad"}

        messages = []
        calls = []
        old_dao = page_module.UserDataDao
        old_confirm = page_module._confirm_automatic_assembly_fallback
        old_automatic = page_module._preview_automatic_assemble_role
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module._confirm_automatic_assembly_fallback = lambda _window, detail: messages.append(detail) or True
            page_module._preview_automatic_assemble_role = lambda _window, role_name, *, confirmed: calls.append(
                (role_name, confirmed)
            )
            window = SimpleNamespace(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_role(
                window,
                "视觉角色",
                confirmed=True,
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module._confirm_automatic_assembly_fallback = old_confirm
            page_module._preview_automatic_assemble_role = old_automatic

        self.assertEqual([("视觉角色", True)], calls)
        self.assertIn("视觉扫描快照", messages[0])
        self.assertIn("原生 UID", messages[0])

    def test_visual_snapshot_does_not_start_automatic_assembly_when_fallback_is_cancelled(self) -> None:
        class PlansDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get_active_loadout_plan_for_role(self, _role_name):
                return {"source_snapshot_id": 7}

            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "gamepad"}

        calls = []
        old_dao = page_module.UserDataDao
        old_confirm = page_module._confirm_automatic_assembly_fallback
        old_automatic = page_module._preview_automatic_assemble_role
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module._confirm_automatic_assembly_fallback = lambda *_args: False
            page_module._preview_automatic_assemble_role = lambda *_args, **_kwargs: calls.append("started")
            window = SimpleNamespace(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_role(window, "视觉角色", confirmed=True)
        finally:
            page_module.UserDataDao = old_dao
            page_module._confirm_automatic_assembly_fallback = old_confirm
            page_module._preview_automatic_assemble_role = old_automatic

        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
