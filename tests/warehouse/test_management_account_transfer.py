# 用真实暂存账号包验证两套管理与三匹配域往返、旧包升级及失败不替换。
import json
import sqlite3
import zipfile
from contextlib import closing
from copy import deepcopy

import pytest

from src.domain.equipment.warehouse.intelligent_rules import attach_origin
from src.domain.equipment.eligibility.management_settings import MatchingScope, split_legacy_management_config
from src.domain.inventory.post_actions import default_post_action_config
from src.features.accounts.manager import AccountManager
from src.services.accounts.transfer import export_account_data, import_account_data
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.storage.user_data_migration_helpers import create_user_database_at_version


def manager(root):
    bundled = root / "bundled"
    bundled.mkdir(parents=True)
    result = AccountManager(
        data_root=root / "data",
        bundled_config_dir=bundled,
        iter_image_files=lambda _: [],
        core_config_files=(),
        account_user_files=(),
    )
    result.initialize()
    return result


def copies(store, account="default"):
    with UserDataDao(store.account_dir(account) / "user_data.sqlite3") as dao:
        return dao.list_application_setting_copies()


def package(path, database, *, account="default", name="迁移测试账号"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format": "nte-account-export", "version": 1, "account": {"id": account, "name": name}}),
        )
        archive.write(database, "account/user_data.sqlite3")
    return path


def test_distinct_management_domains_survive_two_complete_account_round_trips(tmp_path):
    source = manager(tmp_path / "source")
    database = source.account_dir("default") / "user_data.sqlite3"
    expected = {}
    for index, scope in enumerate((MatchingScope.SCAN, MatchingScope.WAREHOUSE), 1):
        service = ManagementSettingsService(database, scope=scope)
        config, matching = service.load()
        changed = deepcopy(config)
        changed["lock"].update(enabled=True, grade="ACE" if index == 1 else "A")
        changed["preserve_rules"] = [
            {
                "name": scope.value,
                "enabled": True,
                "item_type": "tape",
                "main_stats": ["生命值%"],
                "action": "keep" if index == 1 else "discard",
            }
        ]
        new_matching = {"version": 2, "extra_suits": {}, "suit_overrides": {"1003": [f"Suit{index}"]}}
        service.save(changed, new_matching, expected_config=config, expected_matching=matching)
        expected[scope] = service.load()
    identification = {"version": 1, "extra_suits": {"1004": ["Suit3"]}}
    with UserDataDao(database) as dao:
        dao.replace_application_setting_copy(MatchingScope.IDENTIFICATION.setting_key, identification)
        dao.replace_application_setting_copy("work_mode", {"mode": "developer"})
        dao.replace_application_setting_copy("plugins", {"path": "SYNTHETIC_LOCAL_PATH"})
    archive = export_account_data(source, "default", tmp_path / "first.zip")
    for index in range(2):
        destination = manager(tmp_path / f"target-{index}")
        other = destination.create_account("其他账号")
        before_other = copies(destination, other)
        import_account_data(destination, archive)
        imported = destination.account_dir("default") / "user_data.sqlite3"
        for scope, value in expected.items():
            assert ManagementSettingsService(imported, scope=scope).load() == value
        values = copies(destination)
        assert values[MatchingScope.IDENTIFICATION.setting_key] == identification
        assert not values.get("work_mode") and not values.get("plugins")
        assert copies(destination, other) == before_other
        assert not (imported.parent / ".migration-backups").exists()
        archive = export_account_data(destination, "default", tmp_path / f"next-{index}.zip")
        with zipfile.ZipFile(archive) as exported:
            assert all(
                not any(part in name for part in ("logs/", ".migration-backups/", "-wal", "-shm"))
                for name in exported.namelist()
            )


def test_v51_package_upgrade_preserves_static_scan_rules_and_all_warehouse_sources(tmp_path):
    legacy = tmp_path / "legacy.sqlite3"
    create_user_database_at_version(legacy, 51, account_id="default")
    config = default_post_action_config()
    config["discard"].update(enabled=True, discard_unmatched=False)
    ordinary = {"name": "普通", "enabled": True, "item_type": "tape", "main_stats": ["生命值%"], "action": "keep"}
    config["preserve_rules"] = [attach_origin({**ordinary, "name": "静态智能"}, "combo:test"), ordinary]
    matching = {"version": 2, "extra_suits": {}, "suit_overrides": {"1003": ["Suit2"]}}
    with closing(sqlite3.connect(legacy)) as connection, connection:
        for key, value in [
            ("ui", {"full_scan_post_action_config": config}),
            (MatchingScope.IDENTIFICATION.setting_key, matching),
        ]:
            connection.execute("INSERT INTO application_setting_copy VALUES (?,?,?)", (key, json.dumps(value), "old"))
    archive = package(tmp_path / "old.zip", legacy)
    expected_scan, expected_warehouse = split_legacy_management_config(config)
    destination = manager(tmp_path / "target")
    import_account_data(destination, archive)
    for index in range(2):
        database = destination.account_dir("default") / "user_data.sqlite3"
        for scope, expected in [(MatchingScope.SCAN, expected_scan), (MatchingScope.WAREHOUSE, expected_warehouse)]:
            assert ManagementSettingsService(database, scope=scope).load() == (expected, matching)
        assert copies(destination)[MatchingScope.IDENTIFICATION.setting_key] == matching
        assert not (database.parent / ".migration-backups").exists()
        archive = export_account_data(destination, "default", tmp_path / f"upgraded-{index}.zip")
        destination = manager(tmp_path / f"again-{index}")
        import_account_data(destination, archive)
    with closing(sqlite3.connect(legacy)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 51


def test_invalid_management_matching_rejects_package_before_releasing_current_account(tmp_path):
    source, target = manager(tmp_path / "source"), manager(tmp_path / "target")
    database = source.account_dir("default") / "user_data.sqlite3"
    with UserDataDao(database) as dao:
        dao.replace_application_setting_copy(
            MatchingScope.WAREHOUSE.setting_key, {"version": 2, "extra_suits": {}, "suit_overrides": {"1003": []}}
        )
    archive = package(tmp_path / "invalid.zip", database, name="默认账号")
    before_index, before_data = target.read_index(), copies(target)
    released = []
    with pytest.raises(ValueError, match="至少保留一个套装"):
        import_account_data(target, archive, before_replace=released.append)
    assert not released and target.read_index() == before_index and copies(target) == before_data
