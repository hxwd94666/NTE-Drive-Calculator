# 验证 nte-mods-plugin 文件、工作区和 IPC v7 诊断。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.dwmapi_diagnostics import (
    EQUIPMENT_PIPE_NAME,
    collect_dwmapi_diagnostics,
    format_dwmapi_diagnostics,
)
from src.services.equipment_plugin_deployment import (
    MOD_PLUGIN_SIGNATURE,
    MOD_SDK_CACHE_FILES,
    MOD_WORKSPACE_FILES,
)


class DwmapiDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.game_dir = self.root / "game"
        self.game_dir.mkdir()
        self.game = self.game_dir / "HTGame.exe"
        self.game.write_bytes(b"game")
        self.plugin = self.root / "third_party" / "mods-plugin" / "bin" / "dwmapi.dll"
        self.plugin.parent.mkdir(parents=True)
        self.plugin_bytes = MOD_PLUGIN_SIGNATURE + b":plugin"
        self.plugin.write_bytes(self.plugin_bytes)
        workspace = self.root / "third_party" / "mods-plugin" / "workspace"
        for relative in MOD_WORKSPACE_FILES:
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reports_matching_target_and_packaged_plugin(self) -> None:
        (self.game_dir / "dwmapi.dll").write_bytes(self.plugin_bytes)
        writable_workspace = self.root / "data" / "plugins"
        for relative in MOD_WORKSPACE_FILES:
            target = writable_workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test\n", encoding="utf-8")
        (writable_workspace / MOD_SDK_CACHE_FILES[0]).write_bytes(b"generated-sdk")
        (writable_workspace / MOD_SDK_CACHE_FILES[1]).write_text(
            "game-image-checksum", encoding="ascii"
        )

        with patch(
            "src.services.dwmapi_diagnostics.registered_mod_workspace",
            return_value=writable_workspace,
        ):
            report = collect_dwmapi_diagnostics(
                game_executable_path=self.game,
                application_root=self.root,
                recorded_workspace_path=writable_workspace,
                loading_method="loader",
                loader_snapshot={
                    "phase": "running",
                    "loader_path": str(self.root / "nte-mod-loader.exe"),
                },
            )

        self.assertTrue(report["ok"])
        self.assertTrue(report["target_matches_bundled"])
        self.assertTrue(report["target_plugin_is_mods"])
        self.assertTrue(report["registered_workspace_ready"])
        self.assertTrue(report["registered_workspace_matches_record"])
        self.assertTrue(report["registered_workspace_sdk_cache"]["NTE_SDK.bin"]["exists"])
        self.assertTrue(report["registered_workspace_sdk_cache"]["NTE_SDK.checksum"]["exists"])
        self.assertIn("state", report["pipe"])
        self.assertEqual(EQUIPMENT_PIPE_NAME, r"\\.\pipe\nte-mods-plugin-v7")
        formatted = format_dwmapi_diagnostics(report)
        self.assertIn("Mod Loader（备用）", formatted)
        self.assertIn("Microsoft Visual C++ 运行库", formatted)

    def test_reports_invalid_game_path_without_mutating_files(self) -> None:
        report = collect_dwmapi_diagnostics(
            game_executable_path=self.root / "missing.exe",
            application_root=self.root,
        )

        self.assertFalse(report["ok"])
        self.assertIn("HTGame.exe", report["error"])
