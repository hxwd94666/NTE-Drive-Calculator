# 验证装配确认、窗口状态、停止恢复和批量入口。
"""Behavior tests for drive-assembly UI execution flows."""

import unittest
from types import SimpleNamespace

import numpy as np


def _operation_enabled_window(**attributes):
    """Build a controller fixture with the current explicit operation contract."""

    return SimpleNamespace(
        operation_entry=lambda _capability, _label: True,
        operation_guard=lambda _capability: None,
        **attributes,
    )


class DriveAssemblyUiExecutionTests(unittest.TestCase):
    def test_single_role_button_executes_confirmed_plan(self):
        import src.features.inventory.equipment.assembly.assembly_controller as page_module

        calls = []
        old_select = page_module._select_single_role_assembly_mode
        old_fast = page_module._preview_nte_core_assemble_role
        try:
            page_module._select_single_role_assembly_mode = lambda *_args: "fast"
            page_module._preview_nte_core_assemble_role = lambda _window, role_name, **kwargs: calls.append(
                (role_name, kwargs)
            )
            page_module._preview_assemble_role(object(), "真红")
        finally:
            page_module._select_single_role_assembly_mode = old_select
            page_module._preview_nte_core_assemble_role = old_fast

        self.assertEqual([("真红", {"confirmed": True})], calls)

    def test_single_role_button_forwards_selected_loadout_slot(self):
        import src.features.inventory.equipment.assembly.assembly_controller as page_module

        calls = []
        old_select = page_module._select_single_role_assembly_mode
        old_fast = page_module._preview_nte_core_assemble_role
        try:
            page_module._select_single_role_assembly_mode = lambda *_args: "fast"
            page_module._preview_nte_core_assemble_role = lambda _window, role_name, **kwargs: calls.append(
                (role_name, kwargs)
            )
            page_module._preview_assemble_role(object(), "真红", slot_id=71)
        finally:
            page_module._select_single_role_assembly_mode = old_select
            page_module._preview_nte_core_assemble_role = old_fast

        self.assertEqual([("真红", {"slot_id": 71, "confirmed": True})], calls)

    def test_all_role_button_does_not_execute_when_cancelled(self):
        import src.features.inventory.equipment.assembly.assembly_controller as page_module

        calls = []

        class EmptyPlansDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def list_current_loadout_slot_plans(self):
                return []

        old_start = page_module._start_nte_core_equipment_apply
        old_dao = page_module.UserDataDao
        old_info = page_module.QMessageBox.information
        try:
            page_module._start_nte_core_equipment_apply = lambda *_args, **_kwargs: calls.append(True)
            page_module.UserDataDao = lambda *_args, **_kwargs: EmptyPlansDao()
            page_module.QMessageBox.information = lambda *_args, **_kwargs: None
            window = _operation_enabled_window(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_all_roles(window)
        finally:
            page_module._start_nte_core_equipment_apply = old_start
            page_module.UserDataDao = old_dao
            page_module.QMessageBox.information = old_info

        self.assertEqual([], calls)

    def test_all_role_button_executes_current_game_role_flow_when_confirmed(self):
        import src.features.inventory.equipment.assembly.assembly_controller as page_module
        from unittest.mock import patch

        chooser = patch.object(page_module, "select_assembly_slot_ids", return_value=[11])
        selected = chooser.start()
        self.addCleanup(chooser.stop)

        class PlansDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def _row(slot_id, character_id, role_name, snapshot_id):
                plan = {
                    "plan_id": slot_id + 100,
                    "character_id": character_id,
                    "source_snapshot_id": snapshot_id,
                    "payload": {"source_role_name": role_name},
                }
                return {
                    "slot": {
                        "slot_id": slot_id,
                        "character_id": character_id,
                        "slot_key": "primary",
                        "slot_name": role_name,
                    },
                    "plan": plan,
                }

            def list_current_loadout_slot_plans(self):
                return [
                    self._row(11, 1003, "抓包角色", 1),
                    self._row(12, 1004, "视觉角色", 2),
                ]

            def get_loadout_slot(self, slot_id):
                row = next(
                    row for row in self.list_current_loadout_slot_plans()
                    if row["slot"]["slot_id"] == slot_id
                )
                return {**row["slot"], "current_plan": row["plan"]}

            def inventory_snapshot_summary(self, snapshot_id):
                return {"source": "nte_core" if snapshot_id == 1 else "gamepad"}

        calls = []
        old_dao = page_module.UserDataDao
        old_start = page_module._start_nte_core_equipment_apply
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module._start_nte_core_equipment_apply = lambda _window, roles, **kwargs: calls.append((roles, kwargs))
            window = _operation_enabled_window(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_all_roles(window)
        finally:
            page_module.UserDataDao = old_dao
            page_module._start_nte_core_equipment_apply = old_start

        self.assertEqual([([], {"slot_ids": [11]})], calls)

        selected.assert_called_once()

    def test_weighted_result_can_limit_fast_equipment_to_its_selected_roles(self):
        import src.features.inventory.equipment.assembly.assembly_controller as page_module
        from unittest.mock import patch

        chooser = patch.object(page_module, "select_assembly_slot_ids", return_value=[21])
        selected = chooser.start()
        self.addCleanup(chooser.stop)

        class PlansDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def list_current_loadout_slot_plans(self):
                return [
                    {
                        "slot": {
                            "slot_id": 21,
                            "character_id": 1003,
                            "slot_key": "primary",
                            "slot_name": "当前角色",
                        },
                        "plan": {
                            "plan_id": 121,
                            "character_id": 1003,
                            "source_snapshot_id": 1,
                            "payload": {"source_role_name": "当前角色"},
                        },
                    },
                    {
                        "slot": {
                            "slot_id": 22,
                            "character_id": 1004,
                            "slot_key": "primary",
                            "slot_name": "旧方案角色",
                        },
                        "plan": {
                            "plan_id": 122,
                            "character_id": 1004,
                            "source_snapshot_id": 1,
                            "payload": {"source_role_name": "旧方案角色"},
                        },
                    },
                ]

            def get_loadout_slot(self, slot_id):
                row = next(
                    row for row in self.list_current_loadout_slot_plans()
                    if row["slot"]["slot_id"] == slot_id
                )
                return {**row["slot"], "current_plan": row["plan"]}

            def inventory_snapshot_summary(self, _snapshot_id):
                return {"source": "nte_core"}

        calls = []
        old_dao = page_module.UserDataDao
        old_start = page_module._start_nte_core_equipment_apply
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module._start_nte_core_equipment_apply = lambda _window, roles, **kwargs: calls.append((roles, kwargs))
            window = _operation_enabled_window(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_all_roles(
                window,
                role_names=["当前角色"],
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module._start_nte_core_equipment_apply = old_start

        self.assertEqual([([], {"slot_ids": [21]})], calls)

        selected.assert_called_once()

    def test_weighted_result_can_limit_automatic_equipment_to_its_selected_roles(self):
        import src.features.inventory.equipment.assembly.automatic_assembly_controller as page_module
        from unittest.mock import patch

        chooser = patch.object(page_module, "select_assembly_slot_ids", return_value=[31])
        selected = chooser.start()
        self.addCleanup(chooser.stop)

        class PlansDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def list_current_loadout_slot_plans(self):
                return [
                    {
                        "slot": {
                            "slot_id": 31,
                            "character_id": 1003,
                            "slot_key": "primary",
                            "slot_name": "当前角色",
                        },
                        "plan": {
                            "plan_id": 131,
                            "character_id": 1003,
                            "payload": {"source_role_name": "当前角色"},
                        },
                    },
                    {
                        "slot": {
                            "slot_id": 32,
                            "character_id": 1004,
                            "slot_key": "primary",
                            "slot_name": "旧方案角色",
                        },
                        "plan": {
                            "plan_id": 132,
                            "character_id": 1004,
                            "payload": {"source_role_name": "旧方案角色"},
                        },
                    },
                ]

            def get_loadout_slot(self, slot_id):
                row = next(
                    row for row in self.list_current_loadout_slot_plans()
                    if row["slot"]["slot_id"] == slot_id
                )
                return {**row["slot"], "current_plan": row["plan"]}

        calls = []
        old_dao = page_module.UserDataDao
        old_question = page_module.QMessageBox.question
        old_start = page_module._start_automatic_equipment_assembly
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module.QMessageBox.question = lambda *_args, **_kwargs: page_module.QMessageBox.Yes
            page_module._start_automatic_equipment_assembly = lambda _window, roles, **kwargs: calls.append((roles, kwargs))
            window = _operation_enabled_window(user_database_path="unused.sqlite3")
            page_module._preview_automatic_assemble_all_roles(
                window,
                role_names=["当前角色"],
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module.QMessageBox.question = old_question
            page_module._start_automatic_equipment_assembly = old_start

        self.assertEqual([([], {"slot_ids": [31]})], calls)

        selected.assert_called_once()




    def test_single_role_f12_stop_restores_equipment_page_before_dialog(self):
        from src.features.inventory.equipment.assembly.assembly_controller import _return_to_equipment_after_assembly

        calls = []

        class Window:
            def showNormal(self):
                calls.append("show_normal")

            def _go(self, page):
                calls.append(page)

            def raise_(self):
                calls.append("raise")

            def activateWindow(self):
                calls.append("activate")

        _return_to_equipment_after_assembly(Window())
        self.assertEqual(["show_normal", "equipment", "raise", "activate"], calls)

    def test_all_roles_f12_stop_restores_equipment_page_before_dialog(self):
        from src.features.inventory.equipment.assembly.assembly_controller import _assembly_report_dialog

        report = SimpleNamespace(
            role_reports=[],
            executed_actions=0,
            missing_roles=[],
            skipped_roles=[],
            duplicate_roles=[],
            unrecognized_roles=[],
            verification_failures=[],
        )
        _title, _message, completed = _assembly_report_dialog("自动装配", report, 1)
        self.assertFalse(completed)

    def test_verifies_blueprint_against_screenshot_samples_drive_positions(self):
        from src.features.drive_assembly.ui_bridge import verify_blueprint_against_screenshot

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[48:53, 48:53] = 120
        rect = SimpleNamespace(left=10, top=20)
        plan = {
            "drive_blocks": [
                {"block_id": 1, "pixel_position": (60, 70)},
                {"block_id": 2, "pixel_position": (90, 90)},
            ]
        }

        result = verify_blueprint_against_screenshot(image, rect, plan)

        self.assertFalse(result["ok"])
        self.assertEqual([{"block_id": 2, "position": (90, 90)}], result["missing_blocks"])




if __name__ == "__main__":

    unittest.main()
