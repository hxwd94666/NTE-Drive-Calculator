# 验证公共角色弧盘模板仅由官方静态 DAO 投影并能增量刷新。
"""Official role/fork template cache regression tests."""

from __future__ import annotations

import unittest

from src.services.official_role.fork_templates import fork_templates_as_weapon_models


class OfficialRoleForkTemplateServiceTests(unittest.TestCase):
    def test_official_level_and_breakthrough_values_become_panel_stats(self):
        models = fork_templates_as_weapon_models({"forks": [{
            "fork_id": "fork_test", "name_zh": "测试弧盘", "fork_type_name_zh": "聚合",
            "max_star": 5,
            "upgrade_levels": [{"level": 20, "modifiers": [{"property_id": "AtkBase", "value": 100}]}],
            "breakthroughs": [{"stage": 1, "max_fork_level": 20, "modifiers": [
                {"property_id": "AtkBase", "value": 25},
                {"property_id": "CritBase", "value": 0.24},
            ]}],
            "permanent_properties": [
                {"refinement_level": 1, "property_id": "CritBase", "property_value": 0.16},
                {"refinement_level": 5, "property_id": "CritBase", "property_value": 0.32},
            ],
            "star_levels": [],
        }]})

        model = models["测试弧盘"]
        self.assertEqual("fork_test", model["fork_id"])
        self.assertEqual({"攻击力白值": 125.0, "暴击率%": 40.0}, model["sub_stats"])


    def test_selected_fork_uses_highest_available_breakthrough_for_known_crit(self):
        models = fork_templates_as_weapon_models({"forks": [{
            "fork_id": "fork_new", "name_zh": "新弧盘", "max_star": 5,
            "upgrade_levels": [{"level": 20, "modifiers": []}],
            "breakthroughs": [
                {"stage": 1, "max_fork_level": 20, "modifiers": [
                    {"property_id": "CritBase", "value": 0.1},
                ]},
                {"stage": 2, "max_fork_level": 40, "modifiers": [
                    {"property_id": "CritBase", "value": 0.2},
                ]},
            ],
            "permanent_properties": [],
        }]})

        self.assertEqual(20.0, models["新弧盘"]["sub_stats"]["暴击率%"])
        self.assertTrue(models["新弧盘"]["permanent_properties_known"])
