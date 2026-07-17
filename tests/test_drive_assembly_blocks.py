# 验证已保存驱动盘矩阵可转换为装配块坐标数据。
"""Tests for extracting assembly blocks from saved equipped_state data."""

import json
import tempfile
import unittest
from pathlib import Path


class DriveAssemblyBlockTests(unittest.TestCase):
    def test_extracts_numbered_blocks_with_board_relative_offsets(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "blueprint_priority": 7,
                "total_score": 88.8,
                "blueprint_layout": [
                    ["0", "A", "A", "XX", "C"],
                    ["B", "A", "0", "C", "C"],
                    ["B", "0", "D", "D", "0"],
                    ["0", "0", "0", "D", "0"],
                    ["E", "E", "0", "0", "0"],
                ],
                "equipped_drives": [
                    {"uid": "drive-a", "shape_id": "H_2"},
                    {"uid": "drive-c", "shape_id": "L_3"},
                    {"uid": "drive-b", "shape_id": "V_2"},
                    {"uid": "drive-d", "shape_id": "S_4"},
                    {"uid": "drive-e", "shape_id": "I_2"},
                ],
            }
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual(["A", "C", "B", "D", "E"], [block["matrix_name"] for block in blocks])
        self.assertEqual([1, 2, 3, 4, 5], [block["block_id"] for block in blocks])
        self.assertEqual([(1, 2), (1, 3), (2, 2)], blocks[0]["cells"])
        self.assertEqual((1, 2), blocks[0]["top_left"])
        self.assertEqual(0, blocks[0]["left_count"])
        self.assertEqual(0, blocks[0]["up_count"])
        self.assertEqual((1, 5), blocks[1]["top_left"])
        self.assertEqual(2, blocks[1]["left_count"])
        self.assertEqual(0, blocks[1]["up_count"])
        self.assertEqual((2, 1), blocks[2]["top_left"])
        self.assertEqual(0, blocks[2]["left_count"])
        self.assertEqual(0, blocks[2]["up_count"])
        self.assertEqual((3, 3), blocks[3]["top_left"])
        self.assertEqual(1, blocks[3]["left_count"])
        self.assertEqual(1, blocks[3]["up_count"])
        self.assertEqual("drive-d", blocks[3]["drive"]["uid"])
        self.assertEqual("角色A", blocks[0]["blueprint_role_name"])
        self.assertNotIn("blueprint_priority", blocks[0])
        self.assertEqual("H_2", blocks[0]["drive_type"])
        self.assertEqual("S_4", blocks[3]["drive_type"])

    def test_loads_blocks_from_equipped_state_file(self):
        from src.features.drive_assembly.blocks import load_drive_blocks

        state = {
            "角色A": {
                "blueprint_layout": [["X", "Z"], ["X", "Y"]],
                "equipped_drives": [{"uid": "drive-x"}, {"uid": "drive-y"}],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "equipped_state.json"
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            blocks = load_drive_blocks(path)

        self.assertEqual(["X", "Z", "Y"], [block["matrix_name"] for block in blocks])
        self.assertEqual((1, 1), blocks[0]["top_left"])
        self.assertEqual((2, 2), blocks[2]["top_left"])
        self.assertEqual(1, blocks[2]["left_count"])
        self.assertEqual(1, blocks[2]["up_count"])
        self.assertEqual("Y", blocks[2]["drive_type"])

    def test_matches_blueprint_shape_groups_to_saved_drives_by_shape_id(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "blueprint_layout": [["L_3_TR", "L_3_TR", "V_2"], ["L_3_TR", "XX", "V_2"]],
                "equipped_drives": [
                    {"uid": "vertical", "shape_id": "V_2", "quality": "Purple"},
                    {"uid": "corner", "shape_id": "L_3_TR", "quality": "Gold"},
                ],
            }
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual(["L_3_TR", "V_2"], [block["matrix_name"] for block in blocks])
        self.assertEqual(["corner", "vertical"], [block["drive"]["uid"] for block in blocks])
        self.assertEqual(["Gold", "Purple"], [block["drive"]["quality"] for block in blocks])

    def test_skips_empty_replacement_slots_during_game_assembly(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "blueprint_layout": [["H_2", "H_2", "V_2"], ["XX", "XX", "V_2"]],
                "equipped_drives": [
                    {"uid": "empty_taken_h2", "shape_id": "H_2"},
                    {"uid": "real_v2", "shape_id": "V_2"},
                ],
            }
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual(["real_v2"], [block["drive"]["uid"] for block in blocks])
        self.assertEqual(["V_2"], [block["matrix_name"] for block in blocks])

    def test_splits_multiple_same_name_h2_shapes_into_independent_blocks(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state
        from src.features.drive_assembly.page_mapping import map_blocks_to_page

        state = {
            "角色A": {
                "blueprint_layout": [
                    ["XX", "XX", "XX", "XX", "XX"],
                    ["XX", "XX", "XX", "H_2", "H_2"],
                    ["XX", "XX", "XX", "XX", "XX"],
                    ["XX", "XX", "XX", "XX", "XX"],
                    ["XX", "XX", "XX", "H_2", "H_2"],
                ],
                "equipped_drives": [
                    {"uid": "upper", "shape_id": "H_2"},
                    {"uid": "lower", "shape_id": "H_2"},
                ],
            }
        }

        blocks = extract_drive_blocks_from_state(state)
        mapped = map_blocks_to_page(blocks)

        self.assertEqual(["H_2", "H_2"], [block["matrix_name"] for block in blocks])
        self.assertEqual([[(2, 4), (2, 5)], [(5, 4), (5, 5)]], [block["cells"] for block in blocks])
        self.assertEqual(["upper", "lower"], [block["drive"]["uid"] for block in blocks])
        self.assertEqual([(1406, 455), (1406, 734)], [block["pixel_position"] for block in mapped])

    def test_extracts_tape_filter_from_equipped_state_blueprint(self):
        from src.features.drive_assembly.blocks import extract_tape_filters_from_state

        state = {
            "角色A": {
                "equipped_tape": {
                    "uid": "tape-a",
                    "set_name": "迪亚波罗斯",
                    "main_stats": {"攻击力百分比": 30.0},
                    "sub_stats": {"暴击率%": 10.0, "攻击力%": 12.5},
                    "quality": "Gold",
                }
            }
        }

        filters = extract_tape_filters_from_state(state)

        self.assertEqual(
            [
                {
                    "role_name": "角色A",
                    "blueprint_role_name": "角色A",
                    "set_name": "迪亚波罗斯",
                    "main_stat": "攻击力百分比",
                    "sub_stats": ["暴击率%", "攻击力%"],
                    "quality": "Gold",
                    "tape": state["角色A"]["equipped_tape"],
                }
            ],
            filters,
        )

    def test_loads_tape_filters_from_equipped_state_file(self):
        from src.features.drive_assembly.blocks import load_tape_filters

        state = {
            "角色A": {
                "equipped_tape": {
                    "set_name": "森林萤火之心",
                    "main_stats": "暴击率",
                    "sub_stats": {"暴击伤害%": 20.0},
                    "quality": "Purple",
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "equipped_state.json"
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            filters = load_tape_filters(path)

        self.assertEqual("森林萤火之心", filters[0]["set_name"])
        self.assertEqual("暴击率", filters[0]["main_stat"])
        self.assertEqual(["暴击伤害%"], filters[0]["sub_stats"])
        self.assertEqual("Purple", filters[0]["quality"])

    def test_prefers_saved_drive_type_field_when_present(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "blueprint_layout": [["A"]],
                "equipped_drives": [{"uid": "drive-a", "drive_type": "custom-type", "shape_id": "H_2"}],
            }
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual("custom-type", blocks[0]["drive_type"])

    def test_marks_duplicate_drive_blocks_by_equipment_content(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        same_a = {
            "uid": "drive-a",
            "shape_id": "H_2",
            "quality": "Gold",
            "main_stats": {"m1": 1.0, "m2": 2.0},
            "sub_stats": {"s1": 10.0, "s2": 20.0},
            "role_scores": {"A": 99.0},
            "max_score": 99.0,
        }
        same_b = {
            "uid": "drive-b",
            "shape_id": "H_2",
            "quality": "Gold",
            "main_stats": {"m2": 2.0, "m1": 1.0},
            "sub_stats": {"s2": 20.0, "s1": 10.0},
            "role_scores": {"B": 1.0},
            "pick_order": 3,
        }
        different = {
            "uid": "drive-c",
            "shape_id": "V_2",
            "quality": "Gold",
            "main_stats": {"m1": 1.0, "m2": 2.0},
            "sub_stats": {"s1": 10.0, "s2": 20.0},
        }
        state = {
            "role-a": {
                "blueprint_layout": [["A", "B", "C"]],
                "equipped_drives": [same_a, same_b, different],
            }
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual("drive_dup_001", blocks[0]["duplicate_group_id"])
        self.assertEqual("drive_dup_001", blocks[1]["duplicate_group_id"])
        self.assertEqual([1, 2], [blocks[0]["duplicate_index"], blocks[1]["duplicate_index"]])
        self.assertEqual([2, 2], [blocks[0]["duplicate_count"], blocks[1]["duplicate_count"]])
        self.assertTrue(blocks[0]["is_duplicate_equipment"])
        self.assertTrue(blocks[0]["is_duplicate_drive"])
        self.assertEqual(blocks[0]["equipment_signature"], blocks[1]["equipment_signature"])
        self.assertNotIn("duplicate_group_id", blocks[2])
        self.assertNotIn("is_duplicate_equipment", blocks[2])

    def test_marks_duplicate_tape_filters_by_equipment_content(self):
        from src.features.drive_assembly.blocks import extract_tape_filters_from_state

        state = {
            "role-a": {
                "equipped_tape": {
                    "uid": "tape-a",
                    "set_name": "set-a",
                    "main_stats": {"main": 30.0},
                    "sub_stats": {"s1": 1.0, "s2": 2.0},
                    "quality": "Gold",
                    "max_score": 99.0,
                }
            },
            "role-b": {
                "equipped_tape": {
                    "uid": "tape-b",
                    "set_name": "set-a",
                    "main_stats": "main",
                    "sub_stats": {"s2": 2.0, "s1": 1.0},
                    "quality": "Gold",
                    "pick_order": 2,
                }
            },
            "role-c": {
                "equipped_tape": {
                    "uid": "tape-c",
                    "set_name": "set-a",
                    "main_stats": "other-main",
                    "sub_stats": {"s1": 1.0, "s2": 2.0},
                    "quality": "Gold",
                }
            },
        }

        filters = extract_tape_filters_from_state(state)

        self.assertEqual("tape_dup_001", filters[0]["duplicate_group_id"])
        self.assertEqual("tape_dup_001", filters[1]["duplicate_group_id"])
        self.assertEqual([1, 2], [filters[0]["duplicate_index"], filters[1]["duplicate_index"]])
        self.assertEqual([2, 2], [filters[0]["duplicate_count"], filters[1]["duplicate_count"]])
        self.assertTrue(filters[0]["is_duplicate_equipment"])
        self.assertTrue(filters[0]["is_duplicate_tape"])
        self.assertEqual(filters[0]["equipment_signature"], filters[1]["equipment_signature"])
        self.assertNotIn("duplicate_group_id", filters[2])

    def test_omits_blueprint_priority_even_when_score_or_priority_exists(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "total_score": 42.5,
                "blueprint_layout": [["A"]],
                "equipped_drives": [{"uid": "drive-a"}],
            },
            "角色B": {
                "score": 12.0,
                "priority": 9,
                "blueprint_layout": [["B"]],
                "equipped_drives": [{"uid": "drive-b"}],
            },
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual(["角色A", "角色B"], [block["blueprint_role_name"] for block in blocks])
        self.assertNotIn("blueprint_priority", blocks[0])
        self.assertNotIn("blueprint_priority", blocks[1])

    def test_uses_empty_top_left_anchor_for_trap_v_and_h_blocks(self):
        from src.features.drive_assembly.blocks import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "blueprint_layout": [
                    ["0", "P", "0", "0", "0"],
                    ["P", "0", "H", "H", "0"],
                    ["0", "H", "H", "0", "V"],
                    ["0", "0", "0", "V", "V"],
                    ["0", "0", "0", "V", "0"],
                ],
                "equipped_drives": [
                    {"uid": "prefix", "shape_id": "V_2"},
                    {"uid": "trap-h", "shape_id": "Trap_4_H"},
                    {"uid": "trap-v", "shape_id": "Trap_4_V"},
                ],
            }
        }

        blocks = extract_drive_blocks_from_state(state)
        trap_h = blocks[1]
        trap_v = blocks[2]

        self.assertEqual("Trap_4_H", trap_h["drive_type"])
        self.assertEqual((2, 2), trap_h["top_left"])
        self.assertEqual(1, trap_h["left_count"])
        self.assertEqual(1, trap_h["up_count"])
        self.assertEqual("Trap_4_V", trap_v["drive_type"])
        self.assertEqual((3, 4), trap_v["top_left"])
        self.assertEqual(2, trap_v["left_count"])
        self.assertEqual(1, trap_v["up_count"])

    def test_maps_block_centroids_to_default_2k_page_pixels(self):
        from src.features.drive_assembly.page_mapping import map_blocks_to_page

        blocks = [
            {
                "block_id": 1,
                "cells": [(1, 2), (1, 3), (2, 2)],
                "top_left": (1, 2),
            }
        ]

        mapped = map_blocks_to_page(blocks)

        self.assertEqual((1.333333, 2.333333), mapped[0]["shape_centroid"])
        self.assertEqual((1.333333, 2.333333), mapped[0]["grid_centroid"])
        self.assertEqual((1205, 393), mapped[0]["pixel_position"])
        self.assertEqual({"label": "1", "position": (1205, 393)}, mapped[0]["centroid_marker"])
        self.assertEqual((1034, 315), mapped[0]["board_origin"])
        self.assertEqual((93, 93), mapped[0]["cell_size"])

    def test_scales_page_mapping_to_other_16_9_screen_sizes(self):
        from src.features.drive_assembly.page_mapping import map_blocks_to_page

        blocks = [{"block_id": 1, "cells": [(1, 1)], "top_left": (1, 1)}]

        mapped = map_blocks_to_page(blocks, screen_size=(1280, 720))

        self.assertEqual((540, 181), mapped[0]["pixel_position"])
        self.assertEqual((517, 158), mapped[0]["board_origin"])
        self.assertEqual((46.5, 46.5), mapped[0]["cell_size"])

    def test_page_mapping_keeps_16_10_game_controls_top_aligned(self):
        from src.features.drive_assembly.page_mapping import map_blocks_to_page, map_page_controls

        blocks = [{"block_id": 1, "cells": [(1, 2), (1, 3), (2, 2)], "top_left": (1, 2)}]

        mapped = map_blocks_to_page(blocks, screen_size=(2560, 1600))

        self.assertEqual((1205, 393), mapped[0]["pixel_position"])
        self.assertEqual((111, 1347), map_page_controls(screen_size=(2560, 1600))["filter_button"])

    def test_page_mapping_uses_content_rect_offsets_for_windowed_clients(self):
        from src.features.drive_assembly.page_mapping import map_blocks_to_page

        blocks = [{"block_id": 1, "cells": [(1, 1)], "top_left": (1, 1)}]

        mapped = map_blocks_to_page(blocks, screen_size=(1920, 1080), content_rect=(10, 20, 1280, 720))

        self.assertEqual((550, 201), mapped[0]["pixel_position"])
        self.assertEqual((527, 178), mapped[0]["board_origin"])
        self.assertEqual((46.5, 46.5), mapped[0]["cell_size"])

    def test_maps_tape_tab_and_filter_button_controls(self):
        from src.features.drive_assembly.page_mapping import map_page_controls

        controls = map_page_controls()

        self.assertEqual((240, 309), controls["tape_tab"])
        self.assertEqual((111, 1347), controls["filter_button"])
        self.assertEqual(
            [
                {"name": "tape_tab", "position": (240, 309)},
                {"name": "filter_button", "position": (111, 1347)},
            ],
            controls["click_sequence"],
        )

    def test_scales_tape_filter_controls_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_page_controls

        controls = map_page_controls(screen_size=(1280, 720))

        self.assertEqual((120, 155), controls["tape_tab"])
        self.assertEqual((56, 674), controls["filter_button"])

    def test_maps_set_filter_select_control(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_controls

        controls = map_tape_filter_controls()

        self.assertEqual((2067, 393), controls["set_select"])
        self.assertEqual(
            [{"name": "set_select", "position": (2067, 393)}],
            controls["set_filter_sequence"],
        )

    def test_scales_set_filter_select_control_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_controls

        controls = map_tape_filter_controls(screen_size=(1280, 720))

        self.assertEqual((1034, 197), controls["set_select"])

    def test_maps_tape_set_selection_by_set_name(self):
        from src.features.drive_assembly.page_mapping import map_tape_set_selection

        selection = map_tape_set_selection("森林萤火之心")

        self.assertEqual((532, 727), selection["set_option"])
        self.assertEqual((1564, 1186), selection["confirm_filter"])
        self.assertEqual(
            [
                {"name": "set_option", "set_name": "森林萤火之心", "position": (532, 727)},
                {"name": "confirm_filter", "position": (1564, 1186)},
            ],
            selection["selection_sequence"],
        )

    def test_maps_all_visible_tape_sets_from_filter_dialog(self):
        from src.features.drive_assembly.page_mapping import map_tape_set_selection

        expected = {
            "迪亚波罗斯": (532, 493),
            "真红：双生蝶": (762, 493),
            "守卫王国": (994, 493),
            "小小大冒险": (1225, 493),
            "森林萤火之心": (532, 727),
            "街头拳王": (762, 727),
            "影之信条": (994, 727),
            "音速蓝刺猬": (1225, 727),
            "恶魔之血·诅咒": (532, 960),
            "失落光芒": (762, 960),
            "缇娅的夜间酒馆": (994, 960),
            "静谧山庄": (1225, 960),
        }

        self.assertEqual(
            expected,
            {set_name: map_tape_set_selection(set_name)["set_option"] for set_name in expected},
        )

    def test_maps_config_set_name_aliases_to_filter_options(self):
        from src.features.drive_assembly.page_mapping import map_drive_set_selection, map_tape_set_selection

        tape_selection = map_tape_set_selection("恶魔之血：诅咒")
        drive_selection = map_drive_set_selection("恶魔之血：诅咒")

        self.assertEqual("恶魔之血·诅咒", tape_selection["set_name"])
        self.assertEqual((532, 960), tape_selection["set_option"])
        self.assertEqual("恶魔之血·诅咒", drive_selection["set_name"])
        self.assertEqual((532, 960), drive_selection["set_option"])

    def test_scales_tape_set_selection_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_set_selection

        selection = map_tape_set_selection("失落光芒", screen_size=(1280, 720))

        self.assertEqual((381, 480), selection["set_option"])
        self.assertEqual((782, 593), selection["confirm_filter"])

    def test_rejects_unknown_tape_set_selection(self):
        from src.features.drive_assembly.page_mapping import map_tape_set_selection

        with self.assertRaisesRegex(ValueError, "未知套装"):
            map_tape_set_selection("不存在的套装")

    def test_maps_tape_filter_status_quality_and_main_stat_controls(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_refinement

        controls = map_tape_filter_refinement(["Gold", "Purple"], include_status_filters=True)

        self.assertEqual((2273, 618), controls["status_locked"])
        self.assertEqual((1861, 704), controls["status_discarded"])
        self.assertEqual((2273, 704), controls["status_other"])
        self.assertNotIn("status_equipped", [step["name"] for step in controls["refinement_sequence"]])
        self.assertEqual((1861, 929), controls["quality_orange"])
        self.assertEqual((2273, 843), controls["quality_purple"])
        self.assertEqual((2067, 1071), controls["main_stat_expand"])
        self.assertEqual(
            [
                {"name": "status_locked", "position": (2273, 618)},
                {"name": "status_discarded", "position": (1861, 704)},
                {"name": "status_other", "position": (2273, 704)},
                {"name": "quality_orange", "quality": "Gold", "position": (1861, 929)},
                {
                    "name": "verify_quality_selected",
                    "quality": "Gold",
                    "selection_probe_position": (1721, 929),
                    "retry_position": (1861, 929),
                },
                {"name": "quality_purple", "quality": "Purple", "position": (2273, 843)},
                {
                    "name": "verify_quality_selected",
                    "quality": "Purple",
                    "selection_probe_position": (2133, 843),
                    "retry_position": (2273, 843),
                },
                {"name": "main_stat_expand", "position": (2067, 1071)},
                {"name": "wait_after_main_stat_expand", "wait_seconds": 0.5},
            ],
            controls["refinement_sequence"],
        )

    def test_tape_filter_refinement_can_leave_main_stat_expand_for_gamepad(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_refinement

        controls = map_tape_filter_refinement(["Gold"], include_main_stat_expand=False)

        self.assertNotIn("main_stat_expand", [step["name"] for step in controls["refinement_sequence"]])
        self.assertNotIn("wait_after_main_stat_expand", [step["name"] for step in controls["refinement_sequence"]])

    def test_tape_filter_refinement_omits_status_and_quality_when_not_requested(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_refinement

        controls = map_tape_filter_refinement([], include_main_stat_expand=False)

        self.assertEqual([], controls["refinement_sequence"])

    def test_scales_tape_filter_refinement_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_refinement

        controls = map_tape_filter_refinement(["橙色"], screen_size=(1280, 720))

        self.assertEqual((1137, 309), controls["status_locked"])
        self.assertEqual((931, 465), controls["quality_orange"])
        self.assertEqual((1034, 536), controls["main_stat_expand"])

    def test_rejects_unknown_tape_filter_quality(self):
        from src.features.drive_assembly.page_mapping import map_tape_filter_refinement

        with self.assertRaisesRegex(ValueError, "未知品质"):
            map_tape_filter_refinement(["红色"])

    def test_maps_main_stat_scroll_to_second_page(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_scroll

        scroll = map_tape_main_stat_scroll()

        self.assertEqual((2067, 1190), scroll["main_stat_scroll_start"])
        self.assertEqual((2067, 395), scroll["main_stat_scroll_end"])
        self.assertEqual(
            [
                {
                    "name": "main_stat_scroll_to_second_page",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                }
            ],
            scroll["scroll_sequence"],
        )

    def test_scales_main_stat_scroll_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_scroll

        scroll = map_tape_main_stat_scroll(screen_size=(1280, 720))

        self.assertEqual((1034, 595), scroll["main_stat_scroll_start"])
        self.assertEqual((1034, 198), scroll["main_stat_scroll_end"])

    def test_maps_main_stat_gamepad_open_sequence(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_gamepad_open

        sequence = map_tape_main_stat_gamepad_open()["open_sequence"]

        self.assertEqual(11, len(sequence))
        self.assertEqual(["left_down"] * 7, [step["gamepad_stick"] for step in sequence[:7]])
        self.assertEqual(
            {
                "name": "main_stat_gamepad_confirm_expand",
                "gamepad_button": "a",
                "post_action_pause_seconds": 0.2,
            },
            sequence[7],
        )
        self.assertEqual(["left_down"] * 3, [step["gamepad_stick"] for step in sequence[8:]])
        self.assertEqual([0.2] * 11, [step["post_action_pause_seconds"] for step in sequence])

    def test_maps_tape_main_stat_selection_from_blueprint_stat(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_selection

        selection = map_tape_main_stat_selection("攻击力百分比")

        self.assertEqual((2273, 485), selection["main_stat_option"])
        self.assertEqual("攻击力百分比", selection["main_stat"])
        self.assertEqual(
            [{"name": "main_stat_option", "main_stat": "攻击力百分比", "position": (2273, 485)}],
            selection["selection_sequence"],
        )

    def test_maps_second_page_tape_main_stat_options(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_selection

        expected = {
            "生命值百分比": (1861, 485),
            "攻击力百分比": (2273, 485),
            "防御力百分比": (1861, 570),
            "暴击率": (2273, 570),
            "暴击伤害": (1861, 656),
            "环合强度": (2273, 656),
            "倾陷强度": (1861, 742),
            "治疗加成": (2273, 742),
            "光属性异能伤害增强": (1861, 828),
            "灵属性异能伤害增强": (2273, 828),
            "咒属性异能伤害增强": (1861, 914),
            "暗属性异能伤害增强": (2273, 914),
            "魂属性异能伤害增强": (1861, 999),
            "相属性异能伤害增强": (2273, 999),
            "心灵伤害增强": (1861, 1085),
        }

        self.assertEqual(
            expected,
            {main_stat: map_tape_main_stat_selection(main_stat)["main_stat_option"] for main_stat in expected},
        )

    def test_accepts_percent_symbol_tape_main_stat_aliases(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_selection

        aliases = {
            "攻击力%": "攻击力百分比",
            "暴击率%": "暴击率",
            "暴击伤害%": "暴击伤害",
            "光属性异能伤害增强%": "光属性异能伤害增强",
        }
        for raw_name, expected_name in aliases.items():
            with self.subTest(raw_name=raw_name):
                selection = map_tape_main_stat_selection(raw_name)
                self.assertEqual(expected_name, selection["main_stat"])

    def test_scales_tape_main_stat_selection_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_selection

        selection = map_tape_main_stat_selection("暴击率", screen_size=(1280, 720))

        self.assertEqual((1137, 285), selection["main_stat_option"])

    def test_rejects_unknown_tape_main_stat_selection(self):
        from src.features.drive_assembly.page_mapping import map_tape_main_stat_selection

        with self.assertRaisesRegex(ValueError, "未知卡带主词条"):
            map_tape_main_stat_selection("不存在词条")

    def test_maps_scroll_to_bottom_and_open_sub_stat_filter(self):
        from src.features.drive_assembly.page_mapping import map_tape_sub_stat_filter_entry

        entry = map_tape_sub_stat_filter_entry()

        self.assertEqual((2067, 1190), entry["sub_stat_scroll_start"])
        self.assertEqual((2067, 395), entry["sub_stat_scroll_end"])
        self.assertEqual((2067, 898), entry["sub_stat_expand"])
        self.assertEqual(3, len(entry["entry_sequence"]))
        self.assertEqual(
            [
                {
                    "name": "sub_stat_scroll_to_expand",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                },
                {"name": "sub_stat_expand", "position": (2067, 898)},
                {"name": "wait_after_sub_stat_expand", "wait_seconds": 0.5},
            ],
            entry["entry_sequence"],
        )

    def test_scales_sub_stat_filter_entry_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_sub_stat_filter_entry

        entry = map_tape_sub_stat_filter_entry(screen_size=(1280, 720))

        self.assertEqual((1034, 595), entry["sub_stat_scroll_start"])
        self.assertEqual((1034, 198), entry["sub_stat_scroll_end"])
        self.assertEqual((1034, 449), entry["sub_stat_expand"])

    def test_maps_tape_sub_stat_selection_and_fixed_count_four(self):
        from src.features.drive_assembly.page_mapping import map_tape_sub_stat_selection

        selection = map_tape_sub_stat_selection(["暴击率%", "攻击力%", "伤害增加%"])

        self.assertEqual((1861, 721), selection["sub_stat_options"]["暴击率"])
        self.assertEqual((2273, 464), selection["sub_stat_options"]["攻击力百分比"])
        self.assertEqual((1861, 893), selection["sub_stat_options"]["通用伤害增强"])
        self.assertEqual((1861, 1202), selection["sub_stat_count_four"])
        self.assertEqual(
            [
                {
                    "name": "sub_stat_scroll_to_bottom",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                },
                {"name": "sub_stat_option", "sub_stat": "暴击率", "position": (1861, 721)},
                {"name": "sub_stat_option", "sub_stat": "攻击力百分比", "position": (2273, 464)},
                {"name": "sub_stat_option", "sub_stat": "通用伤害增强", "position": (1861, 893)},
                {"name": "sub_stat_count_four", "position": (1861, 1202)},
            ],
            selection["selection_sequence"],
        )

    def test_scales_tape_sub_stat_selection_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_sub_stat_selection

        selection = map_tape_sub_stat_selection(["攻击力%"], screen_size=(1280, 720))

        self.assertEqual((1137, 232), selection["sub_stat_options"]["攻击力百分比"])
        self.assertEqual((931, 601), selection["sub_stat_count_four"])

    def test_rejects_unknown_tape_sub_stat_selection(self):
        from src.features.drive_assembly.page_mapping import map_tape_sub_stat_selection

        with self.assertRaisesRegex(ValueError, "未知卡带副词条"):
            map_tape_sub_stat_selection(["不存在副词条"])

    def test_maps_confirm_and_drag_first_filtered_tape_to_socket(self):
        from src.features.drive_assembly.page_mapping import map_tape_equip_first_result

        equip = map_tape_equip_first_result()

        self.assertEqual((2273, 1322), equip["confirm_filter"])
        self.assertEqual((126, 430), equip["first_tape"])
        self.assertEqual((1267, 1090), equip["tape_socket"])
        self.assertEqual((1546, 953), equip["reuse_prompt_confirm"])
        self.assertEqual((1280, 690), equip["reuse_prompt_probe"])
        self.assertEqual(
            [
                {"name": "confirm_filter", "position": (2273, 1322)},
                {
                    "name": "drag_first_tape_to_socket",
                    "from": (126, 430),
                    "to": (1267, 1090),
                    "duration_ms": 1200,
                },
                {"name": "wait_for_equipment_reuse_prompt", "wait_seconds": 0.3},
                {
                    "name": "confirm_equipment_reuse_prompt",
                    "optional_confirm_position": (1546, 953),
                    "modal_probe_position": (1280, 690),
                    "brightness_threshold": 150,
                },
            ],
            equip["equip_sequence"],
        )

    def test_scales_confirm_and_drag_first_filtered_tape_to_socket(self):
        from src.features.drive_assembly.page_mapping import map_tape_equip_first_result

        equip = map_tape_equip_first_result(screen_size=(1280, 720))

        self.assertEqual((1137, 661), equip["confirm_filter"])
        self.assertEqual((63, 215), equip["first_tape"])
        self.assertEqual((634, 545), equip["tape_socket"])
        self.assertEqual((773, 477), equip["reuse_prompt_confirm"])
        self.assertEqual((640, 345), equip["reuse_prompt_probe"])

    def test_maps_drive_tab_and_filter_button_controls(self):
        from src.features.drive_assembly.page_mapping import map_drive_page_controls

        controls = map_drive_page_controls()

        self.assertEqual((554, 309), controls["drive_tab"])
        self.assertEqual((111, 1347), controls["filter_button"])
        self.assertEqual(
            [
                {"name": "drive_tab", "position": (554, 309)},
                {"name": "filter_button", "position": (111, 1347)},
            ],
            controls["click_sequence"],
        )

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
                {"name": "shape_option", "drive_type": "V_3", "position": (948, 745)},
                {"name": "confirm_shape_filter", "position": (1564, 1186)},
            ],
            selection["selection_sequence"],
        )

    def test_drive_shape_options_match_config_shapes(self):
        import json
        from pathlib import Path

        from src.features.drive_assembly.page_mapping import DEFAULT_DRIVE_SHAPE_OPTIONS, DRIVE_SHAPE_ALIASES

        data = json.loads(Path("config/shapes.json").read_text(encoding="utf-8"))
        expected = {
            item["shape_id"]
            for item in data["shapes"]
            if item.get("shape_id") and item.get("shape_id") != "TAPE_15"
        }

        self.assertEqual(expected, set(DEFAULT_DRIVE_SHAPE_OPTIONS))
        self.assertTrue(set(DRIVE_SHAPE_ALIASES.values()).issubset(expected))

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
            "shape_option",
            "confirm_shape_filter",
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
        self.assertEqual({"name": "reset_filter", "position": (1861, 1322)}, install["install_sequence"][0])
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
                {"name": "drive_tab", "position": (554, 309)},
                {"name": "install_drive_block", "block_id": 1, "sequence_index": 0},
                {"name": "install_drive_block", "block_id": 2, "sequence_index": 1},
            ],
            plan["assembly_sequence"],
        )

    def test_drive_block_installation_resets_filter_and_reuses_cached_set(self):
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
        self.assertEqual("drive_set_select", first_sequence[5]["name"])
        self.assertEqual("失落光芒", plan["install_plans"][1]["set_name"])
        self.assertEqual("filter_button", second_sequence[0]["name"])
        self.assertEqual("reset_filter", second_sequence[1]["name"])
        self.assertEqual("shape_select", second_sequence[2]["name"])
        self.assertEqual("drive_set_select", second_sequence[5]["name"])

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
