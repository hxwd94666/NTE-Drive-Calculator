# 测试角色装备替换候选和写回服务。
import unittest
from unittest.mock import patch

from src.features.role.replacement_service import (
    apply_drive_replacement_plan,
    build_drive_replacement_options,
    build_drive_replacement_plan,
)


class DriveReplacementServiceTests(unittest.TestCase):
    def test_replacement_confirmation_lists_roles_that_will_be_unequipped(self):
        from PySide6.QtWidgets import QMessageBox
        from src.features.role.drive_widget import _confirm_equipment_replacement

        with patch("src.features.role.drive_widget.QMessageBox.question", return_value=QMessageBox.Yes) as question:
            self.assertTrue(_confirm_equipment_replacement(None, "drive", ["角色甲", "角色乙"]))

        _window, _title, message, *_rest = question.call_args.args
        self.assertIn("驱动", message)
        self.assertIn("角色甲", message)
        self.assertIn("角色乙", message)
        self.assertIn("卸下", message)

    def test_build_drive_options_sorts_candidates_and_marks_users(self):
        role_data = {
            "drive": {
                "drives": [
                    {"uid": "old", "shape_id": "H_2", "sub_stats": {"攻击力%": 1}, "quality": "Gold"},
                    {"uid": "equipped", "shape_id": "H_2", "sub_stats": {"攻击力%": 2}, "quality": "Gold"},
                ],
                "blueprint_layout": [],
            }
        }
        inventory = [
            {"uid": "low", "shape_id": "H_2", "sub_stats": {"攻击力%": 1}, "quality": "Gold"},
            {"uid": "high", "shape_id": "H_2", "sub_stats": {"攻击力%": 9}, "quality": "Gold"},
            {"uid": "equipped", "shape_id": "H_2", "sub_stats": {"攻击力%": 99}, "quality": "Gold"},
            {"uid": "wrong_shape", "shape_id": "V_3", "sub_stats": {"攻击力%": 99}, "quality": "Gold"},
        ]
        my_roles = {
            "A": role_data,
            "B": {"drive": {"drives": [{"uid": "high", "shape_id": "H_2"}]}},
        }

        def score_drive(sub_stats, _shape_id, _weights, _quality):
            return float(sub_stats.get("攻击力%", 0.0))

        options = build_drive_replacement_options(
            role_name="A",
            role_data=role_data,
            current_drive=role_data["drive"]["drives"][0],
            inventory=inventory,
            my_roles_data=my_roles,
            weights={},
            score_drive=score_drive,
        )

        self.assertIsNotNone(options)
        self.assertEqual("old", options.current_uid)
        self.assertEqual(["high", "low"], [candidate.drive["uid"] for candidate in options.candidates])
        self.assertEqual(("B",), options.candidates[0].used_by)

    def test_apply_drive_replacement_plan_displaces_other_role(self):
        role_data = {
            "drive": {
                "drives": [
                    {"uid": "old", "shape_id": "H_2", "sub_stats": {"攻击力%": 1}, "quality": "Gold"},
                ]
            }
        }
        form_data = {
            "A": role_data,
            "B": {
                "drive": {
                    "drives": [
                        {"uid": "new", "shape_id": "H_2", "sub_stats": {"攻击力%": 9}, "quality": "Gold"},
                    ]
                }
            },
        }
        new_drive = {"uid": "new", "shape_id": "H_2", "sub_stats": {"攻击力%": 9}, "quality": "Gold"}
        plan = build_drive_replacement_plan("A", "old", new_drive, {"new": ["B"]})

        applied, dirty_roles = apply_drive_replacement_plan(form_data, role_data, plan)

        self.assertTrue(applied)
        self.assertEqual({"A", "B"}, dirty_roles)
        self.assertEqual("new", role_data["drive"]["drives"][0]["uid"])
        self.assertTrue(role_data["drive"]["drives"][0]["is_changed"])
        self.assertEqual("empty_new", form_data["B"]["drive"]["drives"][0]["uid"])


if __name__ == "__main__":
    unittest.main()
