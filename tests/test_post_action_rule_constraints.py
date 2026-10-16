# 测试扫描后处理规则的范围、动作优先级和约束校验。
import unittest
from itertools import permutations
from types import SimpleNamespace

from src.domain.stat_catalog import StatCatalog
from src.domain.equipment.matching import EquipmentMatchContext, RoleSuitMatch
from src.domain.post_actions import (
    preserve_rule_matches_item,
    default_post_action_config,
    merge_post_action_config,
    validate_post_action_config,
    build_state_changes,
    target_state_for_item,
    summarize_post_action_filtering,
)
from src.models.equipment import Drive, Tape


# 测试评分范围时显式提供合法匹配，不再借用“上下文缺失即全角色”的旧回退。
MATCH_CONTEXT = EquipmentMatchContext(matches=(RoleSuitMatch(
    "角色", 1, "suit", "套装甲", True, frozenset({"H_3"}), frozenset()),))


class PostActionRuleConstraintTests(unittest.TestCase):
    def test_processing_category_filters_execution_even_when_other_ranges_are_selected(self):
        drive = Drive(uid="range-drive", quality="Gold", area=3, shape_id="H_3",
                      main_stats={"攻击力": 1.0, "生命值": 1.0}, sub_stats={}, role_scores={"角色": 0.0})
        tape = Tape(uid="range-tape", quality="Gold", area=15, set_name="套装甲",
                    main_stats="攻击力", sub_stats={}, role_scores={"角色": 0.0})
        scoring = SimpleNamespace(roles_db={"角色": {}}, get_grade_tag=lambda *_args: "S")
        for module, target in (("discard", "discarded"), ("lock", "locked")):
            config = default_post_action_config()
            config[module].update(enabled=True, grade="S", shape_ids=["H_3"], set_names=["套装甲"])
            for kind, chosen, excluded in (("drive", drive, tape), ("tape", tape, drive)):
                config[module]["type_scope"] = kind
                self.assertEqual(target, target_state_for_item(chosen, "normal", config, scoring, score_context=MATCH_CONTEXT))
                self.assertEqual("normal", target_state_for_item(excluded, "normal", config, scoring, score_context=MATCH_CONTEXT))
                summary = summarize_post_action_filtering([(1, drive, "normal"), (2, tape, "normal")], config)
                self.assertEqual(1, summary["post_action_candidate_count"])
                self.assertEqual(1, summary["post_action_type_filtered_count"])

    def test_selected_role_scope_is_persisted_inside_post_action_config(self):
        config = default_post_action_config()
        config["discard"]["enabled"] = True
        config["discard"]["role_scope"] = "selected"

        self.assertIn("指定角色", validate_post_action_config(config))

        # 旧文件没有模块内身份列表；只在迁移时把共享值分别复制到两侧。
        for module in ("discard", "lock"):
            config[module].pop("selected_character_ids", None)
        config["selected_character_ids"] = [1003, "1003", "bad", 1004]
        normalized = merge_post_action_config(config)

        self.assertNotIn("selected_character_ids", normalized)
        self.assertEqual([1003, 1004], normalized["discard"]["selected_character_ids"])
        self.assertEqual([1003, 1004], normalized["lock"]["selected_character_ids"])
        self.assertIsNone(validate_post_action_config(normalized))
        normalized["lock"]["selected_character_ids"].clear()
        normalized["lock"].update(enabled=True, role_scope="selected")
        self.assertIn("锁定模块", validate_post_action_config(normalized))
        self.assertEqual([1003, 1004], merge_post_action_config(normalized)["discard"]["selected_character_ids"])

    def test_explicit_empty_module_selection_does_not_reuse_legacy_or_other_module(self):
        normalized = merge_post_action_config({
            "selected_character_ids": [1003],
            "discard": {"selected_character_ids": [], "enabled": True, "role_scope": "selected"},
            "lock": {"selected_character_ids": [1004]},
        })
        self.assertEqual([], normalized["discard"]["selected_character_ids"])
        self.assertEqual([1004], normalized["lock"]["selected_character_ids"])
        self.assertIn("弃置模块", validate_post_action_config(normalized))

    def test_direct_discard_is_normalized_planned_and_order_independent(self):
        item = Drive(uid="rule-drive", quality="Gold", area=3, shape_id="H_3",
                     main_stats={"攻击力": 1.0, "生命值": 1.0}, sub_stats={"攻击力": 1.0}, role_scores={"角色": 0.0})
        scoring = SimpleNamespace(roles_db={})
        rules = [{"name": action, "item_type": "drive", "action": action,
                  "sub_stats": ["攻击力"], "sub_match": 1}
                 for action in ("discard", "keep", "lock")]
        config = default_post_action_config()
        config["preserve_rules"] = rules[:1]
        normalized = merge_post_action_config(config)
        self.assertEqual("discard", normalized["preserve_rules"][0]["action"])
        self.assertIsNone(validate_post_action_config(normalized))
        changes = build_state_changes([(1, item, "normal")], normalized, scoring)
        self.assertEqual(["discarded"], [change["target_state"] for change in changes])
        self.assertEqual([], build_state_changes([(1, item, "discarded")], normalized, scoring))
        self.assertEqual([], build_state_changes([(1, item, "locked")], normalized, scoring))
        normalized["discard"]["on_locked"] = "normal"
        self.assertEqual("discarded", target_state_for_item(item, "locked", normalized, scoring, score_context=MATCH_CONTEXT))
        normalized["lock"].update(enabled=True, on_locked="normal")
        scoring.roles_db = {"角色": {}}
        scoring.get_grade_tag = lambda *_args: "D"
        self.assertEqual("discarded", target_state_for_item(item, "locked", normalized, scoring, score_context=MATCH_CONTEXT))
        normalized["discard"]["on_locked"] = "skip"
        self.assertEqual("normal", target_state_for_item(item, "locked", normalized, scoring, score_context=MATCH_CONTEXT))
        scoring.get_grade_tag = lambda *_args: "SSS"
        self.assertEqual("locked", target_state_for_item(item, "normal", normalized, scoring, score_context=MATCH_CONTEXT))
        # 自定义展示顺序不是 first-hit；直接锁定 > 直接弃置 > 仅保留。
        for ordered in permutations(rules):
            config["preserve_rules"] = list(ordered)
            self.assertEqual("locked", target_state_for_item(item, "normal", config, scoring))
        for ordered in permutations(rules[:2]):
            config["preserve_rules"] = list(ordered)
            self.assertEqual("discarded", target_state_for_item(item, "normal", config, scoring))

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
        self.assertTrue(preserve_rule_matches_item(drive, rule))
        rule["required_sub_stats"] = ["暴击伤害%"]
        self.assertFalse(preserve_rule_matches_item(drive, rule))

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
        self.assertFalse(preserve_rule_matches_item(drive, rule))


if __name__ == "__main__":
    unittest.main()
