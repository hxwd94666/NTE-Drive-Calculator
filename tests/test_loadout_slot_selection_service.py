"""Public behavior tests for explicit role-loadout slot selection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.test_role_loadout_slots import assignment, inventory_snapshot


class LoadoutSlotSelectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "user_data.sqlite3"
        self.dao = UserDataDao(self.database, account_id="selection-test")
        self.snapshot_id = self.dao.import_inventory_snapshot(inventory_snapshot())
        self.service = LoadoutSlotSelectionService(self.dao)

    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

    def _save_primary(self, character_id: int, role_name: str) -> int:
        self.dao.save_loadout_plan(
            name=f"{role_name} 主力",
            character_id=character_id,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={
                "schema": "allocation-official-snapshot-v1",
                "source_role_name": role_name,
            },
        )
        return self.dao.list_loadout_slots(character_id)[0]["slot_id"]

    def test_resolves_current_slot_with_role_snapshot_and_plan(self) -> None:
        primary_slot_id = self._save_primary(1003, "甲")

        selection = self.service.resolve([primary_slot_id], require_native_snapshot=True)[0]

        self.assertEqual(primary_slot_id, selection.slot_id)
        self.assertEqual(1003, selection.character_id)
        self.assertEqual("甲", selection.role_name)
        self.assertEqual(self.snapshot_id, selection.source_snapshot_id)
        self.assertTrue(selection.plan["is_active"])

    def test_rejects_two_slots_for_one_role_before_any_apply(self) -> None:
        primary_slot_id = self._save_primary(1003, "甲")
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        self.dao.save_plan_to_slot(
            secondary_slot_id,
            name="甲 副本",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={
                "schema": "allocation-official-snapshot-v1",
                "source_role_name": "甲",
            },
        )

        with self.assertRaisesRegex(UserDataValidationError, "一次装配只能选择一个槽位"):
            self.service.resolve([primary_slot_id, secondary_slot_id])

    def test_rejects_same_equipment_in_selected_roles(self) -> None:
        primary_slot_id = self._save_primary(1003, "甲")
        secondary_slot_id = self.dao.create_loadout_slot(1004, "主力", slot_key="primary")
        self.dao.save_plan_to_slot(
            secondary_slot_id,
            name="乙 主力",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={
                "schema": "allocation-official-snapshot-v1",
                "source_role_name": "乙",
            },
        )

        with self.assertRaisesRegex(UserDataValidationError, "同时出现在"):
            self.service.resolve([primary_slot_id, secondary_slot_id])

    def test_rejects_missing_or_visual_snapshot_for_fast_apply(self) -> None:
        slot_id = self._save_primary(1003, "甲")
        self.dao._db().execute(
            "UPDATE inventory_snapshot SET source = 'vision' WHERE snapshot_id = ?",
            (self.snapshot_id,),
        )
        self.dao._db().commit()

        with self.assertRaisesRegex(UserDataValidationError, "极速装配只支持 nte-core 原生 UID"):
            self.service.resolve([slot_id], require_native_snapshot=True)

    def test_rejects_custom_role_for_fast_apply_even_with_native_snapshot(self) -> None:
        custom = self.dao.create_custom_character("自建角色")
        slot_id = self._save_primary(int(custom["character_id"]), "自建角色")

        with self.assertRaisesRegex(UserDataValidationError, "是自建角色"):
            self.service.resolve([slot_id], require_native_snapshot=True)


if __name__ == "__main__":
    unittest.main()

