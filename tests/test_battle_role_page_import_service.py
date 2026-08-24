# 验证角色页装备只能单向冻结到战报修改副本。
from __future__ import annotations

import unittest

from src.services.battle_role_page_import_service import (
    BattleRolePageImportService,
)


class BattleRolePageImportServiceTests(unittest.TestCase):
    def test_cultivation_only_preserves_existing_battle_equipment_override(self) -> None:
        existing = [{"kind": "core", "item_id": "BattleCore"}]
        profiles = BattleRolePageImportService.profiles(
            {
                "details": [{
                    "profile": {
                        "character_id": 1004,
                        "equipment_override": existing,
                    },
                    "character": {"character_id": 1004},
                    "equipment_contexts": {},
                }]
            },
            include_equipment=False,
        )

        self.assertEqual(existing, profiles[0]["equipment_override"])

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
