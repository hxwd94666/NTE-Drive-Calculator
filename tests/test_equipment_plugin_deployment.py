# 测试装备插件的部署、备份与恢复流程。
# 测试装备插件的部署、备份与恢复流程。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    MOD_WORKSPACE_FILES,
    deploy_plugin,
    find_game_executables,
    game_executable,
    packaged_mod_workspace,
    packaged_plugin_dll,
    prepare_mod_workspace,
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
        self.workspace_source = (
            self.root / "third_party" / "mods-plugin" / "workspace"
        )
        for relative in MOD_WORKSPACE_FILES:
            target = self.workspace_source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"default:{relative.as_posix()}\n", encoding="utf-8")
        self.workspace = self.root / "writable" / "plugins"
        self.registry_patcher = patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace"
        )
        self.register_workspace = self.registry_patcher.start()

    def tearDown(self) -> None:
        self.registry_patcher.stop()
        self.temp_dir.cleanup()

    def test_deploy_backs_up_then_restore_recovers_existing_dll(self) -> None:
        target = self.game / "dwmapi.dll"
        target.write_bytes(b"original")

        deployed = deploy_plugin(
            game_executable_path=self.executable,
            plugin_dll_path=self.source,
            application_root=self.root,
            writable_workspace_path=self.workspace,
            backup_directory=self.root / "backups",
        )

        self.assertEqual(target.read_bytes(), b"plugin")
        self.assertEqual(
            (self.workspace / "nte-mods" / "equipment.nte").read_text(encoding="utf-8"),
            "default:nte-mods/equipment.nte\n",
        )
        self.register_workspace.assert_called_once_with(self.workspace.resolve())
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
            application_root=self.root,
            writable_workspace_path=self.workspace,
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

    def test_prefers_the_organized_third_party_plugin_location(self) -> None:
        organized = self.root / "third_party" / "mods-plugin" / "bin"
        organized.mkdir(parents=True)
        plugin = organized / "dwmapi.dll"
        plugin.write_bytes(b"plugin")

        self.assertEqual(packaged_plugin_dll(self.root), plugin.resolve())
        self.assertEqual(
            packaged_mod_workspace(self.root),
            self.workspace_source.resolve(),
        )

    def test_workspace_upgrade_preserves_user_edits_and_refreshes_managed_files(self) -> None:
        prepare_mod_workspace(
            application_root=self.root,
            writable_workspace_path=self.workspace,
        )
        equipment = self.workspace / "nte-mods" / "equipment.nte"
        equipment.write_text("custom equipment\n", encoding="utf-8")
        enabled = self.workspace_source / "nte-mods.enabled"
        enabled.write_text("nte_mod_set 1\nload equipment\n", encoding="utf-8")

        prepare_mod_workspace(
            application_root=self.root,
            writable_workspace_path=self.workspace,
        )

        self.assertEqual(equipment.read_text(encoding="utf-8"), "custom equipment\n")
        self.assertEqual(
            (self.workspace / "nte-mods.enabled").read_text(encoding="utf-8"),
            "nte_mod_set 1\nload equipment\n",
        )
