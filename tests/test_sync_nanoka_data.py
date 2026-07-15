# nanoka 同步命令行入口测试。
from __future__ import annotations

import unittest
from unittest import mock

from tools.sync_nanoka_data import _parse_levels, _report_sync_summary, main


class SyncNanokaDataTests(unittest.TestCase):
    def test_parse_levels(self):
        self.assertEqual(_parse_levels("1, 20,80"), (1, 20, 80))
        with self.assertRaises(ValueError):
            _parse_levels("")

    def test_main_rejects_conflicting_modes(self):
        with mock.patch(
            "sys.argv",
            ["sync_nanoka_data.py", "--characters-only", "--weapons-only"],
        ), mock.patch("tools.sync_nanoka_data.build_cli.fail") as fail:
            self.assertEqual(main(), 2)
        fail.assert_called_once()

    def test_report_returns_failure_for_partial_fetch(self):
        summary = {
            "fetch_errors": ["角色: network"],
            "updated_roles": [],
            "missing_remote_roles": [],
            "added_roles": [],
            "skipped_roles": [],
        }
        with mock.patch("tools.sync_nanoka_data.build_cli.ok"), mock.patch(
            "tools.sync_nanoka_data.build_cli.warn"
        ):
            code = _report_sync_summary(
                kind="characters",
                summary=summary,
                add_missing=False,
                show_diffs=False,
            )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
