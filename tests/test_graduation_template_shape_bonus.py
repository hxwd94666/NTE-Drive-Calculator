# 验证毕业模板按图纸中实际额外形状驱动数量计入角色加成。
import unittest

from tools.game_data.build_graduation_templates import _extra_shape_drive_count
from tools.game_data.build_graduation_templates import _top_stat_names
from src.services.graduation_bonus_service import graduation_extra_shape_stats


class GraduationTemplateShapeBonusTests(unittest.TestCase):
    def test_uses_blueprint_module_count_instead_of_shape_label_number(self) -> None:
        count = _extra_shape_drive_count(
            {"shape_label": "Type-3", "shape_grid_count": 3},
            {"module_item_ids": ("three-a", "two", "three-b", "three-c")},
            {
                "three-a": {"grid_count": 3},
                "two": {"grid_count": 2},
                "three-b": {"grid_count": 3},
                "three-c": {"grid_count": 3},
            },
        )

        self.assertEqual(3, count)

    def test_falls_back_to_the_grid_number_encoded_in_the_label(self) -> None:
        count = _extra_shape_drive_count(
            {"shape_label": "Type-2"},
            {"module_item_ids": ("two-a", "three", "two-b", "two-c")},
            {
                "two-a": {"grid_count": 2},
                "three": {"grid_count": 3},
                "two-b": {"grid_count": 2},
                "two-c": {"grid_count": 2},
            },
        )

        self.assertEqual(3, count)

    def test_missing_shape_rule_has_no_bonus_modules(self) -> None:
        self.assertEqual(
            0,
            _extra_shape_drive_count({}, {"module_item_ids": ("two",)}, {"two": {"grid_count": 2}}),
        )

    def test_extra_shape_percent_is_normalized_once_for_direct_damage(self) -> None:
        stats = graduation_extra_shape_stats(
            {
                "properties": (
                    {"property_id": "DamageUpChaosBase", "display_value": 9.0},
                    {"property_id": "AtkAdd", "display_value": 12.0},
                ),
            },
            6,
            {
                "DamageUpChaosBase": {"show_percent": True},
                "AtkAdd": {"show_percent": False},
            },
        )

        self.assertEqual(
            [
                {"property_id": "DamageUpChaosBase", "value": 0.54, "percent": True},
                {"property_id": "AtkAdd", "value": 72.0, "percent": False},
            ],
            stats,
        )

    def test_static_template_tie_breaker_matches_role_page_property_order(self) -> None:
        names = _top_stat_names(
            {"攻击力%": 1.0, "倾陷强度": 1.0, "暴击率": 1.0, "暴击伤害": 1.0},
            {"AtkUp": 1.0, "UnbalIntensityBase": 1.0, "CritBase": 1.0, "CritDamageBase": 1.0},
        )

        self.assertEqual(("攻击力%", "暴击率", "暴击伤害", "倾陷强度"), names)


if __name__ == "__main__":
    unittest.main()
