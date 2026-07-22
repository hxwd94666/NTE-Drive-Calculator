# 测试官方 ID 配装入口的快照固定、套装约束和属性评分。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.sqlite_loadout_optimizer import (
    LoadoutOptimizationError,
    SqliteLoadoutOptimizer,
)
from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


def inventory_item(
    serial: int,
    slot: int,
    *,
    kind: str,
    item_id: str,
    geometry: str,
    suit_id: str,
    score: float,
) -> dict:
    return {
        "uid": {"serial": serial, "slot": slot},
        "kind": kind,
        "item_id": item_id,
        "suit_id": suit_id,
        "geometry": geometry,
        "grid": None if kind == "core" else 3,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": False,
        "discarded": False,
        "equipped": False,
        "equipped_character_uid": None,
        "equipped_character_id": None,
        "equipped_placement": None,
        "names": {},
        "suit_names": {},
        "main_stats": [
            {
                "property_id": "DamageUpIncantationBase",
                "value": score / 100.0,
                "percent": True,
                "names": {},
            }
        ],
        "sub_stats": [],
    }


def snapshot(generation: int, items: list[dict]) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation,
            "observed_at_unix_ms": 1_800_000_000_000 + generation,
            "item_count": len(items),
            "items": items,
        },
    }


class SqliteLoadoutOptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.static_database_env = patch.dict(
            "os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}
        )
        self.static_database_env.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_dao = UserDataDao(
            Path(self.temp_dir.name) / "user.sqlite3", account_id="optimizer-test"
        )
        self.static_dao = StaticGameDataDao()
        blueprint = self.static_dao.get_equipment_plan(1003)
        self.assertIsNotNone(blueprint)
        items = [
            inventory_item(
                1,
                1,
                kind="core",
                item_id="GetEfficiency_orange",
                geometry="Core",
                suit_id="Suit11",
                score=5,
            )
        ]
        for index, item_id in enumerate(blueprint["module_item_ids"], start=10):
            template = self.static_dao.get_equipment_item(item_id)
            geometry = template["geometry_id"].removeprefix("EquipmentGeometry_")
            suit_id = "Suit11" if geometry.startswith("ZhiJiao") else "Suit5"
            items.append(
                inventory_item(
                    index,
                    index,
                    kind="module",
                    item_id=item_id,
                    geometry=geometry,
                    suit_id=suit_id,
                    score=float(index),
                )
            )
        items.append(
            inventory_item(
                99,
                99,
                kind="module",
                item_id="cell3_style3_1_Orange",
                geometry="ZhiJiao1",
                suit_id="Suit5",
                score=999,
            )
        )
        self.first_snapshot_id = self.user_dao.import_inventory_snapshot(snapshot(1, items))
        self.user_dao.import_inventory_snapshot(snapshot(2, []))

    def tearDown(self) -> None:
        self.static_dao.close()
        self.user_dao.close()
        self.temp_dir.cleanup()
        self.static_database_env.stop()

    def test_uses_pinned_snapshot_and_enforces_official_suit_shapes(self) -> None:
        result = SqliteLoadoutOptimizer(self.static_dao, self.user_dao).optimize(
            1003,
            snapshot_id=self.first_snapshot_id,
            property_weights={"DamageUpIncantationBase": 2.0},
        )

        self.assertEqual(result.source_snapshot_id, self.first_snapshot_id)
        self.assertEqual(len(result.assignments), 8)
        self.assertNotIn(99, [item["uid_serial"] for item in result.assignments])
        self.assertIsNotNone(result.plan_id)
        saved = self.user_dao.get_loadout_plan(result.plan_id)
        self.assertEqual(saved["source_snapshot_id"], self.first_snapshot_id)
        self.assertEqual(saved["payload"]["schema"], "official-id-v1")
        self.assertEqual(saved["payload"]["target_suit_id"], "Suit11")

    def test_current_empty_snapshot_does_not_fall_back_to_historical_maximum(self) -> None:
        with self.assertRaisesRegex(LoadoutOptimizationError, "推荐核心"):
            SqliteLoadoutOptimizer(self.static_dao, self.user_dao).optimize(1003)


if __name__ == "__main__":
    unittest.main()
