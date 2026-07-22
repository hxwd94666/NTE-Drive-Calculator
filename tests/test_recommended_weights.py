# 测试开发期工坊权重规范化及缺失角色默认值。
import unittest

from src.domain.recommended_weights import parse_workshop_recommendations


class RecommendedWeightsTest(unittest.TestCase):
    def test_api_rows_map_to_official_ids_and_missing_role_uses_default(self):
        recommendations = parse_workshop_recommendations(
            [{
                "itemId": "1076",
                "name": "真红",
                "weightConfig": {"weights": [
                    {"name": "暴击率%", "value": 1.2, "main_value": 1.0},
                    {"name": "攻击力%", "value": 0.65, "main_value": 0.4},
                    {"name": "未知词条", "value": 99},
                ]},
            }],
            (1075, 1076),
        )

        self.assertEqual("workshop_api", recommendations[1076]["source_kind"])
        self.assertEqual(
            {"CritBase": 1.2, "AtkUp": 0.65},
            {
                row["property_id"]: row["weight"]
                for row in recommendations[1076]["properties"]
            },
        )
        self.assertEqual("default", recommendations[1075]["source_kind"])
        self.assertEqual(
            {
                "DamageUpGeneralBase": 0.75,
                "CritBase": 1.0,
                "CritDamageBase": 1.0,
                "AtkUp": 0.7,
            },
            {
                row["property_id"]: row["weight"]
                for row in recommendations[1075]["properties"]
            },
        )


if __name__ == "__main__":
    unittest.main()
