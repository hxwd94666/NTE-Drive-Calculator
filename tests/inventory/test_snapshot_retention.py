# 验证快照清理的已提交来源、兼容当前指针、参数预算和跨库保护边界。
from __future__ import annotations

import sqlite3

import pytest

from src.storage.sqlite.inventory.inventory_snapshot_usage import exported_snapshot_ids
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError, UserDataValidationError


def snapshot(sequence):
    return {"complete": True, "item_count": 0, "items": [], "generation": 1, "sequence": sequence}


def test_export_rejects_source_pruned_after_outer_read_view_without_changing_transaction(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    with UserDataDao(path, account_id="fixture") as reader, UserDataDao(path) as writer:
        selected = reader.import_inventory_snapshot(snapshot(1))
        with reader.read_consistent_state():
            # 正式构造上下文先读账号；这一步已将 WAL 读视图固定在旧提交。
            reader.profile()
            current = writer.import_inventory_snapshot(snapshot(2))
            assert writer.prune_inventory_snapshots(retain_recent=0)["deleted_snapshot_ids"] == [selected]
            assert reader.inventory_snapshot_summary(selected) is not None
            with pytest.raises(UserDataValidationError, match="已清理或尚未提交"):
                reader.export_inventory_snapshot(selected)
            assert selected not in exported_snapshot_ids(path)
            assert reader._db().in_transaction
            assert reader.inventory_snapshot_summary(selected) is not None
        assert reader.inventory_snapshot_summary(selected) is None
        assert reader.current_inventory_snapshot_id() == current
        assert reader.list_loadout_plans() == []


def test_prune_retains_same_fallback_current_as_public_read(tmp_path):
    with UserDataDao(tmp_path / "fallback.sqlite3", account_id="fixture") as dao:
        old = dao.import_inventory_snapshot(snapshot(1))
        current = dao.import_inventory_snapshot(snapshot(2))
        dao._db().execute("UPDATE inventory_snapshot SET is_current=0")
        dao._db().commit()
        assert dao.current_inventory_snapshot_id() == current
        result = dao.prune_inventory_snapshots(retain_recent=0)
        assert result["deleted_snapshot_ids"] == [old]
        assert result["current_snapshot_ids"] == [current]
        assert dao.current_inventory_snapshot_id() == current


@pytest.mark.parametrize("fail_late_batch", [False, True])
def test_prune_respects_parameter_budget_and_rolls_back_all_batches(tmp_path, fail_late_batch):
    with UserDataDao(tmp_path / "bounded.sqlite3", account_id="fixture") as dao:
        ids = [dao.import_inventory_snapshot(snapshot(n)) for n in range(1002)]
        # 即使非活动历史方案也拥有自己的来源；分批清理不能只保护当前槽位。
        plan = dao.save_loadout_plan(name="history", character_id=1, source_snapshot_id=ids[0], assignments=[])
        dao._db().setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        before = dao.list_inventory_snapshots()
        deletes = []
        dao._db().set_trace_callback(lambda sql: deletes.append(sql) if sql.startswith("DELETE FROM inventory_snapshot WHERE") else None)
        if fail_late_batch:
            dao._db().execute(
                "CREATE TEMP TRIGGER late_failure BEFORE DELETE ON inventory_snapshot "
                f"WHEN OLD.snapshot_id={ids[-2]} BEGIN SELECT RAISE(ABORT,'synthetic-delete-failure'); END"
            )
            with pytest.raises(UserDataError):
                dao.prune_inventory_snapshots(retain_recent=0)
            assert dao.list_inventory_snapshots() == before
        else:
            result = dao.prune_inventory_snapshots(retain_recent=0)
            assert result["deleted_snapshot_ids"] == ids[1:-1]
            assert result["total_after"] == 2
        # trigger 会重复trace同一SQL，按distinct语句证明失败确在第二批，不冒称中途提交。
        assert len(set(deletes)) == 2
        assert not dao._db().in_transaction
        assert dao.get_loadout_plan(plan)["source_snapshot_id"] == ids[0]
        assert dao.current_inventory_snapshot_id() == ids[-1]


def test_successful_export_protects_only_its_database_and_keeps_current(tmp_path):
    paths = [tmp_path / "account-a.sqlite3", tmp_path / "account-b.sqlite3"]
    for index, path in enumerate(paths):
        with UserDataDao(path, account_id=f"fixture-{index}") as dao:
            selected = dao.import_inventory_snapshot(snapshot(1))
            if index == 0:
                summary, items = dao.export_inventory_snapshot(selected)
                assert summary["snapshot_id"] == selected and items == []
            current = dao.import_inventory_snapshot(snapshot(2))
        with UserDataDao(path) as cleanup:
            result = cleanup.prune_inventory_snapshots(retain_recent=0)
            assert result["deleted_snapshot_ids"] == ([] if index == 0 else [selected])
            assert cleanup.current_inventory_snapshot_id() == current
            assert (selected in exported_snapshot_ids(path)) == (index == 0)
