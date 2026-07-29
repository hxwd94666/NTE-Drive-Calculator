# 验证角色识别、去重遍历和逐角色装配计划。
"""Tests for role recognition and role-by-role assembly planning."""

import unittest



class DriveAssemblyRolePlanningTests(unittest.TestCase):
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
