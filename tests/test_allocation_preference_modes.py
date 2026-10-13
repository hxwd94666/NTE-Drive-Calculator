# 验证分配偏好模式的公共行为。
from __future__ import annotations

import unittest

from src.features.allocation.preference_modes import role_preference_mode_error
from src.features.allocation.priority_groups import load_priority_selection


class AllocationPreferenceModeTests(unittest.TestCase):
    def test_role_priority_accepts_empty_optional_preferences(self):
        self.assertIsNone(role_preference_mode_error("role_priority", {}, {}, {}))

    def test_legacy_priority_selection_keeps_first_known_occurrence(self):
        selected, links = load_priority_selection(
            {"priority_list": ["B", "A", "B", "missing", "A"]},
            {"A": {}, "B": {}},
        )
        self.assertEqual(["B", "A"], selected)
        self.assertEqual([">"], links)
