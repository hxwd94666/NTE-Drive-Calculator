# 验证角色识别、去重遍历和逐角色装配计划。
"""Tests for role recognition and role-by-role assembly planning."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


class DriveAssemblyRoleRecognitionTests(unittest.TestCase):
    def test_maps_role_navigation_controls(self):
        from src.features.drive_assembly.role_flow import map_role_navigation_controls

        controls = map_role_navigation_controls()

        self.assertEqual((88, 581), controls["left_kongmu_tab"])
        self.assertEqual((2160, 1322), controls["assemble_button"])
        self.assertEqual(
            [
                {"name": "left_kongmu_tab", "position": (88, 581)},
                {"name": "wait_after_left_kongmu_tab", "wait_seconds": 1.0},
                {"name": "assemble_button", "position": (2160, 1322)},
                {"name": "wait_after_assemble_button", "wait_seconds": 1.2},
            ],
            controls["entry_sequence"],
        )
        self.assertEqual(
            [
                {
                    "name": "assembly_back_to_role_page",
                    "gamepad_button": "b",
                    "post_action_pause_seconds": 1.5,
                },
            ],
            controls["exit_sequence"],
        )

    def test_maps_five_visible_role_slots_and_scroll(self):
        from src.features.drive_assembly.role_flow import map_role_page_reset, map_role_page_scroll, map_role_slots

        self.assertEqual(
            [(2410, 242), (2410, 470), (2410, 697), (2410, 925), (2410, 1152)],
            map_role_slots(),
        )

        scroll = map_role_page_scroll()
        self.assertEqual((2388, 1152), scroll["role_scroll_start"])
        self.assertEqual((2388, 242), scroll["role_scroll_end"])
        self.assertEqual(
            [
                {
                    "name": "role_scroll_next_page",
                    "from": (2388, 1152),
                    "to": (2388, 242),
                    "duration_ms": 700,
                }
            ],
            scroll["scroll_sequence"],
        )

        reset = map_role_page_reset(repeat_count=2)
        self.assertEqual(2, len(reset["reset_sequence"]))
        self.assertEqual("role_scroll_reset_to_first_page", reset["reset_sequence"][0]["name"])
        self.assertEqual((2388, 242), reset["reset_sequence"][0]["from"])
        self.assertEqual((2388, 1152), reset["reset_sequence"][0]["to"])

    def test_defaults_role_dpad_reset_to_five_up_moves(self):
        from src.features.drive_assembly.role_flow import map_dpad_role_reset_sequence

        sequence = map_dpad_role_reset_sequence()

        self.assertEqual(5, len(sequence))
        self.assertEqual(["dpad_up"] * 5, [action["gamepad_button"] for action in sequence])

    def test_maps_role_list_grid_moves_with_three_column_wrap(self):
        from src.features.drive_assembly.role_flow import map_role_list_grid_move_sequence

        self.assertEqual(
            ["left_right"],
            [action.get("gamepad_button") or action.get("gamepad_stick") for action in map_role_list_grid_move_sequence(2, 3)],
        )
        self.assertEqual(
            ["left_left"],
            [action.get("gamepad_button") or action.get("gamepad_stick") for action in map_role_list_grid_move_sequence(3, 2)],
        )
        self.assertEqual(
            ["left_down", "left_right", "left_right"],
            [action.get("gamepad_button") or action.get("gamepad_stick") for action in map_role_list_grid_move_sequence(0, 5)],
        )
        self.assertEqual(
            ["left_up", "left_left", "left_left"],
            [action.get("gamepad_button") or action.get("gamepad_stick") for action in map_role_list_grid_move_sequence(5, 0)],
        )
        self.assertEqual(
            [0.25, 0.25, 0.25],
            [action["post_action_pause_seconds"] for action in map_role_list_grid_move_sequence(0, 5)],
        )

    def test_scales_role_navigation_to_other_screen(self):
        from src.features.drive_assembly.role_flow import map_role_navigation_controls, map_role_slots

        controls = map_role_navigation_controls(screen_size=(1280, 720))
        slots = map_role_slots(screen_size=(1280, 720))

        self.assertEqual((44, 291), controls["left_kongmu_tab"])
        self.assertEqual((1080, 661), controls["assemble_button"])
        self.assertEqual((1205, 121), slots[0])

    def test_maps_role_slot_template_regions(self):
        from src.features.drive_assembly.role_flow import map_role_slot_template_regions

        regions = map_role_slot_template_regions()

        self.assertEqual(5, len(regions))
        self.assertEqual((2290, 122, 2530, 362), regions[0])
        self.assertEqual((2290, 1032, 2530, 1272), regions[4])

    def test_maps_expanded_current_role_name_region(self):
        from src.features.drive_assembly.role_flow import map_current_role_name_region

        self.assertEqual((1738, 252, 2180, 320), map_current_role_name_region())
        self.assertEqual((1688, 228, 2248, 342), map_current_role_name_region(expanded=True))

    def test_recognizes_current_role_with_expanded_ocr_fallback(self):
        from src.features.drive_assembly.role_flow import recognize_current_role_from_image

        class FakeOcr:
            def __init__(self):
                self.calls = 0

            def extract_text(self, crop):
                self.calls += 1
                if self.calls == 1:
                    self.primary_shape = crop.shape[:2]
                    return []
                self.fallback_shape = crop.shape[:2]
                return ["达芙蒂尔"]

        ocr = FakeOcr()
        image = np.zeros((1440, 2560, 3), dtype=np.uint8)

        result = recognize_current_role_from_image(image, ["达芙蒂尔"], ocr)

        self.assertEqual("达芙蒂尔", result.role_name)
        self.assertEqual("ocr_fallback", result.method)
        self.assertEqual((68, 442), ocr.primary_shape)
        self.assertEqual((114, 560), ocr.fallback_shape)

    def test_recognizes_player_name_as_protagonist_alias(self):
        from src.features.drive_assembly.role_flow import recognize_current_role_from_image

        class FakeOcr:
            def extract_text(self, _crop):
                return ["空月"]

        image = np.zeros((1440, 2560, 3), dtype=np.uint8)

        result = recognize_current_role_from_image(
            image,
            ["主角", "空月"],
            FakeOcr(),
            role_aliases={"主角": "空月"},
        )

        self.assertEqual("主角", result.role_name)
        self.assertEqual("空月", result.raw_text)

    def test_fuzzy_role_ocr_accepts_repeated_name_with_one_character_error(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["\u6cd5\u5e1d\u5a05\u6cd5\u5e1d\u5a05S"],
            ["\u6cd5\u8482\u5a05", "\u54c8\u5c3c\u5a05"],
        )

        self.assertEqual("\u6cd5\u8482\u5a05", result.role_name)
        self.assertEqual("ocr_fuzzy", result.method)
        self.assertGreaterEqual(result.confidence, 0.6)

    def test_repeated_ocr_name_with_trailing_level_resolves_to_nana_li(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["娜娜莉娜娜莉6"],
            ["娜娜莉", "浔"],
        )

        self.assertEqual("娜娜莉", result.role_name)
        self.assertEqual("ocr_repeated_name", result.method)

    def test_repeated_single_character_role_ocr_is_resolved(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["浔女浔"],
            ["娜娜莉", "浔", "翳"],
        )

        self.assertEqual("浔", result.role_name)
        self.assertEqual("ocr_repeated_name", result.method)

    def test_repeated_roster_name_with_trailing_noise_resolves(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["达芙蒂尔达芙蒂尔"],
            ["达芙蒂尔", "阿德勒", "翳"],
        )

        self.assertEqual("达芙蒂尔", result.role_name)
        self.assertEqual("ocr_repeated_name", result.method)

    def test_known_yi_misread_resolves_when_static_candidate_is_available(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(["医设"], ["达芙蒂尔", "翳"])

        self.assertEqual("翳", result.role_name)
        self.assertEqual("ocr_yi_fallback", result.method)

    def test_yi_fallback_does_not_override_a_normal_role_match(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(["医设"], ["医设", "翳"])

        self.assertEqual("医设", result.role_name)
        self.assertEqual("ocr", result.method)

    def test_falls_back_to_yi_for_ocr_misread_when_other_roles_do_not_match(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["\u533b\u6bbfB\u6734"],
            ["\u7ff3", "\u7ea2"],
        )

        self.assertEqual("\u7ff3", result.role_name)
        self.assertEqual("ocr_yi_fallback", result.method)
        self.assertEqual("\u533b\u6bbfB\u6734", result.raw_text)

    def test_falls_back_to_yi_for_ocr_fragment_when_other_roles_do_not_match(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["\u533b\u8bbe\u91ab"],
            ["\u7ff3", "\u7ea2"],
        )

        self.assertEqual("\u7ff3", result.role_name)
        self.assertEqual("ocr_yi_fallback", result.method)
        self.assertEqual("\u533b\u8bbe\u91ab", result.raw_text)

    def test_falls_back_to_yi_for_unmatched_ocr_containing_yi_radical(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["\u533b\u68a6"],
            ["\u7ff3", "\u7ea2"],
        )

        self.assertEqual("\u7ff3", result.role_name)
        self.assertEqual("ocr_yi_fallback", result.method)
        self.assertEqual(0.6, result.confidence)

    def test_recognizes_visible_role_slots_from_templates(self):
        from src.features.drive_assembly.role_flow import recognize_role_slots_from_image

        with tempfile.TemporaryDirectory() as temp_dir:
            template_dir = Path(temp_dir)
            role_a = np.zeros((40, 40, 3), dtype=np.uint8)
            cv2.circle(role_a, (20, 20), 12, (255, 255, 255), -1)
            role_b = np.zeros((40, 40, 3), dtype=np.uint8)
            cv2.line(role_b, (6, 6), (34, 34), (255, 255, 255), 4)
            cv2.line(role_b, (34, 6), (6, 34), (255, 255, 255), 4)
            cv2.imwrite(str(template_dir / "A.png"), role_a)
            cv2.imwrite(str(template_dir / "B.png"), role_b)

            image = np.zeros((1440, 2560, 3), dtype=np.uint8)
            image[200:240, 2350:2390] = role_a
            image[428:468, 2350:2390] = role_b

            results = recognize_role_slots_from_image(image, ["A", "B"], template_dir)

        self.assertEqual("A", results[0].role_name)
        self.assertEqual("B", results[1].role_name)
        self.assertIsNone(results[2].role_name)

    def test_resolves_role_from_ocr_before_template(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition([" 真 红 "], ["真红", "空幕"], {"空幕": 0.98})

        self.assertEqual("真红", result.role_name)
        self.assertEqual("ocr", result.method)

    def test_fuzzy_role_ocr_accepts_surrounding_text_and_one_character_error(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(["角色真虹", "暗"], ["真红", "薄荷"])

        self.assertEqual("真红", result.role_name)
        self.assertEqual("ocr_fuzzy", result.method)
        self.assertEqual(0.5, result.confidence)

    def test_fuzzy_role_ocr_rejects_ambiguous_one_character_match(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(["真某"], ["真红", "真夜"])

        self.assertIsNone(result.role_name)

    def test_resolves_role_from_template_when_ocr_fails(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(["噪声"], ["真红", "空幕"], {"真红": 0.81, "空幕": 0.6})

        self.assertEqual("真红", result.role_name)
        self.assertEqual("template", result.method)

    def test_reports_unrecognized_role_when_both_methods_fail(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(["噪声"], ["真红"], {"真红": 0.5})

        self.assertIsNone(result.role_name)
        self.assertEqual("unrecognized", result.method)

if __name__ == "__main__":
    unittest.main()
