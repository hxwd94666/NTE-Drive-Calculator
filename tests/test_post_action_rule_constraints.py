# 测试扫描后处理预留规则的约束校验。
import unittest

from src.domain.stat_catalog import StatCatalog
from src.features.scanning.post_actions import (
    _preserve_rule_matches_item,
    default_post_action_config,
    validate_post_action_config,
)
from src.models.equipment import Drive


class PostActionRuleConstraintTests(unittest.TestCase):
    def test_catalog_exposes_strict_main_and_sub_stat_pools(self):
        catalog = StatCatalog.from_config_dir("config")
        self.assertEqual(set(catalog.tape_main_values), set(catalog.tape_main_stat_pool()))
        self.assertEqual(set(catalog.tape_stat_values), set(catalog.tape_sub_stat_pool()))
        self.assertIn("防御力", catalog.tape_sub_stat_pool())
        self.assertFalse(any("属性异能伤害" in stat for stat in catalog.tape_sub_stat_pool()))

    def test_validation_rejects_insufficient_sub_stat_pool(self):
        config = default_post_action_config()
        config["preserve_rules"] = [{
            "enabled": True,
            "name": "invalid-count",
            "item_type": "drive",
            "sub_stats": ["攻击力"],
            "required_sub_stats": [],
            "sub_match": 2,
        }]
        self.assertIn("少于命中数量", validate_post_action_config(config))

    def test_validation_rejects_too_many_required_stats(self):
        config = default_post_action_config()
        config["preserve_rules"] = [{
            "enabled": True,
            "name": "invalid-required",
            "item_type": "drive",
            "sub_stats": ["攻击力", "暴击率"],
            "required_sub_stats": ["攻击力", "暴击率"],
            "sub_match": 2,
        }]
        self.assertIn("必须包含", validate_post_action_config(config))

    def test_validation_rejects_required_stat_outside_match_pool(self):
        config = default_post_action_config()
        config["preserve_rules"] = [{
            "enabled": True,
            "name": "invalid-required-pool",
            "item_type": "drive",
            "sub_stats": ["A", "B"],
            "required_sub_stats": ["C"],
            "sub_match": 2,
        }]
        self.assertIn("副词条命中池", validate_post_action_config(config))

    def test_validation_rejects_required_stats_without_a_large_enough_match_pool(self):
        config = default_post_action_config()
        config["preserve_rules"] = [{
            "enabled": True,
            "name": "required-without-pool",
            "item_type": "tape",
            "main_stats": ["攻击力%"],
            "sub_stats": [],
            "required_sub_stats": ["暴击率"],
            "sub_match": 2,
        }]
        self.assertIn("副词条命中池", validate_post_action_config(config))

    def test_required_sub_stats_must_all_be_present_in_addition_to_match_count(self):
        drive = Drive(
            uid="rule-required",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力": 10.0, "暴击率": 5.0},
        )
        rule = {
            "enabled": True,
            "item_type": "drive",
            "quality_scope": "all",
            "shape_ids": ["H_3"],
            "sub_stats": ["攻击力", "暴击率", "暴击伤害%"],
            "required_sub_stats": ["攻击力"],
            "sub_match": 2,
        }
        self.assertTrue(_preserve_rule_matches_item(drive, rule))
        rule["required_sub_stats"] = ["暴击伤害%"]
        self.assertFalse(_preserve_rule_matches_item(drive, rule))

    def test_required_stat_outside_match_pool_never_matches(self):
        drive = Drive(
            uid="invalid-required-pool",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"A": 1.0, "B": 1.0, "C": 1.0},
        )
        rule = {
            "enabled": True,
            "item_type": "drive",
            "quality_scope": "all",
            "shape_ids": ["H_3"],
            "sub_stats": ["A", "B"],
            "required_sub_stats": ["C"],
            "sub_match": 2,
        }
        self.assertFalse(_preserve_rule_matches_item(drive, rule))


if __name__ == "__main__":
    unittest.main()
