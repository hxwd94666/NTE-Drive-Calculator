# 验证低风险物品观测的账号存储、稳定化、迁移及取消回滚。
from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.inventory.sync.capture_wait import InventorySyncCancelled
from src.services.inventory.sync.packet_observation import PacketItemObservationSync
from src.storage.sqlite import user_data_base
from src.storage.sqlite.inventory.packet_item_observation_dao import (
    normalize_packet_item_observation,
    read_latest_packet_item_observation,
)
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError, UserDataValidationError
from tests.test_user_data_inventory_dao import item, snapshot
from tests.user_data_migration_helpers import create_user_database_at_version


def observation(*, generation=1, sequence=1, amount=7):
    return {
        "source": "packet", "complete": False, "generation": generation,
        "sequence": sequence, "observed_at_unix_ms": 1_000,
        "item_count": 1,
        "items": [{"uid": {"slot": 1, "serial": 2},
                   "item_id": "known", "amount": amount}],
    }


@pytest.mark.parametrize("change", [
    {"item_count": 0, "items": []}, {"generation": 0},
    {"source": "native"}, {"complete": True},
])
def test_packet_observation_rejects_incomplete_contract(change):
    value = observation()
    value.update(change)
    with pytest.raises(UserDataValidationError):
        normalize_packet_item_observation(value)


def test_saved_packet_items_survive_stop_without_advancing_complete_inventory(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="a") as dao:
        saved = dao.save_packet_item_observation(
            observation(), account_id="a", check=lambda: None
        )
        assert saved == dao.save_packet_item_observation(
            observation(), account_id="a", check=lambda: None
        )
        assert dao.current_inventory_snapshot_id() is None
    row = read_latest_packet_item_observation(database, account_id="a")
    assert row["observation_id"] == saved
    assert row["snapshot"]["items"][0]["amount"] == 7
    assert row["inventory_snapshot_id"] is None
    with pytest.raises(UserDataValidationError, match="账号"):
        read_latest_packet_item_observation(database, account_id="b")


def test_packet_items_can_link_current_complete_snapshot_without_changing_it(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="a") as dao:
        inventory_id = dao.import_inventory_snapshot(snapshot(1, [item(1, 2)]))
        original = dao.raw_snapshot(inventory_id)
        dao.save_packet_item_observation(
            observation(), account_id="a", check=lambda: None,
            inventory_snapshot_id=inventory_id,
        )
        assert dao.current_inventory_snapshot_id() == inventory_id
        assert dao.raw_snapshot(inventory_id) == original
    assert read_latest_packet_item_observation(
        database, account_id="a"
    )["inventory_snapshot_id"] == inventory_id


def test_packet_item_validation_and_cancel_preserve_previous_saved_version(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="a") as dao:
        first = dao.save_packet_item_observation(
            observation(), account_id="a", check=lambda: None
        )
        invalid = observation(generation=2, sequence=2)
        invalid["complete"] = True
        with pytest.raises(UserDataValidationError):
            dao.save_packet_item_observation(invalid, account_id="a", check=lambda: None)
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise InventorySyncCancelled()

        with pytest.raises(InventorySyncCancelled):
            dao.save_packet_item_observation(
                observation(generation=2, sequence=2, amount=8),
                account_id="a", check=cancel,
            )
        assert dao.current_inventory_snapshot_id() is None
    assert read_latest_packet_item_observation(database, account_id="a")["observation_id"] == first


def test_old_account_migrates_and_failed_migration_can_retry(tmp_path):
    database = tmp_path / "old.sqlite3"
    create_user_database_at_version(database, 43, account_id="a")
    assert read_latest_packet_item_observation(database, account_id="a") is None
    original = user_data_base.UserDataDaoCore._execute_migration_script

    def fail(connection, script):
        original(connection, script)
        raise sqlite3.OperationalError("synthetic failure")

    with patch.object(user_data_base.UserDataDaoCore, "_execute_migration_script", staticmethod(fail)):
        with pytest.raises(UserDataError):
            UserDataDao(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 43
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='packet_item_observation'"
        ).fetchone() is None
    with UserDataDao(database) as dao:
        assert dao.save_packet_item_observation(
            observation(), account_id="a", check=lambda: None
        ) > 0
    assert read_latest_packet_item_observation(database, account_id="a") is not None


def test_corrupt_saved_packet_items_are_rejected(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="a") as dao:
        dao.save_packet_item_observation(observation(), account_id="a", check=lambda: None)
        dao._db().execute("UPDATE packet_item_observation SET raw_snapshot_json='{}'")
        dao._db().commit()
    with pytest.raises(UserDataValidationError, match="校验失败"):
        read_latest_packet_item_observation(database, account_id="a")


def test_packet_item_events_settle_before_writing_and_use_current_account(tmp_path):
    database = tmp_path / "account.sqlite3"
    current = {"value": True}
    service = SimpleNamespace(
        account_id="a", capture_source="packet", _event_lock=threading.Lock(),
        _event_ready=threading.Event(), _stop_requested=threading.Event(),
        _context_is_current=lambda: current["value"],
        _operation_guard=lambda _capability: None, _operation_context=None,
    )
    with UserDataDao(database, account_id="a") as dao, \
            patch("src.services.inventory.sync.packet_observation.log_event"):
        sync = PacketItemObservationSync(service, dao, settle_seconds=5.0)
        sync.on_event({"method": "event.inventory.items_observed", "params": observation()})
        with patch("src.services.inventory.sync.packet_observation.time.monotonic", return_value=100.0):
            sync.receive_latest()
        sync.save_if_stable(104.9)
        assert read_latest_packet_item_observation(database, account_id="a") is None
        current["value"] = False
        with pytest.raises(InventorySyncCancelled):
            sync.save_if_stable(105.0)
        current["value"] = True
        sync.save_if_stable(105.0)
        sync.on_event({"method": "event.inventory.items_observed", "params": observation(
            generation=2, sequence=2, amount=8,
        )})
        with patch("src.services.inventory.sync.packet_observation.time.monotonic", return_value=110.0):
            sync.receive_latest()
        sync.save_if_stable(114.9)
        assert read_latest_packet_item_observation(database, account_id="a")["snapshot"]["items"][0]["amount"] == 7
        sync.save_if_stable(115.0)
    latest = read_latest_packet_item_observation(database, account_id="a")["snapshot"]
    assert latest["item_count"] == 1
    assert latest["items"][0]["amount"] == 8
