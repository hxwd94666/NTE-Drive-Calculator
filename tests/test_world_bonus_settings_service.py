# 覆盖账号级世界加成的默认值、正式属性映射与持久化。
import tempfile
import unittest
from pathlib import Path

from src.services.world_bonus_settings_service import (
    WORLD_BONUS_SETTING_KEY,
    WorldBonusSettings,
    WorldBonusSettingsService,
    world_bonus_property_stats,
)
from src.storage.sqlite.user_data_dao import UserDataDao


class WorldBonusSettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "user.sqlite3"
        with UserDataDao(
            self.database_path,
            account_id="world-bonus-test",
            account_name="世界加成测试",
        ):
            pass

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_defaults_match_formal_full_level_values(self) -> None:
        settings = WorldBonusSettingsService(self.database_path).load()

        self.assertEqual(20.0, settings.yaodao_attack_add)
        self.assertAlmostEqual(0.04, settings.quantao_crit_damage)
        self.assertEqual(
            {"AtkAdd": 20.0, "CritDamageBase": 0.04},
            world_bonus_property_stats(settings),
        )

    def test_account_override_round_trips(self) -> None:
        service = WorldBonusSettingsService(self.database_path)
        expected = WorldBonusSettings(12.0, 0.024)

        service.save(expected)

        self.assertEqual(expected, service.load())
        with UserDataDao(self.database_path) as dao:
            self.assertIn(
                WORLD_BONUS_SETTING_KEY,
                dao.list_application_setting_copies(),
            )


if __name__ == "__main__":
    unittest.main()
