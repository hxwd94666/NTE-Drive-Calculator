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
from tools.windows_validation.preflight import file_evidence
from tools.windows_validation.redaction import redact_text
from tools.windows_validation.report import write_report
from tools.windows_validation.sqlite_probe import sqlite_summary


NTE_TEST_TIER = "core"


class WindowsValidationTests(unittest.TestCase):
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
