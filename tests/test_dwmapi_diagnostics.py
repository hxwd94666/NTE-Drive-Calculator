from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.dwmapi_diagnostics import collect_dwmapi_diagnostics


class DwmapiDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.game_dir = self.root / "game"
        self.game_dir.mkdir()
        self.game = self.game_dir / "HTGame.exe"
        self.game.write_bytes(b"game")
        self.plugin = self.root / "third_party" / "equipment-plugin" / "bin" / "dwmapi.dll"
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_bytes(b"plugin")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reports_matching_target_and_packaged_plugin(self) -> None:
        (self.game_dir / "dwmapi.dll").write_bytes(b"plugin")

        report = collect_dwmapi_diagnostics(
            game_executable_path=self.game,
            application_root=self.root,
        )

        self.assertTrue(report["ok"])
        self.assertTrue(report["target_matches_bundled"])
        self.assertIn("state", report["pipe"])

    def test_reports_invalid_game_path_without_mutating_files(self) -> None:
        report = collect_dwmapi_diagnostics(
            game_executable_path=self.root / "missing.exe",
            application_root=self.root,
        )

        self.assertFalse(report["ok"])
        self.assertIn("HTGame.exe", report["error"])
