# 验证全量视觉扫描可进入 SQLite，并在没有抓包快照时作为计算库存兜底。
from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.services.inventory_snapshot_export import export_inventory_snapshot
from src.services.full_visual_snapshot_commit import (
    IncompleteVisionScanError,
    commit_completed_vision_inventory,
)
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

    def test_visual_scan_is_persisted_as_unified_vision_source_and_projects_for_solver(self) -> None:
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
        summary = self.user_dao.inventory_snapshot_summary(snapshot_id)
        self.assertEqual("vision", summary["source"])
        self.assertEqual("mouse", self.user_dao.raw_snapshot(snapshot_id)["capture_driver"])
        projection = SqliteAllocationInventory(self.user_dao, self.static_dao).build(snapshot_id)
        self.assertEqual(snapshot_id, projection.snapshot_id)
        self.assertEqual({"drive", "tape"}, {row["item_type"] for row in projection.items})
        drive = next(row for row in projection.items if row["item_type"] == "drive")
        tape = next(row for row in projection.items if row["item_type"] == "tape")
        self.assertEqual(3.5, drive["sub_stats"]["防御力%"])
        self.assertEqual("Suit6", tape["suit_id"])
        # Purple visual cards infer their unobserved main value from the
        # catalogue and its quality coefficient (37.5% × 0.8).
        self.assertEqual(30.0, tape["main_value"])
        imported = self.user_dao.list_inventory_items(snapshot_id)
        self.assertEqual("EquipmentGeometry_Hen2", next(row for row in imported if row["kind"] == "module")["geometry"])
        self.assertTrue(all(row["level"] == 0 and row["max_level"] == 0 for row in imported))
        self.assertTrue(all(not row["locked"] and not row["discarded"] and not row["equipped"] for row in imported))
        from src.features.inventory.warehouse_presenter import load_warehouse_snapshot

        warehouse = load_warehouse_snapshot(self.database_path)
        self.assertEqual(snapshot_id, warehouse["snapshot_id"])
        self.assertEqual("vision", warehouse["source"])
        self.assertTrue(all(not item["level_known"] and not item["state_known"] for item in warehouse["items"]))

        exported = export_inventory_snapshot(self.database_path, snapshot_id)
        self.assertEqual(snapshot_id, exported["snapshot_id"])
        self.assertEqual(2, len(exported["items"]))
        self.assertEqual(
            "EquipmentGeometry_Hen2",
            next(row for row in exported["items"] if row["kind"] == "module")["geometry"],
        )

    def test_mouse_and_gamepad_visual_scans_share_one_current_inventory(self) -> None:
        mouse_snapshot_id = import_vision_inventory(
            self.database_path,
            [{
                "uid": "drive_mouse", "item_type": "drive", "quality": "Gold", "area": 2,
                "shape_id": "H_2", "main_stats": {"攻击力": 42}, "sub_stats": {"暴击率%": 2.0},
            }],
            capture_driver="mouse",
        )
        gamepad_snapshot_id = import_vision_inventory(
            self.database_path,
            [{
                "uid": "drive_gamepad", "item_type": "drive", "quality": "Gold", "area": 2,
                "shape_id": "H_2", "main_stats": {"攻击力": 42}, "sub_stats": {"暴击率%": 2.0},
            }],
            capture_driver="gamepad",
        )

        self.assertNotEqual(mouse_snapshot_id, gamepad_snapshot_id)
        self.assertEqual(gamepad_snapshot_id, self.user_dao.current_inventory_snapshot_id())
        current = [row for row in self.user_dao.list_inventory_snapshots() if row["is_current"]]
        self.assertEqual([gamepad_snapshot_id], [row["snapshot_id"] for row in current])
        self.assertEqual("mouse", self.user_dao.raw_snapshot(mouse_snapshot_id)["capture_driver"])
        self.assertEqual("gamepad", self.user_dao.raw_snapshot(gamepad_snapshot_id)["capture_driver"])

    def test_v14_migration_preserves_existing_visual_snapshot_and_foreign_keys(self) -> None:
        legacy_snapshot_id = self.user_dao.import_inventory_snapshot(
            {"complete": True, "item_count": 0, "items": []},
            source="gamepad",
        )
        self.user_dao.close()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(
                """
                DROP VIEW current_inventory_item;
                CREATE TABLE inventory_snapshot_v13 (
                    snapshot_id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL CHECK (source IN ('nte_core', 'gamepad', 'import')),
                    generation INTEGER,
                    sequence INTEGER,
                    observed_at_unix_ms INTEGER,
                    captured_at_utc TEXT NOT NULL,
                    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
                    declared_item_count INTEGER NOT NULL CHECK (declared_item_count >= 0),
                    stored_item_count INTEGER NOT NULL CHECK (stored_item_count >= 0),
                    protocol_version INTEGER,
                    raw_snapshot_json TEXT NOT NULL,
                    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
                    created_at_utc TEXT NOT NULL,
                    CHECK (complete = 0 OR declared_item_count = stored_item_count)
                );
                INSERT INTO inventory_snapshot_v13 SELECT * FROM inventory_snapshot;
                DROP TABLE inventory_snapshot;
                ALTER TABLE inventory_snapshot_v13 RENAME TO inventory_snapshot;
                CREATE UNIQUE INDEX idx_inventory_snapshot_one_current
                    ON inventory_snapshot(is_current) WHERE is_current = 1;
                CREATE INDEX idx_inventory_snapshot_captured
                    ON inventory_snapshot(captured_at_utc DESC, snapshot_id DESC);
                CREATE VIEW current_inventory_item AS
                    SELECT item.* FROM inventory_item AS item
                    JOIN inventory_snapshot AS snapshot USING (snapshot_id)
                    WHERE snapshot.is_current = 1;
                DELETE FROM schema_migration WHERE version = 14;
                """
            )
        finally:
            connection.close()

        self.user_dao = UserDataDao(self.database_path)
        self.assertEqual(legacy_snapshot_id, self.user_dao.current_inventory_snapshot_id())
        self.assertEqual([], self.user_dao._db().execute("PRAGMA foreign_key_check").fetchall())
        vision_snapshot_id = import_vision_inventory(self.database_path, [], capture_driver="mouse")
        self.assertEqual(vision_snapshot_id, self.user_dao.current_inventory_snapshot_id())

    def test_latest_complete_snapshot_wins_regardless_of_source(self) -> None:
        nte_snapshot_id = self.user_dao.import_inventory_snapshot(
            {"complete": True, "item_count": 0, "items": []}, source="nte_core"
        )
        visual_snapshot_id = import_vision_inventory(
            self.database_path,
            [{
                "uid": "drive_visual_1", "item_type": "drive", "quality": "Gold", "area": 2,
                "shape_id": "H_2", "main_stats": {"攻击力": 42}, "sub_stats": {"暴击率%": 2.0},
            }],
        )
        self.assertNotEqual(visual_snapshot_id, nte_snapshot_id)
        self.assertEqual(visual_snapshot_id, self.user_dao.current_inventory_snapshot_id())

    def test_incomplete_full_visual_parse_keeps_previous_current_snapshot(self) -> None:
        previous_snapshot_id = self.user_dao.import_inventory_snapshot(
            {"complete": True, "item_count": 0, "items": []},
            source="nte_core",
        )

        with self.assertRaises(IncompleteVisionScanError):
            commit_completed_vision_inventory(
                self.database_path,
                {
                    "parse_scope": "full",
                    "total_count": 2,
                    "failed_count": 1,
                    "vision_items": [{"uid": "only-one"}],
                    "capture_driver": "mouse",
                },
                [],
            )

        self.assertEqual(previous_snapshot_id, self.user_dao.current_inventory_snapshot_id())

    def test_full_visual_scan_replaces_nte_core_snapshot_with_observed_timestamp(self) -> None:
        nte_snapshot_id = self.user_dao.import_inventory_snapshot(
            {
                "complete": True,
                "generation": 99,
                "sequence": 99,
                "observed_at_unix_ms": 1_900_000_000_000,
                "item_count": 0,
                "items": [],
            },
            source="nte_core",
        )
        visual_snapshot_id = import_vision_inventory(
            self.database_path,
            [{
                "uid": "drive_visual_latest", "item_type": "drive", "quality": "Gold", "area": 2,
                "shape_id": "H_2", "main_stats": {"攻击力": 42}, "sub_stats": {"暴击率%": 2.0},
            }],
        )

        self.assertNotEqual(visual_snapshot_id, nte_snapshot_id)
        self.assertEqual(visual_snapshot_id, self.user_dao.current_inventory_snapshot_id())
        current_ids = {
            row["snapshot_id"]
            for row in self.user_dao.list_inventory_snapshots()
            if row["is_current"]
        }
        self.assertEqual({visual_snapshot_id}, current_ids)

    def test_visual_scan_normalizes_legacy_heng_shape_and_short_stat_names(self) -> None:
        snapshot_id = import_vision_inventory(
            self.database_path,
            [
                {
                    "uid": "drive_visual_heng", "item_type": "drive", "quality": "Gold", "area": 3,
                    "shape_id": "HENG3", "main_stats": {"攻击": 63, "生命": 840},
                    "sub_stats": {"小攻击": 24, "防御": 16, "生命": 280},
                },
                {
                    "uid": "tape_visual_short", "item_type": "tape", "quality": "Gold", "area": 15,
                    "set_name": "音速蓝刺猬", "main_stats": "攻击", "sub_stats": {"攻击": 80},
                },
            ],
        )

        projection = SqliteAllocationInventory(self.user_dao, self.static_dao).build(snapshot_id)
        drive = next(row for row in projection.items if row["item_type"] == "drive")
        tape = next(row for row in projection.items if row["item_type"] == "tape")
        self.assertEqual("H_3", drive["shape_id"])
        self.assertEqual({"攻击力": 24.0, "防御力": 16.0, "生命值": 280.0}, drive["sub_stats"])
        self.assertEqual("Suit11", tape["suit_id"])
        self.assertEqual("攻击力%", tape["main_stats"])

    def test_visual_tape_persists_the_catalogued_max_level_main_value(self) -> None:
        snapshot_id = import_vision_inventory(
            self.database_path,
            [{
                "uid": "tape_visual_main", "item_type": "tape", "quality": "Gold", "area": 15,
                "set_name": "失落光芒", "main_stats": "暴击率", "sub_stats": {"攻击力%": 10.0},
            }],
        )

        core = self.user_dao.list_inventory_items(snapshot_id, kind="core")[0]
        self.assertEqual("CritBase", core["main_stats"][0]["property_id"])
        # SQLite stores percentage values as fractions.  The visual importer
        # must store the configured 30% max-level value, not its old 1% stub.
        self.assertAlmostEqual(0.30, core["main_stats"][0]["value"])


if __name__ == "__main__":
    unittest.main()
