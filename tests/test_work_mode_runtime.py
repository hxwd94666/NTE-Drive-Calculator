# 用临时配置与组件替身验证模式运行时的清理、更新等待和检测缓存。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.domain.work_mode import CheckState
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError, PluginDeployment, PluginDeploymentPendingCleanup
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
        self.running_paths = self.stub("running_game_executables", return_value=())
        self.installed_paths = self.stub("find_game_executables", return_value=[])
        self.stub("create_bundled_analysis_client", return_value=SimpleNamespace(supports_battle_page=True))
        self.clean = self.stub("cleanup_managed_plugin", return_value=SimpleNamespace(status="cleaned", detail="cleaned"))
        self.deploy = self.stub("deploy_plugin", autospec=True)
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

    def test_low_mode_checks_npcap_without_a_game_path(self):
        self.policy.select_mode("low", risk_confirmed=True)
        self.policy.set_game_executable("")
        for installed in (False, True):
            with self.subTest(installed=installed), patch(
                "src.services.work_mode_runtime.npcap_installation_present", return_value=installed,
            ):
                probe = self.runtime.tick()
                check = next(item for item in self.policy.build_report(probe).features if item.feature == "npcap")
                self.assertEqual(probe.npcap_available, installed)
                self.assertEqual("download_npcap" in check.actions, not installed)
        self.process.assert_not_called()
        self.clean.assert_not_called()

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

    def test_default_pending_is_unverified_not_an_exit_warning(self):
        self.policy.set_cleanup_pending(True)
        self.assertEqual(self.runtime.cleanup_exit_detail, "")
        self.runtime.cleanup(running=True)
        self.assertEqual(self.runtime.cleanup_state, CheckState.WAITING)
        self.assertEqual(self.runtime.cleanup_exit_detail, "")
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.runtime.cleanup(running=False)
        self.assertFalse(self.policy.settings.pending_cleanup)
        self.assertEqual(self.runtime.cleanup_exit_detail, "")

    def test_old_settings_without_cleanup_field_remain_silent_until_checked(self):
        path = self.root / "old-settings.json"
        path.write_text(json.dumps({"schema_version": 1, "mode": "offline", "deployment": {}}), encoding="utf-8")
        self.runtime.policy = WorkModeService(path)
        self.assertTrue(self.runtime.policy.settings.pending_cleanup)
        self.assertEqual(self.runtime.cleanup_exit_detail, "")
        probe = self.runtime.tick()
        self.assertEqual(probe.cleanup_state, CheckState.WAITING)
        self.assertIn("游戏", probe.cleanup_detail)
        self.assertEqual(self.runtime.cleanup_exit_detail, "")

    def test_missing_path_for_recorded_components_has_actionable_reason(self):
        self.policy.set_game_executable("")
        self.policy.update_deployment({"deployed_sha256": "a" * 64})
        self.policy.set_cleanup_pending(True)
        probe = self.runtime.tick()
        self.assertEqual(probe.cleanup_state, CheckState.WAITING)
        self.assertIn("游戏路径", self.runtime.cleanup_exit_detail)
        self.assertEqual(self.runtime.cleanup_exit_detail, probe.cleanup_detail)
        self.clean.assert_not_called()
        self.process.assert_not_called()

    def test_observed_wait_for_owned_plugin_is_notified_until_cleaned(self):
        self.policy.update_deployment({"game_executable": str(self.game), "deployed_sha256": "a" * 64})
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup(running=True)
        self.assertIn("游戏未关闭", self.runtime.cleanup_exit_detail)
        self.assertEqual(self.runtime.cleanup_state, CheckState.CLEANUP_PENDING)
        self.clean.assert_not_called()
        self.runtime.cleanup(running=False)
        self.assertEqual(self.runtime.cleanup_exit_detail, "")
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_changed_deployment_does_not_reuse_an_old_cleanup_reason(self):
        self.policy.update_deployment({"game_executable": str(self.game), "deployed_sha256": "a" * 64})
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup(running=True)
        self.policy.update_deployment({"loading_method": "proxy"})
        self.assertEqual(self.runtime.cleanup_exit_detail, "")
        self.assertIsNone(self.runtime.cleanup_state)

    def test_cleanup_system_failure_survives_background_detection_and_report(self):
        for code, expected in ((5, "权限不足"), (32, "被其他进程占用"), (3, "路径已不存在")):
            with self.subTest(code=code):
                record = {"game_executable": str(self.game), "deployed_sha256": "a" * 64}
                self.policy.update_deployment(record)
                self.policy.set_cleanup_pending(True)
                cause = OSError(13, "fixture", "fixture.dll", code)
                cause.winerror = code
                error = EquipmentPluginDeploymentError("无法清理已记录组件，请保持游戏关闭并重试。")
                error.__cause__ = cause
                self.clean.side_effect = error
                probe = self.runtime.tick()
                self.assertEqual(probe.cleanup_state, CheckState.FAULT)
                self.assertIn(expected, probe.cleanup_detail)
                self.assertIn(f"系统错误 {code}", probe.cleanup_detail)
                self.assertIn("fixture.dll", probe.cleanup_detail)
                self.assertEqual(self.runtime.cleanup_exit_detail, probe.cleanup_detail)
                checks = {item.feature: item for item in self.policy.build_report(probe).features}
                self.assertEqual(checks["cleanup"].state, CheckState.FAULT)
                self.assertEqual(checks["cleanup"].detail, probe.cleanup_detail)
                self.assertEqual(self.policy.deployment_record, record)
                self.assertTrue(self.policy.settings.pending_cleanup)

    def test_missing_game_path_stays_pending_without_component_operations(self):
        self.policy.set_game_executable("")
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup()
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.assertIn("自动重试", self.runtime.cleanup_detail)
        self.clean.assert_not_called()
        self.process.assert_not_called()
        self.loader.stop_loader.assert_not_called()

    def test_missing_path_skips_game_checks_but_keeps_offline_analysis_available(self):
        self.policy.set_game_executable("")
        self.policy.set_cleanup_pending(True)
        probe = self.runtime.tick()
        self.assertFalse(probe.game_path_valid)
        self.assertTrue(probe.analysis_available)
        self.process.assert_not_called()
        self.clean.assert_not_called()
        self.deploy.assert_not_called()
        self.deployed.assert_not_called()
        self.native.close.assert_not_called()
        checks = {item.feature: item for item in self.policy.build_report(probe).features}
        self.assertEqual(checks["cleanup"].state.value, "waiting")
        self.assertIn("detect_game_path", checks["cleanup"].actions)
        self.assertEqual(checks["local"].state.value, "available")
        self.assertTrue(self.policy.settings.pending_cleanup)

    def test_missing_path_retries_after_interval_and_resumes_cleanup(self):
        self.policy.set_game_executable("")
        self.policy.set_cleanup_pending(True)
        self.runtime.tick()
        self.installed_paths.return_value = [self.game]
        self.clock.return_value = 114.0
        self.runtime.tick()
        self.installed_paths.assert_called_once()
        self.clean.assert_not_called()
        self.clock.return_value = 115.0
        probe = self.runtime.tick()
        self.assertTrue(probe.game_path_valid)
        self.assertEqual(self.policy.settings.game_executable, str(self.game))
        self.clean.assert_called_once()
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_explicit_recheck_retries_discovery_without_waiting(self):
        self.policy.set_game_executable("")
        self.runtime.tick()
        self.running_paths.return_value = (self.game,)
        self.runtime.invalidate()
        probe = self.runtime.tick()
        self.assertTrue(probe.game_path_valid)
        self.assertEqual(self.running_paths.call_count, 2)

    def test_wrong_executable_and_multiple_game_paths_do_not_start_operations(self):
        wrong = self.root / "launcher.exe"
        wrong.write_bytes(b"launcher")
        other = self.root / "other" / "HTGame.exe"
        other.parent.mkdir()
        other.write_bytes(b"game")
        self.policy.set_game_executable(str(wrong))
        self.installed_paths.return_value = [wrong, self.game, other]
        probe = self.runtime.tick()
        self.assertFalse(probe.game_path_valid)
        self.assertEqual(self.runtime.path_candidates, (str(self.game), str(other)))
        self.assertIn("多个", probe.component_update_detail)
        self.assertEqual(self.policy.settings.game_executable, str(wrong))
        self.process.assert_not_called()
        self.policy.set_game_executable(str(other))
        self.assertTrue(self.runtime.tick().game_path_valid)

    def test_disappeared_path_is_rediscovered_automatically(self):
        self.runtime.tick()
        self.game.unlink()
        self.process.reset_mock()
        self.assertFalse(self.runtime.tick().game_path_valid)
        self.process.assert_not_called()
        self.game.write_bytes(b"game")
        self.assertTrue(self.runtime.tick().game_path_valid)
        self.game.unlink()
        probe = self.runtime.tick()
        self.assertFalse(probe.game_path_valid)
        self.assertIn("自动重试", probe.component_update_detail)

    def test_cleanup_discovers_missing_path_before_querying_game_process(self):
        self.policy.set_game_executable("")
        self.policy.set_cleanup_pending(True)
        self.running_paths.return_value = (self.game,)
        self.process.side_effect = lambda: self.policy.settings.game_executable != str(self.game)
        self.runtime.cleanup()
        self.clean.assert_called_once()
        self.assertEqual(self.clean.call_args.kwargs["game_executable_path"], str(self.game))
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_invalid_recorded_cleanup_path_never_uses_new_game_directory(self):
        record = {"game_executable": str(self.root / "removed" / "HTGame.exe"), "deployed_sha256": "a" * 64}
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup()
        self.clean.assert_not_called()
        self.process.assert_not_called()
        self.assertEqual(self.policy.deployment_record, record)
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.assertIn("不改用", self.runtime.cleanup_detail)

    def test_discovery_cannot_overwrite_path_selected_during_scan(self):
        self.policy.set_game_executable("")
        self.installed_paths.side_effect = lambda: self.policy.set_game_executable(str(self.game)) or []
        self.assertTrue(self.runtime.tick().game_path_valid)
        self.assertEqual(self.policy.settings.game_executable, str(self.game))

    def test_duplicate_discovery_sources_save_one_valid_path(self):
        self.policy.set_game_executable("")
        self.running_paths.return_value = (self.game,)
        self.installed_paths.return_value = [self.game]
        self.assertEqual(self.runtime.discover(), (str(self.game),))
        self.assertEqual(self.policy.settings.game_executable, str(self.game))

    def test_native_cleanup_with_invalid_record_preserves_files_and_workspace(self):
        record = {
            "deployment_layout": "native-capture-v1",
            "game_executable": str(self.root / "removed" / "HTGame.exe"),
            "managed_files": {"d3d12.dll": "a" * 64},
            "native_workspace_root": str(self.root / "workspace"),
        }
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        native_cleanup = self.stub("cleanup_native_plugin")
        self.runtime.cleanup()
        native_cleanup.assert_not_called()
        self.loader.stop_loader.assert_not_called()
        self.assertEqual(self.policy.deployment_record, record)
        self.assertTrue(self.policy.settings.pending_cleanup)

    def test_shutdown_during_discovery_never_saves_candidate(self):
        self.policy.set_game_executable("")
        self.installed_paths.side_effect = lambda: self.runtime.close() or [self.game]
        self.runtime.discover()
        self.assertEqual(self.policy.settings.game_executable, "")

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

    def test_external_native_provider_keeps_file_facts_and_uses_live_capabilities(self):
        self.policy.select_mode("developer", risk_confirmed=True)
        self.policy.set_cleanup_pending(False)
        self.process.return_value = True
        self.bundle_value.layout = "native-capture-v1"
        self.bundle_value.native_capabilities = frozenset({
            "combat.hit_buff.v1", "combat.context.v1", "equipment.execute.v1",
        })
        self.runtime._bundle = self.bundle_value
        self.runtime._deployed = self.deployed_value
        self.runtime._native_deployed = SimpleNamespace(files_compatible=False)
        self.stub("native_capture_game_pid", return_value=123)
        self.native.inspect = MagicMock(return_value={
            "hello": {"capabilities": ["native_hit_buff_v1", "battle_axis_v1",
                "native_context_observation_v1", "equipment", "native_equipment_v1"]},
            "status": {"native_status": {"ready": True}},
            "domains": {"domains": []}, "equipment": {"ready": True},
            "inventory_snapshot_ready": True,
        })
        with patch.object(self.runtime, "_inspect_component_files"), patch.object(self.runtime, "_automatic_deploy"):
            probe = self.runtime.tick(allow_connect=True)
        checks = {item.feature: item for item in self.policy.build_report(probe).features}
        self.assertFalse(probe.native_load.files)
        for name in ("native_battle", "native_equipment"):
            fact = getattr(probe, name)
            self.assertFalse(fact.files)
            self.assertTrue(fact.handshake)
            self.assertTrue(fact.supported)
            self.assertEqual(checks[name].state.value, "available")
        self.native.inspect.assert_called_once_with(refresh=True, check_equipment=True)
        self.deploy.assert_not_called()

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
