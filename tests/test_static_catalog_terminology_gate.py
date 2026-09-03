# 验证静态术语资料的准入门禁。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import subprocess
import sys

from tools.quality.check_static_catalog_terminology import scan_files


class StaticCatalogTerminologyGateTest(unittest.TestCase):
    def test_scans_player_facing_string_literals_without_matching_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "page.py"
            source.write_text(
                '# 金币只在注释中不构成玩家文案\nLABEL = "突破金币"\n',
                encoding="utf-8",
            )

            violations = scan_files((root,))

        self.assertEqual([(row.line, row.term) for row in violations], [(2, "金币")])

    def test_accepts_formal_player_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "page.py").write_text(
                'COST = "方斯"\nCAPITAL = "甲硬币"\nSTAR = "混频 1 阶"\n',
                encoding="utf-8",
            )

            violations = scan_files((root,))

        self.assertEqual(violations, ())

    def test_rejects_private_currency_and_quality_name_maps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "page.py").write_text(
                'CURRENCY = {"gold": "方斯"}\nQUALITY = {"ORANGE": "S"}\n',
                encoding="utf-8",
            )

            violations = scan_files((root,))

        self.assertEqual(
            [row.term for row in violations],
            ["gold -> 方斯", "ORANGE -> S"],
        )

    def test_cli_emits_utf8_without_caller_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "page.py"
            source.write_text('LABEL = "金币"\n', encoding="utf-8")
            script = (
                Path(__file__).resolve().parents[1]
                / "tools"
                / "quality"
                / "check_static_catalog_terminology.py"
            )

            completed = subprocess.run(
                [sys.executable, "-B", str(script), str(source)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        output = completed.stdout.decode("utf-8")
        self.assertIn("金币", output)
        self.assertIn("方斯或甲硬币", output)


if __name__ == "__main__":
    unittest.main()
