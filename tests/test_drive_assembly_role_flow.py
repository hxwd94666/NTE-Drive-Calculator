# 验证角色识别、去重遍历和逐角色装配计划。
"""Tests for role recognition and role-by-role assembly planning."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


class DriveAssemblyRoleFlowTests(unittest.TestCase):
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

    def test_recognizes_known_ocr_error_for_yi(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["\u533b\u6bbfB\u6734"],
            ["\u7ff3", "\u7ea2"],
        )

        self.assertEqual("\u7ff3", result.role_name)
        self.assertEqual("ocr_correction", result.method)
        self.assertEqual("\u533b\u6bbfB\u6734", result.raw_text)

    def test_recognizes_observed_ocr_fragment_for_yi(self):
        from src.features.drive_assembly.role_flow import resolve_role_recognition

        result = resolve_role_recognition(
            ["\u533b\u8bbe\u91ab"],
            ["\u7ff3", "\u7ea2"],
        )

        self.assertEqual("\u7ff3", result.role_name)
        self.assertEqual("ocr_correction", result.method)
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

    def test_plans_role_assembly_with_duplicates_and_missing_roles(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, plan_role_assembly_from_observations

        plan = plan_role_assembly_from_observations(
            ["真红", "空幕", "零"],
            [
                [
                    RoleRecognition("真红", "ocr", 1.0, "真红"),
                    RoleRecognition("空幕", "template", 0.82),
                    RoleRecognition("真红", "ocr", 1.0, "真红"),
                    RoleRecognition(None, "unrecognized", 0.0),
                    RoleRecognition("其他角色", "ocr", 1.0),
                ],
                [
                    RoleRecognition("零", "ocr", 1.0, "零"),
                ],
            ],
        )

        self.assertEqual(["真红", "空幕", "零"], plan["planned_roles"])
        self.assertEqual([], plan["missing_roles"])
        self.assertEqual([{"role_name": "真红", "page_index": 0, "slot_index": 2}], plan["duplicates"])
        self.assertEqual([{"page_index": 0, "slot_index": 3, "position": (2410, 925)}], plan["unrecognized"])
        self.assertFalse(plan["complete"])
        self.assertEqual({"name": "role_slot", "role_name": "真红", "position": (2410, 242)}, plan["plans"][0]["action_sequence"][0])
        self.assertEqual("assemble_current_role_from_blueprint", plan["plans"][0]["action_sequence"][-1]["name"])
        self.assertEqual("find_role_then_assemble_blueprint", plan["plans"][0]["flow"])
        self.assertEqual([{"name": "role_scroll_next_page", "from": (2388, 1152), "to": (2388, 242), "duration_ms": 700}], plan["plans"][2]["action_sequence"])

    def test_reports_missing_required_roles(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_observations

        plan = plan_role_assembly_from_observations(["真红", "空幕"], [["真红"]])

        self.assertEqual(["真红"], plan["planned_roles"])
        self.assertEqual(["空幕"], plan["missing_roles"])
        self.assertFalse(plan["complete"])

    def test_collects_role_observation_pages_until_required_roles_are_seen(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, collect_role_observation_pages

        pages = [
            [RoleRecognition("A", "template", 0.9), RoleRecognition("B", "template", 0.9)],
            [RoleRecognition("C", "template", 0.9), RoleRecognition("D", "template", 0.9)],
        ]
        scrolls = []

        observed = collect_role_observation_pages(
            ["A", "C"],
            page_observer=lambda index: pages[index],
            scroll_next_page=lambda index: scrolls.append(index),
            max_pages=4,
        )

        self.assertEqual([pages[0], pages[1]], observed)
        self.assertEqual([0], scrolls)

    def test_collects_role_observation_pages_stops_at_max_pages(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, collect_role_observation_pages

        scrolls = []

        observed = collect_role_observation_pages(
            ["A", "Z"],
            page_observer=lambda _index: [RoleRecognition("A", "template", 0.9)],
            scroll_next_page=lambda index: scrolls.append(index),
            max_pages=2,
        )

        self.assertEqual(2, len(observed))
        self.assertEqual([0], scrolls)

    def test_builds_role_keyed_assembly_payloads_from_equipped_state(self):
        from src.features.drive_assembly.role_flow import build_role_assembly_payloads, required_roles_from_payloads

        state = {
            "真红": {
                "blueprint_layout": [["A", "A"], ["0", "B"]],
                "equipped_drives": [
                    {"uid": "drive-a", "shape_id": "H_2", "sub_stats": {"暴击率%": 10.0}},
                    {"uid": "drive-b", "shape_id": "V_2", "sub_stats": {"攻击力": 80}},
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
                "equipped_drives": [{"uid": "drive-c", "shape_id": "H_2", "sub_stats": {}}],
            },
        }

        payloads = build_role_assembly_payloads(state)

        self.assertEqual(["真红", "空幕"], required_roles_from_payloads(payloads))
        self.assertEqual(2, len(payloads["真红"]["drive_blocks"]))
        self.assertEqual("失落光芒", payloads["真红"]["tape_filter"]["set_name"])
        self.assertEqual((1081, 362), payloads["空幕"]["drive_blocks"][0]["pixel_position"])


    def test_role_traversal_plan_resets_to_first_page_when_requested(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_observations

        plan = plan_role_assembly_from_observations(["A"], [["A"]], reset_to_first_page=True, reset_scroll_count=2)

        self.assertEqual(None, plan["plans"][0]["role_name"])
        self.assertEqual(2, len(plan["plans"][0]["action_sequence"]))
        self.assertEqual("role_scroll_reset_to_first_page", plan["plans"][0]["action_sequence"][0]["name"])
        self.assertEqual("A", plan["plans"][1]["role_name"])

    def test_collects_all_requested_pages_when_not_stopping_after_required_roles(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, collect_role_observation_pages

        pages = [[RoleRecognition("A", "template", 0.9)], [RoleRecognition("B", "template", 0.9)]]
        scrolls = []

        observed = collect_role_observation_pages(
            ["A"],
            page_observer=lambda index: pages[index],
            scroll_next_page=lambda index: scrolls.append(index),
            max_pages=2,
            stop_when_all_seen=False,
        )

        self.assertEqual(2, len(observed))
        self.assertEqual([0], scrolls)

    def test_collects_role_roster_until_post_scroll_repeat(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, collect_role_roster_until_repeat

        pages = [
            [RoleRecognition(role, "template", 0.9) for role in ["A", "B", "C", "D", "E"]],
            [RoleRecognition(role, "template", 0.9) for role in ["F", "G", "H", "I", "J"]],
            [RoleRecognition(role, "template", 0.9) for role in ["I", "J", "K", "L", "M"]],
            [RoleRecognition("N", "template", 0.9)],
        ]
        scrolls = []

        roster = collect_role_roster_until_repeat(
            list("ABCDEFGHIJKLM"),
            page_observer=lambda index: pages[index],
            scroll_next_page=lambda index: scrolls.append(index),
            max_pages=4,
        )

        self.assertEqual(list("ABCDEFGHIJKLM"), roster["roles"])
        self.assertTrue(roster["reached_bottom"])
        self.assertEqual(2, roster["bottom_page_index"])
        self.assertEqual([0, 1], scrolls)
        self.assertEqual(
            [
                {"role_name": "I", "page_index": 2, "slot_index": 0},
                {"role_name": "J", "page_index": 2, "slot_index": 1},
            ],
            roster["duplicates"],
        )

    def test_collects_role_roster_with_dpad_until_down_stops_changing_role(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, collect_role_roster_with_dpad

        observations = iter(
            [
                RoleRecognition("A", "ocr", 1.0, "A"),
                RoleRecognition("B", "ocr", 1.0, "B"),
                RoleRecognition("C", "ocr", 1.0, "C"),
                RoleRecognition("C", "ocr", 1.0, "C"),
                RoleRecognition("C", "ocr", 1.0, "C"),
                RoleRecognition("C", "ocr", 1.0, "C"),
            ]
        )
        presses = []

        roster = collect_role_roster_with_dpad(
            ["A", "C"],
            current_observer=lambda _index: next(observations),
            press_up=lambda: presses.append("up"),
            press_down=lambda: presses.append("down"),
            reset_up_count=4,
            bottom_repeat_limit=3,
            max_roles=10,
        )

        self.assertEqual(["A", "B", "C"], roster["roles"])
        self.assertTrue(roster["reached_bottom"])
        self.assertEqual(["up", "up", "up", "up", "down", "down", "down", "down", "down"], presses)
        self.assertEqual([], roster["missing_expected_roles"])
        self.assertEqual({"A": 0, "B": 1, "C": 2}, roster["role_positions"])
        self.assertEqual(2, roster["current_index"])

    def test_collects_role_roster_from_rs_list_until_required_roles_are_found(self):
        from src.features.drive_assembly.role_flow import RoleRecognition, collect_role_roster_from_role_list

        observations = iter(
            [
                RoleRecognition("A", "ocr", 1.0, "A"),
                RoleRecognition("B", "ocr", 1.0, "B"),
            ]
        )
        inputs = []

        roster = collect_role_roster_from_role_list(
            ["A", "B"],
            current_observer=lambda _index: next(observations),
            press_up=lambda: inputs.append("up"),
            open_role_list=lambda: inputs.append("rs"),
            confirm_selection=lambda: inputs.append("a"),
            move_right=lambda: inputs.append("right"),
            reset_up_count=2,
            max_roles=10,
        )

        self.assertEqual(["A", "B"], roster["roles"])
        self.assertEqual({"A": 0, "B": 1}, roster["role_positions"])
        self.assertEqual(1, roster["current_index"])
        self.assertTrue(roster["list_open"])
        self.assertEqual("all_required_roles_found", roster["stop_reason"])
        self.assertEqual(["up", "up", "rs", "a", "right", "a"], inputs)

    def test_dpad_roster_keeps_real_cursor_indexes_when_some_roles_are_unrecognized(self):
        from src.features.drive_assembly.role_flow import (
            RoleRecognition,
            collect_role_roster_with_dpad,
            plan_role_assembly_from_dpad_roster,
        )

        observations = iter(
            [
                RoleRecognition("主角", "ocr", 1.0, "空月"),
                RoleRecognition(None, "unrecognized", 0.0, ""),
                RoleRecognition("真红", "ocr", 1.0, "真红"),
                RoleRecognition("真红", "ocr", 1.0, "真红"),
                RoleRecognition("真红", "ocr", 1.0, "真红"),
                RoleRecognition("真红", "ocr", 1.0, "真红"),
            ]
        )

        roster = collect_role_roster_with_dpad(
            ["主角"],
            current_observer=lambda _index: next(observations),
            press_up=lambda: None,
            press_down=lambda: None,
            reset_up_count=0,
            bottom_repeat_limit=3,
            max_roles=10,
        )
        plan = plan_role_assembly_from_dpad_roster(["主角"], roster)

        self.assertEqual({"主角": 0, "真红": 2}, roster["role_positions"])
        self.assertEqual(2, roster["current_index"])
        self.assertEqual(
            ["dpad_up", "dpad_up"],
            [action["gamepad_button"] for action in plan["plans"][0]["action_sequence"][:2]],
        )

    def test_plans_tail_roles_from_bottom_reverse_slots(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_roster

        roster = {
            "roles": list("ABCDEFGHIJKLM"),
            "bottom_page_index": 2,
            "duplicates": [],
            "unrecognized": [],
        }

        plan = plan_role_assembly_from_roster(["K", "L", "M"], roster, reset_scroll_count=1)

        self.assertEqual(["K", "L", "M"], plan["planned_roles"])
        self.assertEqual(["bottom_tail", "bottom_tail", "bottom_tail"], [item["positioning"] for item in plan["plans"]])
        self.assertEqual(["find_role_then_assemble_blueprint"] * 3, [item["flow"] for item in plan["plans"]])
        self.assertEqual([2, 3, 4], [item["slot_index"] for item in plan["plans"]])
        self.assertEqual([(2410, 697), (2410, 925), (2410, 1152)], [item["action_sequence"][3]["position"] for item in plan["plans"]])
        self.assertEqual("assemble_current_role_from_blueprint", plan["plans"][0]["action_sequence"][-1]["name"])
        self.assertEqual(
            ["role_scroll_reset_to_first_page", "role_scroll_next_page", "role_scroll_next_page", "role_slot"],
            [action["name"] for action in plan["plans"][0]["action_sequence"][:4]],
        )

    def test_plans_role_assembly_from_dpad_roster(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_dpad_roster

        plan = plan_role_assembly_from_dpad_roster(
            ["A", "C"],
            {"roles": ["A", "B", "C"], "duplicates": [], "unrecognized": []},
        )

        first_actions = plan["plans"][0]["action_sequence"]
        second_actions = plan["plans"][1]["action_sequence"]
        self.assertEqual(["A", "C"], plan["planned_roles"])
        self.assertEqual("sidebar_then_rs_role_list_grid", plan["navigation"])
        self.assertEqual("sidebar_dpad", plan["plans"][0]["navigation"])
        self.assertEqual("rs_role_list_grid", plan["plans"][1]["navigation"])
        self.assertEqual(
            [
                "role_dpad_previous",
                "role_dpad_previous",
                "left_kongmu_tab",
                "wait_after_left_kongmu_tab",
                "assemble_button",
                "wait_after_assemble_button",
                "assemble_current_role_from_blueprint",
                "assembly_back_to_role_page",
            ],
            [a["name"] for a in first_actions],
        )
        self.assertEqual(["dpad_up", "dpad_up", "b"], [a["gamepad_button"] for a in first_actions if "gamepad_button" in a])
        self.assertEqual(["rs", "left_right", "left_right", "a", "b", "b"], [a.get("gamepad_button") or a.get("gamepad_stick") for a in second_actions if "gamepad_button" in a or "gamepad_stick" in a])
        self.assertEqual("assembly_back_to_role_page", first_actions[-1]["name"])
        self.assertEqual("b", first_actions[-1]["gamepad_button"])
        self.assertEqual(
            [
                "open_role_list",
                "role_list_next",
                "role_list_next",
                "confirm_role_list_selection",
                "close_role_list_after_confirmation",
                "left_kongmu_tab",
                "wait_after_left_kongmu_tab",
                "assemble_button",
                "wait_after_assemble_button",
                "assemble_current_role_from_blueprint",
                "assembly_back_to_role_page",
            ],
            [a["name"] for a in second_actions],
        )

    def test_plans_first_assembly_from_open_rs_role_list_then_uses_rs_for_later_roles(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_role_list_roster

        plan = plan_role_assembly_from_role_list_roster(
            ["C", "A", "B"],
            {
                "roles": ["A", "B", "C"],
                "role_positions": {"A": 0, "B": 1, "C": 2},
                "current_index": 2,
                "list_open": True,
                "stop_reason": "all_required_roles_found",
            },
        )

        first_actions = plan["plans"][0]["action_sequence"]
        second_actions = plan["plans"][1]["action_sequence"]
        third_actions = plan["plans"][2]["action_sequence"]

        self.assertEqual(["C", "B", "A"], plan["planned_roles"])
        self.assertEqual("rs_role_list_scan_then_reverse_left", plan["navigation"])
        self.assertEqual("role_list_reverse_left_from_open", plan["plans"][0]["navigation"])
        self.assertEqual("rs_role_list_reverse_left", plan["plans"][1]["navigation"])
        self.assertEqual(["a", "b", "b"], [
            action.get("gamepad_button") or action.get("gamepad_stick")
            for action in first_actions
            if "gamepad_button" in action or "gamepad_stick" in action
        ])
        self.assertEqual(["rs", "left_left", "a", "b", "b"], [
            action.get("gamepad_button") or action.get("gamepad_stick")
            for action in second_actions
            if "gamepad_button" in action or "gamepad_stick" in action
        ])
        self.assertEqual(["rs", "left_left", "a", "b", "b"], [
            action.get("gamepad_button") or action.get("gamepad_stick")
            for action in third_actions
            if "gamepad_button" in action or "gamepad_stick" in action
        ])
        self.assertEqual("all_required_roles_found", plan["scan_stop_reason"])

    def test_later_page_first_target_resets_each_later_selection_to_grid_origin(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_role_list_roster

        plan = plan_role_assembly_from_role_list_roster(
            ["N", "O"],
            {
                "roles": list("ABCDEFGHIJKLMNO"),
                "role_positions": {role: index for index, role in enumerate("ABCDEFGHIJKLMNO")},
                "current_index": 14,
                "list_open": True,
            },
        )

        first, second = plan["plans"]
        self.assertEqual(["O", "N"], plan["planned_roles"])
        self.assertEqual(1, plan["first_target_page"])
        self.assertTrue(plan["reset_until_first_page_target"])
        self.assertEqual("role_list_reverse_left_from_open", first["navigation"])
        self.assertEqual("rs_role_list_reset_then_grid", second["navigation"])
        self.assertEqual(
            ["left_left", "left_down", "left_down", "left_down", "left_down", "left_right"],
            [action["gamepad_stick"] for action in second["action_sequence"] if "gamepad_stick" in action],
        )

    def test_second_page_start_uses_grid_until_a_first_page_role_is_assembled(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_role_list_roster

        plan = plan_role_assembly_from_role_list_roster(
            ["A", "B", "N", "O"],
            {
                "roles": list("ABCDEFGHIJKLMNO"),
                "role_positions": {role: index for index, role in enumerate("ABCDEFGHIJKLMNO")},
                "current_index": 14,
                "list_open": True,
            },
        )

        first, second, third, fourth = plan["plans"]
        self.assertEqual(["O", "N", "B", "A"], plan["planned_roles"])
        self.assertEqual("role_list_reverse_left_from_open", first["navigation"])
        self.assertEqual("rs_role_list_reset_then_grid", second["navigation"])
        self.assertEqual("rs_role_list_reset_then_grid", third["navigation"])
        self.assertEqual("rs_role_list_reverse_left", fourth["navigation"])

    def test_role_list_scan_defensively_pushes_left_four_times_after_opening(self):
        from src.features.drive_assembly.role_flow import collect_role_roster_from_role_list

        observations = iter(["A", "B"])
        inputs = []
        roster = collect_role_roster_from_role_list(
            ["A", "B"],
            current_observer=lambda _index: next(observations),
            press_up=lambda: None,
            open_role_list=lambda: inputs.append("rs"),
            confirm_selection=lambda: None,
            move_right=lambda: None,
            move_left=lambda: inputs.append("left"),
            reset_up_count=0,
            max_roles=4,
        )

        self.assertEqual(["rs", "left", "left", "left", "left"], inputs)
        self.assertEqual(4, roster["initial_left_reset_count"])

    def test_rs_role_list_plan_moves_only_left_in_reverse_roster_order(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_role_list_roster

        plan = plan_role_assembly_from_role_list_roster(
            ["A", "C", "E", "F"],
            {
                "roles": ["A", "B", "C", "D", "E", "F"],
                "role_positions": {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5},
                "current_index": 5,
                "list_open": True,
            },
        )

        self.assertEqual(["F", "E", "C", "A"], plan["planned_roles"])
        self.assertEqual([5, 4, 2, 0], [item["roster_index"] for item in plan["plans"]])
        self.assertEqual(
            [[], ["left_left"], ["left_left", "left_left"], ["left_left", "left_left"]],
            [
                [action["gamepad_stick"] for action in item["action_sequence"] if "gamepad_stick" in action]
                for item in plan["plans"]
            ],
        )

    def test_orders_role_assembly_by_roster_index_to_avoid_backtracking(self):
        from src.features.drive_assembly.role_flow import plan_role_assembly_from_dpad_roster

        plan = plan_role_assembly_from_dpad_roster(
            ["C", "A", "B"],
            {
                "roles": ["A", "B", "C"],
                "role_positions": {"A": 0, "B": 1, "C": 2},
                "current_index": 2,
            },
        )

        self.assertEqual(["A", "B", "C"], plan["planned_roles"])
        self.assertEqual([0, 1, 2], [item["roster_index"] for item in plan["plans"]])
        self.assertEqual(["dpad_up", "dpad_up"], [
            action["gamepad_button"]
            for action in plan["plans"][0]["action_sequence"][:2]
        ])
        self.assertEqual(["rs", "left_right", "a", "b", "b"], [
            action.get("gamepad_button") or action.get("gamepad_stick")
            for action in plan["plans"][1]["action_sequence"]
            if "gamepad_button" in action or "gamepad_stick" in action
        ])
        self.assertEqual(["rs", "left_right", "a", "b", "b"], [
            action.get("gamepad_button") or action.get("gamepad_stick")
            for action in plan["plans"][2]["action_sequence"]
            if "gamepad_button" in action or "gamepad_stick" in action
        ])


if __name__ == "__main__":
    unittest.main()
