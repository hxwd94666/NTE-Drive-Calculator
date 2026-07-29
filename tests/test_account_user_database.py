# 测试账号生命周期与分账号用户数据库的联动。
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.features.accounts.manager import (
    AccountManager,
    export_account_data,
    import_account_data,
)
from src.services.account_settings_service import AccountSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao


class AccountUserDatabaseTests(unittest.TestCase):
    def make_manager(self, root: Path) -> AccountManager:
        bundled = root / "bundled_config"
        bundled.mkdir(parents=True)
        return AccountManager(
            data_root=root / "data",
            bundled_config_dir=bundled,
            iter_image_files=lambda _path: [],
            core_config_files=(),
            account_user_files=(),
        )

    def test_account_lifecycle_creates_and_renames_user_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = self.make_manager(Path(temporary))

            default_state = manager.initialize()
            self.assertTrue(default_state.user_database_path.is_file())
            with UserDataDao(default_state.user_database_path) as database:
                self.assertEqual(database.profile()["account_name"], "默认账号")

            account_id = manager.create_account("测试账号")
            database_path = manager.account_dir(account_id) / "user_data.sqlite3"
            self.assertTrue(database_path.is_file())

            manager.rename_account(account_id, "新名称")
            with UserDataDao(database_path) as database:
                self.assertEqual(database.profile()["account_name"], "新名称")

    def test_mirror_cdk_is_account_scoped_update_setting(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = self.make_manager(Path(temporary))
            first = manager.initialize()
            second_id = manager.create_account("第二账号")
            second_path = manager.account_dir(second_id) / "user_data.sqlite3"

            first_settings = AccountSettingsService(first.user_database_path)
            second_settings = AccountSettingsService(second_path)
            self.assertEqual(first_settings.load("update")["mirror_cdk"], "")
            first_settings.save("update", {"mirror_cdk": "0001bf-private"})

            self.assertEqual(first_settings.load("update")["mirror_cdk"], "0001bf-private")
            self.assertEqual(second_settings.load("update")["mirror_cdk"], "")

    def test_protagonist_game_name_is_account_scoped_ui_setting(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = self.make_manager(Path(temporary))
            first = manager.initialize()
            second_id = manager.create_account("第二账号")
            second_path = manager.account_dir(second_id) / "user_data.sqlite3"

            first_settings = AccountSettingsService(first.user_database_path)
            second_settings = AccountSettingsService(second_path)
            first_settings.save("ui", {"protagonist_game_name": "无度"})

            self.assertEqual(first_settings.load("ui")["protagonist_game_name"], "无度")
            self.assertEqual(second_settings.load("ui")["protagonist_game_name"], "")

    def test_versioned_stats_catalog_replaces_stale_local_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundled = root / "bundled_config"
            bundled.mkdir()
            (bundled / "stats.json").write_text('{"revision":2}', encoding="utf-8")
            manager = AccountManager(
                data_root=root / "data",
                bundled_config_dir=bundled,
                iter_image_files=lambda _path: [],
                core_config_files=("stats.json",),
                account_user_files=(),
            )
            local_stats = root / "data" / "config" / "stats.json"
            local_stats.parent.mkdir(parents=True)
            local_stats.write_text('{"revision":1}', encoding="utf-8")

            manager.seed_user_config()

            self.assertEqual('{"revision":2}', local_stats.read_text(encoding="utf-8"))

    def test_legacy_json_settings_migrate_once_into_account_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            state = manager.initialize()
            legacy = root / "legacy_config"
            legacy.mkdir()
            (legacy / "hotkeys.json").write_text(
                json.dumps({"capture": "F6", "finish": "F7", "stop": "F8"}),
                encoding="utf-8",
            )
            (legacy / "update_config.json").write_text(
                json.dumps(
                    {
                        "never_remind": True,
                        "ignored_version": "1.2.3",
                        "mirror_cdk": "legacy-cdk",
                    }
                ),
                encoding="utf-8",
            )
            (legacy / "ui_preferences.json").write_text(
                json.dumps({"log_enabled": True, "theme": "light"}),
                encoding="utf-8",
            )
            settings = AccountSettingsService(
                state.user_database_path,
                legacy_config_dir=legacy,
            )

            settings.migrate_legacy_settings()

            self.assertEqual(
                settings.load("hotkeys"),
                {"capture": "F6", "finish": "F7", "stop": "F8"},
            )
            self.assertEqual(settings.load("update")["ignored_version"], "1.2.3")
            self.assertEqual(settings.load("update")["mirror_cdk"], "legacy-cdk")
            self.assertTrue(settings.load("update")["never_remind"])
            self.assertTrue(settings.load("ui")["log_enabled"])
            self.assertEqual(settings.load("ui")["theme"], "light")
            with UserDataDao(state.user_database_path) as database:
                self.assertTrue(database.legacy_application_settings_imported())

            (legacy / "hotkeys.json").write_text(
                json.dumps({"capture": "F1", "finish": "F2", "stop": "F3"}),
                encoding="utf-8",
            )
            settings.migrate_legacy_settings()
            self.assertEqual(settings.load("hotkeys")["capture"], "F6")

    def test_account_export_import_round_trip_preserves_database_and_baseline_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_manager = self.make_manager(root / "source")
            source_manager.initialize()
            account_id = source_manager.create_account("Exported Account")
            source_root = source_manager.account_dir(account_id)
            settings = AccountSettingsService(source_root / "user_data.sqlite3")
            settings.save(
                "ui",
                {
                    "theme": "light",
                    "log_enabled": True,
                    "protagonist_game_name": "无度",
                },
            )
            (source_root / "config" / "custom.json").write_text(
                '{"kept":true}',
                encoding="utf-8",
            )
            (source_root / "scanned_images" / "raw_drive_0001.png").write_bytes(
                b"baseline"
            )
            (source_root / "scanned_images" / "raw_drive_0002.png").write_bytes(
                b"excluded"
            )
            archive = export_account_data(
                source_manager,
                account_id,
                root / "export.zip",
            )

            with zipfile.ZipFile(archive) as exported:
                names = set(exported.namelist())
            self.assertIn("account/scanned_images/raw_drive_0001.png", names)
            self.assertNotIn("account/scanned_images/raw_drive_0002.png", names)

            target_manager = self.make_manager(root / "target")
            target_manager.initialize()
            imported_id = import_account_data(target_manager, archive)
            imported_root = target_manager.account_dir(imported_id)

            self.assertEqual(
                target_manager.account_meta(imported_id)["name"],
                "Exported Account",
            )
            self.assertEqual(
                (imported_root / "config" / "custom.json").read_text(encoding="utf-8"),
                '{"kept":true}',
            )
            self.assertEqual(
                (imported_root / "scanned_images" / "raw_drive_0001.png").read_bytes(),
                b"baseline",
            )
            self.assertFalse(
                (imported_root / "scanned_images" / "raw_drive_0002.png").exists()
            )
            imported_settings = AccountSettingsService(
                imported_root / "user_data.sqlite3"
            )
            self.assertEqual(imported_settings.load("ui")["theme"], "light")
            self.assertEqual(
                imported_settings.load("ui")["protagonist_game_name"],
                "无度",
            )
            with UserDataDao(imported_root / "user_data.sqlite3") as database:
                self.assertEqual(database.profile()["account_name"], "Exported Account")

    def test_account_import_rejects_parent_directory_zip_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            manager.initialize()
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as exported:
                exported.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "format": "nte-account-export",
                            "version": 1,
                            "account": {"id": "unsafe", "name": "Unsafe"},
                        }
                    ),
                )
                exported.writestr("account/../../escape.txt", "blocked")

            with self.assertRaisesRegex(ValueError, "unsafe zip path"):
                import_account_data(manager, archive)
            self.assertFalse((root / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
