# 暴击阈值与等级门槛 domain 测试。
import unittest

from src.domain.crit_threshold import (
    CRIT_RANK_BONUS,
    DEFAULT_CRIT_THRESHOLD,
    character_crit_baseline,
    character_crit_total,
    crit_floor_enabled,
    crit_rank_adjustment,
    drive_has_crit,
    loadout_crit_total,
    meets_preference_grade_limit,
    normalize_preference_config,
    persistable_stat_priority_config,
    preference_config_active,
)
from src.domain.grade_limits import meets_min_grade

ALLOWED = frozenset({"攻击力%", "暴击率%", "Crit"})


class CritThresholdDomainTests(unittest.TestCase):
    def test_default_crit_threshold_is_five(self):
        self.assertEqual(5.0, DEFAULT_CRIT_THRESHOLD)
        normalized = normalize_preference_config({"stats": ["攻击力%"]})
        self.assertEqual(5.0, normalized["crit_threshold"])

    def test_normalize_keeps_legacy_crit_min_threshold(self):
        normalized = normalize_preference_config({"stats": ["暴击率%"], "crit_min_threshold": 18})
        self.assertEqual(18.0, normalized["crit_threshold"])

    def test_preference_config_active_ignores_empty(self):
        self.assertFalse(preference_config_active(None))
        self.assertFalse(preference_config_active({}))
        self.assertTrue(preference_config_active({"stats": ["攻击力%"]}))
        self.assertTrue(preference_config_active({"min_grade_limit": "S"}))
        self.assertTrue(preference_config_active({"crit_threshold": 5}))

    def test_crit_floor_requires_explicit_threshold_key(self):
        self.assertFalse(crit_floor_enabled(None))
        self.assertFalse(crit_floor_enabled({}))
        self.assertFalse(crit_floor_enabled({"stats": ["攻击力%"]}))
        self.assertFalse(crit_floor_enabled({"min_grade_limit": "S"}))
        self.assertTrue(crit_floor_enabled({"crit_threshold": 5}))
        self.assertTrue(crit_floor_enabled({"stats": ["攻击力%"], "crit_threshold": 5}))
        self.assertTrue(crit_floor_enabled({"crit_min_threshold": 12}))

    def test_crit_rank_adjustment_only_below_threshold(self):
        self.assertEqual(CRIT_RANK_BONUS, crit_rank_adjustment(3.0, True, 5.0))
        self.assertEqual(0.0, crit_rank_adjustment(5.0, True, 5.0))
        self.assertEqual(0.0, crit_rank_adjustment(1.0, False, 5.0))

    def test_drive_has_crit(self):
        self.assertTrue(drive_has_crit({"sub_stats": {"暴击率%": 3.0}}))
        self.assertFalse(drive_has_crit({"sub_stats": {"攻击力%": 3.0}}))

    def test_loadout_crit_total_sums_tape_drives_and_extra_buff(self):
        role_data = {
            "extra_shape_label": "3格",
            "extra_shape_buffs": {"暴击率%": 2.0, "攻击力%": 10.0},
        }
        tape = {"main_stats": "攻击力%", "sub_stats": {"暴击率%": 10.0}, "quality": "Gold"}
        drives = [
            {"shape_id": "V_3", "area": 3, "sub_stats": {"暴击率%": 3.0}},
            {"shape_id": "H_2", "area": 2, "sub_stats": {"暴击率%": 1.0}},
        ]
        total = loadout_crit_total(role_data, tape, drives, tape_main_values={"攻击力%": 37.5})
        # tape 10 + drives 3+1 + extra buff 2 * one matching 3-cell drive
        self.assertAlmostEqual(16.0, total)

    def test_character_crit_baseline_ignores_weapon_skill(self):
        character = {
            "sub_stats": {"暴击率%": 5.0},
            "weapon": {
                "sub_stats": {"暴击率%": 24.0},
                "skill": [{"key": "暴击率%", "value": 50.0, "cover": 1.0}],
            },
        }
        self.assertAlmostEqual(29.0, character_crit_baseline(character))

    def test_character_crit_total_includes_baseline_and_loadout(self):
        character = {"sub_stats": {"暴击率%": 5.0}, "weapon": {"sub_stats": {"暴击率%": 24.0}}}
        role_data = {"extra_shape_label": "", "extra_shape_buffs": {}}
        tape = {"main_stats": "攻击力%", "sub_stats": {"暴击率%": 10.0}, "quality": "Gold"}
        drives = [{"shape_id": "H_2", "area": 2, "sub_stats": {"暴击率%": 3.0}}]
        total = character_crit_total(character, role_data, tape, drives, tape_main_values={"攻击力%": 37.5})
        self.assertAlmostEqual(42.0, total)

    def test_meets_min_grade_boundaries(self):
        # area 3 => max 30; A requires >= 0.4 => 12
        self.assertTrue(meets_min_grade(12.0, 3, "A"))
        self.assertFalse(meets_min_grade(11.9, 3, "A"))
        self.assertTrue(meets_min_grade(0.0, 3, "D"))

    def test_persistable_stat_priority_config(self):
        self.assertIsNone(persistable_stat_priority_config({"crit_threshold": 5}))
        cfg = persistable_stat_priority_config({"stats": ["攻击力%"], "crit_threshold": 5})
        self.assertEqual(["攻击力%"], cfg["stats"])
        self.assertEqual(5, cfg["crit_threshold"])
        self.assertIsNone(
            persistable_stat_priority_config(
                {"stats": ["未知%"]},
                allowed_stats={"攻击力%"},
            )
        )

    def test_persistable_ui_save(self):
        cases = [
            (
                {
                    "stats": ["攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
                {
                    "stats": ["攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
            ),
            (
                {
                    "stats": ["攻击力%", "攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
                {
                    "stats": ["攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
            ),
            (
                {
                    "stats": [],
                    "equal_priority": True,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
                {
                    "stats": [],
                    "equal_priority": True,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
            ),
            (
                {
                    "stats": [],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "S",
                    "crit_threshold": 5,
                },
                {
                    "stats": [],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "S",
                    "crit_threshold": 5,
                },
            ),
            (
                {
                    "stats": [],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 20,
                },
                {
                    "stats": [],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 20,
                },
            ),
            (
                {
                    "stats": ["未知%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
                None,
            ),
            (
                {
                    "stats": [],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
                None,
            ),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    expected,
                    persistable_stat_priority_config(raw, allowed_stats=ALLOWED, dedupe_stats=True),
                )

    def test_persistable_import(self):
        cases = [
            (
                {"stats": ["攻击力%"]},
                {
                    "stats": ["攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
            ),
            (
                {"stats": ["攻击力%", "攻击力%"]},
                {
                    "stats": ["攻击力%", "攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 5,
                },
            ),
            (
                {"stats": ["攻击力%"], "crit_min_threshold": 18},
                {
                    "stats": ["攻击力%"],
                    "equal_priority": False,
                    "ignore_grade_limit": False,
                    "min_grade_limit": "A",
                    "crit_threshold": 18,
                },
            ),
            ({"crit_threshold": 5}, None),
            ({"stats": ["未知%"]}, None),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    expected,
                    persistable_stat_priority_config(raw, allowed_stats=ALLOWED),
                )

    def test_meets_preference_grade_limit(self):
        cases = [
            ((12.0, 3, None, False), True),
            ((12.0, 3, {}, False), True),
            ((11.9, 3, {"stats": ["攻击力%"]}, False), False),
            ((0.0, 3, {"ignore_grade_limit": True}, False), True),
            ((0.0, 3, {"min_grade_limit": "S"}, False), False),
            ((18.0, 3, {"min_grade_limit": "S"}, False), True),
            ((12.0, 3, {"stats": ["攻击力%"]}, True), True),
            ((12.0, 3, {}, True), False),
            ((12.0, 3, {"crit_threshold": 5}, True), True),
        ]
        for (score, area, config, require_active), expected in cases:
            with self.subTest(score=score, config=config, require_active=require_active):
                self.assertEqual(
                    expected,
                    meets_preference_grade_limit(score, area, config, require_active=require_active),
                )


if __name__ == "__main__":
    unittest.main()
