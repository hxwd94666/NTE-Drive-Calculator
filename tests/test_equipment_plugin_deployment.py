# 测试装备插件的部署、备份与清理流程。
from __future__ import annotations

import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    PluginDeploymentPendingCleanup,
    MOD_PLUGIN_SIGNATURE,
    MOD_SDK_CACHE_FILES,
    MOD_WORKSPACE_FILES,
    deploy_plugin,
    find_game_executables,
    game_process_running,
    game_executable,
    is_mods_plugin_dll,
    packaged_mod_workspace,
    packaged_plugin_dll,
    prepare_mod_workspace,
)
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
        self.source_dir = self.root / "provided"
        self.source_dir.mkdir()
        self.source = self.source_dir / "dwmapi.dll"
        self.plugin_bytes = MOD_PLUGIN_SIGNATURE + b":plugin"
        self.source.write_bytes(self.plugin_bytes)
        self.workspace_source = (
            self.root / "third_party" / "mods-plugin" / "workspace"
        )
        for relative in MOD_WORKSPACE_FILES:
            target = self.workspace_source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"default:{relative.as_posix()}\n", encoding="utf-8")
        native_dll = self.workspace_source / "NTE_Capture.dll"
        (self.workspace_source / "native-capture.json").write_text(json.dumps({
            "protocol_version": 1, "capabilities": ["combat.hit_buff.v1", "combat.context.v1"],
            "sha256": hashlib.sha256(native_dll.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        self.workspace = self.root / "writable" / "plugins"
        self.registry_patcher = patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace"
        )
        self.register_workspace = self.registry_patcher.start()
        self.register_workspace.return_value = (False, None)

    def tearDown(self) -> None:
        self.registry_patcher.stop()
        self.temp_dir.cleanup()

    def test_workspace_rejects_old_capture_without_context_capability(self) -> None:
        path = self.workspace_source / 'native-capture.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['capabilities'] = ['combat.hit_buff.v1']
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(EquipmentPluginDeploymentError):
            prepare_mod_workspace(application_root=self.root, operation_guard=lambda capability: None, writable_workspace_path=self.workspace)
        self.register_workspace.assert_not_called()

    def test_deploy_backs_up_then_cleanup_does_not_reactivate_existing_dll(self) -> None:
        target = self.game / "dwmapi.dll"
        target.write_bytes(b"original")

        deployed = deploy_plugin(
            game_executable_path=self.executable,
            plugin_dll_path=self.source,
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
            backup_directory=self.root / "backups",
        )

        self.assertEqual(target.read_bytes(), self.plugin_bytes)
        self.assertEqual(
            (self.workspace / "nte-mods" / "equipment.nte").read_text(encoding="utf-8"),
            "default:nte-mods/equipment.nte\n",
        )
        self.register_workspace.assert_called_once_with(self.workspace.resolve())
        self.assertIsNotNone(deployed.backup_path)
        with patch("src.services.managed_plugin_cleanup.mod_workspace_registry_snapshot", return_value=(False, None)), patch(
            "src.services.managed_plugin_cleanup.cleanup_mod_workspace", return_value=True,
        ):
            result = cleanup_managed_plugin(
                game_executable_path=self.executable,
                game_running=lambda: False,
            )
        self.assertEqual(result.status, "cleaned")
        self.assertFalse(target.exists())
        self.assertEqual(deployed.backup_path.read_bytes(), b"original")

    def test_cleanup_removes_a_different_version_of_legacy_proxy(self) -> None:
        deploy_plugin(
            game_executable_path=self.executable,
            plugin_dll_path=self.source,
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
            backup_directory=self.root / "backups",
        )
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

    def test_prefers_the_organized_third_party_plugin_location(self) -> None:
        organized = self.root / "third_party" / "mods-plugin" / "bin"
        organized.mkdir(parents=True)
        plugin = organized / "dwmapi.dll"
        plugin.write_bytes(self.plugin_bytes)

        self.assertEqual(packaged_plugin_dll(self.root), plugin.resolve())
        self.assertEqual(
            packaged_mod_workspace(self.root),
            self.workspace_source.resolve(),
        )

    def test_workspace_upgrade_preserves_user_edits_and_refreshes_managed_files(self) -> None:
        prepare_mod_workspace(
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
        )
        equipment = self.workspace / "nte-mods" / "equipment.nte"
        equipment.write_text("custom equipment\n", encoding="utf-8")
        enabled = self.workspace_source / "nte-mods.enabled"
        enabled.write_text("nte_mod_set 1\nload equipment\n", encoding="utf-8")

        prepare_mod_workspace(
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
        )

        self.assertEqual(equipment.read_text(encoding="utf-8"), "custom equipment\n")
        self.assertEqual(
            (self.workspace / "nte-mods.enabled").read_text(encoding="utf-8"),
            "nte_mod_set 1\nload equipment\n",
        )

    def test_workspace_refresh_preserves_plugin_generated_sdk_cache(self) -> None:
        prepare_mod_workspace(
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
        )
        sdk_cache = self.workspace / MOD_SDK_CACHE_FILES[0]
        sdk_checksum = self.workspace / MOD_SDK_CACHE_FILES[1]
        sdk_cache.write_bytes(b"generated-sdk")
        sdk_checksum.write_text("game-image-checksum", encoding="ascii")

        prepare_mod_workspace(
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
        )

        self.assertEqual(sdk_cache.read_bytes(), b"generated-sdk")
        self.assertEqual(sdk_checksum.read_text(encoding="ascii"), "game-image-checksum")

    def test_bundled_workspace_can_be_registered_without_copying_onto_itself(self) -> None:
        equipment = self.workspace_source / "nte-mods" / "equipment.nte"
        before = equipment.read_bytes()

        prepared = prepare_mod_workspace(
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace_source,
        )

        self.assertEqual(prepared, self.workspace_source.resolve())
        self.assertEqual(equipment.read_bytes(), before)
        self.assertFalse(
            (self.workspace_source / ".nte-drive-calc-managed.json").exists()
        )
        self.register_workspace.assert_called_once_with(self.workspace_source.resolve())

    def test_rejects_a_legacy_or_unrelated_dwmapi(self) -> None:
        self.source.write_bytes(b"legacy proxy")

        self.assertFalse(is_mods_plugin_dll(self.source))
        with self.assertRaisesRegex(EquipmentPluginDeploymentError, "新版"):
            deploy_plugin(
                game_executable_path=self.executable,
                plugin_dll_path=self.source,
                application_root=self.root, operation_guard=lambda capability: None,
                writable_workspace_path=self.workspace,
                backup_directory=self.root / "backups",
            )

    def test_registration_failure_rolls_back_the_existing_dll(self) -> None:
        target = self.game / "dwmapi.dll"
        target.write_bytes(b"original")
        self.register_workspace.side_effect = EquipmentPluginDeploymentError("registry")

        with self.assertRaisesRegex(EquipmentPluginDeploymentError, "已回滚"):
            deploy_plugin(
                game_executable_path=self.executable,
                plugin_dll_path=self.source,
                application_root=self.root, operation_guard=lambda capability: None,
                writable_workspace_path=self.workspace,
                backup_directory=self.root / "backups",
            )

        self.assertEqual(target.read_bytes(), b"original")

    def test_deployment_records_the_previous_workspace_value(self) -> None:
        self.register_workspace.return_value = (True, r"C:\previous\mods")

        deployed = deploy_plugin(
            game_executable_path=self.executable,
            plugin_dll_path=self.source,
            application_root=self.root, operation_guard=lambda capability: None,
            writable_workspace_path=self.workspace,
            backup_directory=self.root / "backups",
        )

        self.assertTrue(deployed.workspace_registry_value_existed)
        self.assertEqual(
            deployed.workspace_registry_value_before,
            r"C:\previous\mods",
        )

    def test_deployment_without_mode_guard_cannot_write(self) -> None:
        with self.assertRaises(PermissionError):
            deploy_plugin(
                game_executable_path=self.executable, plugin_dll_path=self.source,
                application_root=self.root, writable_workspace_path=self.workspace,
                backup_directory=self.root / "backups",
            )
        self.assertFalse(self.workspace.exists())
        self.assertFalse((self.game / "dwmapi.dll").exists())

    def test_game_running_does_not_prepare_or_replace_files(self) -> None:
        with self.assertRaisesRegex(EquipmentPluginDeploymentError, "游戏正在运行"):
            deploy_plugin(
                game_executable_path=self.executable, plugin_dll_path=self.source,
                application_root=self.root, operation_guard=lambda capability: None,
                writable_workspace_path=self.workspace, backup_directory=self.root / "backups",
                game_running=lambda: True,
            )
        self.assertFalse(self.workspace.exists())

    def test_authorization_revoked_after_copy_preserves_pending_without_restoring_backup(self) -> None:
        target = self.game / "dwmapi.dll"
        target.write_bytes(b"old proxy")
        allowed = True

        def guard(capability):
            if not allowed:
                raise PermissionError("revoked")

        def register(workspace):
            nonlocal allowed
            allowed = False
            raise EquipmentPluginDeploymentError("registration failed")

        self.register_workspace.side_effect = register
        with self.assertRaises(PluginDeploymentPendingCleanup) as pending:
            deploy_plugin(
                game_executable_path=self.executable, plugin_dll_path=self.source,
                application_root=self.root, operation_guard=guard,
                writable_workspace_path=self.workspace, backup_directory=self.root / "backups",
            )
        self.assertEqual(target.read_bytes(), self.plugin_bytes)
        self.assertEqual(pending.exception.deployment.target_path, target)
        self.assertEqual(pending.exception.deployment.backup_path.read_bytes(), b"old proxy")

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
