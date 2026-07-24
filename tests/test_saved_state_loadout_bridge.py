# 测试配装页结果到官方 SQLite 配装方案的转换。
"""测试配装页结果到官方 SQLite 配装方案的转换。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.saved_state_loadout_bridge import (
    SavedStateLoadoutBridge,
    SavedStateLoadoutError,
    character_id_for_saved_role,
    character_ids_for_saved_role,
    character_ids_for_static_role,
    resolve_character_id_for_saved_role,
    resolve_character_id_for_static_role,
)
from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


def _inventory_item(
    *,
    slot: int,
    serial: int,
    kind: str,
    geometry: str | None = None,
    equipped_character_id: int | None = None,
    equipped_character_uid: dict | None = None,
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
        "equipped": equipped_character_id is not None,
        "equipped_character_uid": equipped_character_uid,
        "equipped_character_id": equipped_character_id,
        "equipped_placement": None,
        "names": {},
        "suit_names": {},
        "main_stats": [],
        "sub_stats": [],
    }


def _snapshot(items: list[dict], *, generation: int = 1) -> dict:
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


class SavedStateLoadoutBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.static_database_env = patch.dict(
            "os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}
        )
        self.static_database_env.start()
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
        self.static_database_env.stop()

    def test_static_role_lookup_does_not_need_legacy_role_configuration(self) -> None:
        self.assertEqual((1003,), character_ids_for_static_role("早雾", self.static_dao))
        self.assertEqual((1046, 1051), character_ids_for_static_role("主角", self.static_dao))
        self.assertEqual(
            1003,
            resolve_character_id_for_static_role(
                "早雾", self.static_dao, self.user_dao, snapshot_id=self.snapshot_id
            ),
        )

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
            snapshot_id=self.snapshot_id,
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
        with self.assertRaisesRegex(SavedStateLoadoutError, "稳定背包"):
            SavedStateLoadoutBridge(self.user_dao, self.static_dao).save_role_plan(
                role_name="测试角色",
                role_state=role_state,
                character_id=1003,
                snapshot_id=self.snapshot_id,
            )

    def test_saves_module_only_plan_when_no_core_is_available(self) -> None:
        role_state = {
            "blueprint_layout": [
                ["XX", "XX", "XX", "XX", "XX"],
                ["XX", "L_3_TL", "L_3_TL", "XX", "XX"],
                ["XX", "L_3_TL", "XX", "XX", "XX"],
            ],
            "equipped_drives": [
                {"uid": "nte-module-41-410", "shape_id": "L_3_TL"}
            ],
        }
        saved = SavedStateLoadoutBridge(self.user_dao, self.static_dao).save_role_plan(
            role_name="测试角色", role_state=role_state, character_id=1003,
            snapshot_id=self.snapshot_id,
        )
        plan = self.user_dao.get_loadout_plan(saved.plan_id)
        self.assertEqual(1, saved.module_count)
        self.assertEqual(["module"], [row["kind"] for row in plan["assignments"]])

    def test_rejects_implicit_current_snapshot(self) -> None:
        with self.assertRaisesRegex(SavedStateLoadoutError, "必须显式指定"):
            SavedStateLoadoutBridge(self.user_dao, self.static_dao).prepare_role_plan(
                role_name="测试角色", role_state={}, character_id=1003,
            )

    def test_save_role_plan_uses_explicit_snapshot_after_current_changes(self) -> None:
        role_state = {
            "blueprint_layout": [
                ["XX", "XX", "XX", "XX", "XX"],
                ["XX", "L_3_TL", "L_3_TL", "XX", "XX"],
                ["XX", "L_3_TL", "XX", "XX", "XX"],
            ],
            "equipped_drives": [
                {"uid": "nte-module-41-410", "shape_id": "L_3_TL"}
            ],
            "equipped_tape": {"uid": "nte-core-51-510"},
        }
        self.user_dao.import_inventory_snapshot(
            _snapshot([_inventory_item(slot=99, serial=990, kind="module")], generation=2)
        )

        saved = SavedStateLoadoutBridge(
            self.user_dao, self.static_dao
        ).save_role_plan(
            role_name="测试角色",
            role_state=role_state,
            character_id=1003,
            snapshot_id=self.snapshot_id,
        )

        self.assertEqual(self.snapshot_id, saved.snapshot_id)
        self.assertEqual(
            self.snapshot_id,
            self.user_dao.get_loadout_plan(saved.plan_id)["source_snapshot_id"],
        )

    def test_character_id_bridge_accepts_numeric_official_id(self) -> None:
        self.assertEqual(
            1003,
            character_id_for_saved_role("测试角色", {"测试角色": {"workshop_item_id": "1003"}}),
        )

    def test_protagonist_exposes_both_official_character_ids(self) -> None:
        roles = {
            "主角": {
                "workshop_item_id": "1046",
                "workshop_item_ids": ["1046", "1051"],
            }
        }

        self.assertEqual((1046, 1051), character_ids_for_saved_role("主角", roles))

    def test_protagonist_selects_character_id_from_current_equipped_instance(self) -> None:
        character_uid = {"slot": 160762209, "serial": 347096673}
        self.user_dao.import_inventory_snapshot(
            _snapshot(
                [
                    _inventory_item(
                        slot=41,
                        serial=410,
                        kind="module",
                        geometry="ZhiJiao2",
                        equipped_character_id=1051,
                        equipped_character_uid=character_uid,
                    ),
                    _inventory_item(
                        slot=51,
                        serial=510,
                        kind="core",
                        equipped_character_id=1051,
                        equipped_character_uid=character_uid,
                    ),
                ],
                generation=2,
            )
        )
        roles = {
            "主角": {
                "workshop_item_id": "1046",
                "workshop_item_ids": ["1046", "1051"],
            }
        }

        self.assertEqual(
            1051,
            resolve_character_id_for_saved_role("主角", roles, self.user_dao),
        )

    def test_male_protagonist_selects_1046_from_current_instance(self) -> None:
        character_uid = {"slot": 71, "serial": 710}
        self.user_dao.import_inventory_snapshot(
            _snapshot(
                [
                    _inventory_item(
                        slot=41,
                        serial=410,
                        kind="module",
                        geometry="ZhiJiao2",
                        equipped_character_id=1046,
                        equipped_character_uid=character_uid,
                    ),
                    _inventory_item(slot=51, serial=510, kind="core"),
                ],
                generation=2,
            )
        )
        roles = {
            "主角": {
                "workshop_item_id": "1046",
                "workshop_item_ids": ["1046", "1051"],
            }
        }

        self.assertEqual(
            1046,
            resolve_character_id_for_saved_role("主角", roles, self.user_dao),
        )

    def test_protagonist_uses_requested_snapshot_not_later_current_snapshot(self) -> None:
        male_uid = {"slot": 71, "serial": 710}
        requested_snapshot_id = self.user_dao.import_inventory_snapshot(
            _snapshot(
                [
                    _inventory_item(
                        slot=41,
                        serial=410,
                        kind="module",
                        geometry="ZhiJiao2",
                        equipped_character_id=1046,
                        equipped_character_uid=male_uid,
                    )
                ],
                generation=2,
            )
        )
        self.user_dao.import_inventory_snapshot(
            _snapshot(
                [
                    _inventory_item(
                        slot=42,
                        serial=420,
                        kind="module",
                        geometry="ZhiJiao2",
                        equipped_character_id=1051,
                        equipped_character_uid={"slot": 72, "serial": 720},
                    )
                ],
                generation=3,
            )
        )
        roles = {
            "主角": {
                "workshop_item_id": "1046",
                "workshop_item_ids": ["1046", "1051"],
            }
        }

        self.assertEqual(
            1046,
            resolve_character_id_for_saved_role(
                "主角",
                roles,
                self.user_dao,
                snapshot_id=requested_snapshot_id,
            ),
        )

    def test_protagonist_uses_history_when_current_instance_is_empty(self) -> None:
        character_uid = {"slot": 160762209, "serial": 347096673}
        self.user_dao.import_inventory_snapshot(
            _snapshot(
                [
                    _inventory_item(
                        slot=41,
                        serial=410,
                        kind="module",
                        geometry="ZhiJiao2",
                        equipped_character_id=1051,
                        equipped_character_uid=character_uid,
                    )
                ],
                generation=2,
            )
        )
        self.user_dao.import_inventory_snapshot(
            _snapshot(
                [_inventory_item(slot=41, serial=410, kind="module")],
                generation=3,
            )
        )
        roles = {
            "主角": {
                "workshop_item_id": "1046",
                "workshop_item_ids": ["1046", "1051"],
            }
        }

        self.assertEqual(
            1051,
            resolve_character_id_for_saved_role("主角", roles, self.user_dao),
        )

    def test_protagonist_uses_persisted_manual_mapping_when_never_equipped(self) -> None:
        self.user_dao.upsert_character_instance_mapping(1051, {"slot": 72, "serial": 720})
        roles = {"主角": {"workshop_item_id": "1046", "workshop_item_ids": ["1046", "1051"]}}
        self.assertEqual(1051, resolve_character_id_for_saved_role("主角", roles, self.user_dao))


if __name__ == "__main__":
    unittest.main()
