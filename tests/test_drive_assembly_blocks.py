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

        controls = map_tape_filter_refinement(["Gold", "Purple"])

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
                {"name": "quality_purple", "quality": "Purple", "position": (2273, 843)},
                {"name": "main_stat_expand", "position": (2067, 1071)},
            ],
            controls["refinement_sequence"],
        )

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

        selection = map_tape_main_stat_selection("攻击力%")

        self.assertEqual("攻击力百分比", selection["main_stat"])
        self.assertEqual((2273, 485), selection["main_stat_option"])

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
        self.assertEqual((2067, 1226), entry["sub_stat_expand"])
        self.assertEqual(4, len(entry["entry_sequence"]))
        self.assertEqual(
            [
                {
                    "name": "sub_stat_scroll_to_bottom",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                },
                {
                    "name": "sub_stat_scroll_to_bottom",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                },
                {
                    "name": "sub_stat_scroll_to_bottom",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                },
                {"name": "sub_stat_expand", "position": (2067, 1226)},
            ],
            entry["entry_sequence"],
        )

    def test_scales_sub_stat_filter_entry_to_other_screens(self):
        from src.features.drive_assembly.page_mapping import map_tape_sub_stat_filter_entry

        entry = map_tape_sub_stat_filter_entry(screen_size=(1280, 720))

        self.assertEqual((1034, 595), entry["sub_stat_scroll_start"])
        self.assertEqual((1034, 198), entry["sub_stat_scroll_end"])
        self.assertEqual((1034, 613), entry["sub_stat_expand"])

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
        self.assertEqual(
            [
                {"name": "confirm_filter", "position": (2273, 1322)},
                {
                    "name": "drag_first_tape_to_socket",
                    "from": (126, 430),
                    "to": (1267, 1090),
                    "duration_ms": 700,
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

    def test_maps_drive_filter_refinement_and_sub_stats(self):
        from src.features.drive_assembly.page_mapping import map_drive_filter_refinement

        controls = map_drive_filter_refinement(["Gold"], ["暴击率%", "攻击力"])

        self.assertEqual((2273, 679), controls["status_locked"])
        self.assertEqual((1861, 766), controls["status_discarded"])
        self.assertEqual((1861, 989), controls["quality_orange"])
        self.assertEqual((2067, 1136), controls["sub_stat_expand"])
        self.assertEqual((1861, 721), controls["sub_stat_options"]["暴击率"])
        self.assertEqual((1861, 636), controls["sub_stat_options"]["攻击力"])
        self.assertEqual((1861, 1202), controls["sub_stat_count_four"])

    def test_maps_drive_block_installation_to_precomputed_pixel_position(self):
        from src.features.drive_assembly.page_mapping import map_drive_block_installation

        block = {
            "block_id": 3,
            "drive_type": "L_3",
            "pixel_position": (1205, 393),
            "drive": {"quality": "Gold", "sub_stats": {"暴击率%": 10.0, "攻击力": 80}},
        }

        install = map_drive_block_installation(block)

        self.assertEqual((1095, 745), install["shape_option"])
        self.assertEqual((126, 430), install["first_drive"])
        self.assertEqual((1205, 393), install["target_position"])
        self.assertEqual(
            {"name": "drag_first_drive_to_block", "block_id": 3, "from": (126, 430), "to": (1205, 393), "duration_ms": 700},
            install["install_sequence"][-1],
        )

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
                {"name": "filter_button", "position": (111, 1347)},
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
        self.assertEqual("reset_filter", first_sequence[0]["name"])
        self.assertEqual("drive_set_select", first_sequence[1]["name"])
        self.assertEqual("失落光芒", plan["install_plans"][1]["set_name"])
        self.assertEqual("reset_filter", second_sequence[0]["name"])
        self.assertEqual("drive_set_select", second_sequence[1]["name"])


if __name__ == "__main__":
    unittest.main()
