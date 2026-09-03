# 验证主题属于应用全局配置并兼容旧账号主题迁移。
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.account_settings_service import AccountSettingsService
from src.services.global_theme_settings_service import GlobalThemeSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.features.scanning.post_action_dialog import (
    load_scan_post_action_config,
    save_scan_post_action_config,
)


class GlobalThemeSettingsTests(unittest.TestCase):
    def test_global_theme_survives_account_specific_legacy_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_database = root / "first.sqlite3"
            second_database = root / "second.sqlite3"
            with UserDataDao(
                first_database,
                account_id="first",
                account_name="First",
            ) as first:
                first.replace_application_setting_copy("ui", {"theme": "light"})
            with UserDataDao(
                second_database,
                account_id="second",
                account_name="Second",
            ) as second:
                second.replace_application_setting_copy("ui", {"theme": "black"})

            first_settings = AccountSettingsService(first_database)
            second_settings = AccountSettingsService(second_database)
            global_settings = GlobalThemeSettingsService(
                root / "config" / "global_ui_preferences.json"
            )

            self.assertEqual(
                global_settings.load(
                    legacy_theme=first_settings.legacy_theme_preference()
                ),
                "light",
            )
            self.assertEqual(
                global_settings.load(
                    legacy_theme=second_settings.legacy_theme_preference()
                ),
                "light",
            )

            global_settings.save("black")
            self.assertEqual(global_settings.load(legacy_theme="dark"), "black")

    def test_new_install_does_not_promote_the_obsolete_static_theme_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "user_data.sqlite3"
            with UserDataDao(database_path, account_id="account", account_name="Account"):
                pass
            settings = AccountSettingsService(database_path)
            global_settings = GlobalThemeSettingsService(
                root / "config" / "global_ui_preferences.json"
            )

            self.assertIsNone(settings.legacy_theme_preference())
            self.assertEqual(
                "black",
                global_settings.load(legacy_theme=settings.legacy_theme_preference()),
            )

    def test_account_ui_settings_drop_legacy_theme_on_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "user_data.sqlite3"
            with UserDataDao(
                database_path,
                account_id="account",
                account_name="Account",
            ):
                pass
            settings = AccountSettingsService(database_path)

            saved = settings.save(
                "ui",
                {"theme": "light", "log_enabled": True},
            )

            self.assertNotIn("theme", saved)
            with UserDataDao(database_path) as database:
                stored = database.list_application_setting_copies()["ui"]
            self.assertNotIn("theme", stored)

    def test_full_scan_preferences_are_persisted_per_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_database = root / "first.sqlite3"
            second_database = root / "second.sqlite3"
            with UserDataDao(first_database, account_id="first", account_name="First"):
                pass
            with UserDataDao(second_database, account_id="second", account_name="Second"):
                pass

            first = AccountSettingsService(first_database)
            second = AccountSettingsService(second_database)
            first.save(
                "ui",
                {
                    "full_scan_capture_driver": "gamepad",
                    "full_scan_dual_thread_processing": False,
                    "full_scan_amd_compatibility": True,
                    "full_scan_post_action_config": {"discard": {"enabled": True}},
                },
            )

            first_loaded = first.load("ui")
            second_loaded = second.load("ui")
            self.assertEqual("gamepad", first_loaded["full_scan_capture_driver"])
            self.assertFalse(first_loaded["full_scan_dual_thread_processing"])
            self.assertTrue(first_loaded["full_scan_amd_compatibility"])
            self.assertEqual(
                {"discard": {"enabled": True}},
                first_loaded["full_scan_post_action_config"],
            )
            self.assertEqual("mouse", second_loaded["full_scan_capture_driver"])
            self.assertTrue(second_loaded["full_scan_dual_thread_processing"])
            self.assertFalse(second_loaded["full_scan_amd_compatibility"])
            self.assertEqual({}, second_loaded["full_scan_post_action_config"])

    def test_full_scan_management_rules_use_account_sqlite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_database = root / "first.sqlite3"
            second_database = root / "second.sqlite3"
            first_config = root / "first-config"
            second_config = root / "second-config"
            with UserDataDao(first_database, account_id="first", account_name="First"):
                pass
            with UserDataDao(second_database, account_id="second", account_name="Second"):
                pass

            save_scan_post_action_config(
                first_config,
                {"discard": {"enabled": True, "grade": "A"}},
                user_database_path=first_database,
            )
            first = load_scan_post_action_config(
                first_config,
                user_database_path=first_database,
            )
            second = load_scan_post_action_config(
                second_config,
                user_database_path=second_database,
            )

            self.assertTrue(first["discard"]["enabled"])
            self.assertEqual("A", first["discard"]["grade"])
            self.assertFalse(second["discard"]["enabled"])
            self.assertFalse((first_config / "scan_post_actions.json").exists())

    def test_legacy_theme_removal_preserves_other_account_ui_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "user_data.sqlite3"
            with UserDataDao(
                database_path,
                account_id="account",
                account_name="Account",
            ) as database:
                database.replace_application_setting_copy(
                    "ui",
                    {
                        "theme": "light",
                        "log_enabled": True,
                        "protagonist_game_name": "零",
                    },
                )
            settings = AccountSettingsService(database_path)

            self.assertEqual(settings.legacy_theme_preference(), "light")
            settings.remove_legacy_theme_preference()

            loaded = settings.load("ui")
            self.assertNotIn("theme", loaded)
            self.assertTrue(loaded["log_enabled"])
            self.assertEqual(loaded["protagonist_game_name"], "零")

    def test_global_theme_file_is_compact_and_recoverable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "global_ui_preferences.json"
            service = GlobalThemeSettingsService(path)

            self.assertEqual(service.load(), "black")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"theme": "black"},
            )
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(service.load(legacy_theme="light"), "light")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"theme": "light"},
            )


if __name__ == "__main__":
    unittest.main()
