# 用临时配置与组件替身验证模式运行时的清理、更新等待和检测缓存。
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.domain.work_mode import CheckState
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from src.services.native_plugin_deployment import NativePluginDeployment, PluginDeploymentPendingCleanup
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
            restore_native_workspace_record=MagicMock(),
            cleanup_native_workspace=MagicMock(
                return_value=SimpleNamespace(status="cleaned", detail="workspace cleaned")
            ),
            snapshot=MagicMock(return_value=SimpleNamespace(phase="stopped")),
            start_loader=MagicMock(),
        )
        self.process = MagicMock(return_value=False)
        self.runtime = WorkModeRuntime(
            policy=self.policy, native_session=self.native, loader=self.loader,
            application_root=self.root, config_dir=self.root / "config", game_running=self.process,
        )
        self.bundle_value = SimpleNamespace(ready=True, roles={}, issues=(), layout="native-capture-v1", native_capabilities=frozenset())
        self.deployed_value = SimpleNamespace(files_compatible=False, files={})
        self.bundle = self.stub("inspect_game_component_bundle", return_value=self.bundle_value)
        self.deployed = self.stub("inspect_deployed_native_plugin", return_value=self.deployed_value)
        self.stub("resolve_nte_core_executable", return_value=self.game)
        self.stub("npcap_installation_present", return_value=False)
        self.stub("find_spec", return_value=None)
        self.running_paths = self.stub("running_game_executables", return_value=())
        self.installed_paths = self.stub("find_game_executables", return_value=[])
        self.stub("create_bundled_analysis_client", return_value=SimpleNamespace(supports_battle_page=True))
        self.clean = self.stub("cleanup_managed_plugin", return_value=SimpleNamespace(status="cleaned", detail="cleaned"))
        self.deploy = self.stub("deploy_native_plugin", autospec=True)
        self.clock = self.stub("monotonic", return_value=100.0)

    def stub(self, name, **kwargs):
        patcher = patch("src.services.work_mode_runtime." + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def enable_auto(self):
        self.policy.select_mode("medium", risk_confirmed=True)
        self.policy.enable_auto_sync_after_preflight()
        self.runtime._bundle = self.bundle_value
        self.runtime._native_deployed = self.deployed_value

    def test_preflight_preview_does_not_clean_deploy_or_close_session(self):
        self.enable_auto()
        self.policy.set_cleanup_pending(True)
        with patch.object(self.runtime, "cleanup") as cleanup, patch.object(
            self.runtime, "_automatic_deploy",
        ) as deploy:
            self.runtime.tick(preview=True)
        cleanup.assert_not_called()
        deploy.assert_not_called()
        self.native.close.assert_not_called()

    def test_inspection_failure_preserves_shared_reason_and_unknown_handshake(self):
        from src.integrations.nte_core_protocol import NteCoreTimeoutError
        self.enable_auto()
        self.policy.set_cleanup_pending(False)
        self.process.return_value = True
        self.deployed_value.files_compatible = True
        self.stub("native_capture_game_pid", return_value=123)
        self.native.inspect = MagicMock(side_effect=NteCoreTimeoutError("native.snapshot.status", 10))
        with patch.object(self.runtime, "_inspect_component_files"), patch.object(self.runtime, "_automatic_deploy"):
            probe = self.runtime.tick(allow_connect=True)
        self.assertIn("native.snapshot.status", probe.native_diagnostic)
        self.assertTrue(probe.native_load.files)
        self.assertTrue(probe.native_inventory.pipe)
        self.assertIsNone(probe.native_inventory.handshake)
        self.assertIsNone(probe.native_inventory.ready)
        self.assertEqual(probe.native_inventory.fault, "")

    def test_endpoint_probe_failure_keeps_file_report_and_reason(self):
        self.enable_auto()
        self.policy.set_cleanup_pending(False)
        self.process.return_value = True
        self.deployed_value.files_compatible = True
        self.stub("native_capture_game_pid", side_effect=PermissionError("private text"))
        with patch.object(self.runtime, "_inspect_component_files"), patch.object(self.runtime, "_automatic_deploy"):
            probe = self.runtime.tick(allow_connect=True)
        self.assertIn("权限", probe.native_diagnostic)
        self.assertNotIn("private text", probe.native_diagnostic)
        self.assertTrue(probe.native_load.files)
        self.assertIsNone(probe.native_inventory.pipe)

    def deployment(self):
        return NativePluginDeployment(self.game, self.root / "d3d12.dll", "a" * 64,
            self.root, None, {"d3d12.dll": "a" * 64})

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

    def test_manual_deployment_adopts_unrecorded_legacy_workspace(self):
        self.policy.select_mode("medium", risk_confirmed=True)
        self.policy.set_cleanup_pending(True)
        revision = self.runtime.prepare_manual_native_deployment(
            expected_operation_revision=self.policy.operation_revision,
        )
        self.assertEqual(revision, self.policy.operation_revision)
        self.clean.assert_called_once_with(
            game_executable_path=str(self.game),
            mod_workspace_path=None,
            game_running=self.process,
            allow_unrecorded_workspace_adoption=True,
            cleanup_legacy_proxy=True,
        )
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_explicit_cleanup_persists_registered_legacy_workspace_before_dispatch(self):
        old = self.root / "old-workspace"
        current = self.root / "registered-workspace"
        record = {"game_executable": str(self.game), "workspace_path": str(old),
                  "deployed_sha256": "a" * 64}
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        snapshot = self.stub("mod_workspace_registry_snapshot", return_value=(True, str(current)))

        def cleaned(**kwargs):
            stored = json.loads((self.root / "work-mode.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["deployment"], {**record, "workspace_path": str(current)})
            self.assertTrue(stored["pending_cleanup"])
            self.assertEqual(kwargs["mod_workspace_path"], str(current))
            self.assertTrue(kwargs["cleanup_legacy_proxy"])
            return SimpleNamespace(status="cleaned", detail="cleaned")

        self.clean.side_effect = cleaned
        self.runtime.cleanup(allow_unrecorded_legacy_workspace=True)
        snapshot.assert_called_once_with()
        self.assertEqual(self.policy.deployment_record, {"loading_method": "native-capture"})
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_background_cleanup_keeps_mismatched_workspace_pending(self):
        old = self.root / "old-workspace"
        self.policy.update_deployment({"game_executable": str(self.game), "workspace_path": str(old)})
        self.policy.set_cleanup_pending(True)
        snapshot = self.stub("mod_workspace_registry_snapshot", return_value=(True, str(self.root / "other")))
        self.clean.return_value = SimpleNamespace(status="conflict", detail="workspace conflict")
        self.runtime.cleanup()
        snapshot.assert_not_called()
        self.assertEqual(self.clean.call_args.kwargs["mod_workspace_path"], str(old))
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(old))
        self.assertTrue(self.policy.settings.pending_cleanup)

    def test_explicit_reconciliation_preserves_retry_record_on_late_conflict(self):
        old = self.root / "old-workspace"
        current = self.root / "registered-workspace"
        self.policy.update_deployment({"game_executable": str(self.game), "workspace_path": str(old)})
        self.policy.set_cleanup_pending(True)
        self.stub("mod_workspace_registry_snapshot", return_value=(True, str(current)))
        self.clean.return_value = SimpleNamespace(status="conflict", detail="registry changed")
        self.runtime.cleanup(allow_unrecorded_legacy_workspace=True)
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(current))
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.assertEqual(self.runtime.cleanup_state, CheckState.FAULT)

    def test_explicit_reconciliation_waits_for_game_exit_before_record_write(self):
        old = self.root / "old-workspace"
        self.policy.update_deployment({"game_executable": str(self.game), "workspace_path": str(old)})
        self.policy.set_cleanup_pending(True)
        snapshot = self.stub("mod_workspace_registry_snapshot", return_value=(True, str(self.root / "other")))
        self.runtime.cleanup(running=True, allow_unrecorded_legacy_workspace=True)
        snapshot.assert_not_called()
        self.clean.assert_not_called()
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(old))

    def test_game_start_during_reconciliation_preserves_original_record(self):
        old = self.root / "old-workspace"
        self.policy.update_deployment({"game_executable": str(self.game), "workspace_path": str(old)})
        self.policy.set_cleanup_pending(True)
        self.stub("mod_workspace_registry_snapshot", return_value=(True, str(self.root / "other")))
        self.process.side_effect = [False, True]
        self.runtime.cleanup(allow_unrecorded_legacy_workspace=True)
        self.clean.assert_not_called()
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(old))
        self.assertEqual(self.runtime.cleanup_state, CheckState.CLEANUP_PENDING)

    def test_explicit_reconciliation_rejects_relative_registry_workspace(self):
        old = self.root / "old-workspace"
        self.policy.update_deployment({"game_executable": str(self.game), "workspace_path": str(old)})
        self.policy.set_cleanup_pending(True)
        self.stub("mod_workspace_registry_snapshot", return_value=(True, "relative-workspace"))
        with self.assertRaises(EquipmentPluginDeploymentError):
            self.runtime.cleanup(allow_unrecorded_legacy_workspace=True)
        self.clean.assert_not_called()
        self.assertEqual(self.policy.deployment_record["workspace_path"], str(old))
        self.assertTrue(self.policy.settings.pending_cleanup)

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

    def test_removed_recorded_game_executable_cleans_exact_old_directory(self):
        record = {"game_executable": str(self.root / "removed" / "HTGame.exe"), "deployed_sha256": "a" * 64}
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup()
        self.clean.assert_called_once_with(
            game_executable_path=record["game_executable"],
            mod_workspace_path=None,
            game_running=self.process,
            allow_unrecorded_workspace_adoption=False,
            cleanup_legacy_proxy=False,
        )
        self.assertEqual(self.policy.deployment_record, {"loading_method": "native-capture"})
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_tick_cleans_recorded_old_directory_without_a_current_game_path(self):
        old_path = self.root / "removed" / "HTGame.exe"
        self.policy.set_game_executable("")
        self.policy.update_deployment({"game_executable": str(old_path), "deployed_sha256": "a" * 64})
        self.policy.set_cleanup_pending(True)
        probe = self.runtime.tick()
        self.clean.assert_called_once_with(
            game_executable_path=str(old_path),
            mod_workspace_path=None,
            game_running=self.process,
            allow_unrecorded_workspace_adoption=False,
            cleanup_legacy_proxy=False,
        )
        self.assertFalse(probe.game_path_valid)
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_malformed_recorded_cleanup_path_stays_pending(self):
        record = {"game_executable": "removed/HTGame.exe", "deployed_sha256": "a" * 64}
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        self.runtime.cleanup()
        self.clean.assert_not_called()
        self.assertEqual(self.policy.deployment_record, record)
        self.assertTrue(self.policy.settings.pending_cleanup)
        self.assertIn("无效", self.runtime.cleanup_detail)

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

    def test_native_cleanup_with_removed_executable_cleans_recorded_files_and_workspace(self):
        record = {
            "deployment_layout": "native-capture-v1",
            "game_executable": str(self.root / "removed" / "HTGame.exe"),
            "managed_files": {"d3d12.dll": "a" * 64},
            "native_workspace_root": str(self.root / "workspace"),
        }
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        native_cleanup = self.stub(
            "cleanup_native_plugin",
            return_value=SimpleNamespace(status="cleaned", detail="native cleaned"),
        )
        self.runtime.cleanup()
        native_cleanup.assert_called_once_with(
            game_executable_path=record["game_executable"],
            managed_files=record["managed_files"],
            game_running=self.process,
        )
        self.loader.restore_native_workspace_record.assert_called_once()
        self.loader.cleanup_native_workspace.assert_called_once()
        self.assertEqual(self.policy.deployment_record, {"loading_method": "native-capture"})
        self.assertFalse(self.policy.settings.pending_cleanup)

    def test_explicit_native_cleanup_also_retires_leftover_legacy_proxy(self):
        (self.root / "dwmapi.dll").write_bytes(b"old proxy")
        old = self.root / "old-workspace"
        current = self.root / "registered-workspace"
        record = {
            "deployment_layout": "native-capture-v1",
            "game_executable": str(self.game),
            "managed_files": {"d3d12.dll": "a" * 64},
            "workspace_path": str(old),
        }
        self.policy.update_deployment(record)
        self.policy.set_cleanup_pending(True)
        self.stub("cleanup_native_plugin", return_value=SimpleNamespace(status="cleaned", detail="native cleaned"))
        self.stub("mod_workspace_registry_snapshot", return_value=(True, str(current)))

        def clean_legacy(**kwargs):
            stored = json.loads((self.root / "work-mode.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["deployment"]["workspace_path"], str(current))
            self.assertEqual(kwargs["mod_workspace_path"], str(current))
            self.assertTrue(kwargs["cleanup_legacy_proxy"])
            return SimpleNamespace(status="cleaned", detail="legacy cleaned")

        self.clean.side_effect = clean_legacy
        self.runtime.cleanup(allow_unrecorded_legacy_workspace=True)
        self.assertFalse(self.policy.settings.pending_cleanup)
        self.assertEqual(self.policy.deployment_record, {"loading_method": "native-capture"})

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


    def test_automatic_management_waits_for_manifest_or_game_exit(self):
        self.enable_auto()
        self.runtime._bundle = SimpleNamespace(ready=False)
        self.runtime._automatic_deploy(False)
        self.assertIn("核对", self.runtime.cleanup_detail)
        self.runtime._bundle = self.bundle_value
        self.runtime._automatic_deploy(True)
        self.assertIn("等待游戏退出", self.runtime.cleanup_detail)
        self.deploy.assert_not_called()

    def test_automatic_management_replaces_fixed_names_without_prior_hash_registration(self):
        self.enable_auto()
        self.deploy.return_value = self.deployment()
        self.deployed_value.files = {
            "d3d12.dll": SimpleNamespace(present=True, sha256="b" * 64,
                matches_bundle=False, matches_record=False, matches_predecessor=False),
            "NTE_Capture.dll": SimpleNamespace(present=False, sha256=None,
                matches_bundle=False, matches_record=False, matches_predecessor=False),
        }
        self.runtime._automatic_deploy(False)
        self.deploy.assert_called_once()
        self.assertEqual({"d3d12.dll": "b" * 64, "NTE_Capture.dll": None},
                         self.deploy.call_args.kwargs["expected_existing_files"])
        self.assertEqual("a" * 64, self.policy.deployment_record["deployed_sha256"])
        self.assertIn("已部署", self.runtime.cleanup_detail)

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
        self.runtime._native_deployed = self.deployed_value
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
        self.native.inspect.reset_mock()
        self.policy.set_auto_sync_enabled(True)
        with patch.object(self.runtime, "_inspect_component_files"), patch.object(self.runtime, "_automatic_deploy"):
            self.runtime.tick()
        self.native.inspect.assert_called_once_with(refresh=False, check_equipment=False)
        self.deploy.assert_not_called()
