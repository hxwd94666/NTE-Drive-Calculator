# 验证已保存驱动盘矩阵可转换为装配块坐标数据。
"""Tests for extracting assembly blocks from saved equipped_state data."""

import json
import tempfile
import unittest
from pathlib import Path


class DriveAssemblyBlockTests(unittest.TestCase):
    def test_extracts_numbered_blocks_with_board_relative_offsets(self):
        from src.domain.drive_layout import extract_drive_blocks_from_state

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
        from src.domain.drive_layout import load_drive_blocks

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
        from src.domain.drive_layout import extract_drive_blocks_from_state

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
        from src.domain.drive_layout import extract_drive_blocks_from_state

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
        from src.domain.drive_layout import extract_drive_blocks_from_state
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
        from src.domain.drive_layout import extract_tape_filters_from_state

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
        from src.domain.drive_layout import load_tape_filters

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
        from src.domain.drive_layout import extract_drive_blocks_from_state

        state = {
            "角色A": {
                "blueprint_layout": [["A"]],
                "equipped_drives": [{"uid": "drive-a", "drive_type": "custom-type", "shape_id": "H_2"}],
            }
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual("custom-type", blocks[0]["drive_type"])

    def test_marks_duplicate_drive_blocks_by_equipment_content(self):
        from src.domain.drive_layout import extract_drive_blocks_from_state

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

    def test_marks_drives_with_the_same_filter_fields_even_when_values_differ(self):
        from src.domain.drive_layout import extract_drive_blocks_from_state

        state = {
            "role-a": {
                "blueprint_layout": [["H_2", "H_2"]],
                "equipped_drives": [
                    {
                        "uid": "drive-a",
                        "shape_id": "H_2",
                        "quality": "Gold",
                        "set_name": "ignored-set-a",
                        "sub_stats": {"crit_rate": 2.0, "attack": 12.0},
                        "grade": "ACE",
                    }
                ],
            },
            "role-b": {
                "blueprint_layout": [["H_2", "H_2"]],
                "equipped_drives": [
                    {
                        "uid": "drive-b",
                        "shape_id": "H_2",
                        "quality": "Gold",
                        "set_name": "ignored-set-b",
                        "sub_stats": {"attack": 80.0, "crit_rate": 4.0},
                        "grade": "SSS",
                    }
                ],
            },
        }

        blocks = extract_drive_blocks_from_state(state)

        self.assertEqual(["drive_dup_001", "drive_dup_001"], [block["duplicate_group_id"] for block in blocks])
        self.assertTrue(all(block["is_duplicate_drive"] for block in blocks))

    def test_marks_duplicate_tape_filters_by_equipment_content(self):
        from src.domain.drive_layout import extract_tape_filters_from_state

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
        from src.domain.drive_layout import extract_drive_blocks_from_state

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
        from src.domain.drive_layout import extract_drive_blocks_from_state

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

if __name__ == "__main__":
    unittest.main()
