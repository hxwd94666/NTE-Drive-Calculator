# 测试配装页结果到官方 SQLite 配装方案的转换。
"""测试配装页结果到官方 SQLite 配装方案的转换。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.saved_state_loadout_bridge import (
    SavedStateLoadoutBridge,
    SavedStateLoadoutError,
    character_id_for_saved_role,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def _inventory_item(
    *, slot: int, serial: int, kind: str, geometry: str | None = None
) -> dict:
    return {
        "uid": {"slot": slot, "serial": serial},
        "kind": kind,
        "item_id": "cell3_style1_1_Orange" if kind == "module" else "Attack_orange",
        "suit_id": "Suit1",
        "geometry": geometry,
        "grid": 3 if kind == "module" else None,
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
        "main_stats": [],
        "sub_stats": [],
    }


def _snapshot(items: list[dict]) -> dict:
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


class SavedStateLoadoutBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_dao = UserDataDao(
            Path(self.temp_dir.name) / "user.sqlite3", account_id="bridge-test"
        )
        self.snapshot_id = self.user_dao.import_inventory_snapshot(
            _snapshot(
                [
                    _inventory_item(
                        slot=41,
                        serial=410,
                        kind="module",
                        geometry="ZhiJiao2",
                    ),
                    _inventory_item(slot=51, serial=510, kind="core"),
                ]
            )
        )
        self.static_dao = StaticGameDataDao()

    def tearDown(self) -> None:
        self.static_dao.close()
        self.user_dao.close()
        self.temp_dir.cleanup()

    def test_saves_official_uid_and_anchor_for_legacy_l_shape(self) -> None:
        role_state = {
            "blueprint_layout": [
                ["XX", "XX", "XX", "XX", "XX"],
                ["XX", "L_3_TL", "L_3_TL", "XX", "XX"],
                ["XX", "L_3_TL", "XX", "XX", "XX"],
                ["XX", "XX", "XX", "XX", "XX"],
                ["XX", "XX", "XX", "XX", "XX"],
            ],
            "equipped_drives": [
                {"uid": "nte-module-41-410", "shape_id": "L_3_TL"}
            ],
            "equipped_tape": {"uid": "nte-core-51-510"},
        }

        saved = SavedStateLoadoutBridge(
            self.user_dao, self.static_dao
        ).save_role_plan(
            role_name="测试角色",
            role_state=role_state,
            character_id=1003,
        )

        plan = self.user_dao.get_loadout_plan(saved.plan_id)
        self.assertEqual(self.snapshot_id, saved.snapshot_id)
        self.assertEqual("saved-state-official-loadout-v1", plan["payload"]["schema"])
        self.assertEqual(
            {
                "uid_serial": 410,
                "uid_slot": 41,
                "kind": "module",
                "target_row": 2,
                "target_column": 2,
                "rotation": 0,
            },
            {
                key: plan["assignments"][0][key]
                for key in (
                    "uid_serial",
                    "uid_slot",
                    "kind",
                    "target_row",
                    "target_column",
                    "rotation",
                )
            },
        )
        self.assertEqual("core", plan["assignments"][1]["kind"])

    def test_rejects_uid_missing_from_current_snapshot(self) -> None:
        role_state = {
            "blueprint_layout": [
                ["H_2", "H_2", "XX", "XX", "XX"],
            ],
            "equipped_drives": [
                {"uid": "nte-module-999-999", "shape_id": "H_2"}
            ],
            "equipped_tape": {"uid": "nte-core-51-510"},
        }
        with self.assertRaisesRegex(SavedStateLoadoutError, "当前稳定背包"):
            SavedStateLoadoutBridge(self.user_dao, self.static_dao).save_role_plan(
                role_name="测试角色",
                role_state=role_state,
                character_id=1003,
            )

    def test_character_id_bridge_accepts_numeric_official_id(self) -> None:
        self.assertEqual(
            1003,
            character_id_for_saved_role("测试角色", {"测试角色": {"workshop_item_id": "1003"}}),
        )


if __name__ == "__main__":
    unittest.main()
