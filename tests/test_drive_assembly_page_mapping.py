# 验证已保存驱动盘矩阵可转换为装配块坐标数据。
"""Tests for extracting assembly blocks from saved equipped_state data."""

import unittest


class DriveAssemblyPageMappingTests(unittest.TestCase):
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

    def test_page_mapping_scales_controls_to_supported_16_by_9_sizes(self):
        from src.features.drive_assembly.page_mapping import map_page_controls

        cases = (
            ((3840, 2160), (360, 464), (167, 2021)),
            ((1920, 1080), (180, 232), (83, 1010)),
        )
        for screen_size, tape_tab, filter_button in cases:
            with self.subTest(screen_size=screen_size):
                controls = map_page_controls(screen_size=screen_size)
                self.assertEqual(tape_tab, controls["tape_tab"])
                self.assertEqual(filter_button, controls["filter_button"])

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
                {"name": "tape_tab", "position": (240, 309), "post_action_pause_seconds": 0.6},
                {"name": "filter_button", "position": (111, 1347), "post_action_pause_seconds": 0.6},
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
            [
                {"name": "set_select", "position": (2067, 393)},
                {"name": "wait_after_tape_set_dialog_open", "wait_seconds": 0.5},
            ],
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
                {"name": "wait_after_tape_set_dialog_close", "wait_seconds": 0.8},
            ],
            selection["selection_sequence"],
        )

    def test_maps_tape_set_selection_with_display_wrapper(self):
        from src.features.drive_assembly.page_mapping import map_tape_set_selection

        selection = map_tape_set_selection("「失落光芒」")

        self.assertEqual("失落光芒", selection["set_name"])
        self.assertEqual((762, 960), selection["set_option"])

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
                {"name": "status_locked", "position": (2273, 618), "post_action_pause_seconds": 0.3},
                {"name": "status_discarded", "position": (1861, 704), "post_action_pause_seconds": 0.3},
                {"name": "status_other", "position": (2273, 704), "post_action_pause_seconds": 0.3},
                {"name": "quality_orange", "quality": "Gold", "position": (1861, 929), "post_action_pause_seconds": 0.3},
                {
                    "name": "verify_quality_selected",
                    "quality": "Gold",
                    "selection_probe_position": (1721, 929),
                    "retry_position": (1861, 929),
                },
                {"name": "quality_purple", "quality": "Purple", "position": (2273, 843), "post_action_pause_seconds": 0.3},
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
            [{"name": "main_stat_option", "main_stat": "攻击力百分比", "position": (2273, 485), "post_action_pause_seconds": 0.3}],
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
                {"name": "sub_stat_option", "sub_stat": "暴击率", "position": (1861, 721), "post_action_pause_seconds": 0.3},
                {"name": "sub_stat_option", "sub_stat": "攻击力百分比", "position": (2273, 464), "post_action_pause_seconds": 0.3},
                {"name": "sub_stat_option", "sub_stat": "通用伤害增强", "position": (1861, 893), "post_action_pause_seconds": 0.3},
                {"name": "sub_stat_count_four", "position": (1861, 1202), "post_action_pause_seconds": 0.3},
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
                {"name": "confirm_filter", "position": (2273, 1322), "post_action_pause_seconds": 0.0},
                {"name": "wait_after_tape_filter_confirm", "wait_seconds": 0.6, "post_action_pause_seconds": 0.0},
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
                    "post_action_pause_seconds": 0.8,
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
                {"name": "drive_tab", "position": (554, 309), "post_action_pause_seconds": 0.6},
                {"name": "filter_button", "position": (111, 1347), "post_action_pause_seconds": 0.6},
            ],
            controls["click_sequence"],
        )

if __name__ == "__main__":
    unittest.main()
