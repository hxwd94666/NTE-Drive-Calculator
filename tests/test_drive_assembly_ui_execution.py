# 验证装配确认、窗口状态、停止恢复和批量入口。
"""Behavior tests for drive-assembly UI execution flows."""

import unittest
from types import SimpleNamespace
from pathlib import Path

import numpy as np



class DriveAssemblyUiExecutionTests(unittest.TestCase):
    def test_single_role_button_executes_confirmed_plan(self):
        import src.features.inventory.equipment_assembly_controller as page_module

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
        import src.features.inventory.equipment_assembly_controller as page_module

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
        import src.features.inventory.equipment_assembly_controller as page_module

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
            window = SimpleNamespace(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_all_roles(window, confirmed=True)
        finally:
            page_module._start_nte_core_equipment_apply = old_start
            page_module.UserDataDao = old_dao
            page_module.QMessageBox.information = old_info

        self.assertEqual([], calls)

    def test_all_role_button_executes_current_game_role_flow_when_confirmed(self):
        import src.features.inventory.equipment_assembly_controller as page_module

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
            window = SimpleNamespace(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_all_roles(window, confirmed=True)
        finally:
            page_module.UserDataDao = old_dao
            page_module._start_nte_core_equipment_apply = old_start

        self.assertEqual([([], {"slot_ids": [11]})], calls)

    def test_weighted_result_can_limit_fast_equipment_to_its_selected_roles(self):
        import src.features.inventory.equipment_assembly_controller as page_module

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
            window = SimpleNamespace(user_database_path="unused.sqlite3")
            page_module._preview_nte_core_assemble_all_roles(
                window,
                confirmed=True,
                role_names=["当前角色"],
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module._start_nte_core_equipment_apply = old_start

        self.assertEqual([([], {"slot_ids": [21]})], calls)

    def test_weighted_result_can_limit_automatic_equipment_to_its_selected_roles(self):
        import src.features.inventory.equipment_automatic_assembly_controller as page_module

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
        old_warning = page_module._confirm_automatic_assembly_duplicate_warning
        old_start = page_module._start_automatic_equipment_assembly
        try:
            page_module.UserDataDao = lambda *_args, **_kwargs: PlansDao()
            page_module.QMessageBox.question = lambda *_args, **_kwargs: page_module.QMessageBox.Yes
            page_module._confirm_automatic_assembly_duplicate_warning = lambda _window: True
            page_module._start_automatic_equipment_assembly = lambda _window, roles, **kwargs: calls.append((roles, kwargs))
            window = SimpleNamespace(user_database_path="unused.sqlite3")
            page_module._preview_automatic_assemble_all_roles(
                window,
                role_names=["当前角色"],
            )
        finally:
            page_module.UserDataDao = old_dao
            page_module.QMessageBox.question = old_question
            page_module._confirm_automatic_assembly_duplicate_warning = old_warning
            page_module._start_automatic_equipment_assembly = old_start

        self.assertEqual([([], {"slot_ids": [31]})], calls)

    def test_confirmed_assembly_minimizes_calculator_before_execution(self):
        import src.features.inventory.equipment_automatic_assembly_controller as page_module

        class Signal:
            def __init__(self):
                self.callback = None

            def connect(self, callback):
                self.callback = callback

        class FakeWorker:
            def __init__(self, *, target, parent):
                self.target = target
                self.parent = parent
                self.result_ready = Signal()
                self.error = Signal()
                self.started = False

            def start(self):
                self.started = True

        class Window:
            def __init__(self):
                self.calls = []

            def showMinimized(self):
                self.calls.append("minimized")

            def showNormal(self):
                self.calls.append("show_normal")

            def _go(self, page):
                self.calls.append(page)

            def raise_(self):
                self.calls.append("raise")

            def activateWindow(self):
                self.calls.append("activate")

            def _refresh_equip(self):
                self.calls.append("refresh")

        old_worker = page_module.WorkerThread
        old_state = page_module._sqlite_automatic_assembly_state
        old_aliases = page_module._prompt_protagonist_alias_if_needed
        old_report = page_module._assembly_report_dialog
        old_info = page_module.QMessageBox.information
        old_question = page_module.QMessageBox.question
        old_warning = page_module.QMessageBox.warning
        old_critical = page_module.QMessageBox.critical
        try:
            page_module.WorkerThread = FakeWorker
            page_module._sqlite_automatic_assembly_state = lambda _path, _roles, **_kwargs: {"角色": {}}
            page_module._prompt_protagonist_alias_if_needed = lambda *_args: {}
            page_module._assembly_report_dialog = lambda *_args: ("完成", "ok", True)
            page_module.QMessageBox.information = lambda *_args, **_kwargs: None
            page_module.QMessageBox.question = (
                lambda *_args, **_kwargs: page_module.QMessageBox.Ok
            )
            page_module.QMessageBox.warning = lambda *_args, **_kwargs: None
            page_module.QMessageBox.critical = lambda *_args, **_kwargs: None

            window = Window()
            window.user_database_path = "unused.sqlite3"
            page_module._start_automatic_equipment_assembly(window, ["角色"])
            worker = window._automatic_equipment_apply_worker
            self.assertTrue(worker.started)
            self.assertEqual(["minimized"], window.calls)

            worker.result_ready.callback(SimpleNamespace())
            self.assertEqual(
                ["minimized", "show_normal", "equipment", "raise", "activate", "refresh"],
                window.calls,
            )

            window.calls.clear()
            worker.error.callback("失败")
            self.assertEqual(["show_normal", "equipment", "raise", "activate"], window.calls)
        finally:
            page_module.WorkerThread = old_worker
            page_module._sqlite_automatic_assembly_state = old_state
            page_module._prompt_protagonist_alias_if_needed = old_aliases
            page_module._assembly_report_dialog = old_report
            page_module.QMessageBox.information = old_info
            page_module.QMessageBox.question = old_question
            page_module.QMessageBox.warning = old_warning
            page_module.QMessageBox.critical = old_critical

    def test_automatic_assembly_keeps_cloud_nte_mode_disabled_without_frontend_entry(self):
        import src.features.inventory.equipment_automatic_assembly_controller as page_module

        class Signal:
            def connect(self, _callback):
                return None

        class FakeWorker:
            def __init__(self, *, target, parent):
                self.target = target
                self.result_ready = Signal()
                self.error = Signal()

            def start(self):
                return None

        calls = []
        old_worker = page_module.WorkerThread
        old_state = page_module._sqlite_automatic_assembly_state
        old_aliases = page_module._prompt_protagonist_alias_if_needed
        old_info = page_module.QMessageBox.information
        old_question = page_module.QMessageBox.question
        old_execute = page_module.execute_selected_role_from_current_game_page
        old_paths = page_module._assembly_runtime_paths
        try:
            page_module.WorkerThread = FakeWorker
            page_module._sqlite_automatic_assembly_state = lambda _path, _roles, **_kwargs: {"角色": {}}
            page_module._prompt_protagonist_alias_if_needed = lambda *_args: {}
            page_module.QMessageBox.information = lambda *_args, **_kwargs: None
            page_module.QMessageBox.question = (
                lambda *_args, **_kwargs: page_module.QMessageBox.Ok
            )
            page_module.execute_selected_role_from_current_game_page = lambda *_args, **kwargs: calls.append(kwargs)
            page_module._assembly_runtime_paths = lambda _window: (Path("templates"), Path("record"))

            window = SimpleNamespace(user_database_path="unused.sqlite3", _ui_preferences={"cloud_nte_mode": True})
            page_module._start_automatic_equipment_assembly(window, ["角色"])
            window._automatic_equipment_apply_worker.target()
        finally:
            page_module.WorkerThread = old_worker
            page_module._sqlite_automatic_assembly_state = old_state
            page_module._prompt_protagonist_alias_if_needed = old_aliases
            page_module.QMessageBox.information = old_info
            page_module.QMessageBox.question = old_question
            page_module.execute_selected_role_from_current_game_page = old_execute
            page_module._assembly_runtime_paths = old_paths

        self.assertEqual([False], [call["cloud_nte_mode"] for call in calls])

    def test_automatic_assembly_uses_configured_global_stop_hotkey(self):
        import src.features.inventory.equipment_automatic_assembly_controller as page_module

        class Signal:
            def connect(self, _callback):
                return None

        class FakeWorker:
            def __init__(self, *, target, parent):
                self.target = target
                self.parent = parent
                self.result_ready = Signal()
                self.error = Signal()

            def start(self):
                return None

        class Hotkeys:
            def __init__(self):
                self.configuration = SimpleNamespace(stop="F8")
                self.active_owner = None
                self.stop_callback = None

            def start(self, *, owner, on_stop):
                self.active_owner = owner
                self.stop_callback = on_stop

            def stop(self, *, owner):
                if owner == self.active_owner:
                    self.active_owner = None

        calls = []
        old_worker = page_module.WorkerThread
        old_state = page_module._sqlite_automatic_assembly_state
        old_aliases = page_module._prompt_protagonist_alias_if_needed
        old_question = page_module.QMessageBox.question
        old_execute = page_module.execute_selected_role_from_current_game_page
        old_paths = page_module._assembly_runtime_paths
        try:
            page_module.WorkerThread = FakeWorker
            page_module._sqlite_automatic_assembly_state = lambda *_args, **_kwargs: {"角色": {}}
            page_module._prompt_protagonist_alias_if_needed = lambda *_args: {}
            page_module.QMessageBox.question = lambda *_args, **_kwargs: page_module.QMessageBox.Ok
            page_module.execute_selected_role_from_current_game_page = (
                lambda *_args, **kwargs: calls.append(kwargs)
            )
            page_module._assembly_runtime_paths = lambda _window: (Path("templates"), Path("record"))

            hotkeys = Hotkeys()
            window = SimpleNamespace(
                user_database_path="unused.sqlite3",
                global_hotkey_manager=hotkeys,
            )
            page_module._start_automatic_equipment_assembly(window, ["角色"])
            self.assertEqual("automatic_equipment_apply", hotkeys.active_owner)
            hotkeys.stop_callback()
            window._automatic_equipment_apply_worker.target()
        finally:
            page_module.WorkerThread = old_worker
            page_module._sqlite_automatic_assembly_state = old_state
            page_module._prompt_protagonist_alias_if_needed = old_aliases
            page_module.QMessageBox.question = old_question
            page_module.execute_selected_role_from_current_game_page = old_execute
            page_module._assembly_runtime_paths = old_paths

        self.assertTrue(calls[0]["should_stop"]())

    def test_single_role_f12_stop_restores_equipment_page_before_dialog(self):
        from src.features.inventory.page import _return_to_equipment_after_assembly

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
        from src.features.inventory.page import _assembly_report_dialog

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
