# 验证 Windows 半自动验证器的脱敏、只读探针和报告输出边界。
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.windows_validation.log_probe import inspect_logs, timestamp_logs
from tools.windows_validation.models import CheckResult, StepResult, ValidationReport
from tools.windows_validation.mouse_scan_probe import (
    compare_mouse_scan_to_account,
    inspect_mouse_scan_report,
)
from tools.windows_validation.preflight import file_evidence
from tools.windows_validation.redaction import redact_text
from tools.windows_validation.report import write_report
from tools.windows_validation.sqlite_probe import sqlite_summary


NTE_TEST_TIER = "core"


class WindowsValidationTests(unittest.TestCase):
    def test_accepts_complete_2k_mouse_scan_report(self) -> None:
        payload = {
            "schema": "mouse-visual-scan-report-v1",
            "status": "complete",
            "resolution": {"width": 2560, "height": 1440},
            "inventory": {"expected": 29, "captured": 29},
            "preflight": {"checked": 28, "matched": 28},
            "wheel_commands": 3,
            "pages": [
                {
                    "item_range": [1, 28],
                    "captured": 28,
                    "wheel_amounts": [-280, -280, -120],
                    "overlap_row": 0,
                },
                {"item_range": [29, 29], "captured": 1, "wheel_amounts": [], "overlap_row": None},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mouse_scan_last_report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = inspect_mouse_scan_report(path)

        self.assertTrue(result["passed"])
        self.assertEqual([], result["issues"])

    def test_rejects_incomplete_or_discontinuous_mouse_scan_report(self) -> None:
        payload = {
            "schema": "mouse-visual-scan-report-v1",
            "status": "stopped",
            "resolution": {"width": 1600, "height": 900},
            "inventory": {"expected": 29, "captured": 2},
            "preflight": {"checked": 28, "matched": 27},
            "wheel_commands": 1,
            "pages": [{"item_range": [2, 3], "captured": 2, "wheel_amounts": [-999]}],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mouse_scan_last_report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = inspect_mouse_scan_report(path)

        self.assertFalse(result["passed"])
        self.assertGreaterEqual(len(result["issues"]), 5)

    def test_matches_mouse_report_to_current_account_vision_snapshot(self) -> None:
        result = compare_mouse_scan_to_account(
            {"passed": True, "captured": 29},
            {
                "current_inventory": {
                    "snapshot_id": 7,
                    "source": "vision",
                    "capture_driver": "mouse",
                    "complete": True,
                    "stored_item_count": 29,
                }
            },
        )

        self.assertTrue(result["passed"])
        self.assertEqual(7, result["snapshot_id"])
    def test_collects_read_only_sqlite_and_file_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "user_data.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE inventory_snapshot(id INTEGER)")
                connection.execute("INSERT INTO inventory_snapshot VALUES (1)")
                connection.commit()
            before = database.read_bytes()

            summary = sqlite_summary(database)
            evidence = file_evidence((database,))

            self.assertEqual(1, summary["inventory_snapshot_count"])
            self.assertIn("sha256", evidence[str(database.resolve())])
            self.assertEqual(before, database.read_bytes())

    def test_sqlite_summary_projects_current_mouse_vision_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "user_data.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """CREATE TABLE inventory_snapshot(
                           snapshot_id INTEGER PRIMARY KEY,
                           source TEXT NOT NULL,
                           complete INTEGER NOT NULL,
                           declared_item_count INTEGER NOT NULL,
                           stored_item_count INTEGER NOT NULL,
                           raw_snapshot_json TEXT NOT NULL,
                           is_current INTEGER NOT NULL
                       )"""
                )
                connection.execute(
                    "INSERT INTO inventory_snapshot VALUES(7, 'vision', 1, 29, 29, ?, 1)",
                    (json.dumps({"capture_driver": "mouse"}),),
                )
                connection.commit()

            summary = sqlite_summary(database)

        self.assertEqual("vision", summary["current_inventory"]["source"])
        self.assertEqual("mouse", summary["current_inventory"]["capture_driver"])
        self.assertEqual(29, summary["current_inventory"]["stored_item_count"])

    def test_detects_timestamp_logs_and_expected_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp)
            (log_dir / "nte_runtime.log").write_text(
                'INFO inventory_sync.started operation_id="abc"\n',
                encoding="utf-8",
            )
            (log_dir / "nte_runtime_20260729_120000.log").write_text(
                "INFO inventory_sync.succeeded\n",
                encoding="utf-8",
            )

            result = inspect_logs(log_dir, expected_events=("inventory_sync.",))

            self.assertEqual(1, len(timestamp_logs(log_dir)))
            self.assertTrue(result["operation_id_present"])
            self.assertTrue(result["expected_events"]["inventory_sync."])

    def test_redacts_credentials_and_private_roots(self) -> None:
        root = Path("C:/Users/example/private")
        text = (
            "token=secret C:\\Users\\example\\private\\logs "
            "https://mirror.invalid/file?auth=hidden"
        )

        redacted = redact_text(text, roots=(root,))

        self.assertNotIn("secret", redacted)
        self.assertNotIn("hidden", redacted)
        self.assertNotIn(str(root), redacted)

    def test_writes_local_json_and_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            report = ValidationReport(
                "session",
                "2026-07-29T12:00:00",
                "app.exe",
                {"administrator": True},
                {},
                steps=[
                    StepResult(
                        "startup",
                        "启动",
                        "passed",
                        checks=(CheckResult("logs", "passed", "日志通过"),),
                    )
                ],
                hashes_after={},
                finished_at="2026-07-29T12:01:00",
            )

            markdown, machine = write_report(report, output)

            self.assertIn("启动：passed", markdown.read_text(encoding="utf-8"))
            self.assertEqual("session", json.loads(machine.read_text(encoding="utf-8"))["session_id"])


if __name__ == "__main__":
    unittest.main()
