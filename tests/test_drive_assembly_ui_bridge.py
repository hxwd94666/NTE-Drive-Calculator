# 验证装配页面调用的计划汇总入口。
"""Tests for drive assembly UI bridge helpers."""

import unittest
from types import SimpleNamespace

import numpy as np


class DriveAssemblyUiBridgeTests(unittest.TestCase):
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
        self.assertEqual(["install_tape", "install_drives"], [action["name"] for action in plan["actions"]])
        self.assertEqual("真红：卡带 1，驱动 2", summarize_assembly_plan(plan))

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

    def test_equipment_page_exposes_assemble_all_button(self):
        from PySide6.QtWidgets import QApplication, QPushButton

        from src.features.inventory.page import _page_equipment

        app = QApplication.instance() or QApplication([])
        clicked = []

        class FakeWindow:
            def _refresh_equip(self):
                pass

            def _clear_all_equipment(self):
                pass

            def _import_all_to_my_roles(self):
                pass

            def _preview_assemble_all_roles(self):
                clicked.append("all")

        window = FakeWindow()
        page = _page_equipment(window)

        button = next(button for button in page.findChildren(QPushButton) if button.text() == "一键装配所有角色")
        button.click()

        self.assertEqual(["all"], clicked)
        app.processEvents()

    def test_equipment_role_card_exposes_single_role_assemble_button(self):
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

            def _import_to_my_role(self, _role_name):
                pass

            def _preview_assemble_role(self, role_name):
                clicked.append(role_name)

            def _calc_grade(self, _score, _area):
                return "A"

        window = FakeWindow()
        _render_equip_role(window, "真红", {"total_score": 1.0, "total_grade": "A"})

        button = next(button for button in window.equip_content.findChildren(QPushButton) if button.text() == "装配该角色")
        button.click()

        self.assertEqual(["真红"], clicked)
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
        original_execute = page_module.execute_role_assembly_plan
        original_question = page_module.QMessageBox.question
        original_information = page_module.QMessageBox.information
        try:
            page_module._reload_equipped_state_from_disk = lambda _self: None
            page_module.build_single_role_assembly_plan = lambda *_args, **_kwargs: plan
            page_module.summarize_assembly_plan = lambda _plan: "summary"
            page_module.execute_role_assembly_plan = lambda p, **_kwargs: calls.append(p) or SimpleNamespace(executed_actions=3)
            page_module.QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Yes
            page_module.QMessageBox.information = lambda *_args, **_kwargs: None

            page_module._preview_assemble_role(FakeWindow(), "鐪熺孩")
        finally:
            page_module._reload_equipped_state_from_disk = original_reload
            page_module.build_single_role_assembly_plan = original_build
            page_module.summarize_assembly_plan = original_summary
            page_module.execute_role_assembly_plan = original_execute
            page_module.QMessageBox.question = original_question
            page_module.QMessageBox.information = original_information

        self.assertEqual([plan], calls)

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
            page_module.execute_all_roles_from_current_game_page = lambda state: calls.append(state)
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
                lambda state: calls.append(state) or SimpleNamespace(role_reports=[1, 2], executed_actions=9)
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
