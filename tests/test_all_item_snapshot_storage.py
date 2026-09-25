# 验证完整物品归档的迁移、账号隔离、取消回滚及装备集合不变。
from copy import deepcopy
import sqlite3
from unittest.mock import patch

import pytest

from src.domain.all_item_snapshot import ALL_ITEMS_SCOPE
from src.storage.sqlite import user_data_base
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError, UserDataValidationError
from tests.test_user_data_inventory_dao import item, snapshot
from tests.user_data_migration_helpers import create_user_database_at_version


def all_items(count=3):
    return dict(providerId="synthetic", domain="inventory", snapshotId="all-1", generation="1",
                domainKey="synthetic-domain", revision="3", observedUnixUs="1000", observedMonotonicMs="20",
                ready=True, enabled=True, dirty=False, state="ready", recordCount=count, sourceRecordCount=count,
                enumerationComplete=True, failed=False, truncated=False, collectionComplete=True,
                collectionScope=ALL_ITEMS_SCOPE, complete=False, sourceCoverage="unknown", changeCoverage="unknown",
                missing=["client_inventory_containers_not_server_account_proof"],
                records=[{"kind":"synthetic", "itemId": str(index), "unknownField": None} for index in range(count)])


def save(dao, raw=None, check=lambda: None):
    return dao.save_all_item_snapshot(raw if raw is not None else all_items(), account_id="a", check=check)


def test_old_database_upgrades_without_touching_equipment(tmp_path):
    database = tmp_path / "old.sqlite3"
    create_user_database_at_version(database, 42, account_id="a")
    with patch.object(user_data_base, "SCHEMA_VERSION", 42), UserDataDao(database) as dao:
        equipment_id = dao.import_inventory_snapshot(snapshot(1, [item(1, 2)]), protocol_version=1)
        before = dao.raw_snapshot(equipment_id)
    with UserDataDao(database) as dao:
        assert dao.latest_all_item_snapshot() is None
        saved = save(dao)
        assert saved == save(dao)
        assert dao.latest_all_item_snapshot() == all_items()
        assert dao.current_inventory_snapshot_id() == equipment_id
        assert dao.raw_snapshot(equipment_id) == before
        assert len(dao.list_current_inventory_items()) == 1
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_failure_rolls_back_and_can_retry(tmp_path):
    database = tmp_path / "old.sqlite3"
    create_user_database_at_version(database, 42, account_id="a")
    original = user_data_base.UserDataDaoCore._execute_migration_script
    def fail(connection, script):
        original(connection, script)
        raise sqlite3.OperationalError("synthetic failure")
    with patch.object(user_data_base.UserDataDaoCore, "_execute_migration_script", staticmethod(fail)):
        with pytest.raises(UserDataError):
            UserDataDao(database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 42
        assert db.execute("SELECT name FROM sqlite_master WHERE name='all_item_snapshot'").fetchone() is None
    with UserDataDao(database) as dao:
        assert save(dao) > 0


@pytest.mark.parametrize("change", [{"truncated": True}, {"failed": True}, {"dirty": True},
                                  {"enumerationComplete": False}, {"collectionComplete": False},
                                  {"collectionScope": "EQUIP"}, {"sourceRecordCount": 4},
                                  {"records": []}, {"complete": True}])
def test_partial_snapshot_never_replaces_last_archive(tmp_path, change):
    with UserDataDao(tmp_path / "a.sqlite3", account_id="a") as dao:
        save(dao)
        raw = all_items(); raw.update(snapshotId="new", **change)
        with pytest.raises(UserDataValidationError):
            save(dao, raw)
        assert dao.latest_all_item_snapshot() == all_items()


def test_identity_conflict_account_isolation_and_unknown_fields(tmp_path):
    with UserDataDao(tmp_path / "a.sqlite3", account_id="a") as a, UserDataDao(tmp_path / "b.sqlite3", account_id="b") as b:
        save(a)
        assert b.latest_all_item_snapshot() is None
        with pytest.raises(UserDataValidationError):
            save(b)
        changed = all_items(); changed["records"][0]["unknownField"] = 100
        with pytest.raises(UserDataValidationError):
            save(a, changed)
        assert a.latest_all_item_snapshot()["records"][0]["unknownField"] is None
        assert a.current_inventory_snapshot_id() is None


def test_cancel_before_commit_rolls_back_including_retention(tmp_path):
    with UserDataDao(tmp_path / "a.sqlite3", account_id="a") as dao:
        save(dao)
        before = deepcopy(dao.latest_all_item_snapshot())
        checks = []
        def cancel():
            checks.append(True)
            if len(checks) == 3:
                raise InterruptedError("cancelled")
        changed = all_items(); changed["snapshotId"] = "new"
        with pytest.raises(InterruptedError):
            save(dao, changed, check=cancel)
        assert dao.latest_all_item_snapshot() == before
        assert save(dao, changed) > 0


def test_archive_retention_is_separate_from_equipment(tmp_path):
    with UserDataDao(tmp_path / "a.sqlite3", account_id="a") as dao:
        equipment_id = dao.import_inventory_snapshot(snapshot(1, [item(1, 2)]), protocol_version=1)
        for index in range(22):
            raw = all_items(0); raw["snapshotId"] = str(index)
            save(dao, raw)
        assert dao._db().execute("SELECT COUNT(*) FROM all_item_snapshot").fetchone()[0] == 20
        assert dao.current_inventory_snapshot_id() == equipment_id
