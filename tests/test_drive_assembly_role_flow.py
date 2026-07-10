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

        self.assertEqual((176, 581), controls["left_kongmu_tab"])
        self.assertEqual((2160, 1322), controls["assemble_button"])
        self.assertEqual(
            [
                {"name": "left_kongmu_tab", "position": (176, 581)},
                {"name": "assemble_button", "position": (2160, 1322)},
            ],
            controls["entry_sequence"],
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

    def test_scales_role_navigation_to_other_screen(self):
        from src.features.drive_assembly.role_flow import map_role_navigation_controls, map_role_slots

        controls = map_role_navigation_controls(screen_size=(1280, 720))
        slots = map_role_slots(screen_size=(1280, 720))

        self.assertEqual((88, 291), controls["left_kongmu_tab"])
        self.assertEqual((1080, 661), controls["assemble_button"])
        self.assertEqual((1205, 121), slots[0])

    def test_maps_role_slot_template_regions(self):
        from src.features.drive_assembly.role_flow import map_role_slot_template_regions

        regions = map_role_slot_template_regions()

        self.assertEqual(5, len(regions))
        self.assertEqual((2290, 122, 2530, 362), regions[0])
        self.assertEqual((2290, 1032, 2530, 1272), regions[4])

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
        self.assertEqual([2, 3, 4], [item["slot_index"] for item in plan["plans"]])
        self.assertEqual([(2410, 697), (2410, 925), (2410, 1152)], [item["action_sequence"][3]["position"] for item in plan["plans"]])
        self.assertEqual(
            ["role_scroll_reset_to_first_page", "role_scroll_next_page", "role_scroll_next_page", "role_slot"],
            [action["name"] for action in plan["plans"][0]["action_sequence"][:4]],
        )


if __name__ == "__main__":
    unittest.main()
