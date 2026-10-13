# 验证公开原生同步 owner 在真实 SQL 最终事务阶段复核代次、取消和权限。
from __future__ import annotations

import hashlib
import sqlite3
from threading import Event
from unittest.mock import patch

import pytest

from src.services.inventory.sync.service import InventorySyncService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.inventory.sync.test_service import item, snapshot
from tests.inventory.sync.test_native_runtime import NativeLease

NTE_TEST_TIER = "core"


def database_facts(path):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA query_only = ON")
        tables = ("inventory_snapshot", "inventory_item", "inventory_item_stat", "character_instance_mapping")
        facts = {name: tuple(connection.execute("SELECT * FROM " + name + " ORDER BY rowid")) for name in tables}
        facts["current"] = tuple(connection.execute("SELECT snapshot_id FROM inventory_snapshot WHERE is_current=1"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    return facts, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("revocation", ["valid", "context", "stop", "permission"])
def test_native_owner_final_sql_guard_preserves_previous_facts(tmp_path, revocation):
    database = tmp_path / "synthetic.sqlite3"
    characters = [{"character_id": 101, "uid": {"slot": 5, "serial": 501}}]
    previous = snapshot(item(9), generation=90, sequence=1, characters=characters)
    candidate = snapshot(item(1), generation=91, sequence=2, characters=characters)
    with UserDataDao(database, account_id="synthetic") as seed:
        old_id = seed.import_inventory_snapshot(previous, source="nte_core", protocol_version=1)
    before, before_sha = database_facts(database)
    app_generation = [7]
    invalidated, finished = Event(), Event()
    traces, events, states, opened = [], [], [], []
    owner = []
    lease = NativeLease()
    emit = lease.emit
    lease.emit = lambda event: emit(candidate if event.get("method") == "event.inventory.snapshot" else event)

    def guard(_capability):
        if revocation == "permission" and invalidated.is_set():
            raise PermissionError("synthetic permission revoked")

    def factory(path, **values):
        dao = UserDataDao(path, **values)
        opened.append(dao)
        connection = dao._db()

        def trace(sql):
            statement = " ".join(sql.split())
            traces.append(statement)
            # 此钩子在最后指针 UPDATE 执行前触发；此前插入和旧指针清除已在真实事务内完成。
            # 只改变 owner 状态，不从 SQLite trace 抛异常，也不注入额外 SQL。
            if statement.startswith("UPDATE inventory_snapshot SET is_current = 1 WHERE snapshot_id ="):
                assert connection.in_transaction
                if revocation != "valid":
                    invalidated.set()
                    if revocation == "context":
                        app_generation[0] = 8
                    elif revocation == "stop":
                        owner[0].request_stop()

        connection.set_trace_callback(trace)
        return dao

    service = InventorySyncService(
        database, account_id="synthetic", capture_source="native", client_factory=lambda: lease,
        dao_factory=factory, settle_seconds=0.001, poll_seconds=0.001, raw_capture_enabled=False,
        operation_guard=guard, context_is_current=lambda: app_generation[0] == 7,
    )
    owner.append(service)

    def published(state):
        states.append(state)
        if revocation == "valid" and state.last_snapshot_id not in (None, old_id):
            service.request_stop()
        if state.phase == "stopped" or (state.phase == "error" and not state.running):
            finished.set()

    service.add_state_handler(published)
    # 外部 Core/lease 是合成输入；Service、最终门禁、稳定器、DAO 和事务均真实执行。
    with patch("src.services.inventory.sync.runtime.log_event", side_effect=lambda _level, event, *_args, **_kw: events.append(event)):
        try:
            service.start()
            assert finished.wait(3), "同步 owner 未在限定时间内收尾"
        finally:
            service.stop(timeout=3)
    after, after_sha = database_facts(database)
    assert lease.closed and lease.capture_stopped
    assert all(dao._connection is None for dao in opened)
    assert "BEGIN IMMEDIATE" in traces
    assert "UPDATE inventory_snapshot SET is_current = 0 WHERE is_current = 1" in traces
    assert any(sql.startswith("UPDATE inventory_snapshot SET is_current = 1 WHERE snapshot_id =") for sql in traces)
    assert "inventory_sync.snapshot_commit_retry" not in events
    if revocation == "valid":
        assert not invalidated.is_set()
        assert after["current"] == ((old_id + 1,),)
        assert len(after["inventory_snapshot"]) == len(before["inventory_snapshot"]) + 1
        assert "COMMIT" in traces and "ROLLBACK" not in traces
        assert "inventory_sync.snapshot_committed" in events
        assert service.state.last_snapshot_id == old_id + 1
        assert service.inventory_observation_cursor() == 1
        with UserDataDao(database) as check:
            # 原生来源代次属于数据协议，保持 91；不写成冻结的 AppContext 代次 7。
            assert check.inventory_snapshot_summary(old_id + 1)["generation"] == 91
    else:
        assert invalidated.is_set()
        assert after == before and after_sha == before_sha
        assert "ROLLBACK" in traces and "COMMIT" not in traces
        assert "inventory_sync.snapshot_committed" not in events
        assert all(state.last_snapshot_id in (None, old_id) for state in states)
        assert service.inventory_observation_cursor() == 0
        assert service.state.stop_reason == {
            "context": "context_changed", "stop": "stop_requested", "permission": "operation_failed",
        }[revocation]
