# 验证装配计划、识别、筛选顺序和执行器生命周期。
"""Behavior tests for drive-assembly planning and bridge helpers."""

import unittest
from types import SimpleNamespace
from pathlib import Path
import tempfile

import numpy as np



class DriveAssemblyUiBridgeTests(unittest.TestCase):
    def test_startup_guard_rejects_fuzzy_role_matches(self):
        from src.features.drive_assembly.ui_bridge import is_role_detail_startup_recognition

        self.assertTrue(is_role_detail_startup_recognition(SimpleNamespace(role_name="A", method="ocr")))
        self.assertTrue(is_role_detail_startup_recognition(SimpleNamespace(role_name="A", method="ocr_fallback")))
        self.assertFalse(is_role_detail_startup_recognition(SimpleNamespace(role_name="A", method="ocr_fuzzy")))
        self.assertFalse(is_role_detail_startup_recognition(SimpleNamespace(role_name=None, method="ocr")))

    def test_assembly_recorder_writes_pngs_under_record_directory(self):
        from src.features.drive_assembly.ui_bridge import AssemblyRunRecorder

        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = AssemblyRunRecorder(Path(temp_dir) / "record")
            path = recorder.save_image(np.zeros((8, 12, 3), dtype=np.uint8), "startup")

            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertEqual("record", path.parent.parent.name)
            self.assertEqual("001_startup.png", path.name)

    def test_assembly_recorder_captures_completed_duplicate_status_filters(self):
        from src.features.drive_assembly.ui_bridge import AssemblyRunRecorder

        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = AssemblyRunRecorder(Path(temp_dir) / "record")
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

        self.assertTrue(completed)
        self.assertIn("第 3 个角色", message)
        self.assertIn("unknown-one", message)
        self.assertIn("第 8 个角色", message)
        self.assertIn("不影响本次装配结果", message)
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

    def test_assembly_report_explains_duplicate_drive_verification_failure(self):
        from src.features.inventory.page import _assembly_report_dialog

        report = SimpleNamespace(
            role_reports=[object()],
            executed_actions=20,
            missing_roles=[],
            skipped_roles=[],
            duplicate_roles=[],
            unrecognized_roles=[],
            verification_failures=[
                {
                    "role_name": "早雾",
                    "missing_blocks": [
                        {"block_id": 49, "is_duplicate_drive": True, "duplicate_count": 2},
                    ],
                },
            ],
        )

        _title, message, completed = _assembly_report_dialog("自动装配", report)

        self.assertFalse(completed)
        self.assertIn("重复驱动块 #49", message)
        self.assertIn("自动装配无法唯一定位目标", message)
        self.assertIn("手动补装", message)

    def test_enables_randomization_when_the_assembly_backend_supports_it(self):
        from src.features.drive_assembly.ui_bridge import enable_assembly_randomization

        class RandomizableBackend:
            def __init__(self):
                self.enabled = False

            def enable_randomization(self):
                self.enabled = True

        backend = RandomizableBackend()

        self.assertTrue(enable_assembly_randomization(backend))
        self.assertTrue(backend.enabled)
        self.assertFalse(enable_assembly_randomization(object()))

    def test_closes_assembly_backend_when_it_supports_close(self):
        from src.features.drive_assembly.ui_bridge import close_assembly_backend

        class ClosableBackend:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        backend = ClosableBackend()

        self.assertTrue(close_assembly_backend(backend))
        self.assertTrue(backend.closed)
        self.assertFalse(close_assembly_backend(object()))

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
                    "post_action_pause_seconds": 0.8,
                },
            ],
            plan["actions"][0]["sequence"],
        )
        self.assertEqual("真红：卡带 1，驱动 2", summarize_assembly_plan(plan))

    def test_tape_filter_sequence_opens_main_stat_with_mouse_wheel_before_sub_stat_bottom(self):
        from src.features.drive_assembly.ui_bridge import build_single_role_assembly_plan

        state = self._state()
        role_name = next(iter(state))
        plan = build_single_role_assembly_plan(state, role_name)
        tape_action = next(action for action in plan["actions"] if action["name"] == "install_tape")
        sequence_names = [step["name"] for step in tape_action["sequence"]]
        main_stat_step = next(step for step in tape_action["sequence"] if step["name"] == "main_stat_option")

        expected_order = [
            "main_stat_expand",
            "main_stat_wheel_to_options",
            "main_stat_option",
            "sub_stat_scroll_to_expand",
            "sub_stat_expand",
            "sub_stat_scroll_to_bottom",
            "sub_stat_option",
            "sub_stat_count_four",
        ]
        indexes = [sequence_names.index(name) for name in expected_order]

        self.assertEqual(sorted(indexes), indexes)
        self.assertEqual(
            {"name": "main_stat_wheel_to_options", "position": (2067, 760), "wheel_clicks": -13, "wheel_click_interval_seconds": 0.06, "post_action_pause_seconds": 0.8},
            next(step for step in tape_action["sequence"] if step["name"] == "main_stat_wheel_to_options"),
        )
        self.assertIn("main_stat_expand", sequence_names)
        self.assertNotIn("main_stat_scroll_to_second_page", sequence_names)
        self.assertNotIn("ocr_target_text", main_stat_step)
        self.assertEqual((1861, 600), main_stat_step["position"])

    def test_tape_status_filters_are_used_only_for_duplicate_tape_and_missing_quality_is_ignored(self):
        from src.features.drive_assembly.ui_bridge import tape_install_sequence

        base_filter = {
            "set_name": "失落光芒",
            "main_stat": "生命值百分比",
            "sub_stats": [],
            "quality": "",
        }
        normal_names = [step["name"] for step in tape_install_sequence(base_filter, None, None)]
        duplicate_names = [
            step["name"] for step in tape_install_sequence({**base_filter, "is_duplicate_tape": True}, None, None)
        ]

        self.assertFalse(any(name.startswith("status_") for name in normal_names))
        self.assertFalse(any(name.startswith("quality_") for name in normal_names))
        self.assertEqual(
            ["status_locked", "status_discarded", "status_other"],
            [name for name in duplicate_names if name.startswith("status_")],
        )
        self.assertFalse(any(name.startswith("quality_") for name in duplicate_names))

    def test_duplicate_tape_filter_order_resets_then_filters_before_equipping(self):
        from src.features.drive_assembly.ui_bridge import tape_install_sequence

        sequence = tape_install_sequence(
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
            "main_stat_expand",
            "main_stat_wheel_to_options",
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

    def test_role_recognition_candidates_include_templates_and_payload_roles(self):
        import tempfile
        from pathlib import Path

        from src.features.drive_assembly.ui_bridge import role_recognition_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "非目标角色.png").write_bytes(b"fake")
            roles = role_recognition_candidates(["目标角色"], temp_dir, {"已保存角色": {}})

        self.assertEqual(["目标角色", "已保存角色"], roles[:2])
        self.assertIn("非目标角色", roles)
        self.assertIn("达芙蒂尔", roles)
        self.assertIn("翳", roles)

    def test_role_recognition_candidates_include_role_aliases(self):
        import tempfile

        from src.features.drive_assembly.ui_bridge import role_recognition_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            roles = role_recognition_candidates(["主角"], temp_dir, {}, {"主角": "空月"})

        self.assertEqual(["主角", "空月"], roles[:2])





if __name__ == "__main__":

    unittest.main()
