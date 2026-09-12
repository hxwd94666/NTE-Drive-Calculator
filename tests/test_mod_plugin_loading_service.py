# 验证代理 DLL 与备用 Loader 的互斥加载 contract。
from __future__ import annotations

import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.mod_loader import (
    MOD_LOADER_STOP_TIMEOUT_MS,
    ModLoaderRuntimeError,
    ModLoaderRuntimeSnapshot,
    _managed_stop_event_name,
    game_launcher_executable,
    packaged_mod_loader,
)
from src.services.equipment_plugin_deployment import (
    MOD_PLUGIN_SIGNATURE,
    MOD_WORKSPACE_FILES,
)
from src.services.mod_plugin_loading_service import (
    ModPluginLoadingError,
    ModPluginLoadingService,
)


class _FakeRuntime:
    def __init__(self, *, phase: str = "stopped") -> None:
        self.phase = phase
        self.started_payload: Path | None = None
        self.started_launcher: Path | None = None
        self.stop_calls: list[int] = []

    def snapshot(self, *, payload_path):
        return ModLoaderRuntimeSnapshot(
            self.phase,
            Path("loader.exe"),
            Path(payload_path),
            123 if self.phase == "running" else None,
        )

    def start(self, *, payload_path, launcher_path):
        self.started_payload = Path(payload_path)
        self.started_launcher = Path(launcher_path)
        self.phase = "running"
        return self.snapshot(payload_path=payload_path)

    def stop(self, *, timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS):
        self.stop_calls.append(timeout_ms)
        was_running = self.phase == "running"
        self.phase = "stopped"
        return was_running

    def close(self):
        self.stop(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)


class _FailingRuntime(_FakeRuntime):
    def start(self, *, payload_path, launcher_path):
        raise ModLoaderRuntimeError("launch failed")


class ModPluginLoadingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        process = patch("src.services.mod_plugin_loading_service.game_process_running", return_value=False)
        process.start()
        self.addCleanup(process.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plugin = (
            self.root / "third_party" / "mods-plugin" / "bin" / "dwmapi.dll"
        )
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_bytes(MOD_PLUGIN_SIGNATURE + b":0.4.1")
        workspace = self.root / "third_party" / "mods-plugin" / "workspace"
        for relative in MOD_WORKSPACE_FILES:
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test\n", encoding="utf-8")
        (workspace / "native-capture.json").write_text(json.dumps({
            "protocol_version": 1,
            "capabilities": ["combat.hit_buff.v1", "combat.context.v1"],
            "sha256": hashlib.sha256((workspace / "NTE_Capture.dll").read_bytes()).hexdigest(),
        }), encoding="utf-8")
        self.install_root = self.root / "games" / "Neverness To Everness"
        self.launcher = self.install_root / "NTELauncher.exe"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_bytes(b"launcher")
        self.game = (
            self.install_root / "Client" / "WindowsNoEditor" / "HT"
            / "Binaries" / "Win64" / "HTGame.exe"
        )
        self.game.parent.mkdir(parents=True)
        self.game.write_bytes(b"game")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_packaged_loader_accepts_a_user_replacement_without_hash_pin(self) -> None:
        loader = (
            self.root
            / "third_party"
            / "mod-loader"
            / "bin"
            / "nte-mod-loader.exe"
        )
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"loader")

        self.assertEqual(packaged_mod_loader(self.root), loader.resolve())

    def test_managed_stop_event_matches_upstream_control_contract(self) -> None:
        self.assertEqual(
            _managed_stop_event_name(
                process_id=0x1234,
                session_suffix=0xABCDEF,
            ),
            "Local\\NTE-DPS-TOOL-ModLoader-0000123400abcdef",
        )

    def test_launcher_is_resolved_from_the_selected_game_installation(self) -> None:
        self.assertEqual(
            game_launcher_executable(self.game),
            self.launcher.resolve(),
        )

    def test_launcher_resolution_rejects_a_nonstandard_game_layout(self) -> None:
        unrelated_game = self.root / "other" / "HTGame.exe"
        unrelated_game.parent.mkdir()
        unrelated_game.write_bytes(b"game")

        with self.assertRaisesRegex(ModLoaderRuntimeError, "官方 Client 目录结构"):
            game_launcher_executable(unrelated_game)

    def test_loader_rejects_an_unknown_proxy_without_changing_it(self) -> None:
        proxy = self.game.parent / "dwmapi.dll"
        old_proxy = MOD_PLUGIN_SIGNATURE + b":old-release"
        proxy.write_bytes(old_proxy)
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None,
            runtime=runtime,
        )
        backup_root = self.root / "account" / "equipment_plugin_backups"

        with patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime",
            return_value={"supported": True, "ready": True, "files": {}},
        ):
            with self.assertRaisesRegex(ModPluginLoadingError, "归属未知"):
                service.start_loader(
                    game_executable_path=self.game,
                    writable_workspace_path=self.root / "writable-mods",
                    proxy_backup_directory=backup_root,
                )

        self.assertEqual(proxy.read_bytes(), old_proxy)
        self.assertFalse(backup_root.exists())
        self.assertIsNone(runtime.started_payload)

    def test_loader_directly_removes_the_current_packaged_proxy(self) -> None:
        proxy = self.game.parent / "dwmapi.dll"
        proxy.write_bytes(self.plugin.read_bytes())
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None,
            runtime=_FakeRuntime(),
        )
        backup_root = self.root / "account" / "equipment_plugin_backups"

        with patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime",
            return_value={"supported": True, "ready": True, "files": {}},
        ):
            result = service.start_loader(
                game_executable_path=self.game,
                writable_workspace_path=self.root / "writable-mods",
                proxy_backup_directory=backup_root,
            )

        self.assertFalse(proxy.exists())
        self.assertTrue(result.removed_proxy.known)
        self.assertIsNone(result.removed_proxy.backup_path)
        self.assertFalse(backup_root.exists())

    def test_loader_prepares_workspace_and_starts_audited_payload(self) -> None:
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None,
            runtime=runtime,
        )
        writable = self.root / "writable-mods"

        with patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot",
            return_value=(True, "C:\\previous-mods"),
        ), patch(
            "src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime",
            return_value={"supported": True, "ready": True, "files": {}},
        ), patch(
            "src.services.mod_plugin_loading_service.cleanup_mod_workspace",
            return_value=True,
        ) as restore_workspace:
            result = service.start_loader(
                game_executable_path=self.game,
                writable_workspace_path=writable,
                proxy_backup_directory=self.root / "backups",
            )
            service.stop_loader()

        self.assertEqual(result.runtime.phase, "running")
        self.assertEqual(
            runtime.started_payload,
            (self.root / "third_party" / "mods-plugin" / "bin" / "dwmapi.dll").resolve(),
        )
        self.assertEqual(runtime.started_launcher, self.launcher.resolve())
        self.assertTrue((writable / "nte-mods.enabled").is_file())
        restore_workspace.assert_called_once_with(
            workspace_path=writable.resolve(),
        )

    def test_loader_start_failure_restores_previous_workspace_registration(self) -> None:
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None,
            runtime=_FailingRuntime(),
        )
        writable = self.root / "writable-mods"
        proxy = self.game.parent / "dwmapi.dll"
        proxy.write_bytes(self.plugin.read_bytes())
        backup_root = self.root / "account" / "equipment_plugin_backups"

        with patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot",
            return_value=(True, "C:\\previous-mods"),
        ), patch(
            "src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime",
            return_value={"supported": True, "ready": True, "files": {}},
        ), patch(
            "src.services.mod_plugin_loading_service.rollback_mod_workspace",
            return_value=True,
        ) as restore_workspace:
            with self.assertRaisesRegex(ModPluginLoadingError, "launch failed"):
                service.start_loader(
                    game_executable_path=self.game,
                    writable_workspace_path=writable,
                    proxy_backup_directory=backup_root,
                )

        self.assertEqual(proxy.read_bytes(), self.plugin.read_bytes())
        self.assertFalse(backup_root.exists())
        restore_workspace.assert_called_once_with(
            workspace_path=writable.resolve(),
            previous_value="C:\\previous-mods",
            previous_value_existed=True,
        )

    def test_proxy_deployment_is_blocked_while_loader_runs(self) -> None:
        runtime = _FakeRuntime(phase="running")
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None,
            runtime=runtime,
        )

        with self.assertRaisesRegex(ModPluginLoadingError, "正在运行"):
            service.ensure_proxy_deployment_allowed()

        service.stop_loader()
        self.assertEqual(runtime.stop_calls, [MOD_LOADER_STOP_TIMEOUT_MS])

    def test_missing_operation_guard_blocks_start_before_workspace_mutation(self) -> None:
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(application_root=self.root, runtime=runtime)
        workspace = self.root / "blocked-workspace"
        with self.assertRaises(PermissionError):
            service.start_loader(
                game_executable_path=self.game, writable_workspace_path=workspace,
                proxy_backup_directory=self.root / "backups",
            )
        self.assertIsNone(runtime.started_payload)
        self.assertFalse(workspace.exists())

    def test_scoped_guard_denies_automatic_start_before_any_workspace_change(self) -> None:
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None, runtime=runtime,
        )
        workspace = self.root / "scoped-workspace"
        guard = unittest.mock.Mock(side_effect=PermissionError("automatic disabled"))
        with self.assertRaisesRegex(PermissionError, "automatic disabled"):
            service.start_loader(
                game_executable_path=self.game, writable_workspace_path=workspace,
                proxy_backup_directory=self.root / "backups", scoped_guard=guard,
            )
        self.assertIsNone(runtime.started_payload)
        self.assertFalse(workspace.exists())

    def test_scoped_revocation_after_registration_prevents_load_and_registry_rollback(self) -> None:
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None, runtime=runtime,
        )
        allowed = True
        workspace = self.root / "scoped-workspace"

        def guard(capability):
            if not allowed:
                raise PermissionError("automatic disabled")

        def register(path):
            nonlocal allowed
            allowed = False
            return False, None

        with patch("src.services.equipment_plugin_deployment._register_mod_workspace", side_effect=register), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot", return_value=(False, None),
        ), patch("src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime", return_value={"ready": True}), patch(
            "src.services.mod_plugin_loading_service.rollback_mod_workspace",
        ) as rollback:
            with self.assertRaises(ModPluginLoadingError):
                service.start_loader(
                    game_executable_path=self.game, writable_workspace_path=workspace,
                    proxy_backup_directory=self.root / "backups", scoped_guard=guard,
                )
        self.assertIsNone(runtime.started_payload)
        self.assertEqual(service.pending_workspace_cleanup_path, workspace.resolve())
        rollback.assert_not_called()

    def test_stop_loader_retains_cleanup_record_until_game_exits(self) -> None:
        running = False
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(
            application_root=self.root, operation_guard=lambda capability: None,
            game_running=lambda: running, runtime=runtime,
        )
        workspace = self.root / "writable-mods"
        with patch("src.services.equipment_plugin_deployment._register_mod_workspace", return_value=(False, None)), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot", return_value=(False, None),
        ), patch("src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime", return_value={"ready": True}), patch(
            "src.services.mod_plugin_loading_service.cleanup_mod_workspace", return_value=True,
        ) as cleanup:
            service.start_loader(
                game_executable_path=self.game, writable_workspace_path=workspace,
                proxy_backup_directory=self.root / "backups",
            )
            running = True
            service.stop_loader()
            self.assertEqual(service.pending_workspace_cleanup_path, workspace.resolve())
            cleanup.assert_not_called()
            running = False
            self.assertTrue(service.cleanup_workspace_registration())
            self.assertIsNone(service.pending_workspace_cleanup_path)
            cleanup.assert_called_once_with(workspace_path=workspace.resolve())


if __name__ == "__main__":
    unittest.main()
