# 测试账号生命周期与分账号用户数据库的联动。
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import nullcontext
from unittest.mock import patch
from pathlib import Path

from src.features.accounts.manager import AccountManager
from src.features.scanning.post_action_dialog import load_scan_post_action_config, save_scan_post_action_config
from src.services.accounts.transfer import export_account_data, import_account_data
from src.services.accounts.settings import AccountSettingsService
from src.integrations.accounts import transfer_archive
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

    def test_account_settings_are_isolated_between_databases(self):
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
            first_settings.save("ui", {"protagonist_game_name": "无度"})
            self.assertEqual(first_settings.load("ui")["protagonist_game_name"], "无度")
            self.assertEqual(second_settings.load("ui")["protagonist_game_name"], "")

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

            self.assertEqual(settings.legacy_theme_preference(), "light")
            settings.migrate_legacy_settings()

            self.assertEqual(
                settings.load("hotkeys"),
                {
                    "capture": "F6",
                    "finish": "F7",
                    "stop": "F8",
                    "battle_rerecord": "F11",
                },
            )
            self.assertEqual(settings.load("update")["ignored_version"], "1.2.3")
            self.assertEqual(settings.load("update")["mirror_cdk"], "legacy-cdk")
            self.assertTrue(settings.load("update")["never_remind"])
            self.assertTrue(settings.load("ui")["log_enabled"])
            self.assertNotIn("theme", settings.load("ui"))
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
                    "log_enabled": True,
                    "protagonist_game_name": "无度",
                },
            )
            settings.save("update", {"mirror_cdk": "SYNTHETIC_PRIVATE_CDK"})
            with UserDataDao(source_root / "user_data.sqlite3") as database:
                database.replace_application_setting_copy("ui", {
                    "protagonist_game_name": "无度", "theme": "light",
                    "equipment_plugin_game_executable": "SYNTHETIC_PRIVATE_PATH",
                    "equipment_plugin_risk_acknowledged": True,
                })
            (source_root / "config" / "guide_seen.json").write_text(
                '{"seen":true}', encoding="utf-8",
            )
            save_scan_post_action_config(source_root / "config", {
                "discard": {"grade": "A"}, "selected_character_ids": [1004],
            })
            (source_root / "config" / "custom.json").write_text(
                '{"kept":true}',
                encoding="utf-8",
            )
            (source_root / "config" / "work_mode.json").write_text(
                '{"mode":"developer","risk_acknowledged":true}', encoding="utf-8",
            )
            (source_root / "scanned_images" / "raw_drive_0001.png").write_bytes(
                b"baseline"
            )
            (source_root / "scanned_images" / "raw_drive_0002.png").write_bytes(
                b"excluded"
            )
            (source_root / "logs" / "runtime.log").write_text(
                "excluded",
                encoding="utf-8",
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
            self.assertNotIn("account/logs/runtime.log", names)
            self.assertNotIn("account/user_data.sqlite3-wal", names)
            self.assertNotIn("account/user_data.sqlite3-shm", names)
            self.assertNotIn("account/config/custom.json", names)
            self.assertNotIn("account/config/work_mode.json", names)
            self.assertIn("account/config/guide_seen.json", names)
            self.assertIn("account/config/scan_post_actions.json", names)
            with zipfile.ZipFile(archive) as exported:
                database_bytes = exported.read("account/user_data.sqlite3")
            self.assertNotIn(b"SYNTHETIC_PRIVATE_CDK", database_bytes)
            self.assertNotIn(b"SYNTHETIC_PRIVATE_PATH", database_bytes)
            self.assertEqual(settings.load("update")["mirror_cdk"], "SYNTHETIC_PRIVATE_CDK")

            target_manager = self.make_manager(root / "target")
            target_manager.initialize()
            local_mode = target_manager.config_dir / "work_mode.json"
            local_mode.parent.mkdir(exist_ok=True)
            local_mode.write_bytes(b'{"mode":"offline"}')
            imported_id = import_account_data(target_manager, archive)
            imported_root = target_manager.account_dir(imported_id)

            self.assertEqual(
                target_manager.account_meta(imported_id)["name"],
                "Exported Account",
            )
            self.assertEqual(local_mode.read_bytes(), b'{"mode":"offline"}')
            scan_preferences = load_scan_post_action_config(
                imported_root / "config", user_database_path=imported_root / "user_data.sqlite3",
            )
            self.assertEqual(scan_preferences["discard"]["grade"], "A")
            self.assertNotIn("selected_character_ids", scan_preferences)
            self.assertEqual(scan_preferences["discard"]["selected_character_ids"], [1004])
            self.assertEqual(scan_preferences["lock"]["selected_character_ids"], [1004])
            self.assertEqual(
                json.loads((imported_root / "config" / "guide_seen.json").read_text(encoding="utf-8")),
                {"seen": True},
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
            self.assertNotIn("theme", imported_settings.load("ui"))
            self.assertEqual(
                imported_settings.load("ui")["protagonist_game_name"],
                "无度",
            )
            with UserDataDao(imported_root / "user_data.sqlite3") as database:
                self.assertEqual(database.profile()["account_name"], "Exported Account")

    def test_import_invalid_database_never_releases_runtime_or_changes_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            state = manager.initialize()
            original_index = manager.accounts_index_file.read_bytes()
            original_database = state.user_database_path.read_bytes()
            archive = root / "invalid.zip"
            with zipfile.ZipFile(archive, "w") as exported:
                exported.writestr("manifest.json", json.dumps({
                    "format": "nte-account-export", "version": 1,
                    "account": {"id": "default", "name": "默认账号"},
                }))
                exported.writestr("account/user_data.sqlite3", b"not a database")
            released = []
            with self.assertRaises(Exception):
                import_account_data(manager, archive, before_replace=released.append)
            self.assertEqual(released, [])
            self.assertEqual(original_index, manager.accounts_index_file.read_bytes())
            self.assertEqual(original_database, state.user_database_path.read_bytes())

    def test_import_matches_formal_id_not_display_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_manager(root / "source")
            source.initialize()
            source_id = source.create_account("source-id")
            source.rename_account(source_id, "Same display name")
            archive = export_account_data(source, source_id, root / "account.zip")
            target = self.make_manager(root / "target")
            target.initialize()
            other_id = target.create_account("other-id")
            target.rename_account(other_id, "Same display name")
            original = (target.account_dir(other_id) / "user_data.sqlite3").read_bytes()
            released = []
            imported_id = import_account_data(target, archive, before_replace=released.append)
            self.assertEqual(imported_id, source_id)
            self.assertEqual(released, [])
            self.assertEqual(original, (target.account_dir(other_id) / "user_data.sqlite3").read_bytes())
            with UserDataDao(target.account_dir(imported_id) / "user_data.sqlite3") as database:
                self.assertEqual(database.profile()["account_id"], imported_id)

    def test_candidate_setting_contract_is_checked_before_replacing_old_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            state = manager.initialize()
            valid = export_account_data(manager, "default", root / "valid.zip")
            with zipfile.ZipFile(valid) as package:
                members = {name: package.read(name) for name in package.namelist()}
            original = state.user_database_path.read_bytes()
            original_index = manager.accounts_index_file.read_bytes()
            # 同一正式 decoder 的不同输入，防止只给 retention 单字段打补丁。
            for value in ({"inventory_snapshot_retention_count": 0},
                          {"inventory_sync_method": "unknown"},
                          {"inventory_settle_seconds": 0},
                          {"legacy_hotkey": ""},
                          {"inventory_snapshot_retention_count": 7}):
                with self.subTest(value=value):
                    candidate = root / "candidate.sqlite3"
                    candidate.write_bytes(members["account/user_data.sqlite3"])
                    with UserDataDao(candidate) as dao:
                        if "legacy_hotkey" not in value:
                            dao.replace_application_setting_copy("sync", value)
                    if "legacy_hotkey" in value:
                        with sqlite3.connect(candidate) as connection:
                            connection.execute("DELETE FROM application_setting_migration")
                            connection.execute("DELETE FROM application_setting_copy WHERE setting_key='hotkeys'")
                        connection.close()
                    archive = root / "candidate.zip"
                    with zipfile.ZipFile(archive, "w") as package:
                        for name, content in members.items():
                            package.writestr(name, candidate.read_bytes() if name.endswith("user_data.sqlite3") else content)
                        if "legacy_hotkey" in value:
                            package.writestr("account/config/hotkeys.json", '{"capture":""}')
                    released = []
                    if value == {"inventory_snapshot_retention_count": 7}:
                        self.assertEqual(import_account_data(manager, archive, before_replace=released.append), "default")
                        self.assertEqual(released, ["default"])
                        self.assertEqual(AccountSettingsService(state.user_database_path).load("sync")["inventory_snapshot_retention_count"], 7)
                    else:
                        with self.assertRaises(ValueError):
                            import_account_data(manager, archive, before_replace=released.append)
                        self.assertEqual(released, [])
                        self.assertEqual(state.user_database_path.read_bytes(), original)
                        self.assertEqual(manager.accounts_index_file.read_bytes(), original_index)

    def test_archive_alias_limits_and_database_identity_fail_before_owner_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            state = manager.initialize()
            original_index = manager.accounts_index_file.read_bytes()
            original_database = state.user_database_path.read_bytes()
            valid = export_account_data(manager, "default", root / "valid.zip")
            with zipfile.ZipFile(valid) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            for scenario in ("duplicate", "case-alias", "path-alias", "size", "identity", "schema", "constraint", "trigger"):
                with self.subTest(scenario=scenario):
                    payload = dict(members)
                    if scenario in {"identity", "schema", "constraint", "trigger"}:
                        candidate = root / "candidate.sqlite3"
                        candidate.write_bytes(members["account/user_data.sqlite3"])
                        with sqlite3.connect(candidate) as connection:
                            if scenario == "identity":
                                connection.execute("UPDATE database_profile SET account_id='different'")
                            elif scenario == "schema":
                                connection.execute("DROP TABLE inventory_item")
                            elif scenario == "constraint":
                                connection.execute("DROP INDEX idx_inventory_snapshot_one_current")
                            else:
                                connection.execute("CREATE TRIGGER fault BEFORE UPDATE ON database_profile "
                                                   "BEGIN SELECT RAISE(ABORT, 'FAULT'); END")
                        connection.close()
                        payload["account/user_data.sqlite3"] = candidate.read_bytes()
                    bad = root / f"{scenario}.zip"
                    with zipfile.ZipFile(bad, "w") as archive:
                        for name, data in payload.items():
                            archive.writestr(name, data)
                        extra = {"duplicate": "manifest.json", "case-alias": "ACCOUNT/USER_DATA.SQLITE3",
                                 "path-alias": "account/config/../user_data.sqlite3"}.get(scenario)
                        if extra:
                            with self.assertWarns(UserWarning) if scenario == "duplicate" else nullcontext():
                                archive.writestr(extra, b"duplicate")
                    released = []
                    with patch.object(transfer_archive, "MAX_TOTAL_BYTES", 1 if scenario == "size" else transfer_archive.MAX_TOTAL_BYTES):
                        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                            import_account_data(manager, bad, before_replace=released.append)
                    self.assertEqual(released, [])
                    self.assertEqual(manager.accounts_index_file.read_bytes(), original_index)
                    self.assertEqual(state.user_database_path.read_bytes(), original_database)

    def test_export_failure_preserves_previous_package_and_unknown_id_has_no_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            manager.initialize()
            destination = root / "existing.zip"
            destination.write_bytes(b"previous-package")
            with patch.object(sqlite3, "connect", side_effect=sqlite3.OperationalError("SYNTHETIC_BUSY")):
                with self.assertRaises(Exception):
                    export_account_data(manager, "default", destination)
            self.assertEqual(destination.read_bytes(), b"previous-package")
            with self.assertRaises(ValueError):
                export_account_data(manager, "missing-account", destination)
            self.assertEqual(destination.read_bytes(), b"previous-package")

    def test_legacy_import_upgrades_only_candidate_and_drops_local_backup_and_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            state = manager.initialize()
            previous = state.user_database_path.read_bytes()
            legacy = root / "legacy.sqlite3"
            schema = Path(__file__).resolve().parents[2] / "src/storage/sqlite/schema/001_user_data.sql"
            with sqlite3.connect(legacy) as connection:
                connection.executescript(schema.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO schema_migration VALUES (1, 'synthetic')")
                connection.execute("INSERT INTO database_profile VALUES (1, 'default', 'Legacy', 't', 't')")
                connection.execute("INSERT INTO sync_settings VALUES (1, 'nte_core', 'gamepad', NULL, 0, 5, 't')")
            connection.close()
            archive = root / "legacy.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("manifest.json", json.dumps({"format": "nte-account-export", "version": 1,
                                                              "account": {"id": "default", "name": "Legacy"}}))
                package.write(legacy, "account/user_data.sqlite3")
                package.writestr("account/config/update_config.json", '{"mirror_cdk":"SYNTHETIC_SECRET","never_remind":true}')
                package.writestr("account/config/work_mode.json", '{"mode":"developer"}')
            released = []

            def release(account_id):
                self.assertEqual(state.user_database_path.read_bytes(), previous)
                released.append(account_id)

            self.assertEqual(import_account_data(manager, archive, before_replace=release), "default")
            self.assertEqual(released, ["default"])
            with UserDataDao(state.user_database_path) as database:
                self.assertEqual(database.profile()["account_name"], "Legacy")
            self.assertFalse((state.account_data_root / ".migration-backups").exists())
            self.assertFalse((state.user_config_dir / "work_mode.json").exists())
            self.assertEqual(json.loads((state.user_config_dir / "update_config.json").read_text(encoding="utf-8")),
                             {"never_remind": True})

    def test_account_import_releases_existing_target_before_directory_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_manager = self.make_manager(root / "source")
            source_manager.initialize()
            source_id = source_manager.create_account("Replace Me")
            archive = export_account_data(
                source_manager, source_id, root / "replace.zip",
            )

            target_manager = self.make_manager(root / "target")
            target_manager.initialize()
            target_id = target_manager.create_account("Replace Me")
            released: list[str] = []

            imported_id = import_account_data(
                target_manager, archive, before_replace=released.append,
            )

            self.assertEqual(imported_id, target_id)
            self.assertEqual(released, [target_id])

    def test_invalid_import_keeps_the_existing_matching_account_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self.make_manager(root)
            manager.initialize()
            account_id = manager.create_account("Existing Account")
            marker = manager.account_dir(account_id) / "config" / "marker.json"
            marker.write_text('{"keep":true}', encoding="utf-8")
            archive = root / "unsafe-replacement.zip"
            with zipfile.ZipFile(archive, "w") as exported:
                exported.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "format": "nte-account-export",
                            "version": 1,
                            "account": {"id": "existing", "name": "Existing Account"},
                        }
                    ),
                )
                exported.writestr("account/../../escape.txt", "blocked")

            with self.assertRaisesRegex(ValueError, "unsafe zip path"):
                import_account_data(manager, archive)

            self.assertEqual('{"keep":true}', marker.read_text(encoding="utf-8"))
            self.assertEqual("Existing Account", manager.account_meta(account_id)["name"])
            self.assertFalse((root / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
