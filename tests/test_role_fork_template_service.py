# 验证公共角色弧盘模板仅由官方静态 DAO 投影并能增量刷新。
"""Official role/fork template cache regression tests."""

from __future__ import annotations

import unittest

from src.services.role_fork_template_service import (
    fork_templates_as_weapon_models,
    load_official_role_fork_templates,
)


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

    def test_release_demon_blade_includes_refinement_one_permanent_crit(self):
        models = fork_templates_as_weapon_models(load_official_role_fork_templates())
        model = next(item for item in models.values() if item["fork_id"] == "fork_DemonBlade")

        self.assertEqual(1, model["mix_level"])
        self.assertEqual(40.0, model["sub_stats"]["暴击率%"])
