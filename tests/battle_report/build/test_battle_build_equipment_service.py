# 验证角色页配装上下文被冻结为不追随活动指针的战报副本。
from __future__ import annotations

import unittest

from src.services.battle_report.build.equipment import (
    BattleRolePageImportService,
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
            "grid_count": None,
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
        self.assertEqual(0, frozen[0]["grid_count"])
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


class BattleRolePageImportServiceTests(unittest.TestCase):
    def test_cultivation_only_preserves_existing_battle_equipment_override(self) -> None:
        existing = [{"kind": "core", "item_id": "BattleCore"}]
        profiles = BattleRolePageImportService.profiles(
            {
                "details": [{
                    "profile": {
                        "character_id": 1004,
                        "fork_id": "fork_Rose",
                        "fork_level": 70,
                        "fork_breakthrough_stage": 5,
                        "equipment_override": existing,
                    },
                    "character": {"character_id": 1004},
                    "equipment_contexts": {},
                }]
            },
            include_equipment=False,
        )

        self.assertEqual(existing, profiles[0]["equipment_override"])
        self.assertEqual(5, profiles[0]["fork_breakthrough_stage"])

    def test_equipment_import_freezes_current_role_page_context(self) -> None:
        profiles = BattleRolePageImportService.profiles(
            {
                "details": [{
                    "profile": {"character_id": 1004},
                    "character": {"character_id": 1004},
                    "equipment_contexts": {
                        "current": {
                            "available": True,
                            "title": "游戏当前",
                            "source_kind": "role_page_current",
                            "items": [{
                                "kind": "core",
                                "item_id": "CurrentCore",
                                "uid": {"slot": 7, "serial": 11},
                                "main_stats": [{
                                    "property_id": "AtkAdd",
                                    "value": 777.0,
                                    "is_percent": False,
                                }],
                            }],
                            "calculation_items": [],
                        }
                    },
                }]
            },
            include_equipment=True,
        )

        profile = profiles[0]
        self.assertEqual("current", profile["equipment_context_key"])
        self.assertEqual("role_page_current", profile["equipment_source_kind"])
        self.assertEqual("CurrentCore", profile["equipment_override"][0]["item_id"])
        self.assertEqual(7, profile["equipment_override"][0]["uid_slot"])
        self.assertEqual(777.0, profile["equipment_override"][0]["stats"][0]["value"])


if __name__ == "__main__":
    unittest.main()
