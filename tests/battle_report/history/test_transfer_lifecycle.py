# 验证导出取消和静态输入失效不发布文件，导入失效整体回滚。
from concurrent.futures import CancelledError
from contextlib import closing
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from src.integrations.battle_report_bundle import read_battle_report_bundle, write_battle_report_bundle_atomic
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_report.history.service import BattleReportHistoryService
from src.services.battle_report.persistence import BattleReportPersistenceDependencies
from src.services.battle_report.history.transfer import (
    BattleReportTransferDependencies, BattleReportTransferService,
    StaleBattleReportTransferContextError,
)
from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao, UserDataValidationError


def insert_summary(dao, operation):
    raw = json.dumps({"total_damage": 10, "total_hits": 1}, sort_keys=True)
    result = dao.insert_auto_summary_snapshot(
        capture_operation_id=operation, combat_context_kind="non_abyss",
        abyss_floor=None, has_first_half=False, has_second_half=False,
        captured_at_utc="2026-01-01T00:00:00+00:00", finalized_at_utc="2026-01-01T00:00:01+00:00",
        dps_time_mode="subtract_time_stop", duration_seconds=1.0,
        total_damage=10.0, total_dps=10.0, total_damage_taken=0.0,
        total_hits=1, character_count=0, skill_count=0, character_ids=[],
        abyss_detected=False, abyss_success=False, payload_schema_version=1,
        raw_summary_json=raw, raw_summary_sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )
    return result["record"]["battle_record_id"]


class BattleHistoryTransferLifecycleTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "account.sqlite3"
        self.static = self.root / "static.sqlite3"
        with closing(sqlite3.connect(self.static)) as connection:
            connection.executescript("""
                CREATE TABLE schema_migration(version INTEGER);
                INSERT INTO schema_migration VALUES (39);
                CREATE TABLE dataset(dataset_id TEXT, importer_version INTEGER, built_at_utc TEXT);
                INSERT INTO dataset VALUES ('fixture', 1, '2026-01-01T00:00:00Z');
            """)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"database": {
            "dataset_id": "fixture", "schema_version": 39,
            "filename": self.static.name, "size_bytes": self.static.stat().st_size,
            "sha256": hashlib.sha256(self.static.read_bytes()).hexdigest(),
        }, "build_tool": {"importer_version": 1}}), encoding="utf-8")
        self.index = self.root / "accounts.json"
        self.index.write_text(json.dumps({"accounts": [{"id": "fixture", "name": "fixture"}]}), encoding="utf-8")
        with UserDataDao(self.database, account_id="fixture") as dao:
            self.record_id = insert_summary(dao, "lifecycle-source")
        self.current = True
        self.native = Mock()
        self.native.load.side_effect = NativeAnalysisError("fixture component absent")
        self.history = BattleReportHistoryService(
            dependencies=BattleReportPersistenceDependencies(
                "fixture", self.database, 1, self.static),
            context_is_current=lambda _: self.current, native_page_loader=self.native,
        )
        self.service = self.make_service(self.database, self.history)
        self.target = self.root / "export.ntebr"

    def make_service(self, database, history):
        return BattleReportTransferService(
            dependencies=BattleReportTransferDependencies(
                "fixture", 1, database, self.index, self.static, self.manifest, "fixture"),
            context_is_current=lambda _: self.current, history_service=history,
        )

    def test_native_cancellation_preserves_existing_export(self):
        self.target.write_bytes(b"previous-user-export")
        self.native.load.side_effect = CancelledError("cancelled fixture")
        with self.assertRaises(CancelledError):
            self.service.export_reports([self.record_id], self.target)
        self.assertEqual(b"previous-user-export", self.target.read_bytes())
        self.assertEqual([self.target], list(self.root.glob("*.ntebr")))

    def test_unavailable_native_projection_keeps_raw_export_and_unknown(self):
        outcome = self.service.export_reports([self.record_id], self.target)
        payload = read_battle_report_bundle(self.target)
        self.assertEqual(1, outcome.report_count)
        report = payload["reports"][0]
        self.assertIsNone(report["derived_analysis"]["target_inference"])
        self.assertIsNone(report["derived_analysis"]["target_life_projection"])
        self.assertEqual(10, report["nte_core"]["summary"]["raw"]["total_damage"])

    def _try_change_manifest(self, blocked):
        try:
            with self.manifest.open("r+b") as stream:
                stream.write(b"X")
        except OSError:
            blocked.append(True)

    def test_manifest_pin_spans_final_check_atomic_replace_and_reader_exit(self):
        blocked = []
        replace = os.replace
        def replace_with_competing_writer(source, target):
            self._try_change_manifest(blocked)
            return replace(source, target)
        with patch("src.integrations.battle_report_bundle.os.replace", side_effect=replace_with_competing_writer):
            outcome = self.service.export_reports([self.record_id], self.target)
        self.assertEqual([True], blocked)
        self.assertEqual(1, outcome.report_count)
        self.assertEqual(1, len(read_battle_report_bundle(self.target)["reports"]))
        with self.manifest.open("r+b") as stream:
            stream.write(b"{")

    def test_manifest_pin_spans_import_final_validation_real_commit_and_exit(self):
        self.service.export_reports([self.record_id], self.target)
        payload = read_battle_report_bundle(self.target)
        payload["bundle"]["user_database_schema_version"] = 44
        write_battle_report_bundle_atomic(self.target, payload, before_replace=lambda: None)
        destination = self.root / "destination.sqlite3"
        with UserDataDao(destination, account_id="fixture") as dao:
            insert_summary(dao, "keep-existing")
        service = self.make_service(destination, self.history)
        blocked = []
        original_import = UserDataDao.import_battle_report_transfer_rows
        def import_with_competing_writer(dao, rows, *, before_commit):
            def final_check():
                before_commit()
                self._try_change_manifest(blocked)
            return original_import(dao, rows, before_commit=final_check)
        with patch.object(UserDataDao, "import_battle_report_transfer_rows", new=import_with_competing_writer):
            result = service.import_bundle(self.target)
        self.assertEqual([True], blocked)
        self.assertEqual(1, len(result.imported_record_ids))
        with UserDataDao(destination) as dao:
            self.assertEqual({"keep-existing", "lifecycle-source"}, {row["capture_operation_id"] for row in dao.list_battle_records()})
        with self.manifest.open("r+b") as stream:
            stream.write(b"{")

    def test_account_change_at_commit_rolls_back_all_imported_rows(self):
        self.service.export_reports([self.record_id], self.target)
        payload = read_battle_report_bundle(self.target)
        payload["bundle"]["user_database_schema_version"] = 44
        write_battle_report_bundle_atomic(self.target, payload, before_replace=lambda: None)
        destination = self.root / "rollback.sqlite3"
        with UserDataDao(destination, account_id="fixture") as dao:
            insert_summary(dao, "keep-existing")
        service = self.make_service(destination, self.history)
        original_import = UserDataDao.import_battle_report_transfer_rows
        def import_then_invalidate(dao, rows, *, before_commit):
            def final_check():
                self.current = False
                before_commit()
            return original_import(dao, rows, before_commit=final_check)
        with patch.object(UserDataDao, "import_battle_report_transfer_rows", new=import_then_invalidate):
            with self.assertRaises(StaleBattleReportTransferContextError):
                service.import_bundle(self.target)
        with UserDataDao(destination) as dao:
            self.assertEqual(["keep-existing"], [row["capture_operation_id"] for row in dao.list_battle_records()])
        with self.manifest.open("r+b") as stream:
            stream.write(b"{")

    def test_current_schema_round_trip_preserves_rows_and_is_idempotent(self):
        self.service.export_reports([self.record_id], self.target)
        payload = read_battle_report_bundle(self.target)
        self.assertEqual(SCHEMA_VERSION, payload["bundle"]["user_database_schema_version"])
        destination = self.root / "roundtrip.sqlite3"
        with UserDataDao(destination, account_id="fixture"):
            pass
        service = self.make_service(destination, self.history)
        first = service.import_bundle(self.target)
        second = service.import_bundle(self.target)
        self.assertEqual(1, len(first.imported_record_ids))
        self.assertEqual(1, second.skipped_existing_count)
        with UserDataDao(destination) as dao:
            self.assertEqual(["lifecycle-source"], [row["capture_operation_id"] for row in dao.list_battle_records()])

    def test_future_schema_rejected_and_historical_dataset_not_reinterpreted(self):
        self.service.export_reports([self.record_id], self.target)
        payload = read_battle_report_bundle(self.target)
        payload["bundle"]["user_database_schema_version"] = SCHEMA_VERSION + 1
        write_battle_report_bundle_atomic(self.target, payload, before_replace=lambda: None)
        with self.assertRaises(UserDataValidationError):
            self.service.import_bundle(self.target)
        # 旧包来源允许不同于当前发行；仅拒绝本次本机输入变化，不重写历史原始行。
        payload["bundle"]["user_database_schema_version"] = 44
        payload["bundle"]["static_data"]["dataset_id"] = "historical-other-dataset"
        write_battle_report_bundle_atomic(self.target, payload, before_replace=lambda: None)
        result = self.service.import_bundle(self.target)
        self.assertEqual(1, result.skipped_existing_count)

    def test_static_pin_is_released_after_cancelled_export(self):
        original = self.static.read_bytes()
        blocked = []
        def cancel_while_pinned(*args, **kwargs):
            try:
                with self.static.open("r+b") as stream:
                    stream.write(b"X")
            except OSError:
                blocked.append(True)
            raise CancelledError("cancelled fixture")
        self.native.load.side_effect = cancel_while_pinned
        with self.assertRaises(CancelledError):
            self.service.export_reports([self.record_id], self.target)
        self.assertEqual([True], blocked)
        self.assertEqual(original, self.static.read_bytes())
        # 操作失败后租约和只读DAO都须物理释放。
        with self.static.open("r+b") as stream:
            stream.write(original[:1])


NTE_TEST_TIER = "core"
