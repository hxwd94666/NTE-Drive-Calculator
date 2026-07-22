# 验证官方 SQLite 方案替换只变更目标 UID，并保留图纸坐标。
import unittest


class SqliteEquipmentReplacementTests(unittest.TestCase):
    def test_replacement_preserves_assignment_position_and_kind(self):
        from src.features.inventory.page import _replacement_assignments

        plan = {
            "assignments": [
                {"uid_serial": 1, "uid_slot": 2, "kind": "module", "target_row": 3, "target_column": 4, "rotation": 90, "raw_assignment": {"uid": {"serial": 1, "slot": 2}}},
                {"uid_serial": 8, "uid_slot": 2, "kind": "core", "target_row": None, "target_column": None, "rotation": None, "raw_assignment": {"uid": {"serial": 8, "slot": 2}}},
            ]
        }

        updated = _replacement_assignments(plan, "nte-module-2-1", {"_uid_serial": 9, "_uid_slot": 7})

        self.assertEqual((9, 7), (updated[0]["uid_serial"], updated[0]["uid_slot"]))
        self.assertEqual((3, 4, 90), (updated[0]["target_row"], updated[0]["target_column"], updated[0]["rotation"]))
        self.assertEqual("module", updated[0]["kind"])
        self.assertEqual((8, 2), (updated[1]["uid_serial"], updated[1]["uid_slot"]))

    def test_active_sqlite_user_map_deduplicates_roles_and_excludes_current_role(self):
        from src.features.inventory.page import _active_sqlite_equipment_users

        class FakeDao:
            def list_active_loadout_plans_by_role(self):
                return {
                    "当前角色": {"assignments": [{"kind": "module", "uid_serial": 1, "uid_slot": 2}]},
                    "薄荷": {"assignments": [
                        {"kind": "module", "uid_serial": 1, "uid_slot": 2},
                        {"kind": "module", "uid_serial": 1, "uid_slot": 2},
                    ]},
                    "主角": {"assignments": [{"kind": "core", "uid_serial": 9, "uid_slot": 8}]},
                }

        users = _active_sqlite_equipment_users(FakeDao(), "当前角色")

        self.assertEqual(("薄荷",), users[("module", 1, 2)])
        self.assertEqual(("主角",), users[("core", 9, 8)])
        self.assertNotIn("当前角色", users[("module", 1, 2)])


if __name__ == "__main__":
    unittest.main()
