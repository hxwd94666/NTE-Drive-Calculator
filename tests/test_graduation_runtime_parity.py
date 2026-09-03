# 防止构建期毕业基准与角色页实际分母再次出现不同口径。
import tempfile
import unittest
from pathlib import Path

from src.features.official_role.role_calculation import (
    _graduation_tooltip,
    graduation_benchmark_damage,
)
from src.services.official_role_page_service import load_official_role_detail
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class GraduationRuntimeParityTests(unittest.TestCase):
    def test_v31_runtime_reprojects_permanent_fork_into_old_benchmark(self) -> None:
        with StaticGameDataDao() as static_dao:
            if int(static_dao.summary()["schema_version"]) >= 32:
                self.skipTest("v32 已持久化弧盘常驻属性和重建后的毕业基准")
            template = next(
                row for row in static_dao.list_character_graduation_templates()
                if row.get("fork_id") == "fork_Time"
            )
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_database = Path(temporary_directory) / "graduation-v31.sqlite3"
            with UserDataDao(user_database, account_id="graduation-v31"):
                pass
            detail = load_official_role_detail(
                user_database,
                int(template["character_id"]),
                include_inventory_contexts=False,
            )

        runtime_damage = float(graduation_benchmark_damage(detail) or 0.0)
        self.assertGreater(runtime_damage, float(template["benchmark_damage"]))
        fork = next(
            row for row in detail["forks"] if row.get("fork_id") == "fork_Time"
        )
        refine_one = next(
            row for row in fork["permanent_properties"]
            if int(row.get("refinement_level") or 0) == 1
        )
        self.assertEqual("AtkUp", refine_one["property_id"])
        self.assertAlmostEqual(0.16, float(refine_one["property_value"]))

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
            tooltip.startswith("直伤毕业基准（满级角色、满级专武）：")
        )
        self.assertIn("卡带主词条：", tooltip)
        self.assertIn("毕业副词条：", tooltip)
        self.assertIn("毕业率 = 当前养成与配装直伤 ÷ 本基准，结果不封顶。", tooltip)
        self.assertIn("弧盘常驻", tooltip)
        self.assertIn("好感10", tooltip)
        self.assertIn("家具加成", tooltip)
        self.assertTrue(tooltip.endswith("不计条件被动、机制伤害和队友加成。"))

    def test_static_benchmark_matches_runtime_default_weight_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_database = Path(temporary_directory) / "graduation-parity.sqlite3"
            with UserDataDao(user_database, account_id="graduation-parity"):
                pass
            with StaticGameDataDao() as static_dao:
                if int(static_dao.summary()["schema_version"]) < 32:
                    self.skipTest(
                        "v31 无常驻属性持久表；运行时兼容投影会有意重算旧毕业基准"
                    )
                templates = static_dao.list_character_graduation_templates()
            self.assertTrue(templates)
            for template in templates:
                with self.subTest(character_id=template["character_id"]):
                    detail = load_official_role_detail(
                        user_database,
                        int(template["character_id"]),
                        include_inventory_contexts=False,
                    )
                    runtime_damage = float(
                        graduation_benchmark_damage(detail) or 0.0
                    )
                    self.assertAlmostEqual(
                        float(template["benchmark_damage"]),
                        runtime_damage,
                        places=6,
                    )


if __name__ == "__main__":
    unittest.main()
