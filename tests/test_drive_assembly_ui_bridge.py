# 验证装配页面调用的计划汇总入口。
"""Tests for drive assembly UI bridge helpers."""

import unittest
from types import SimpleNamespace
from pathlib import Path
import tempfile

import numpy as np


class DriveAssemblyUiBridgeTests(unittest.TestCase):
    def test_startup_guard_rejects_fuzzy_role_matches(self):
        from src.features.drive_assembly.ui_bridge import _is_role_detail_startup_recognition

        self.assertTrue(_is_role_detail_startup_recognition(SimpleNamespace(role_name="A", method="ocr")))
        self.assertTrue(_is_role_detail_startup_recognition(SimpleNamespace(role_name="A", method="ocr_fallback")))
        self.assertFalse(_is_role_detail_startup_recognition(SimpleNamespace(role_name="A", method="ocr_fuzzy")))
        self.assertFalse(_is_role_detail_startup_recognition(SimpleNamespace(role_name=None, method="ocr")))

    def test_assembly_recorder_writes_pngs_under_record_directory(self):
        from src.features.drive_assembly.ui_bridge import _AssemblyRunRecorder

        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = _AssemblyRunRecorder(Path(temp_dir) / "record")
            path = recorder.save_image(np.zeros((8, 12, 3), dtype=np.uint8), "startup")

            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertEqual("record", path.parent.parent.name)
            self.assertEqual("001_startup.png", path.name)

    def test_assembly_recorder_captures_completed_duplicate_status_filters(self):
        from src.features.drive_assembly.ui_bridge import _AssemblyRunRecorder

        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = _AssemblyRunRecorder(Path(temp_dir) / "record")
            captured_labels = []
            recorder.capture_foreground = captured_labels.append

            recorder.record_action(
                {"name": "status_other", "duplicate_status_filter": True, "block_id": 12},
                "A",
            )

            self.assertEqual(["duplicate_status_filters_block_12_A"], captured_labels)

    def test_assembly_report_lists_unrecognized_role_details(self):
        from src.features.inventory.page import _assembly_report_dialog

        report = SimpleNamespace(
            role_reports=[object()],
            executed_actions=212,
            missing_roles=[],
            skipped_roles=[],
            duplicate_roles=[],
            unrecognized_roles=[
                {"roster_index": 2, "raw_text": "unknown-one"},
                {"roster_index": 7, "raw_text": ""},
            ],
            verification_failures=[],
        )

        _title, message, completed = _assembly_report_dialog("assembly", report)

        self.assertFalse(completed)
        self.assertIn("第 3 个角色", message)
        self.assertIn("unknown-one", message)
        self.assertIn("第 8 个角色", message)
        self.assertIn("未读取到文字", message)

    def test_assembly_report_lists_missing_drive_block_ids(self):
        from src.features.inventory.page import _assembly_report_dialog

        report = SimpleNamespace(
            role_reports=[object()],
            executed_actions=20,
            missing_roles=[],
            skipped_roles=[],
            duplicate_roles=[],
            unrecognized_roles=[],
            verification_failures=[
                {"role_name": "A", "missing_blocks": [{"block_id": 5}]},
            ],
        )

        _title, message, completed = _assembly_report_dialog("assembly", report)

        self.assertFalse(completed)
        self.assertIn("#5", message)

    def test_enables_randomization_when_the_assembly_backend_supports_it(self):
        from src.features.drive_assembly.ui_bridge import _enable_assembly_randomization

        class RandomizableBackend:
            def __init__(self):
                self.enabled = False

            def enable_randomization(self):
                self.enabled = True

        backend = RandomizableBackend()

        self.assertTrue(_enable_assembly_randomization(backend))
        self.assertTrue(backend.enabled)
        self.assertFalse(_enable_assembly_randomization(object()))

    def test_closes_assembly_backend_when_it_supports_close(self):
        from src.features.drive_assembly.ui_bridge import _close_assembly_backend

        class ClosableBackend:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        backend = ClosableBackend()

        self.assertTrue(_close_assembly_backend(backend))
        self.assertTrue(backend.closed)
        self.assertFalse(_close_assembly_backend(object()))

    def _state(self):
        return {
            "真红": {
                "blueprint_layout": [["A", "A"], ["0", "B"]],
                "equipped_drives": [
                    {"uid": "drive-a", "shape_id": "H_2", "quality": "Gold", "sub_stats": {"暴击率%": 10.0}},
                    {"uid": "drive-b", "shape_id": "V_2", "quality": "Purple", "sub_stats": {"攻击力": 80}},
                ],
                "equipped_tape": {
                    "set_name": "失落光芒",
                    "main_stats": "生命值百分比",
                    "sub_stats": {"暴击率%": 10.0},
                    "quality": "Gold",
                },
            },
            "空幕": {
                "blueprint_layout": [["C"]],
                "equipped_drives": [{"uid": "drive-c", "shape_id": "H_2", "quality": "Gold", "sub_stats": {}}],
            },
        }

    def test_builds_single_role_assembly_plan_summary(self):
        from src.features.drive_assembly.ui_bridge import build_single_role_assembly_plan, summarize_assembly_plan

        plan = build_single_role_assembly_plan(self._state(), "真红")

        self.assertTrue(plan["available"])
        self.assertEqual("真红", plan["role_name"])
        self.assertEqual(1, plan["tape_count"])
        self.assertEqual(2, plan["drive_count"])
        self.assertEqual(
            ["prepare_assembly_page", "install_tape", "install_drives"],
            [action["name"] for action in plan["actions"]],
        )
        self.assertEqual(
            [
                {"name": "unload_existing_drives", "position": (1524, 1252)},
                {"name": "wait_for_unload_existing_drives_prompt", "wait_seconds": 1.0},
                {
                    "name": "confirm_unload_existing_drives_prompt",
                    "optional_confirm_position": (1546, 953),
                    "modal_probe_position": (1280, 690),
                    "brightness_threshold": 150,
                },
            ],
            plan["actions"][0]["sequence"],
        )
        self.assertEqual("真红：卡带 1，驱动 2", summarize_assembly_plan(plan))

    def test_tape_filter_sequence_opens_main_stat_with_gamepad_before_sub_stat_bottom(self):
        from src.features.drive_assembly.ui_bridge import build_single_role_assembly_plan

        state = self._state()
        role_name = next(iter(state))
        plan = build_single_role_assembly_plan(state, role_name)
        tape_action = next(action for action in plan["actions"] if action["name"] == "install_tape")
        sequence_names = [step["name"] for step in tape_action["sequence"]]
        main_stat_step = next(step for step in tape_action["sequence"] if step["name"] == "main_stat_option")

        expected_order = [
            "main_stat_gamepad_down_to_expand",
            "main_stat_gamepad_confirm_expand",
            "main_stat_gamepad_down_to_options",
            "main_stat_option",
            "sub_stat_scroll_to_expand",
            "sub_stat_expand",
            "sub_stat_scroll_to_bottom",
            "sub_stat_option",
            "sub_stat_count_four",
        ]
        indexes = [sequence_names.index(name) for name in expected_order]

        self.assertEqual(sorted(indexes), indexes)
        main_stat_open_steps = [
            step for step in tape_action["sequence"]
            if step["name"].startswith("main_stat_gamepad")
        ]
        self.assertEqual(
            ["left_down"] * 7 + ["a"] + ["left_down"] * 3,
            [step.get("gamepad_stick") or step.get("gamepad_button") for step in main_stat_open_steps],
        )
        self.assertNotIn("main_stat_expand", sequence_names)
        self.assertNotIn("main_stat_scroll_to_second_page", sequence_names)
        self.assertIn("ocr_target_text", main_stat_step)
        self.assertIn("ocr_search_region", main_stat_step)
        self.assertIn("fallback_position", main_stat_step)

    def test_tape_status_filters_are_used_only_for_duplicate_tape_and_missing_quality_is_ignored(self):
        from src.features.drive_assembly.ui_bridge import _tape_install_sequence

        base_filter = {
            "set_name": "失落光芒",
            "main_stat": "生命值百分比",
            "sub_stats": [],
            "quality": "",
        }
        normal_names = [step["name"] for step in _tape_install_sequence(base_filter, None, None)]
        duplicate_names = [
            step["name"]
            for step in _tape_install_sequence({**base_filter, "is_duplicate_tape": True}, None, None)
        ]

        self.assertFalse(any(name.startswith("status_") for name in normal_names))
        self.assertFalse(any(name.startswith("quality_") for name in normal_names))
        self.assertEqual(
            ["status_locked", "status_discarded", "status_other"],
            [name for name in duplicate_names if name.startswith("status_")],
        )
        self.assertFalse(any(name.startswith("quality_") for name in duplicate_names))

    def test_duplicate_tape_filter_order_resets_then_filters_before_equipping(self):
        from src.features.drive_assembly.ui_bridge import _tape_install_sequence

        sequence = _tape_install_sequence(
            {
                "set_name": "失落光芒",
                "main_stat": "生命值百分比",
                "sub_stats": ["暴击率%"],
                "quality": "Gold",
                "is_duplicate_tape": True,
            },
            None,
            None,
        )
        names = [step["name"] for step in sequence]

        expected_order = [
            "reset_filter",
            "set_select",
            "wait_after_tape_set_dialog_open",
            "set_option",
            "confirm_filter",
            "wait_after_tape_set_dialog_close",
            "status_locked",
            "status_discarded",
            "status_other",
            "quality_orange",
            "main_stat_gamepad_down_to_expand",
            "main_stat_option",
            "sub_stat_scroll_to_expand",
            "sub_stat_option",
        ]
        indexes = [names.index(name) for name in expected_order]

        self.assertEqual(indexes, sorted(indexes))
        self.assertLess(names.index("sub_stat_option"), len(names) - 1 - names[::-1].index("confirm_filter"))

    def test_full_role_plan_keeps_duplicate_drive_status_filters(self):
        from src.features.drive_assembly.ui_bridge import build_single_role_assembly_plan

        duplicate_drive = {"uid": "drive-a", "shape_id": "H_2", "quality": "Gold", "sub_stats": {}}
        state = {
            "A": {"blueprint_layout": [["H_2", "H_2"]], "equipped_drives": [duplicate_drive]},
            "B": {
                "blueprint_layout": [["H_2", "H_2"]],
                "equipped_drives": [{**duplicate_drive, "uid": "drive-b"}],
            },
        }

        plan = build_single_role_assembly_plan(state, "A")
        drive_action = next(action for action in plan["actions"] if action["name"] == "install_drives")
        install = drive_action["install_plans"][0]

        self.assertTrue(plan["drive_blocks"][0]["is_duplicate_drive"])
        self.assertTrue(install["duplicate_status_filter_enabled"])
        self.assertEqual(
            ["status_locked", "status_discarded", "status_other"],
            [step["name"] for step in install["install_sequence"] if step["name"].startswith("status_")],
        )

    def test_drive_install_plan_verifies_each_drive_target_after_drag(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        install = map_drive_block_installation(
            {"block_id": 3, "drive_type": "V_3", "cells": [(3, 5), (4, 5), (5, 5)], "drive": {"quality": "Gold"}}
        )
        verify = next(step for step in install["install_sequence"] if step["name"] == "verify_drive_block_installed")

        self.assertEqual(3, verify["block_id"])
        self.assertEqual(install["first_drive"], verify["retry_from"])
        self.assertEqual(install["target_position"], verify["retry_to"])
        self.assertEqual(1.0, verify["retry_settle_seconds"])

    def test_reports_single_role_without_payload(self):
        from src.features.drive_assembly.ui_bridge import build_single_role_assembly_plan, summarize_assembly_plan

        plan = build_single_role_assembly_plan(self._state(), "不存在")

        self.assertFalse(plan["available"])
        self.assertIn("未找到", summarize_assembly_plan(plan))

    def test_builds_all_role_assembly_plan_summary(self):
        from src.features.drive_assembly.ui_bridge import build_all_role_assembly_plan, summarize_assembly_plan

        plan = build_all_role_assembly_plan(self._state())

        self.assertEqual(2, plan["role_count"])
        self.assertEqual(2, plan["ready_count"])
        self.assertEqual(["真红", "空幕"], plan["roles"])
        self.assertIn("可装配角色：2/2", summarize_assembly_plan(plan))
        self.assertIn("- 真红：卡带 1，驱动 2", summarize_assembly_plan(plan))
        self.assertIn("- 空幕：卡带 0，驱动 1", summarize_assembly_plan(plan))

    def test_equipment_page_exposes_renamed_single_action_button(self):
        from PySide6.QtWidgets import QApplication, QPushButton

        from src.features.inventory.page import _page_equipment

        app = QApplication.instance() or QApplication([])
        clicked = []

        class FakeWindow:
            def _refresh_equip(self):
                pass

            def _clear_all_equipment(self):
                pass

            def _preview_assemble_all_roles(self):
                clicked.append("all")

        window = FakeWindow()
        page = _page_equipment(window)

        button = next(button for button in page.findChildren(QPushButton) if button.text() == "一键装配")
        button.click()

        self.assertEqual(["all"], clicked)
        self.assertFalse(any(button.text() == "一键装配所有角色" for button in page.findChildren(QPushButton)))
        app.processEvents()

    def test_inventory_mixin_exposes_assembly_methods(self):
        from src.ui.main_window_mixins import InventoryPageMixin

        self.assertTrue(hasattr(InventoryPageMixin, "_optimize_saved_equipment"))
        self.assertTrue(hasattr(InventoryPageMixin, "_preview_assemble_role"))
        self.assertTrue(hasattr(InventoryPageMixin, "_preview_assemble_all_roles"))

    def test_role_recognition_candidates_include_templates_and_payload_roles(self):
        import tempfile
        from pathlib import Path

        from src.features.drive_assembly.ui_bridge import _role_recognition_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "非目标角色.png").write_bytes(b"fake")
            roles = _role_recognition_candidates(["目标角色"], temp_dir, {"已保存角色": {}})

        self.assertEqual(["目标角色", "已保存角色", "非目标角色"], roles)

    def test_role_recognition_candidates_include_role_aliases(self):
        import tempfile

        from src.features.drive_assembly.ui_bridge import _role_recognition_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            roles = _role_recognition_candidates(["主角"], temp_dir, {}, {"主角": "空月"})

        self.assertEqual(["主角", "空月"], roles)

    def test_equipment_role_card_exposes_renamed_single_action_button(self):
        from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

        from src.features.inventory.page import _render_equip_role

        app = QApplication.instance() or QApplication([])
        clicked = []

        class FakeWindow:
            roles_db = {}

            def __init__(self):
                self.equip_content = QWidget()
                self.equip_content_layout = QVBoxLayout(self.equip_content)

            def _show_saved_plan_diff_dialog(self, _role_name, _diff):
                pass

            def _delete_role_equipment(self, _role_name):
                pass

            def _preview_assemble_role(self, role_name):
                clicked.append(role_name)

            def _calc_grade(self, _score, _area):
                return "A"

        window = FakeWindow()
        _render_equip_role(window, "真红", {"total_score": 1.0, "total_grade": "A"})

        button = next(button for button in window.equip_content.findChildren(QPushButton) if button.text() == "装配")
        button.click()

        self.assertEqual(["真红"], clicked)
        self.assertFalse(any(button.text() == "装配该角色" for button in window.equip_content.findChildren(QPushButton)))
        app.processEvents()


    def test_single_role_button_executes_confirmed_plan(self):
        from PySide6.QtWidgets import QMessageBox

        import src.features.inventory.page as page_module

        calls = []
        plan = {"role_name": "鐪熺孩", "available": True, "actions": [{"name": "install_tape", "sequence": []}]}

        class FakeWindow:
            equipped_state = {"鐪熺孩": {}}

        original_reload = page_module._reload_equipped_state_from_disk
        original_build = page_module.build_single_role_assembly_plan
        original_summary = page_module.summarize_assembly_plan
        original_execute = page_module.execute_selected_role_from_current_game_page
        original_question = page_module.QMessageBox.question
        original_information = page_module.QMessageBox.information
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_single_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_selected_role_from_current_game_page = (
                lambda state, role_name, **_kwargs: calls.append((state, role_name))
                or SimpleNamespace(role_reports=[1], executed_actions=3)
            )
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Yes
            page_module.QMessageBox.information = lambda *_args, **_kwargs: None

            page_module._preview_assemble_role(FakeWindow(), "鐪熺孩")
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_single_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_selected_role_from_current_game_page = original_execute
            page_module.QMessageBox.question = original_question
            page_module.QMessageBox.information = original_information

        self.assertEqual([({"鐪熺孩": {}}, "鐪熺孩")], calls)

    def test_all_role_button_does_not_execute_when_cancelled(self):
        from PySide6.QtWidgets import QMessageBox

        import src.features.inventory.page as page_module

        calls = []
        plan = {"role_plans": [{"role_name": "鐪熺孩", "available": True, "actions": []}]}

        class FakeWindow:
            equipped_state = {"鐪熺孩": {}}

        original_reload = page_module._reload_equipped_state_from_disk
        original_build = page_module.build_all_role_assembly_plan
        original_summary = page_module.summarize_assembly_plan
        original_execute = page_module.execute_all_roles_from_current_game_page
        original_question = page_module.QMessageBox.question
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_all_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_all_roles_from_current_game_page = lambda state, **_kwargs: calls.append(state)
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.No

            page_module._preview_assemble_all_roles(FakeWindow())
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_all_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_all_roles_from_current_game_page = original_execute
            page_module.QMessageBox.question = original_question

        self.assertEqual([], calls)

    def test_all_role_button_executes_current_game_role_flow_when_confirmed(self):
        from types import SimpleNamespace

        from PySide6.QtWidgets import QMessageBox

        import src.features.inventory.page as page_module

        calls = []
        plan = {"role_plans": [{"role_name": "鐪熺孩", "available": True, "actions": []}]}

        class FakeWindow:
            equipped_state = {"鐪熺孩": {}}

        original_reload = page_module._reload_equipped_state_from_disk
        original_build = page_module.build_all_role_assembly_plan
        original_summary = page_module.summarize_assembly_plan
        original_execute = page_module.execute_all_roles_from_current_game_page
        original_question = page_module.QMessageBox.question
        original_information = page_module.QMessageBox.information
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_all_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_all_roles_from_current_game_page = (
                lambda state, **_kwargs: calls.append(state) or SimpleNamespace(role_reports=[1], executed_actions=9)
            )
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Yes
            page_module.QMessageBox.information = lambda *_args, **_kwargs: None

            page_module._preview_assemble_all_roles(FakeWindow())
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_all_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_all_roles_from_current_game_page = original_execute
            page_module.QMessageBox.question = original_question
            page_module.QMessageBox.information = original_information

        self.assertEqual([{"鐪熺孩": {}}], calls)


    def test_confirmed_assembly_minimizes_calculator_before_execution(self):
        from PySide6.QtWidgets import QMessageBox

        import src.features.inventory.page as page_module

        calls = []
        plan = {"roles": ["demo"], "role_plans": [{"role_name": "demo", "available": True, "actions": []}]}

        class FakeWindow:
            equipped_state = {"demo": {}}

            def showMinimized(self):
                calls.append("minimized")

            def showNormal(self):
                calls.append("show_normal")

            def _go(self, page):
                calls.append(page)

            def raise_(self):
                calls.append("raise")

            def activateWindow(self):
                calls.append("activate")

        original_reload = page_module._reload_equipped_state_from_disk
        original_build = page_module.build_all_role_assembly_plan
        original_summary = page_module.summarize_assembly_plan
        original_execute = page_module.execute_all_roles_from_current_game_page
        original_question = page_module.QMessageBox.question
        original_information = page_module.QMessageBox.information
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_all_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_all_roles_from_current_game_page = (
                lambda _state, **_kwargs: calls.append("executed")
                or SimpleNamespace(role_reports=[1], executed_actions=1)
            )
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Yes
            page_module.QMessageBox.information = lambda *_args, **_kwargs: calls.append("dialog")

            page_module._preview_assemble_all_roles(FakeWindow())
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_all_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_all_roles_from_current_game_page = original_execute
            page_module.QMessageBox.question = original_question
            page_module.QMessageBox.information = original_information

        self.assertEqual(
            ["minimized", "executed", "show_normal", "equipment", "raise", "activate", "dialog"],
            calls,
        )

    def test_single_role_f12_stop_restores_equipment_page_before_dialog(self):
        from PySide6.QtWidgets import QMessageBox

        import src.features.inventory.page as page_module

        calls = []
        plan = {"role_name": "demo", "available": True, "actions": []}

        class FakeWindow:
            equipped_state = {"demo": {}}

            def showMinimized(self):
                calls.append("minimized")

            def showNormal(self):
                calls.append("show_normal")

            def _go(self, page):
                calls.append(page)

            def raise_(self):
                calls.append("raise")

            def activateWindow(self):
                calls.append("activate")

        def stop_execution(*_args, **_kwargs):
            raise page_module.AssemblyExecutionStopped()

        original_reload = page_module._reload_equipped_state_from_disk
        original_build = page_module.build_single_role_assembly_plan
        original_summary = page_module.summarize_assembly_plan
        original_execute = page_module.execute_selected_role_from_current_game_page
        original_question = page_module.QMessageBox.question
        original_warning = page_module.QMessageBox.warning
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_single_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_selected_role_from_current_game_page = stop_execution
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Yes
            page_module.QMessageBox.warning = lambda *_args, **_kwargs: calls.append("dialog")

            page_module._preview_assemble_role(FakeWindow(), "demo")
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_single_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_selected_role_from_current_game_page = original_execute
            page_module.QMessageBox.question = original_question
            page_module.QMessageBox.warning = original_warning

        self.assertEqual(["minimized", "show_normal", "equipment", "raise", "activate", "dialog"], calls)

    def test_all_roles_f12_stop_restores_equipment_page_before_dialog(self):
        from PySide6.QtWidgets import QMessageBox

        import src.features.inventory.page as page_module

        calls = []
        plan = {"roles": ["demo"], "role_plans": [{"role_name": "demo", "available": True, "actions": []}]}

        class FakeWindow:
            equipped_state = {"demo": {}}

            def showMinimized(self):
                calls.append("minimized")

            def showNormal(self):
                calls.append("show_normal")

            def _go(self, page):
                calls.append(page)

            def raise_(self):
                calls.append("raise")

            def activateWindow(self):
                calls.append("activate")

        def stop_execution(*_args, **_kwargs):
            raise page_module.AssemblyExecutionStopped()

        original_reload = page_module._reload_equipped_state_from_disk
        original_build = page_module.build_all_role_assembly_plan
        original_summary = page_module.summarize_assembly_plan
        original_execute = page_module.execute_all_roles_from_current_game_page
        original_question = page_module.QMessageBox.question
        original_warning = page_module.QMessageBox.warning
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_all_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_all_roles_from_current_game_page = stop_execution
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Yes
            page_module.QMessageBox.warning = lambda *_args, **_kwargs: calls.append("dialog")

            page_module._preview_assemble_all_roles(FakeWindow())
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_all_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_all_roles_from_current_game_page = original_execute
            page_module.QMessageBox.question = original_question
            page_module.QMessageBox.warning = original_warning

        self.assertEqual(["minimized", "show_normal", "equipment", "raise", "activate", "dialog"], calls)

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
