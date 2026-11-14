# 验证两套管理配置和匹配域的迁移保真、原子保存、隔离与失败重试。
import json
import sqlite3
from concurrent.futures import CancelledError
from contextlib import closing
from copy import deepcopy

import pytest

from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.equipment.warehouse.intelligent_rules import attach_origin, generated_rules
from src.domain.inventory.post_actions import default_post_action_config, evaluate_target_state_for_item
from src.models.equipment import Tape
from src.services.identification.matching_settings import MatchingSettingsService
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.storage.user_data_migration_helpers import create_user_database_at_version


def test_skip_scoring_defaults_for_existing_intelligent_rules_and_preserves_explicit_choice():
    assert normalize_warehouse_config()["skip_scoring"] is False
    assert normalize_warehouse_config({"skip_scoring": True})["skip_scoring"] is False
    legacy = {"preserve_rules": generated_rules({"grade": "SSS"}, strong_set_name=None)}
    before = deepcopy(legacy)
    assert normalize_warehouse_config(legacy)["skip_scoring"] is True
    for choice in (False, True):
        normalized = normalize_warehouse_config({**legacy, "skip_scoring": choice})
        assert normalized["skip_scoring"] is choice
        assert normalize_warehouse_config(normalized) == normalized
    assert legacy == before
    # 不把字符串或数字当成明确的跳过指令。
    assert normalize_warehouse_config({**legacy, "skip_scoring": "false"})["skip_scoring"] is False


def test_skip_scoring_saves_per_account_without_changing_scanning_or_thresholds(tmp_path):
    paths = [tmp_path / f"account-{index}.sqlite3" for index in range(2)]
    for index, path in enumerate(paths):
        with UserDataDao(path, account_id=f"fixture-{index}"):
            pass
    warehouse = ManagementSettingsService(paths[0], scope=MatchingScope.WAREHOUSE)
    scan = ManagementSettingsService(paths[0], scope=MatchingScope.SCAN)
    scan_before = scan.load()
    original, matching = warehouse.load()
    config = deepcopy(original)
    config["preserve_rules"] = generated_rules({"grade": "SSS"}, strong_set_name=None)
    for choice in (True, False):
        config["skip_scoring"] = choice
        config = warehouse.save(config, matching, expected_config=original, expected_matching=matching)
        assert warehouse.load()[0]["skip_scoring"] is choice
        assert config["lock"] == original["lock"] and config["discard"] == original["discard"]
        assert scan.load() == scan_before
        assert ManagementSettingsService(paths[1], scope=MatchingScope.WAREHOUSE).load()[0]["skip_scoring"] is False
        original = deepcopy(config)


def old_case(path):
    create_user_database_at_version(path, 51)
    ordinary = {"enabled": True, "name": "手工", "item_type": "tape", "action": "keep", "main_stats": ["生命值%"]}
    auto = attach_origin({**ordinary, "name": "自动词条", "action": "discard"}, "combo:test")
    dynamic = generated_rules({"grade": "SSS"}, strong_set_name="套装")[0]
    copied = {k: v for k, v in dynamic.items() if k != "intelligent"}
    copied.update(name="动态副本", enabled=False)
    config = default_post_action_config()
    config["discard"].update(enabled=True, discard_unmatched=False)
    config["preserve_rules"] = [auto, ordinary, dynamic, copied]
    config["recommendation"] = {"rule_scheme_version": 3, "last_drive_grade": "SSS"}
    matching = {"version": 2, "extra_suits": {}, "suit_overrides": {"1003": ["Suit1"]}}
    with sqlite3.connect(path) as db:
        for key, value in [
            ("ui", {"full_scan_post_action_config": config, "log_enabled": True}),
            ("identification_role_suits", matching),
        ]:
            db.execute("INSERT INTO application_setting_copy VALUES (?,?,?)", (key, json.dumps(value), "old"))
    return config, matching


def test_migration_preserves_scanning_results_and_clones_matching_without_link(tmp_path):
    path = tmp_path / "old.sqlite3"
    old, matching = old_case(path)
    with UserDataDao(path) as dao:
        copies = dao.list_application_setting_copies()
    scan, scan_match = ManagementSettingsService(path, scope=MatchingScope.SCAN).load()
    warehouse, warehouse_match = ManagementSettingsService(path, scope=MatchingScope.WAREHOUSE).load()
    assert scan_match == warehouse_match == matching
    assert copies["identification_role_suits"] == matching
    assert [r["name"] for r in scan["preserve_rules"]] == ["自动词条", "手工"]
    assert all(not r.get("intelligent") and not r.get("smart_kind") for r in scan["preserve_rules"])
    assert scan["discard"]["discard_unmatched"] is True
    assert "discard_unmatched" not in warehouse["discard"]
    assert len(warehouse["preserve_rules"]) == 4
    assert warehouse["preserve_rules"][-1]["intelligent"]
    item = Tape(uid="synthetic", quality="Gold", area=15, set_name="套装", main_stats="生命值%", sub_stats={})
    for state in ["normal", "locked", "discarded"]:
        assert (
            evaluate_target_state_for_item(item, state, old, None)[0]
            == evaluate_target_state_for_item(item, state, scan, None)[0]
        )
    with UserDataDao(path) as dao:
        assert dao.list_application_setting_copies() == copies
    assert list((path.parent / ".migration-backups").glob("*.sqlite3"))


def test_save_isolated_by_management_and_account_and_detects_stale_edits(tmp_path):
    first, second = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    for path in (first, second):
        with UserDataDao(path, account_id=path.stem):
            pass
    scan = ManagementSettingsService(first, scope=MatchingScope.SCAN)
    warehouse = ManagementSettingsService(first, scope=MatchingScope.WAREHOUSE)
    scan_base, scan_matching = scan.load()
    ware_base, ware_matching = warehouse.load()
    value = deepcopy(ware_base)
    value["lock"]["grade"] = "ACE"
    changed_matching = {"version": 1, "extra_suits": {"1003": ["Suit2"]}}
    warehouse.save(value, changed_matching, expected_config=ware_base, expected_matching=ware_matching)
    assert scan.load() == (scan_base, scan_matching)
    assert MatchingSettingsService(first).load() == {"version": 1, "extra_suits": {}}
    assert ManagementSettingsService(second, scope=MatchingScope.WAREHOUSE).load() == (ware_base, ware_matching)
    with pytest.raises(UserDataValidationError):
        warehouse.save(ware_base, ware_matching, expected_config=ware_base, expected_matching=ware_matching)
    assert warehouse.load() == (value, changed_matching)


def test_transaction_cancel_rolls_back_all_fields_and_retry_succeeds(tmp_path):
    path = tmp_path / "account.sqlite3"
    with UserDataDao(path, account_id="test"):
        pass
    service = ManagementSettingsService(path, scope=MatchingScope.SCAN)
    config, matching = service.load()
    changed = deepcopy(config)
    changed["lock"]["grade"] = "ACE"
    calls = []

    def cancel():
        calls.append(True)
        if len(calls) >= 3:
            raise CancelledError()

    with pytest.raises(CancelledError):
        service.save(
            changed,
            {"version": 1, "extra_suits": {"1003": ["Suit1"]}},
            expected_config=config,
            expected_matching=matching,
            check_current=cancel,
        )
    assert service.load() == (config, matching)
    service.save(changed, matching, expected_config=config, expected_matching=matching)
    assert service.load()[0] == changed


def test_migration_failure_is_atomic_and_retry_preserves_values(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    old, matching = old_case(path)
    original = UserDataDao._execute_migration_script

    def fail(connection, script):
        if "管理配置与匹配域分离" in script:
            raise RuntimeError("injected")
        return original(connection, script)

    with monkeypatch.context() as patch:
        patch.setattr(UserDataDao, "_execute_migration_script", staticmethod(fail))
        with pytest.raises(RuntimeError, match="injected"):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 51
        values = {
            k: json.loads(v) for k, v in db.execute("SELECT setting_key,value_json FROM application_setting_copy")
        }
        assert values["ui"]["full_scan_post_action_config"] == old
        assert "scan_management_role_suits" not in values
    assert ManagementSettingsService(path, scope=MatchingScope.SCAN).load()[1] == matching


def test_matching_identity_contains_scope_and_rejects_cross_scope(tmp_path):
    path = tmp_path / "new.sqlite3"
    with UserDataDao(path, account_id="test"):
        pass
    scan = MatchingSettingsService(path, scope=MatchingScope.SCAN)
    warehouse = MatchingSettingsService(path, scope=MatchingScope.WAREHOUSE)
    assert scan.identity(scan.load()) != warehouse.identity(warehouse.load())
    with pytest.raises(CancelledError):
        warehouse.require_identity(scan.identity(scan.load()))
    assert normalize_warehouse_config()["version"] == 1


def test_old_json_can_seed_new_scopes_after_database_schema_upgrade(tmp_path):
    path = tmp_path / "json.sqlite3"
    config = default_post_action_config()
    config["discard"].update(grade="B", discard_unmatched=False)
    config["preserve_rules"] = [{"enabled": True, "name": "旧文件规则", "item_type": "tape", "main_stats": ["生命值%"]}]
    with UserDataDao(path, account_id="legacy-json") as dao:
        dao.initialize_equipment_management_settings(config)
    scan = ManagementSettingsService(path, scope=MatchingScope.SCAN)
    warehouse = ManagementSettingsService(path, scope=MatchingScope.WAREHOUSE)
    assert scan.load()[0]["discard"]["grade"] == "B"
    assert warehouse.load()[0]["discard"]["grade"] == "B"
    changed, matching = scan.load()
    changed["preserve_rules"] = []
    scan.save(changed, matching, expected_config=scan.load()[0], expected_matching=matching)
    with UserDataDao(path) as dao:
        dao.initialize_equipment_management_settings(config)
    assert scan.load()[0]["preserve_rules"] == []
    assert warehouse.load()[0]["preserve_rules"][0]["name"] == "旧文件规则"


def test_portable_settings_include_all_management_domains_without_local_preferences():
    from src.storage.sqlite.accounts.transfer_database import portable_settings

    config = normalize_warehouse_config()
    value = portable_settings(
        "ui", {"warehouse_management_config": config, "theme": "black", "private_path": "not-exported"}
    )
    assert value == {"warehouse_management_config": config}
    for scope in (MatchingScope.SCAN, MatchingScope.WAREHOUSE):
        matching = {"version": 1, "extra_suits": {}, "private_path": "not-exported"}
        assert portable_settings(scope.setting_key, matching) == {"version": 1, "extra_suits": {}}


def test_legacy_custom_keep_rules_are_retained_in_both_new_management_scopes():
    from src.domain.equipment.eligibility.management_settings import split_legacy_management_config

    raw = {
        "custom_keep": {
            "tape_rules": [{"main_stats": ["生命值%"], "action": "lock"}],
            "drive_rules": [{"sub_stats": ["攻击力%"], "minimum_sub_matches": 1}],
        }
    }
    scan, warehouse = split_legacy_management_config(raw)
    assert len(scan["preserve_rules"]) == len(warehouse["preserve_rules"]) == 2
    assert [row["action"] for row in warehouse["preserve_rules"]] == ["lock", "keep"]
    assert normalize_warehouse_config(warehouse) == warehouse


def test_unrelated_account_ui_save_preserves_both_management_configs(tmp_path):
    from src.services.accounts.settings import AccountSettingsService

    path = tmp_path / "account.sqlite3"
    with UserDataDao(path, account_id="test"):
        pass
    expected = {}
    for scope in (MatchingScope.SCAN, MatchingScope.WAREHOUSE):
        service = ManagementSettingsService(path, scope=scope)
        config, matching = service.load()
        changed = deepcopy(config)
        changed["lock"]["grade"] = "ACE"
        expected[scope] = (
            service.save(changed, matching, expected_config=config, expected_matching=matching),
            matching,
        )
    settings = AccountSettingsService(path)
    draft = settings.load("ui")
    draft["log_enabled"] = not draft["log_enabled"]
    settings.save("ui", draft)
    for scope, value in expected.items():
        assert ManagementSettingsService(path, scope=scope).load() == value


@pytest.mark.parametrize("scope", [MatchingScope.SCAN, MatchingScope.WAREHOUSE])
def test_stale_ui_preferences_do_not_restore_an_older_management_configuration(tmp_path, scope):
    from src.services.accounts.settings import AccountSettingsService

    path = tmp_path / "account.sqlite3"
    with UserDataDao(path, account_id="test"):
        pass
    settings = AccountSettingsService(path)
    service = ManagementSettingsService(path, scope=scope)
    base, matching = service.load()
    service.save(base, matching, expected_config=base, expected_matching=matching)
    stale = settings.load("ui")
    changed = deepcopy(base)
    changed["lock"]["grade"] = "ACE"
    saved = service.save(changed, matching, expected_config=base, expected_matching=matching)
    stale["log_enabled"] = True
    settings.save("ui", stale)
    assert service.load() == (saved, matching)


def test_v52_backup_can_restore_the_exact_v51_settings_without_downgrading_new_data(tmp_path):
    from src.storage.sqlite.accounts.transfer_database import backup_account_database

    path = tmp_path / "old.sqlite3"
    old, matching = old_case(path)
    with UserDataDao(path) as dao:
        upgraded = dao.list_application_setting_copies()
    service = ManagementSettingsService(path, scope=MatchingScope.WAREHOUSE)
    config, baseline_matching = service.load()
    changed = deepcopy(config)
    changed["lock"]["grade"] = "ACE"
    service.save(changed, baseline_matching, expected_config=config, expected_matching=baseline_matching)
    # 恢复只在隔离副本演练；新版事实先一致备份，真实当前库和 schema 标记均不回退。
    preserved, restored = tmp_path / "preserved.sqlite3", tmp_path / "restored.sqlite3"
    backup_account_database(path, preserved)
    backups = sorted((tmp_path / ".migration-backups").glob("user-data-v51-*.sqlite3"))
    assert len(backups) == 1
    backup_account_database(backups[0], restored)
    with closing(sqlite3.connect(restored)) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 51
        values = {
            key: json.loads(raw)
            for key, raw in db.execute("SELECT setting_key,value_json FROM application_setting_copy")
        }
    assert values["ui"]["full_scan_post_action_config"] == old
    assert values["identification_role_suits"] == matching
    assert "warehouse_management_config" not in values["ui"]
    assert ManagementSettingsService(preserved, scope=MatchingScope.WAREHOUSE).load() == service.load()
    assert upgraded["ui"]["warehouse_management_config"] != service.load()[0]
