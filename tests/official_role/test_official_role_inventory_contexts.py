# 验证轻量角色详情与按需替换候选仍保留槽位快照、真实 UID 和锁约束。
"""Narrow saved-item reads and deferred pools must preserve equipment semantics."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.official_role.inventory_contexts import load_role_inventory_contexts, load_role_replacement_detail


def item(serial, slot=1):
    return {"uid_serial": serial, "uid_slot": slot, "kind": "module", "item_id": "drive", "level": 1,
            "main_stats": [], "sub_stats": [], "equipped": False}


class _Inventory:
    def __init__(self):
        self.reads = []
        self.snapshots = {10: [item(1), item(2), item(3)], 11: [item(4)], 20: [item(9)]}
        self.plans = [
            {"slot": {"slot_id": 1, "slot_key": "primary", "sort_order": 0, "character_id": 1001, "slot_name": "主力"},
             "plan": {"source_snapshot_id": 10, "assignments": [{**item(1), "target_row": 2, "target_column": 3}]}},
            {"slot": {"slot_id": 2, "slot_key": "secondary", "sort_order": 1, "character_id": 1001, "slot_name": "备用"},
             "plan": {"source_snapshot_id": 11, "assignments": [item(4)]}},
        ]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def current_inventory_snapshot_id(self):
        return 20

    def inventory_snapshot_summary(self, snapshot_id):
        return {"snapshot_id": snapshot_id, "source": "nte_core"}

    def list_inventory_items(self, snapshot_id, *, uids=None, **_filters):
        self.reads.append((snapshot_id, uids))
        return deepcopy([row for row in self.snapshots[snapshot_id]
                         if uids is None or (row["uid_serial"], row["uid_slot"]) in uids])

    def list_current_loadout_slot_plans(self):
        return deepcopy(self.plans)

    def list_current_loadout_equipment_owners(self):
        return [{"uid_slot": 1, "uid_serial": 2, "character_id": 1002}]

    def list_allocation_locked_equipment_owners(self):
        return [{"uid_slot": 1, "uid_serial": 3}]


class _Static:
    database_path = Path("fixture-static.sqlite3")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def list_characters(self):
        return [{"character_id": 1002, "name_zh": "fixture"}]


class OfficialRoleInventoryContextsTests(unittest.TestCase):
    def setUp(self):
        from src.integrations.static_catalog_release import StaticCatalogReleaseReader
        release = StaticCatalogReleaseReader().freeze(Path(__file__).resolve().parents[2] / "data/game_static.sqlite3")
        self.source = {"static_database_path": str(release.database_path), "static_schema_version": release.schema_version,
                       "static_release": release}
        self.inventory = _Inventory()
        self.static = _Static()
        self.catalog = SimpleNamespace(character_icon=lambda _id: None)
        self.character = {"character_id": 1001, "name_zh": "fixture role"}

    def load(self, candidates):
        return load_role_inventory_contexts(
            self.inventory, self.static, self.catalog, self.character, 1001, lambda _key, factory: factory(),
            include_candidates=candidates,
        )

    def test_light_model_keeps_items_coordinates_and_secondary_source(self):
        full = self.load(True)
        self.inventory.reads.clear()
        light = self.load(False)
        self.assertEqual(full["saved_items"], light["saved_items"])
        self.assertEqual(full["extra_saved_contexts"]["saved:2"]["items"], light["extra_saved_contexts"]["saved:2"]["items"])
        self.assertEqual(2, light["saved_items"][0]["target_row"])
        self.assertEqual([], light["replacement_items"])
        self.assertEqual([], light["extra_saved_contexts"]["saved:2"]["replacement_items"])
        self.assertIn((10, ((1, 1),)), self.inventory.reads)
        self.assertIn((11, ((4, 1),)), self.inventory.reads)
        self.assertNotIn((10, None), self.inventory.reads)

    def test_deferred_pool_uses_selected_historical_snapshot_and_current_locks(self):
        light = self.load(False)
        detail = {**self.source, "equipment_contexts": {
            "saved": {"items": light["saved_items"], "plan": light["saved_plan"]},
            "saved:2": light["extra_saved_contexts"]["saved:2"],
        }}
        self.inventory.reads.clear()
        with patch("src.storage.sqlite.user_data_dao.UserDataDao", return_value=self.inventory), patch(
            "src.integrations.game_assets.catalog.GameUiAssetCatalog", return_value=self.catalog
        ):
            enriched = load_role_replacement_detail(
                "fixture-user", self.source["static_database_path"], None, detail, "saved",
                static_schema_version=self.source["static_schema_version"], static_release=self.source["static_release"],
            )
        pool = enriched["equipment_contexts"]["saved"]["replacement_items"]
        self.assertEqual([(10, None)], self.inventory.reads)
        self.assertEqual([1, 2, 3], [row["uid_serial"] for row in pool])
        self.assertTrue(pool[1]["equipped"])
        self.assertEqual(1002, pool[1]["equipped_character_id"])
        self.assertTrue(pool[2]["allocation_reserved"])
        self.assertEqual(light["saved_items"], enriched["equipment_contexts"]["saved"]["items"])
        self.assertNotIn("replacement_items", detail["equipment_contexts"]["saved"])


if __name__ == "__main__":
    unittest.main()
