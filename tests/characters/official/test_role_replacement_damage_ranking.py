# 验证替换候选使用角色功能共享的直伤收益排序。
"""角色替换直伤排序服务的回归测试。"""

from unittest import TestCase
from unittest.mock import patch

from src.features.role.replacement_service import (
    build_equipment_role_context,
    equipment_user_map,
    rank_replacement_candidates_by_damage,
)


class RoleReplacementDamageRankingTests(TestCase):
    def test_context_replaces_legacy_equipment_without_mutating_base_stats(self):
        base = {"sub_stats": {"攻击力": 100}, "drive": {"drives": [{"uid": "old"}]}}

        context = build_equipment_role_context(
            base,
            [{"uid": "new_drive", "shape_id": "H_2", "sub_stats": {"攻击力": 20}}],
            {"uid": "new_tape", "main_stats": {"攻击力": 50}, "sub_stats": {}},
        )

        self.assertEqual("old", base["drive"]["drives"][0]["uid"])
        self.assertEqual("new_drive", context["drive"]["drives"][0]["uid"])
        self.assertEqual("new_tape", context["tape"]["uid"])

    def test_tape_candidates_are_sorted_by_direct_damage_margin(self):
        first = {"uid": "first"}
        second = {"uid": "second"}
        with patch("src.features.role.replacement_service.calc_tape_margin", return_value=1.5), patch(
            "src.features.role.replacement_service.calc_tape_replacement_margin",
            side_effect=[2.0, 8.0],
        ):
            current, ranked = rank_replacement_candidates_by_damage({}, "tape", {"uid": "current"}, [first, second])

        self.assertEqual(1.5, current)
        self.assertEqual(["second", "first"], [item["uid"] for _margin, item in ranked])

    def test_role_user_map_does_not_repeat_a_role_for_duplicate_legacy_entries(self):
        users = equipment_user_map(
            {"薄荷": {"drive": {"drives": [{"uid": "drive-1"}, {"uid": "drive-1"}]}}},
            "当前角色",
            "drive",
        )

        self.assertEqual(["薄荷"], users["drive-1"])
