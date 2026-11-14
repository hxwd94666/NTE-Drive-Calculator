# 验证已保存驱动盘矩阵可转换为装配块坐标数据。
"""Tests for extracting assembly blocks from saved equipped_state data."""

import unittest


class DriveAssemblyFilterMappingTests(unittest.TestCase):
    def test_maps_assembly_page_prepare_controls(self):
        from src.features.drive_assembly.page_mapping import map_assembly_page_prepare_controls

        controls = map_assembly_page_prepare_controls()

        self.assertEqual((1524, 1252), controls["unload_existing_drives"])
        self.assertEqual((1546, 953), controls["unload_prompt_confirm"])
        self.assertEqual((1280, 690), controls["unload_prompt_probe"])
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
            controls["prepare_sequence"],
        )

    def test_maps_drive_shape_selection_by_drive_type(self):
        from src.features.drive_assembly.page_mapping import map_drive_shape_selection

        selection = map_drive_shape_selection("V_3")

        self.assertEqual((2067, 540), selection["shape_select"])
        self.assertEqual((948, 745), selection["shape_option"])
        self.assertEqual((1564, 1186), selection["confirm_filter"])
        self.assertEqual(
            [
                {"name": "shape_select", "position": (2067, 540)},
                {"name": "wait_after_drive_shape_dialog_open", "wait_seconds": 0.5},
                {"name": "shape_option", "drive_type": "V_3", "position": (948, 745)},
                {"name": "confirm_shape_filter", "position": (1564, 1186)},
                {"name": "wait_after_drive_shape_dialog_close", "wait_seconds": 0.8},
            ],
            selection["selection_sequence"],
        )

    def test_maps_legacy_drive_shape_alias_to_config_shape_id(self):
        from src.features.drive_assembly.page_mapping import map_drive_shape_selection

        selection = map_drive_shape_selection("L_3")

        self.assertEqual("L_3_BL", selection["drive_type"])
        self.assertEqual((1095, 745), selection["shape_option"])

    def test_maps_drive_filter_refinement_and_sub_stats(self):
        from src.features.drive_assembly.page_mapping import map_drive_filter_refinement

        controls = map_drive_filter_refinement(["Gold"], ["暴击率%", "攻击力"])

        self.assertEqual((2273, 765), controls["status_locked"])
        self.assertEqual((1861, 851), controls["status_discarded"])
        self.assertEqual((1861, 1075), controls["quality_orange"])
        self.assertEqual((2067, 890), controls["sub_stat_expand"])
        self.assertEqual((1861, 721), controls["sub_stat_options"]["暴击率"])
        self.assertEqual((1861, 636), controls["sub_stat_options"]["攻击力"])
        self.assertEqual((1861, 1202), controls["sub_stat_count_four"])
        self.assertEqual(
            [
                "status_locked",
                "status_discarded",
                "status_other",
                "quality_orange",
                "verify_quality_selected",
                "drive_filter_scroll_to_bottom",
                "sub_stat_expand",
                "wait_after_drive_sub_stat_expand",
                "drive_sub_stat_scroll_to_bottom",
                "sub_stat_option",
                "sub_stat_option",
                "sub_stat_count_four",
            ],
            [step["name"] for step in controls["refinement_sequence"]],
        )

    def test_drive_block_filter_uses_shape_then_one_scroll_per_filter_stage(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        block = {
            "block_id": 9,
            "drive_type": "V_3",
            "pixel_position": (1205, 548),
            "drive": {"quality": "Gold", "sub_stats": {"暴击率%": 10.0, "攻击力": 80}},
        }

        install = map_drive_block_installation(block, open_filter=True)
        names = [step["name"] for step in install["install_sequence"]]
        expected_order = [
            "filter_button",
            "reset_filter",
            "shape_select",
            "wait_after_drive_shape_dialog_open",
            "shape_option",
            "confirm_shape_filter",
            "wait_after_drive_shape_dialog_close",
            "quality_orange",
            "verify_quality_selected",
            "drive_filter_scroll_to_bottom",
            "sub_stat_expand",
            "drive_sub_stat_scroll_to_bottom",
            "sub_stat_option",
            "sub_stat_count_four",
            "confirm_filter",
            "force_drag_first_drive_to_block",
        ]

        indexes = [names.index(name) for name in expected_order]
        self.assertEqual(sorted(indexes), indexes)
        self.assertEqual(1, names.count("drive_filter_scroll_to_bottom"))
        self.assertEqual(1, names.count("drive_sub_stat_scroll_to_bottom"))

    def test_maps_drive_block_installation_to_precomputed_pixel_position(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        block = {
            "block_id": 3,
            "drive_type": "L_3_BL",
            "pixel_position": (1205, 393),
            "drive": {"quality": "Gold", "sub_stats": {"暴击率%": 10.0, "攻击力": 80}},
        }

        install = map_drive_block_installation(block)

        self.assertEqual("L_3_BL", install["drive_type"])
        self.assertEqual((1095, 745), install["shape_option"])
        self.assertEqual((126, 430), install["first_drive"])
        self.assertEqual((1205, 393), install["target_position"])
        self.assertNotIn("status_locked", [step["name"] for step in install["install_sequence"]])
        self.assertNotIn("status_discarded", [step["name"] for step in install["install_sequence"]])
        self.assertNotIn("status_other", [step["name"] for step in install["install_sequence"]])
        self.assertEqual(
            {"name": "reset_filter", "position": (1861, 1322), "post_action_pause_seconds": 0.6},
            install["install_sequence"][0],
        )
        drag_index = next(
            index
            for index, step in enumerate(install["install_sequence"])
            if step["name"] == "force_drag_first_drive_to_block"
        )
        self.assertEqual(
            {"name": "force_drag_first_drive_to_block", "block_id": 3, "from": (126, 430), "to": (1205, 393), "duration_ms": 1200},
            install["install_sequence"][drag_index],
        )
        self.assertEqual({"name": "wait_for_equipment_reuse_prompt", "wait_seconds": 0.3}, install["install_sequence"][drag_index + 1])
        self.assertEqual(
            {
                "name": "confirm_equipment_reuse_prompt",
                "block_id": 3,
                "optional_confirm_position": (1546, 953),
                "modal_probe_position": (1280, 690),
                "brightness_threshold": 150,
            },
            install["install_sequence"][drag_index + 2],
        )
        self.assertEqual({"name": "wait_after_drive_block_install", "wait_seconds": 1.0}, install["install_sequence"][drag_index + 3])
        verify = install["install_sequence"][drag_index + 4]
        self.assertEqual("verify_drive_block_installed", verify["name"])
        self.assertEqual((1205, 393), verify["target_position"])

    def test_duplicate_drive_block_installation_filters_non_equipped_statuses(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        block = {
            "block_id": 4,
            "drive_type": "H_2",
            "pixel_position": (1112, 362),
            "is_duplicate_drive": True,
            "drive": {"quality": "Gold", "sub_stats": {"暴击率%": 10.0}},
        }

        install = map_drive_block_installation(block)
        sequence_names = [step["name"] for step in install["install_sequence"]]

        self.assertIn("status_locked", sequence_names)
        self.assertIn("status_discarded", sequence_names)
        self.assertIn("status_other", sequence_names)
        self.assertTrue(install["duplicate_status_filter_enabled"])
        status_steps = [step for step in install["install_sequence"] if step["name"].startswith("status_")]
        self.assertEqual([4, 4, 4], [step["block_id"] for step in status_steps])
        self.assertTrue(all(step["duplicate_status_filter"] for step in status_steps))
        self.assertLess(sequence_names.index("shape_option"), sequence_names.index("status_locked"))
        self.assertLess(sequence_names.index("status_other"), sequence_names.index("quality_orange"))
        self.assertFalse(any(name.startswith("set_") for name in sequence_names))

    def test_duplicate_group_metadata_enables_drive_status_filters(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        install = map_drive_block_installation(
            {
                "block_id": 9,
                "drive_type": "H_2",
                "cells": [(1, 1), (1, 2)],
                "duplicate_count": 2,
                "drive": {"quality": "Gold", "sub_stats": {}},
            }
        )

        self.assertTrue(install["duplicate_status_filter_enabled"])
        self.assertEqual(
            ["status_locked", "status_discarded", "status_other"],
            [step["name"] for step in install["install_sequence"] if step["name"].startswith("status_")],
        )

    def test_each_duplicate_drive_resets_filters_before_status_selection(self):
        from src.features.drive_assembly.page_mapping import map_drive_blocks_installation

        blocks = [
            {
                "block_id": block_id,
                "drive_type": "H_2",
                "cells": [(1, 1), (1, 2)],
                "is_duplicate_drive": True,
                "drive": {"uid": "drive-shared", "quality": "Gold", "sub_stats": {}},
            }
            for block_id in (1, 2)
        ]

        plan = map_drive_blocks_installation(blocks)

        for install in plan["install_plans"]:
            names = [step["name"] for step in install["install_sequence"]]
            self.assertLess(names.index("reset_filter"), names.index("status_locked"))
            self.assertEqual(1, names.count("reset_filter"))

    def test_maps_drive_block_installation_from_cells_when_pixel_position_missing(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        block = {
            "block_id": 1,
            "drive_type": "H_2",
            "cells": [(1, 1), (1, 2)],
            "drive": {"quality": "Purple", "sub_stats": {"暴击伤害%": 20.0}},
        }

        install = map_drive_block_installation(block, screen_size=(1280, 720))

        self.assertEqual((400, 244), install["shape_option"])
        self.assertEqual((63, 215), install["first_drive"])
        self.assertEqual((564, 181), install["target_position"])

    def test_rejects_unknown_drive_shape_selection(self):
        from src.features.drive_assembly.page_mapping import map_drive_shape_selection

        with self.assertRaisesRegex(ValueError, "未知驱动块外形"):
            map_drive_shape_selection("UNKNOWN")

    def test_maps_multiple_drive_blocks_as_separate_installations(self):
        from src.features.drive_assembly.page_mapping import map_drive_blocks_installation

        blocks = [
            {
                "block_id": 1,
                "drive_type": "H_2",
                "pixel_position": (1112, 362),
                "drive": {"quality": "Gold", "sub_stats": {"暴击率%": 10.0}},
            },
            {
                "block_id": 2,
                "drive_type": "V_3",
                "pixel_position": (1205, 548),
                "drive": {"quality": "Purple", "sub_stats": {"攻击力": 80}},
            },
        ]

        plan = map_drive_blocks_installation(blocks)

        self.assertEqual([1, 2], [install["block_id"] for install in plan["install_plans"]])
        self.assertEqual(2, len(plan["install_plans"]))
        self.assertEqual((1112, 362), plan["install_plans"][0]["target_position"])
        self.assertEqual((1205, 548), plan["install_plans"][1]["target_position"])
        self.assertEqual(
            [
                {"name": "drive_tab", "position": (554, 309), "post_action_pause_seconds": 0.6},
                {"name": "install_drive_block", "block_id": 1, "sequence_index": 0},
                {"name": "install_drive_block", "block_id": 2, "sequence_index": 1},
            ],
            plan["assembly_sequence"],
        )

    def test_drive_block_installation_resets_filter_without_selecting_a_set(self):
        from src.features.drive_assembly.page_mapping import map_drive_blocks_installation

        blocks = [
            {
                "block_id": 1,
                "drive_type": "H_2",
                "pixel_position": (1112, 362),
                "drive": {"quality": "Gold", "set_name": "失落光芒", "sub_stats": {}},
            },
            {
                "block_id": 2,
                "drive_type": "V_2",
                "pixel_position": (1205, 548),
                "drive": {"quality": "Gold", "sub_stats": {}},
            },
        ]

        plan = map_drive_blocks_installation(blocks)

        first_sequence = plan["install_plans"][0]["install_sequence"]
        second_sequence = plan["install_plans"][1]["install_sequence"]
        self.assertEqual("filter_button", first_sequence[0]["name"])
        self.assertEqual("reset_filter", first_sequence[1]["name"])
        self.assertEqual("shape_select", first_sequence[2]["name"])
        self.assertEqual("wait_after_drive_shape_dialog_open", first_sequence[3]["name"])
        self.assertEqual("quality_orange", first_sequence[7]["name"])
        self.assertFalse(any(step["name"].startswith("set_") for step in first_sequence))
        self.assertEqual("filter_button", second_sequence[0]["name"])
        self.assertEqual("reset_filter", second_sequence[1]["name"])
        self.assertEqual("shape_select", second_sequence[2]["name"])
        self.assertEqual("wait_after_drive_shape_dialog_open", second_sequence[3]["name"])
        self.assertEqual("quality_orange", second_sequence[7]["name"])
        self.assertFalse(any(step["name"].startswith("set_") for step in second_sequence))

    def test_maps_filter_open_before_reset_for_every_drive_block(self):
        from src.features.drive_assembly.page_mapping import map_drive_blocks_installation

        blocks = [
            {
                "block_id": 1,
                "drive_type": "H_2",
                "pixel_position": (1112, 362),
                "drive": {"quality": "Gold", "sub_stats": {}},
            },
            {
                "block_id": 2,
                "drive_type": "V_2",
                "pixel_position": (1205, 548),
                "drive": {"quality": "Purple", "sub_stats": {}},
            },
        ]

        plan = map_drive_blocks_installation(blocks)

        self.assertEqual("drive_tab", plan["assembly_sequence"][0]["name"])
        for install in plan["install_plans"]:
            self.assertEqual("filter_button", install["install_sequence"][0]["name"])
            self.assertEqual("reset_filter", install["install_sequence"][1]["name"])

if __name__ == "__main__":
    unittest.main()
