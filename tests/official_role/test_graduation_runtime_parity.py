# 防止构建期毕业基准与角色页实际分母再次出现不同口径。
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.features.official_role.role_calculation import _graduation_tooltip
from src.services.official_role.graduation import graduation_benchmark_damage
from src.services.official_role.page import load_official_role_detail
from src.services.characters.workshop_weight_template_service import WORKSHOP_WEIGHT_TEMPLATE_ENV
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class GraduationRuntimeParityTests(unittest.TestCase):
    def test_tooltip_describes_the_direct_damage_benchmark_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_database = Path(temporary_directory) / "graduation-tooltip.sqlite3"
            with UserDataDao(user_database, account_id="graduation-tooltip"):
                pass
            with StaticGameDataDao() as static_dao:
                character_id = int(
                    static_dao.list_character_graduation_templates()[0][
                        "character_id"
                    ]
                )
            detail = load_official_role_detail(
                user_database,
                character_id,
                include_inventory_contexts=False,
            )

        tooltip = _graduation_tooltip(detail)
        self.assertTrue(
            tooltip.startswith("空幕直伤毕业基准（满级角色、满级精1弧盘）：")
        )
        self.assertIn("卡带主词条：", tooltip)
        self.assertIn("毕业副词条：", tooltip)
        self.assertIn("毕业率 = 满练度配当前空幕直伤 ÷ 满练度配毕业空幕直伤，结果不封顶。", tooltip)
        self.assertIn("弧盘常驻", tooltip)
        self.assertIn("好感10", tooltip)
        self.assertIn("家具满加成", tooltip)
        self.assertNotIn("当前空幕按满级属性投影", tooltip)
        self.assertTrue(tooltip.endswith("只计算直伤，不计条件被动、机制伤害和队友加成。"))

    @patch.dict(os.environ, {WORKSHOP_WEIGHT_TEMPLATE_ENV: ""})
    def test_static_benchmark_matches_runtime_default_weight_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_database = Path(temporary_directory) / "graduation-parity.sqlite3"
            with UserDataDao(user_database, account_id="graduation-parity"):
                pass
            with StaticGameDataDao() as static_dao:
                if int(static_dao.summary()["schema_version"]) < 32:
                    self.skipTest("v31 无常驻属性持久表；运行时兼容投影会有意重算旧毕业基准")
                templates = static_dao.list_character_graduation_templates()
            self.assertTrue(templates)
            for template in templates:
                with self.subTest(character_id=template["character_id"]):
                    detail = load_official_role_detail(
                        user_database,
                        int(template["character_id"]),
                        include_inventory_contexts=False,
                    )
                    runtime_damage = float(graduation_benchmark_damage(detail) or 0.0)
                    self.assertAlmostEqual(
                        float(template["benchmark_damage"]), runtime_damage, places=6,
                    )


if __name__ == "__main__":
    unittest.main()
