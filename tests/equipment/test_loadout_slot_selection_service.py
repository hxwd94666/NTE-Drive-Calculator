# 测试具名配装槽位选择服务。
"""Public behavior tests for explicit role-loadout slot selection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.equipment.loadouts.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.equipment.test_role_loadout_slots import assignment, inventory_snapshot


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

    def test_confirmation_reference_rejects_replaced_plan_without_consuming_latest(self) -> None:
        slot_id = self._save_primary(1003, "甲")
        selected = self.service.resolve([slot_id])[0]
        references = {slot_id: (selected.plan_id, selected.source_snapshot_id)}
        self.service.assert_current_plans(references)
        self.dao.save_plan_to_slot(slot_id, name="新方案", assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "甲"})
        with self.assertRaisesRegex(UserDataValidationError, "已变化"):
            self.service.assert_current_plans(references)

    def test_fast_apply_checks_frozen_identity_before_constructing_input_service(self) -> None:
        from unittest.mock import Mock
        from src.services.equipment.assembly.bulk_apply import BulkEquipmentApplyService
        slot_id = self._save_primary(1003, "甲")
        selected = self.service.resolve([slot_id])[0]
        factory = Mock()
        service = BulkEquipmentApplyService(self.database, object(), apply_service_factory=factory, operation_guard=lambda _capability: None,
            expected_plans={slot_id: (selected.plan_id + 1, selected.source_snapshot_id)})
        with self.assertRaisesRegex(UserDataValidationError, "已变化"):
            service.run(slot_ids=[slot_id])
        factory.assert_not_called()

    def test_fast_apply_does_not_consume_a_plan_replaced_during_preparation(self) -> None:
        from unittest.mock import Mock, patch
        from src.services.equipment.assembly.bulk_apply import BulkEquipmentApplyService
        slot_id = self._save_primary(1003, "甲")
        selected = self.service.resolve([slot_id])[0]
        factory = Mock()
        factory.return_value.require_stable_snapshot.return_value = self.snapshot_id
        original = LoadoutSlotSelectionService.assert_current_plans

        def changed_after_gate(service, references, **kwargs):
            original(service, references, **kwargs)
            self.dao.save_plan_to_slot(slot_id, name="并发保存", assignments=[assignment()],
                source_snapshot_id=self.snapshot_id, payload={"source_role_name": "甲"})

        service = BulkEquipmentApplyService(self.database, object(), apply_service_factory=factory,
            operation_guard=lambda _capability: None,
            expected_plans={slot_id: (selected.plan_id, selected.source_snapshot_id)})
        with patch.object(LoadoutSlotSelectionService, "assert_current_plans", changed_after_gate):
            with self.assertRaisesRegex(UserDataValidationError, "已变化"):
                service.run(slot_ids=[slot_id])
        factory.return_value.validate_plan_for_fast_apply.assert_not_called()

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

        selected = self.service.resolve([primary_slot_id, secondary_slot_id], defer_equipment_conflicts=True)
        references = {row.slot_id: (row.plan_id, row.source_snapshot_id) for row in selected}
        # 身份核对可先于虚拟／缺件剔除；默认视觉调用方仍保留装备冲突门禁。
        self.service.assert_current_plans(references, defer_equipment_conflicts=True)
        with self.assertRaisesRegex(UserDataValidationError, "同时出现在"):
            self.service.assert_current_plans(references)
        self.dao.save_plan_to_slot(primary_slot_id, name="新方案", assignments=[assignment()],
            source_snapshot_id=self.snapshot_id, payload={"source_role_name": "甲"})
        with self.assertRaisesRegex(UserDataValidationError, "已变化"):
            self.service.assert_current_plans(references, defer_equipment_conflicts=True)

    def test_rejects_missing_or_visual_snapshot_for_fast_apply(self) -> None:
        slot_id = self._save_primary(1003, "甲")
        self.dao._db().execute(
            "UPDATE inventory_snapshot SET source = 'vision' WHERE snapshot_id = ?",
            (self.snapshot_id,),
        )
        self.dao._db().commit()

        with self.assertRaisesRegex(UserDataValidationError, "极速装配需要原生同步提供的装备标识"):
            self.service.resolve([slot_id], require_native_snapshot=True)

    def test_rejects_custom_role_for_fast_apply_even_with_native_snapshot(self) -> None:
        custom = self.dao.create_custom_character("自建角色")
        slot_id = self._save_primary(int(custom["character_id"]), "自建角色")

        with self.assertRaisesRegex(UserDataValidationError, "是自建角色"):
            self.service.resolve([slot_id], require_native_snapshot=True)


if __name__ == "__main__":
    unittest.main()

