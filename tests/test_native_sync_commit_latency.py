# 验证原生候选短等待、提交前失效、重试和旧快照安全清理。
from threading import Event
from time import monotonic

from src.services.inventory_sync_service import InventorySyncService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_inventory_sync_service import item, snapshot
from tests.test_native_inventory_sync_runtime import NativeLease


def test_native_default_commits_after_short_quiet_window_and_prunes_unreferenced(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    with UserDataDao(path, account_id="test") as dao:
        for serial in (2, 3, 4):
            snapshot_id = dao.import_inventory_snapshot(snapshot(item(serial), sequence=serial))
            if serial == 2:
                protected_id = snapshot_id
                dao.save_loadout_plan(name="冻结方案", character_id=1003, source_snapshot_id=protected_id,
                                      assignments=[{"uid_slot": 8, "uid_serial": 2, "kind": "module",
                                                    "target_row": 1, "target_column": 1, "rotation": 0}])
        previous_id = dao.current_inventory_snapshot_id()
        dao.update_sync_settings(inventory_settle_seconds=15, inventory_snapshot_retention_count=20)
    lease = NativeLease()
    service = InventorySyncService(path, account_id="test", capture_source="native", client_factory=lambda: lease,
                                   operation_guard=lambda _cap: None, poll_seconds=0.005)
    collected, saved = [], []
    service.add_state_handler(lambda state: (collected.append(monotonic()) if state.phase == "collecting"
                                             else saved.append(monotonic()) if state.phase == "saving" else None))
    try:
        service.start()
        result = service.wait_for_snapshot(after_snapshot_id=previous_id, timeout=4)
        assert result.last_item_count == 1
        assert 0.19 <= saved[0] - collected[0] < 2
        with UserDataDao(path, account_id="test") as dao:
            assert {row["snapshot_id"] for row in dao.list_inventory_snapshots()} == {protected_id, result.last_snapshot_id}
            assert dao.list_loadout_plans(1003)[0]["source_snapshot_id"] == protected_id
            assert dao.current_inventory_snapshot_id() == result.last_snapshot_id
            assert dao.get_sync_settings()["inventory_settle_seconds"] == 15
    finally:
        service.stop()


def test_revision_invalidated_during_quiet_window_never_commits_old_candidate(tmp_path):
    rejected = Event()
    class ChangingLease(NativeLease):
        def status(self):
            if rejected.is_set() and self.ready and not self.sent:
                self.emit(snapshot(item(1), sequence=2))
                self.sent = True
            return super().status()

        def confirm_inventory_snapshot(self, metadata):
            if not rejected.is_set():
                self.ready = False
                rejected.set()
                return False
            return self.ready

    lease = ChangingLease()
    service = InventorySyncService(tmp_path / "retry.sqlite3", account_id="test", capture_source="native",
                                   client_factory=lambda: lease, operation_guard=lambda _cap: None,
                                   poll_seconds=0.005)
    try:
        service.start()
        assert rejected.wait(3)
        with UserDataDao(service.database_path) as dao:
            assert dao.current_inventory_snapshot_id() is None
        lease.sent, lease.ready = False, True
        assert service.wait_for_snapshot(timeout=3).last_item_count == 1
    finally:
        service.stop()


def test_unsaved_calculation_input_survives_cleanup_across_dao_connections(tmp_path):
    path = tmp_path / "calculation.sqlite3"
    with UserDataDao(path, account_id="test") as dao:
        calculating = dao.import_inventory_snapshot(snapshot(item(1)))
        summary, rows = dao.export_inventory_snapshot(calculating)
        unused = dao.import_inventory_snapshot(snapshot(item(2), sequence=2))
        current = dao.import_inventory_snapshot(snapshot(item(3), sequence=3))
    with UserDataDao(path) as other:
        result = other.prune_inventory_snapshots(retain_recent=0)
        assert result["deleted_snapshot_ids"] == [unused]
        assert summary["snapshot_id"] == calculating and len(rows) == 1
        assert {row["snapshot_id"] for row in other.list_inventory_snapshots()} == {calculating, current}
        other.save_loadout_plan(name="计算后保存", character_id=1003, source_snapshot_id=calculating,
                                assignments=[{"uid_slot": 8, "uid_serial": 1, "kind": "module",
                                              "target_row": 1, "target_column": 1, "rotation": 0}])
