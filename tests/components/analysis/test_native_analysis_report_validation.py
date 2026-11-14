# 验证真实战报差分器的精度、证据保留、隐私输出与只读快照边界。
from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.battle_report.evaluate_native_analysis import (
    _case_modes,
    _checkpoint,
    _clone_database,
    _exit_code,
    _record_inventory,
    compare_results,
)


@dataclass(frozen=True)
class _Evidence:
    status: str
    original_damage: float
    candidate_damage: float
    confidence: str
    gaps: tuple[str, ...]
    event_id: str


class NativeAnalysisReportValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = _Evidence("complete", 100.0, 90.0, "高", (), "private-uid-123")

    def test_all_evidence_and_damage_fields_are_compared(self) -> None:
        for kwargs in (
            {"status": "partial"}, {"confidence": "中"}, {"gaps": ("unknown",)},
            {"original_damage": 100.01}, {"candidate_damage": 89.99},
            {"event_id": "another-private-uid"},
        ):
            with self.subTest(kwargs=kwargs):
                result = compare_results(self.evidence, replace(self.evidence, **kwargs))
                self.assertFalse(result["equal"])
                self.assertGreater(result["difference_count"], 0)

    def test_floating_tolerance_is_tight_and_nonfinite_values_fail(self) -> None:
        self.assertTrue(compare_results(1.0, 1.0 + 5e-10)["equal"])
        self.assertFalse(compare_results(1.0, 1.0 + 2e-9)["equal"])
        self.assertTrue(compare_results(1e9, 1e9 + 0.0005)["equal"])
        self.assertFalse(compare_results(1e9, 1e9 + 0.002)["equal"])
        self.assertFalse(compare_results(float("nan"), float("nan"))["equal"])
        self.assertFalse(compare_results(float("inf"), float("inf"))["equal"])

    def test_integer_identity_does_not_use_damage_float_tolerance(self) -> None:
        self.assertFalse(compare_results(10000000000000, 10000000000001)["equal"])
        self.assertFalse(compare_results(1, 1.0)["equal"])

    def test_difference_cap_preserves_total_count_and_hides_private_values(self) -> None:
        left = {"private-uid-key": ["private-uid-value", "private-uid-2", 3.0]}
        right = {"private-uid-key": ["different-uid", "different-uid-2", 4.0]}
        result = compare_results(left, right, limit=1)
        self.assertEqual(result["difference_count"], 3)
        self.assertEqual(len(result["differences"]), 1)
        self.assertTrue(result["differences_truncated"])
        self.assertNotIn("private-uid", json.dumps(result))
        self.assertNotIn("different-uid", json.dumps(result))

    def test_missing_mapping_keys_are_differences_without_private_key_output(self) -> None:
        result = compare_results({"private-uid-key": 1}, {"another-private-key": 1})
        self.assertEqual(result["difference_count"], 2)
        self.assertNotIn("private", json.dumps(result))

    def test_mode_selection_does_not_duplicate_unedited_reports(self) -> None:
        self.assertEqual(_case_modes({"has_active_build_edit": False}, "both"), ("original",))
        self.assertEqual(
            _case_modes({"has_active_build_edit": True}, "both"), ("original", "effective"),
        )
        self.assertEqual(_case_modes({"has_active_build_edit": False}, "effective"), ("effective",))

    def test_exit_code_rejects_mismatch_error_and_zero_total_native_coverage(self) -> None:
        summary = {
            "mismatched_cases": 0, "error_cases": 0, "native_formula_jobs": 10,
            "zero_native_cases": [],
        }
        self.assertEqual(_exit_code(summary), 0)
        for changes in (
            {"mismatched_cases": 1}, {"error_cases": 1}, {"native_formula_jobs": 0},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(_exit_code(dict(summary, **changes)), 1)
        self.assertEqual(_exit_code(dict(summary, zero_native_cases=[{"record_id": 1}])), 0)

    def test_backup_reads_committed_wal_and_mutating_copy_preserves_live_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            destination = Path(directory) / "copy.sqlite3"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute("CREATE TABLE evidence(value INTEGER)")
                connection.execute("INSERT INTO evidence VALUES (41)")
                connection.commit()
                self.assertTrue(Path(str(source) + "-wal").is_file())
                _clone_database(source, destination)
                with closing(sqlite3.connect(destination)) as copied:
                    self.assertEqual(copied.execute("SELECT value FROM evidence").fetchone()[0], 41)
                    copied.execute("UPDATE evidence SET value=99")
                    copied.commit()
                self.assertEqual(connection.execute("SELECT value FROM evidence").fetchone()[0], 41)

    def test_inventory_includes_reports_with_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript("""
                    CREATE TABLE battle_record (
                        battle_record_id INTEGER, axis_stored_hits INTEGER, axis_complete INTEGER
                    );
                    CREATE TABLE battle_build_snapshot (
                        battle_record_id INTEGER, source_inventory_snapshot_id INTEGER,
                        account_generation INTEGER, static_dataset_id TEXT,
                        static_schema_version INTEGER, profile_schema_version INTEGER,
                        formula_model_version TEXT
                    );
                    CREATE TABLE battle_build_edit (battle_record_id INTEGER, is_active INTEGER);
                    CREATE TABLE battle_axis_capture (capture_id INTEGER, battle_record_id INTEGER);
                    CREATE TABLE battle_hit_evidence (capture_id INTEGER);
                    INSERT INTO battle_record VALUES (1, 0, 0), (2, 1, 1), (3, 1, 1);
                    INSERT INTO battle_build_snapshot (battle_record_id) VALUES (2), (3);
                    INSERT INTO battle_build_edit VALUES (2, 1);
                    INSERT INTO battle_axis_capture VALUES (20, 2);
                    INSERT INTO battle_hit_evidence VALUES (20);
                """)
            inventory = _record_inventory(database)
            self.assertEqual(len(inventory), 3)
            self.assertEqual(inventory[0]["skip_reasons"], ["missing_build_snapshot", "missing_hit_axis"])
            self.assertEqual(inventory[1]["skip_reasons"], [])
            self.assertTrue(inventory[1]["has_active_build_edit"])
            self.assertEqual(inventory[2]["skip_reasons"], ["missing_hit_axis"])

    def test_checkpoint_records_incomplete_run_and_zero_native_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "checkpoint.json"
            report = {
                "finished": False, "skipped_records": [{"record_id": 3}],
                "cases": [{"record_id": 2, "build_mode": "original", "status": "passed",
                           "native_formula_jobs": 0, "native_stats": {"jobs": 0}}],
            }
            _checkpoint(output, report)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(saved["finished"])
            self.assertEqual(saved["summary"]["native_formula_jobs"], 0)
            self.assertEqual(len(saved["summary"]["zero_native_cases"]), 1)
            self.assertEqual(saved["summary"]["no_native_coverage_cases"], 1)
            self.assertEqual(saved["cases"][0]["status"], "no_native_coverage")
            self.assertEqual(saved["summary"]["skipped_records"], 1)
            self.assertFalse(output.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
