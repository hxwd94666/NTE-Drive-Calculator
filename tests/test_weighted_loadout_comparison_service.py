# 测试计算结果与已保存配装槽位的冻结新旧对比。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.features.allocation.plan_diff_pairing import pair_drive_diff_items
from src.optimizer.contracts import DIFF_ADDED, DIFF_CHANGED, DIFF_REMOVED
from src.services.weighted_loadout_comparison_service import (
    freeze_weighted_loadout_comparisons,
    refresh_weighted_loadout_comparisons,
)


def _stat(property_id: str, value: float):
    return SimpleNamespace(property_id=property_id, value=value, percent=False)


class _UserDao:
    def list_loadout_slots(self, character_id):
        assert character_id == 1003
        return [{
            "slot_id": 9,
            "slot_key": "primary",
            "slot_name": "主力",
            "current_plan": {
                "source_snapshot_id": 7,
                "payload": {"assignment_scores": {"nte-module-1-10": 12.0}},
                "assignments": [{
                    "kind": "module",
                    "uid_slot": 1,
                    "uid_serial": 10,
                    "target_row": 1,
                    "target_column": 1,
                    "raw_assignment": {},
                }],
            },
        }]

    def list_inventory_items(self, snapshot_id, *, uids):
        assert snapshot_id == 7
        assert uids == {(10, 1)}
        return [{
            "kind": "module",
            "uid_slot": 1,
            "uid_serial": 10,
            # Real inventory payloads may use the shorthand while allocation
            # assignments use the fully-qualified static shape ID.
            "geometry": "Hen2",
            "quality": "orange",
            "main_stats": (),
            "sub_stats": ({"property_id": "AtkAdd", "value": 12.0},),
        }]


class _StaticDao:
    def list_equipment_attributes(self):
        return [{"attribute_id": "AtkAdd", "display_name_zh": "攻击力"}]

    def list_suits(self):
        return []

    def list_shapes(self):
        return [{"shape_id": "EquipmentGeometry_Hen2", "legacy_shape_id": "H_2"}]


class WeightedLoadoutComparisonServiceTests(unittest.TestCase):
    def test_freezes_changed_slot_with_rich_old_and_new_rows(self):
        candidate = SimpleNamespace(
            uid=(2, 20), kind="module", quality="purple",
            grid_count=2, geometry="EquipmentGeometry_Hen2",
            sub_stats=(_stat("AtkAdd", 20.0),), main_stats=(), suit_id=None,
        )
        assignment = SimpleNamespace(
            uid=(2, 20), kind="module", score=20.0, grid_count=2,
            geometry="EquipmentGeometry_Hen2", suit_id=None,
        )
        option = SimpleNamespace(character_id=1003, assignments=(assignment,))
        context = SimpleNamespace(candidates=(candidate,), attributes=())

        result = freeze_weighted_loadout_comparisons(
            _UserDao(), _StaticDao(), context, (option,),
        )

        comparison = result[1003][0]
        self.assertTrue(comparison.diff[DIFF_CHANGED])
        self.assertEqual("nte-module-1-10", comparison.diff[DIFF_REMOVED][0]["uid"])
        self.assertEqual("nte-module-2-20", comparison.diff[DIFF_ADDED][0]["uid"])
        self.assertEqual("Purple", comparison.diff[DIFF_ADDED][0]["quality"])
        pairs, unmatched_old, unmatched_new = pair_drive_diff_items(
            list(comparison.diff[DIFF_REMOVED]),
            list(comparison.diff[DIFF_ADDED]),
        )
        self.assertEqual(1, len(pairs))
        self.assertFalse(unmatched_old)
        self.assertFalse(unmatched_new)
        self.assertEqual("H_2", pairs[0][0]["shape_id"])
        self.assertEqual("H_2", pairs[0][1]["shape_id"])
        self.assertEqual(2, pairs[0][0]["area"])

        unchanged = SimpleNamespace(
            character_id=1003,
            assignments=(SimpleNamespace(
                uid=(1, 10), kind="module", score=12.0, grid_count=2,
                geometry="EquipmentGeometry_Hen2", suit_id=None,
            ),),
        )
        refreshed = refresh_weighted_loadout_comparisons(
            result, SimpleNamespace(candidates=(), attributes=()), (unchanged,),
        )
        self.assertFalse(refreshed[1003][0].diff[DIFF_CHANGED])


if __name__ == "__main__":
    unittest.main()
