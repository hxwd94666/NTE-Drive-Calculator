# 验证角色页配装上下文被冻结为不追随活动指针的战报副本。
from __future__ import annotations

import unittest

from src.services.battle_build_equipment_service import (
    apply_equipment_override,
    freeze_equipment_context,
)


class BattleBuildEquipmentServiceTests(unittest.TestCase):
    def test_freeze_uses_matching_full_level_calculation_items(self) -> None:
        raw = {
            "kind": "core",
            "item_id": "Core_Test",
            "uid_slot": 7,
            "uid_serial": 11,
            "main_stats": [{"property_id": "AtkAdd", "value": 100.0}],
        }
        projected = {
            **raw,
            "main_stats": [{"property_id": "AtkAdd", "value": 777.0}],
        }

        frozen = freeze_equipment_context(
            {"items": [raw], "calculation_items": [projected]}
        )

        self.assertEqual(777.0, frozen[0]["stats"][0]["value"])
        raw["main_stats"][0]["value"] = 999.0
        projected["main_stats"][0]["value"] = 999.0
        self.assertEqual(777.0, frozen[0]["stats"][0]["value"])

    def test_explicit_empty_override_clears_equipment_but_missing_one_preserves_it(self) -> None:
        character = {"equipment": [{"item_id": "original"}]}

        self.assertFalse(apply_equipment_override(character, {}))
        self.assertEqual("original", character["equipment"][0]["item_id"])
        self.assertTrue(
            apply_equipment_override(
                character,
                {
                    "equipment_override": [],
                    "equipment_context_title": "空配装",
                    "equipment_source_kind": "battle_frozen",
                },
            )
        )
        self.assertEqual([], character["equipment"])


if __name__ == "__main__":
    unittest.main()
