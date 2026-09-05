# 验证背包保存错误的分类、隐私边界和失败后重试契约。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.services.inventory_sync_service import InventorySyncService
from src.storage.sqlite.inventory_save_error import InventorySnapshotSaveError
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_inventory_sync_service import FakeCoreClient
from tests.test_user_data_dao import item, snapshot


class InventorySaveDiagnosticsTests(unittest.TestCase):
    def test_extended_sqlite_codes_keep_precise_reason(self) -> None:
        for code, name, category in (
            (sqlite3.SQLITE_BUSY, "SQLITE_BUSY", "BUSY"),
            (sqlite3.SQLITE_READONLY, "SQLITE_READONLY", "READONLY"),
            (sqlite3.SQLITE_FULL, "SQLITE_FULL", "FULL"),
            (sqlite3.SQLITE_CORRUPT, "SQLITE_CORRUPT", "CORRUPT"),
            (sqlite3.SQLITE_IOERR, "SQLITE_IOERR", "IO"),
            (sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY, "SQLITE_CONSTRAINT_FOREIGNKEY", "CONSTRAINT"),
        ):
            with self.subTest(name=name):
                cause = sqlite3.OperationalError("private payload must not be logged")
                cause.sqlite_errorcode = code
                cause.sqlite_errorname = name
                error = InventorySnapshotSaveError(cause, stage="insert_item", rollback_error=None)
                self.assertEqual(error.error_code, f"SNAPSHOT_SAVE_{category}")
                self.assertEqual(error.diagnostics["sqlite_errorcode"], code)
                self.assertIn(name, str(error))
                self.assertNotIn("private payload", str(error.diagnostics))

    def test_schema_detail_and_rollback_failure_are_retained_safely(self) -> None:
        error = InventorySnapshotSaveError(
            sqlite3.OperationalError("no such table: inventory_item"),
            stage="insert_item",
            rollback_error=sqlite3.OperationalError("disk I/O error"),
        )
        self.assertEqual(error.error_code, "SNAPSHOT_SAVE_SCHEMA")
        self.assertIn("no such table: inventory_item", str(error))
        self.assertEqual(error.diagnostics["rollback_status"], "failed")
        self.assertIn("disk I/O error", str(error.diagnostics["rollback_error"]))

    def test_failed_insert_preserves_current_snapshot_and_allows_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "user.sqlite3"
            with UserDataDao(path, account_id="test") as dao:
                previous = dao.import_inventory_snapshot(snapshot(1, [item(1, 8)]))
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(
                        "CREATE TRIGGER reject_inventory BEFORE INSERT ON inventory_item "
                        "BEGIN SELECT RAISE(ABORT, 'private payload'); END"
                    )
                with self.assertRaises(InventorySnapshotSaveError) as caught:
                    dao.import_inventory_snapshot(snapshot(2, [item(2, 8)]))
                error = caught.exception
                self.assertEqual(error.error_code, "SNAPSHOT_SAVE_CONSTRAINT")
                self.assertEqual(error.diagnostics["save_stage"], "insert_item")
                self.assertEqual(error.diagnostics["rollback_status"], "succeeded")
                self.assertIsInstance(error.__cause__, sqlite3.IntegrityError)
                self.assertNotIn("private payload", str(error))
                self.assertEqual(dao.current_inventory_snapshot_id(), previous)
                self.assertEqual(len(dao.list_inventory_snapshots()), 1)
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("DROP TRIGGER reject_inventory")
                current = dao.import_inventory_snapshot(snapshot(2, [item(2, 8)]))
                self.assertEqual(dao.current_inventory_snapshot_id(), current)
                self.assertNotEqual(current, previous)

    def test_service_publishes_sqlite_detail_and_logs_it_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "user.sqlite3"
            with UserDataDao(path, account_id="test"):
                pass
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TRIGGER reject_inventory BEFORE INSERT ON inventory_item "
                    "BEGIN SELECT RAISE(ABORT, 'private payload'); END"
                )
            core = FakeCoreClient()
            service = InventorySyncService(
                path, client_factory=lambda: core, settle_seconds=0.05, poll_seconds=0.005,
            )
            with patch("src.services.inventory_sync_runtime.log_event") as log:
                try:
                    service.start()
                    service.wait_for_phase("waiting", timeout=2.0)
                    core.emit(snapshot(1, [item(1, 8)]))
                    state = service.wait_for_phase("error", timeout=2.0)
                    self.assertEqual(state.error_code, "SNAPSHOT_SAVE_CONSTRAINT")
                    self.assertIn("SQLITE_CONSTRAINT_TRIGGER", state.error)
                    self.assertIn("insert_item", state.error)
                    self.assertNotIn("private payload", state.error)
                    fields = next(
                        call.kwargs for call in log.call_args_list
                        if call.args[1] == "inventory_sync.snapshot_commit_retry"
                    )
                    self.assertEqual(fields["sqlite_errorcode"], sqlite3.SQLITE_CONSTRAINT_TRIGGER)
                    self.assertEqual(fields["rollback_status"], "succeeded")
                    self.assertNotIn("private payload", str(fields))
                    with closing(sqlite3.connect(path)) as connection, connection:
                        connection.execute("DROP TRIGGER reject_inventory")
                    saved = service.wait_for_snapshot(timeout=4.0)
                    self.assertEqual(saved.phase, "listening")
                    self.assertIsNone(saved.error_code)
                finally:
                    service.stop()
