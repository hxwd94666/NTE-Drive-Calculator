# 测试装备插件的部署、备份与恢复流程。
# 测试装备插件的部署、备份与恢复流程。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    deploy_plugin,
    find_game_executables,
    game_executable,
    restore_plugin,
)


class EquipmentPluginDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.executable = self.game / "HTGame.exe"
        self.executable.write_bytes(b"game")
        self.source_dir = self.root / "provided"
        self.source_dir.mkdir()
        self.source = self.source_dir / "dwmapi.dll"
        self.source.write_bytes(b"plugin")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_deploy_backs_up_then_restore_recovers_existing_dll(self) -> None:
        target = self.game / "dwmapi.dll"
        target.write_bytes(b"original")

        deployed = deploy_plugin(
            game_executable_path=self.executable,
            plugin_dll_path=self.source,
            backup_directory=self.root / "backups",
        )

        self.assertEqual(target.read_bytes(), b"plugin")
        self.assertIsNotNone(deployed.backup_path)
        restore_plugin(
            game_executable_path=self.executable,
            deployed_sha256=deployed.deployed_sha256,
            backup_path=deployed.backup_path,
        )
        self.assertEqual(target.read_bytes(), b"original")

    def test_restore_refuses_a_dll_modified_after_deployment(self) -> None:
        deployed = deploy_plugin(
            game_executable_path=self.executable,
            plugin_dll_path=self.source,
            backup_directory=self.root / "backups",
        )
        (self.game / "dwmapi.dll").write_bytes(b"changed by another tool")

        with self.assertRaisesRegex(EquipmentPluginDeploymentError, "其他程序修改"):
            restore_plugin(
                game_executable_path=self.executable,
                deployed_sha256=deployed.deployed_sha256,
                backup_path=deployed.backup_path,
            )

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

    def test_accepts_a_quoted_path_copied_from_windows_explorer(self) -> None:
        self.assertEqual(game_executable(f'"{self.executable}"'), self.executable.resolve())
