# 测试已保存配装使用当前角色养成生成完整面板。
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services import saved_loadout_attribute_summary_service as service


class _StaticDao:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SavedLoadoutAttributeSummaryServiceTests(unittest.TestCase):
    def test_combines_saved_equipment_with_current_profile(self) -> None:
        detail = {
            "profile": {
                "character_level": 80,
                "breakthrough_stage": 6,
                "likeability_level_10_enabled": True,
            },
            "growth_rows": [{
                "level": 80,
                "breakthrough_stage": 6,
                "atk_base": 100.0,
                "hp_base": 1000.0,
                "def_base": 100.0,
            }],
            "attributes": {
                "AtkAdd": {"display_name_zh": "攻击力", "show_percent": False},
                "AtkUp": {"display_name_zh": "攻击力%", "show_percent": True},
                "CritBase": {"display_name_zh": "暴击率", "show_percent": True},
            },
            "likeability_bonus": {
                "properties": [
                    {"property_id": "AtkUp", "value": 0.10},
                    {"property_id": "CritBase", "value": 0.04},
                ],
            },
            "world_bonus": {
                "yaodao_attack_add": 20.0,
                "quantao_crit_damage": 0.0,
            },
            "forks": (),
        }
        saved_item = {
            "kind": "core",
            "main_stats": ({"property_id": "AtkAdd", "value": 30.0},),
            "sub_stats": (),
        }
        with (
            patch.object(service, "load_official_role_detail", return_value=detail),
            patch.object(service, "StaticGameDataDao", _StaticDao),
            patch.object(
                service,
                "project_equipment_items_to_max_level",
                return_value=(saved_item,),
            ),
        ):
            summaries = service.load_saved_loadout_attribute_summaries(
                "user.sqlite3", "static.sqlite3", 1003, (saved_item,),
            )

        equipment = {row.key: row.value for row in summaries["equipment"]}
        character = {row.key: row.value for row in summaries["character"]}
        self.assertEqual(30.0, equipment["AtkAdd"])
        self.assertAlmostEqual(160.0, character["PanelAtk"])
        self.assertAlmostEqual(0.09, character["PanelCritRate"])


if __name__ == "__main__":
    unittest.main()
