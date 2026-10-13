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
from tools.windows_validation.redaction import redact_text, redact_value
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

    def test_redacts_complete_auth_values_and_structured_secrets(self) -> None:
        values = (
            "Authorization: Bearer bearer-secret",
            'token="two word secret"',
            "https://mirror.invalid/file?token=secret&part=other-secret",
            "https://name:secret@mirror.invalid/file",
            "https://mirror.invalid/file?%74oken=secret",
            "https://mirror.invalid/file#token=secret",
        )
        for value in values:
            with self.subTest(value=value):
                redacted = redact_text(value)
                self.assertNotIn("secret", redacted)
                self.assertNotIn("mirror.invalid", redacted)
        self.assertEqual("https://public.invalid/docs?v=2", redact_text("https://public.invalid/docs?v=2"))
        root = Path("C:/Users/example/private")
        result = redact_value({"token": "dict-secret", "nested": [{"Authorization": "Bearer nested-secret"}], str(root / "evidence"): "ok"}, roots=(root,))
        self.assertNotIn("secret", json.dumps(result))
        self.assertTrue(all(str(root) not in key for key in result))
        self.assertEqual("ok", result["<path>" + str(root / "evidence")[len(str(root)):]])

    def test_redacts_apostrophes_inside_authenticated_urls(self) -> None:
        values = (
            "https://host.invalid/file?signature=A'TOKEN",
            "https://host.invalid/file?signature=A%27TOKEN",
            "https://USER:AA'TOKEN@host.invalid/file",
            "https://USER:AA%27TOKEN@host.invalid/file",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual("<redacted-url>", redact_text(value))
                # 外层引号的归属不确定时保守吞掉尾引号，不把凭据尾段放回报告。
                for quote in ("'", '"'):
                    redacted = redact_text(quote + value + quote)
                    self.assertNotIn("TOKEN", redacted)
                    self.assertNotIn("host.invalid", redacted)
                    self.assertIn("<redacted-url>", redacted)

    def test_dict_mouse_item_range_returns_failed_present_evidence(self) -> None:
        payload = {
            "schema": "mouse-visual-scan-report-v1",
            "status": "complete",
            "resolution": {"width": 1920, "height": 1080},
            "inventory": {"expected": 1, "captured": 1},
            "preflight": {"checked": 1, "matched": 1},
            "wheel_commands": 0,
            "pages": [{"item_range": {"first": 1, "last": 1}, "captured": 1}],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = inspect_mouse_scan_report(path)
        self.assertTrue(result["present"])
        self.assertFalse(result["passed"])
        self.assertIn("第 1 页物品范围无效。", result["issues"])
        self.assertEqual(1, result["expected"])
        self.assertEqual(1, result["captured"])

    def test_malformed_mouse_report_is_failed_evidence_not_an_exception(self) -> None:
        malformed = ([], {"resolution": []}, {"resolution": {"width": "bad"}}, {"pages": [7]})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.json"
            for payload in malformed:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    result = inspect_mouse_scan_report(path)
                    self.assertFalse(result["passed"])
                    self.assertTrue(result["issues"])

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
