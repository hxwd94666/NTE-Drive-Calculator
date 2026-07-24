# 验证 SQLite 稳定背包到现有配装求解器输入的内存投影。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


def stat(property_id: str, value: float, *, percent: bool = False) -> dict:
    return {
        "property_id": property_id,
        "value": value,
        "percent": percent,
        "names": {},
    }


def item(
    serial: int,
    slot: int,
    *,
    kind: str,
    discarded: bool = False,
) -> dict:
    module = kind == "module"
    return {
        "uid": {"serial": serial, "slot": slot},
        "kind": kind,
        "item_id": "cell3_style3_1_Orange" if module else "GetEfficiency_orange",
        "suit_id": "Suit6",
        "geometry": "ZhiJiao1" if module else "Core",
        "grid": 3 if module else None,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": False,
        "discarded": discarded,
        "equipped": False,
        "equipped_character_uid": None,
        "equipped_character_id": None,
        "equipped_placement": None,
        "names": {},
        "suit_names": {},
        "main_stats": (
            [stat("AtkAdd", 63.0), stat("HPMaxAdd", 840.0)]
            if module
            else [stat("DamageUpCosmosBase", 0.375, percent=True)]
        ),
        "sub_stats": [stat("CritDamageBase", 0.06, percent=True)],
    }


def snapshot(items: list[dict]) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": 1,
            "sequence": 1,
            "observed_at_unix_ms": 1_800_000_000_000,
            "item_count": len(items),
            "items": items,
        },
    }


class SqliteAllocationInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.static_database_env = patch.dict(
            "os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}
        )
        self.static_database_env.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_dao = UserDataDao(
            Path(self.temp_dir.name) / "user.sqlite3",
            account_id="projection-test",
        )
        self.static_dao = StaticGameDataDao()

    def tearDown(self) -> None:
        self.static_dao.close()
        self.user_dao.close()
        self.temp_dir.cleanup()
        self.static_database_env.stop()

    def test_projects_official_ids_without_writing_an_inventory_json(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(
                [
                    item(101, 11, kind="module"),
                    item(202, 22, kind="core"),
                    item(303, 33, kind="module", discarded=True),
                ]
            )
        )

        result = SqliteAllocationInventory(self.user_dao, self.static_dao).build(snapshot_id)

        self.assertEqual(result.snapshot_id, snapshot_id)
        self.assertEqual(result.discarded_count, 1)
        self.assertEqual(len(result.items), 3)
        drive = next(row for row in result.items if row["item_type"] == "drive")
        self.assertEqual(drive["uid"], "nte-module-11-101")
        self.assertEqual(drive["shape_id"], "L_3_BL")
        self.assertEqual(drive["set_name"], "未知套装")
        self.assertEqual(drive["quality"], "Gold")
        self.assertEqual(drive["main_stats"], {"攻击力": 63.0, "生命值": 840.0})
        self.assertEqual(drive["sub_stats"], {"暴击伤害%": 6.0})
        discarded_drive = next(row for row in result.items if row["uid"] == "nte-module-33-303")
        self.assertTrue(discarded_drive["discarded"])
        self.assertTrue(drive["is_duplicate_drive"])
        self.assertEqual(drive["duplicate_group_id"], discarded_drive["duplicate_group_id"])
        self.assertEqual(2, drive["duplicate_count"])
        self.assertTrue(drive["official"]["is_duplicate_drive"])
        core = next(row for row in result.items if row["item_type"] == "tape")
        self.assertEqual(core["uid"], "nte-core-22-202")
        self.assertEqual(core["shape_id"], "TAPE_15")
        self.assertEqual(core["set_name"], "失落光芒")
        self.assertEqual(core["main_stats"], "光属性异能伤害增强%")

    def test_rejects_implicit_current_snapshot(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "必须显式指定稳定背包快照",
        ):
            SqliteAllocationInventory(self.user_dao, self.static_dao).build()


if __name__ == "__main__":
    unittest.main()
