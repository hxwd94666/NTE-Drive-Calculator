# 测试装备插件的部署、备份与清理流程。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.equipment_plugin_deployment import find_game_executables, game_process_running, game_executable
from src.services.managed_plugin_cleanup import cleanup_managed_plugin


class EquipmentPluginDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        process = patch("src.services.equipment_plugin_deployment.game_process_running", return_value=False)
        process.start()
        self.addCleanup(process.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.executable = self.game / "HTGame.exe"
        self.executable.write_bytes(b"game")
        self.addCleanup(self.temp_dir.cleanup)

    def test_cleanup_removes_a_different_version_of_legacy_proxy(self) -> None:
        (self.game / "dwmapi.dll").write_bytes(b"changed by another tool")

        with patch("src.services.managed_plugin_cleanup.mod_workspace_registry_snapshot", return_value=(False, None)), patch(
            "src.services.managed_plugin_cleanup.cleanup_mod_workspace", return_value=True,
        ):
            result = cleanup_managed_plugin(
                game_executable_path=self.executable,
                game_running=lambda: False,
            )
        self.assertEqual(result.status, "cleaned")
        self.assertFalse((self.game / "dwmapi.dll").exists())

    def test_detects_the_standard_nte_install_path(self) -> None:
        expected = (
            self.root / "Games" / "Neverness To Everness" / "Client"
            / "WindowsNoEditor" / "HT" / "Binaries" / "Win64" / "HTGame.exe"
        )
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"game")

        self.assertEqual(
            find_game_executables([self.root / "Games"]),
            [expected.resolve()],
        )

    def test_detects_standard_nte_path_directly_under_a_disk_root(self) -> None:
        volume_root = self.root / "volume"
        expected = (
            volume_root / "Neverness To Everness" / "Client" / "WindowsNoEditor"
            / "HT" / "Binaries" / "Win64" / "HTGame.exe"
        )
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"game")

        self.assertEqual(
            find_game_executables([volume_root]),
            [expected.resolve()],
        )

    def test_detects_registered_nte_install_without_scanning_the_disk(self) -> None:
        install_root = self.root / "custom-library" / "Neverness To Everness"
        expected = (
            install_root / "Client" / "WindowsNoEditor" / "HT"
            / "Binaries" / "Win64" / "HTGame.exe"
        )
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"game")

        with (
            patch(
                "src.services.equipment_plugin_deployment._registry_game_roots",
                return_value=[install_root],
            ),
            patch(
                "src.services.equipment_plugin_deployment._disk_roots",
                return_value=[],
            ),
        ):
            self.assertEqual(find_game_executables(), [expected.resolve()])

    def test_detects_nte_in_the_games_directory_below_a_disk_root(self) -> None:
        volume_root = self.root / "volume"
        expected = (
            volume_root / "games" / "Neverness To Everness" / "Client"
            / "WindowsNoEditor" / "HT" / "Binaries" / "Win64" / "HTGame.exe"
        )
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"game")

        with (
            patch(
                "src.services.equipment_plugin_deployment._registry_game_roots",
                return_value=[],
            ),
            patch(
                "src.services.equipment_plugin_deployment._disk_roots",
                return_value=[volume_root],
            ),
        ):
            self.assertEqual(find_game_executables(), [expected.resolve()])

    def test_accepts_a_quoted_path_copied_from_windows_explorer(self) -> None:
        self.assertEqual(game_executable(f'"{self.executable}"'), self.executable.resolve())


    @patch("src.services.equipment_plugin_deployment.subprocess.run")
    def test_detects_running_game_process_from_tasklist_csv(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = '"HTGame.exe","123","Console","1","100 K"\n'

        self.assertTrue(game_process_running())

    @patch("src.services.equipment_plugin_deployment.subprocess.run")
    def test_treats_tasklist_without_game_as_not_running(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "INFO: No tasks are running which match the specified criteria.\n"

        self.assertFalse(game_process_running())
