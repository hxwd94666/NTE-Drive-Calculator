# 防止构建期毕业基准与角色页实际分母再次出现不同口径。
import tempfile
import unittest
from pathlib import Path

from src.features.official_role.role_calculation import _graduation_benchmark_damage
from src.services.official_role_page_service import load_official_role_detail
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class GraduationRuntimeParityTests(unittest.TestCase):
    def test_static_benchmark_matches_runtime_default_weight_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_database = Path(temporary_directory) / "graduation-parity.sqlite3"
            with UserDataDao(user_database, account_id="graduation-parity"):
                pass
            with StaticGameDataDao() as static_dao:
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
                        _graduation_benchmark_damage(detail) or 0.0
                    )
                    self.assertAlmostEqual(
                        float(template["benchmark_damage"]),
                        runtime_damage,
                        places=6,
                    )


if __name__ == "__main__":
    unittest.main()
