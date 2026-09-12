# 用临时配置与组件替身验证模式运行时的清理、更新等待和检测缓存。
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.services.equipment_plugin_deployment import PluginDeployment, PluginDeploymentPendingCleanup
from src.services.work_mode_runtime import WorkModeRuntime
from src.services.work_mode_service import WorkModeService


class WorkModeRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "HTGame.exe"
        self.game.write_bytes(b"game")
        self.policy = WorkModeService(self.root / "work-mode.json")
        self.policy.set_game_executable(str(self.game))
        self.policy.set_cleanup_pending(False)
        self.native = SimpleNamespace(battle_active=False, close=MagicMock())
        self.loader = SimpleNamespace(
            pending_workspace_cleanup_path=None, active_payload_sha256="",
            stop_loader=MagicMock(), cleanup_workspace_registration=MagicMock(return_value=True),
            snapshot=MagicMock(return_value=SimpleNamespace(phase="stopped")),
            start_loader=MagicMock(),
        )
        self.process = MagicMock(return_value=False)
        self.runtime = WorkModeRuntime(
            policy=self.policy, native_session=self.native, loader=self.loader,
            application_root=self.root, config_dir=self.root / "config", game_running=self.process,
        )
        self.bundle_value = SimpleNamespace(ready=True, roles={"proxy": "dwmapi.dll"}, issues=())
        self.deployed_value = SimpleNamespace(
            compatible=False, proxy_present=False, proxy_matches_record=False,
            proxy_matches_package=False, workspace_valid=False,
            workspace_matches_package=False,
            workspace_registered=True,
            native_capabilities=frozenset(), equipment_script_valid=False,
        )
        self.bundle = self.stub("inspect_game_component_bundle", return_value=self.bundle_value)
        self.deployed = self.stub("inspect_deployed_plugin", return_value=self.deployed_value)
        self.stub("resolve_nte_core_executable", return_value=self.game)
        self.stub("npcap_installation_present", return_value=False)
        self.stub("find_spec", return_value=None)
        self.stub("mod_workspace_registry_snapshot", return_value=(False, None))
        self.clean = self.stub("cleanup_managed_plugin", return_value=SimpleNamespace(status="cleaned", detail="cleaned"))
        self.deploy = self.stub("deploy_plugin")
        self.clock = self.stub("monotonic", return_value=100.0)

    def stub(self, name, **kwargs):
        patcher = patch("src.services.work_mode_runtime." + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def enable_auto(self):
        self.policy.select_mode("medium", risk_confirmed=True)
        self.runtime._bundle = self.bundle_value
        self.runtime._deployed = self.deployed_value

    def deployment(self):
        return PluginDeployment(
            self.game, self.root / "dwmapi.dll", None, "a" * 64, self.root / "workspace",
        )

    def test_cleanup_while_battle_active_does_not_touch_components(self):
        self.native.battle_active = True
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup()
        self.loader.stop_loader.assert_not_called()
        self.clean.assert_not_called()
        self.assertTrue(self.policy.settings.pending_cleanup)

    def test_cleanup_waits_for_game_exit_without_hashing_deployment(self):
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup(running=True)
        self.clean.assert_not_called()
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.runtime.cleanup(running=False)
        self.clean.assert_called_once()
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_missing_game_path_and_unknown_registration_stay_pending(self):
        self.policy.set_game_executable("")
        self.policy.set_cleanup_pending(True)
        with patch("src.services.work_mode_runtime.mod_workspace_registry_snapshot", return_value=(True, "unknown")):
            self.runtime.cleanup(running=False)
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.assertIn("无法确认", self.runtime.cleanup_detail)
        self.clean.assert_not_called()

    def test_cleanup_conflict_preserves_record_for_restart(self):
        record = {"game_executable": str(self.game), "deployed_sha256": "a" * 64}
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        self.clean.return_value = SimpleNamespace(status="conflict", detail="modified")
        self.runtime.cleanup(running=False)
        reopened = WorkModeService(self.root / "work-mode.json")
        self.assertTrue(reopened.settings.pending_cleanup)
        self.assertEqual(reopened.deployment_record, record)

    def test_pending_loader_workspace_is_saved_before_stopping(self):
        self.loader.pending_workspace_cleanup_path = self.root / "loaded-workspace"
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup(running=True)
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(self.root / "loaded-workspace"))

    def test_automatic_management_waits_for_manifest_or_game_exit(self):
        self.enable_auto()
        self.runtime._bundle = SimpleNamespace(ready=False)
        self.runtime._automatic_deploy(False)
        self.assertIn("核对", self.runtime.cleanup_detail)
        self.runtime._bundle = self.bundle_value
        self.runtime._automatic_deploy(True)
        self.assertIn("等待游戏退出", self.runtime.cleanup_detail)
        self.deploy.assert_not_called()

    def test_automatic_management_never_replaces_unknown_modified_proxy(self):
        self.enable_auto()
        self.deployed_value.proxy_present = True
        self.runtime._automatic_deploy(False)
        self.deploy.assert_not_called()
        self.assertIn("归属未知", self.runtime.cleanup_detail)

    def test_auto_update_does_not_run_during_battle(self):
        self.enable_auto()
        self.native.battle_active = True
        self.runtime._automatic_deploy(False)
        self.deploy.assert_not_called()

    def test_deployment_interrupted_after_copy_is_persisted_for_cleanup(self):
        self.enable_auto()
        self.deploy.side_effect = PluginDeploymentPendingCleanup("pending", deployment=self.deployment())
        self.runtime._automatic_deploy(False)
        reopened = WorkModeService(self.root / "work-mode.json")
        self.assertTrue(reopened.settings.pending_cleanup)
        self.assertEqual(reopened.deployment_record["deployed_sha256"], "a" * 64)
        self.assertEqual(reopened.deployment_record["game_executable"], str(self.game))

    def test_failed_deployment_is_not_retried_every_tick(self):
        self.enable_auto()
        self.deploy.side_effect = OSError("fixture failure")
        with self.assertRaises(OSError):
            self.runtime._automatic_deploy(False)
        self.runtime._automatic_deploy(False)
        self.deploy.assert_called_once()
        self.assertIn("fixture failure", self.runtime._auto_error)
        probe = self.runtime.tick()
        self.assertEqual(probe.component_update_state.value, "fault")
        self.assertIn("fixture failure", probe.component_update_detail)
        self.runtime.invalidate()
        self.assertEqual(self.runtime._auto_error, "")

    def test_manual_detection_respects_pause(self):
        self.policy.select_mode("medium", risk_confirmed=True)
        self.policy.set_paused(True)
        self.process.return_value = True
        self.native.inspect = MagicMock()
        self.runtime.tick(allow_connect=True)
        self.native.inspect.assert_not_called()

    def prepare_auto_loader(self):
        self.enable_auto()
        self.policy.update_deployment({"loading_method": "loader"})
        self.bundle_value.roles["loader"] = "nte-mod-loader.exe"
        self.stub("packaged_plugin_dll", return_value=(self.root / "dwmapi.dll").resolve())
        self.stub("packaged_mod_loader", return_value=(self.root / "nte-mod-loader.exe").resolve())
        self.loader.start_loader.return_value = SimpleNamespace(workspace_path=self.root / "workspace", removed_proxy=None)

    def test_automatic_management_respects_loader_method_even_with_compatible_proxy(self):
        self.prepare_auto_loader()
        self.deployed_value.compatible = True
        self.deployed_value.workspace_matches_package = True
        self.runtime._automatic_deploy(False)
        self.loader.start_loader.assert_called_once()
        self.deploy.assert_not_called()
        self.assertEqual(self.policy.deployment_record["loading_method"], "loader")
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(self.root / "workspace"))

    def test_automatic_loader_waits_for_game_exit_without_start_or_proxy_deploy(self):
        self.prepare_auto_loader()
        self.runtime._automatic_deploy(True)
        self.loader.start_loader.assert_not_called()
        self.deploy.assert_not_called()

    def test_automatic_loader_guard_rechecks_pause(self):
        self.prepare_auto_loader()

        def start(**arguments):
            self.policy.set_paused(True)
            arguments["scoped_guard"]("native_load")

        self.loader.start_loader.side_effect = start
        with self.assertRaises(PermissionError):
            self.runtime._automatic_deploy(False)
        self.assertIn("失败", self.runtime.cleanup_detail)
        self.assertTrue(self.policy.settings.paused)
        self.deploy.assert_not_called()

    def test_automatic_loader_guard_rechecks_runtime_closed(self):
        self.prepare_auto_loader()

        def start(**arguments):
            self.runtime.close()
            arguments["scoped_guard"]("native_load")

        self.loader.start_loader.side_effect = start
        with self.assertRaisesRegex(PermissionError, "应用正在退出"):
            self.runtime._automatic_deploy(False)
        self.deploy.assert_not_called()

    def test_automatic_loader_failure_persists_owned_registration(self):
        self.prepare_auto_loader()
        self.loader.pending_workspace_cleanup_path = self.root / "workspace"
        self.loader.start_loader.side_effect = PermissionError("revoked after register")
        with self.assertRaises(PermissionError):
            self.runtime._automatic_deploy(False)
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(self.root / "workspace"))

    def test_automatic_loader_rejects_unverified_actual_loader_path(self):
        self.prepare_auto_loader()
        self.bundle_value.roles["loader"] = "another-loader.exe"
        with self.assertRaisesRegex(Exception, "实际输入"):
            self.runtime._automatic_deploy(False)
        self.loader.start_loader.assert_not_called()

    def test_file_checks_are_cached_and_process_checked_once_per_tick(self):
        self.runtime.tick()
        self.runtime.tick()
        self.bundle.assert_called_once()
        self.deployed.assert_called_once()
        self.assertEqual(self.process.call_count, 2)
        self.clock.return_value = 116.0
        self.runtime.tick()
        self.assertEqual(self.bundle.call_count, 2)
        self.assertEqual(self.deployed.call_count, 2)
        self.assertEqual(self.process.call_count, 3)

    def test_manual_loader_files_are_usable_without_proxy_deployment(self):
        payload = self.root / "dwmapi.dll"
        payload.write_bytes(b"fixture loader payload")
        self.loader.active_payload_sha256 = hashlib.sha256(payload.read_bytes()).hexdigest()
        self.loader.snapshot.return_value = SimpleNamespace(phase="running")
        self.deployed_value.workspace_valid = True
        with patch("src.services.work_mode_runtime.packaged_plugin_dll", return_value=payload):
            probe = self.runtime.tick()
        self.assertTrue(probe.native_load.files)
        self.assertFalse(self.deployed_value.proxy_present)
        self.deploy.assert_not_called()
