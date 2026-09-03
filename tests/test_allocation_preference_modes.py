# 验证分配偏好模式的公共行为。
from __future__ import annotations

import unittest

from src.features.allocation.preference_modes import (
    role_preference_mode_error,
    without_crit_rate_bounds,
)


class AllocationPreferenceModeTests(unittest.TestCase):
    def test_global_optimal_drops_crit_floor_and_cap_but_keeps_stat_preferences(self):
        modes, caps = without_crit_rate_bounds(
            {
                "A": {"crit_threshold": 70, "stats": ["攻击力%"]},
                "B": {"crit_min_threshold": 60},
            },
            {"A": 60, "B": 80},
        )

        self.assertEqual({"A": {"stats": ["攻击力%"]}}, modes)
        self.assertEqual({}, caps)
        self.assertIsNotNone(role_preference_mode_error("global_optimal", {}, modes, caps))

    def test_global_optimal_accepts_crit_floor_and_cap_after_bounds_are_dropped(self):
        modes, caps = without_crit_rate_bounds(
            {"A": {"crit_threshold": 70}}, {"A": 60},
        )

        self.assertEqual({}, modes)
        self.assertEqual({}, caps)
        self.assertIsNone(role_preference_mode_error("global_optimal", {}, modes, caps))
