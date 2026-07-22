# 验证全量视觉扫描可进入 SQLite，并在没有抓包快照时作为计算库存兜底。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.services.vision_inventory_snapshot import import_vision_inventory
from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class VisionInventorySnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.static_database_env = patch.dict(
            "os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}
        )
        self.static_database_env.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "user.sqlite3"
        self.user_dao = UserDataDao(self.database_path, account_id="vision-test")
        self.static_dao = StaticGameDataDao()

    def tearDown(self) -> None:
        self.static_dao.close()
        self.user_dao.close()
        self.temp_dir.cleanup()
        self.static_database_env.stop()

    def test_visual_scan_is_persisted_as_gamepad_fallback_and_projects_for_solver(self) -> None:
        snapshot_id = import_vision_inventory(
            self.database_path,
            [
                {
                    "uid": "drive_visual_1", "item_type": "drive", "quality": "Gold", "area": 2,
                    "shape_id": "H_2", "main_stats": {"攻击力": 42},
                    "sub_stats": {"暴击率%": 2.0, "防御力百分比": 3.5},
                },
                {
                    "uid": "tape_visual_1", "item_type": "tape", "quality": "Purple", "area": 15,
                    "set_name": "「失落光芒」", "main_stats": "光属性异能伤害增强%", "sub_stats": {"攻击力%": 10.0},
                },
            ],
        )

        self.assertEqual(snapshot_id, self.user_dao.current_inventory_snapshot_id())
        self.assertEqual("gamepad", self.user_dao.inventory_snapshot_summary(snapshot_id)["source"])
        projection = SqliteAllocationInventory(self.user_dao, self.static_dao).build()
        self.assertEqual(snapshot_id, projection.snapshot_id)
        self.assertEqual({"drive", "tape"}, {row["item_type"] for row in projection.items})
        drive = next(row for row in projection.items if row["item_type"] == "drive")
        self.assertEqual(3.5, drive["sub_stats"]["防御力%"])
        imported = self.user_dao.list_inventory_items(snapshot_id)
        self.assertTrue(all(row["level"] == 0 and row["max_level"] == 0 for row in imported))
        self.assertTrue(all(not row["locked"] and not row["discarded"] and not row["equipped"] for row in imported))
        from src.features.inventory.warehouse import load_warehouse_snapshot

        warehouse = load_warehouse_snapshot(self.database_path)
        self.assertEqual(snapshot_id, warehouse["snapshot_id"])
        self.assertEqual("gamepad", warehouse["source"])
        self.assertTrue(all(not item["level_known"] and not item["state_known"] for item in warehouse["items"]))

    def test_nte_core_snapshot_has_priority_over_visual_scan(self) -> None:
        visual_snapshot_id = import_vision_inventory(
            self.database_path,
            [{
                "uid": "drive_visual_1", "item_type": "drive", "quality": "Gold", "area": 2,
                "shape_id": "H_2", "main_stats": {"攻击力": 42}, "sub_stats": {"暴击率%": 2.0},
            }],
        )
        nte_snapshot_id = self.user_dao.import_inventory_snapshot(
            {"complete": True, "item_count": 0, "items": []}, source="nte_core"
        )

        self.assertNotEqual(visual_snapshot_id, nte_snapshot_id)
        self.assertEqual(nte_snapshot_id, self.user_dao.current_inventory_snapshot_id())


if __name__ == "__main__":
    unittest.main()
