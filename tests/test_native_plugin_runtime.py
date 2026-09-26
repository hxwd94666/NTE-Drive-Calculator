# 以合成整包与临时游戏目录验证原生模式检测、自动部署和加载方式隔离。
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from src.services import work_mode_runtime as module
from src.services.work_mode_service import WorkModeService
from src.services.work_mode_checks import build_work_mode_report
from tests.test_native_plugin_bundle import make_bundle, game_files


def setup_runtime(tmp_path, monkeypatch, *, deployed=True):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    if not deployed:
        for relative in module.inspect_deployed_native_plugin(application_root=root, game_executable_path=game).files:
            (game.parent / relative).unlink()
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    policy.enable_auto_sync_after_preflight()
    policy.set_game_executable(str(game))
    policy.set_cleanup_pending(False)
    native = SimpleNamespace(battle_active=False, close=Mock())
    loader = SimpleNamespace(active_payload_sha256="", pending_workspace_cleanup_path=None,
                             stop_loader=Mock(), snapshot=Mock(return_value=SimpleNamespace(phase="stopped")))
    runtime = module.WorkModeRuntime(policy=policy, native_session=native, loader=loader,
                                    application_root=root, config_dir=tmp_path / "config", game_running=lambda: False)
    monkeypatch.setattr(module, "create_bundled_analysis_client", lambda **_kw: None)
    monkeypatch.setattr(module, "resolve_nte_core_executable", lambda: game)
    monkeypatch.setattr(module, "npcap_installation_present", lambda: False)
    monkeypatch.setattr(module, "find_spec", lambda _name: None)
    old_deploy = Mock(side_effect=AssertionError("legacy deployment must not run"))
    assert not hasattr(module, "deploy_plugin")
    return runtime, policy, game, old_deploy


def test_native_files_enable_snapshot_domains_without_fabricating_battle_or_equipment(tmp_path, monkeypatch):
    runtime, policy, _game, old_deploy = setup_runtime(tmp_path, monkeypatch)
    probe = runtime.tick()
    assert probe.native_load.files and probe.native_inventory.files and probe.native_character.files
    assert probe.native_battle.supported is False and probe.native_equipment.supported is False
    report = build_work_mode_report(policy.settings, probe)
    rows = {row.feature: row for row in report.features}
    assert "未提供此项功能" in rows["native_battle"].detail
    assert "未提供此项功能" in rows["native_equipment"].detail
    assert rows["native_inventory"].detail == "等待启动游戏。"
    old_deploy.assert_not_called()


def test_automatic_native_deployment_writes_actual_record_and_cleans_only_owned_files(tmp_path, monkeypatch):
    runtime, policy, game, old_deploy = setup_runtime(tmp_path, monkeypatch, deployed=False)
    runtime.tick()
    record = policy.deployment_record
    assert record["loading_method"] == "native-capture" and len(record["managed_files"]) == 2
    assert all((game.parent / relative).is_file() for relative in record["managed_files"])
    runtime.native_session.close.assert_called()
    old_deploy.assert_not_called()
    other = game.parent / "other-tool.dll"
    other.write_bytes(b"unrelated")
    policy.select_mode("offline")
    runtime.cleanup(running=False)
    assert not policy.settings.pending_cleanup
    assert all(not (game.parent / relative).exists() for relative in record["managed_files"])
    assert other.read_bytes() == b"unrelated"


def test_manual_native_deployment_rejects_unresolved_cleanup_before_replacing_record(tmp_path, monkeypatch):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch, deployed=False)
    runtime.tick()
    record = policy.deployment_record
    target = game.parent / "NTE_Capture.dll"
    target.unlink()
    target.mkdir()
    policy.set_paused(True)
    policy.set_cleanup_pending(True)
    with pytest.raises(module.EquipmentPluginDeploymentError):
        runtime.prepare_manual_native_deployment(expected_operation_revision=policy.operation_revision)
    assert policy.settings.pending_cleanup and policy.deployment_record == record
    assert target.is_dir()


def test_manual_native_redeploy_consumes_finished_cleanup_and_survives_next_tick(tmp_path, monkeypatch):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch, deployed=False)
    runtime.tick()
    policy.set_paused(True)
    policy.set_cleanup_pending(True)
    revision = runtime.prepare_manual_native_deployment(expected_operation_revision=policy.operation_revision)
    assert revision == policy.operation_revision and not policy.settings.pending_cleanup
    deployed = module.deploy_native_plugin(application_root=runtime.root, game_executable_path=game,
        operation_guard=policy.require, game_running=lambda: False)
    runtime.save_deployment(deployed)
    runtime.tick()
    assert not policy.settings.pending_cleanup
    assert (game.parent / "NTE_Capture.dll").is_file() and (game.parent / "d3d12.dll").is_file()


def test_loader_choice_never_runs_d3d_automatic_deployment(tmp_path, monkeypatch):
    runtime, policy, game, old_deploy = setup_runtime(tmp_path, monkeypatch, deployed=False)
    policy.update_deployment({"loading_method": "loader"})
    native_deploy = Mock(side_effect=AssertionError("D3D must not override Loader"))
    monkeypatch.setattr(module, "deploy_native_plugin", native_deploy)
    start = Mock()
    monkeypatch.setattr(runtime, "start_native_loader", start)
    runtime.tick()
    start.assert_called_once_with(automatic=True)
    assert policy.deployment_record["loading_method"] == "loader"
    assert not (game.parent / "d3d12.dll").exists()
    native_deploy.assert_not_called()
    old_deploy.assert_not_called()


def test_manual_deployment_ui_ignores_config_saves_and_cleans_old_pending_record(tmp_path, monkeypatch):
    from src.ui.controllers import native_plugin_deployment_ui as ui
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch, deployed=False)
    runtime.tick()
    policy.set_cleanup_pending(True)
    original_revision = policy.operation_revision
    assert policy.settings.revision != original_revision
    window = SimpleNamespace(
        app_context=SimpleNamespace(paths=SimpleNamespace(root=runtime.root)),
        native_game_session=runtime.native_session, _mod_plugin_loading_service=runtime.loader,
        work_mode_service=policy, work_mode_runtime=runtime,
        operation_generation=lambda: (policy.operation_revision, 1),
        _stop_inventory_sync=Mock(), character_profile_sync_controller=SimpleNamespace(request_stop=Mock()),
        _refresh_equipment_plugin_status=Mock(), operation_unavailable=Mock(),
        work_mode_controller=SimpleNamespace(component_state_changed=Mock()),
    )
    def confirm(*_args):
        policy.update_deployment(policy.deployment_record)
        policy.set_auto_sync_enabled(False)
        return True
    monkeypatch.setattr(ui, '_confirm_d3d_deployment', confirm)
    information = Mock()
    monkeypatch.setattr(ui.QMessageBox, 'information', information)
    monkeypatch.setattr(ui, 'deploy_native_plugin',
                        lambda **kwargs: module.deploy_native_plugin(**kwargs, game_running=lambda: False))
    ui.deploy_native_plugin_from_settings(window)
    information.assert_called_once()
    window.work_mode_controller.component_state_changed.assert_called_once_with()
    window.operation_unavailable.assert_not_called()
    assert not policy.settings.pending_cleanup
    assert policy.operation_revision == original_revision
    assert (game.parent / 'NTE_Capture.dll').is_file()
    assert (game.parent / 'd3d12.dll').is_file()


@pytest.mark.parametrize('change', ['mode', 'path', 'pause'])
def test_manual_deployment_still_rejects_changed_operation_authority(tmp_path, monkeypatch, change):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch)
    revision = policy.operation_revision
    if change == 'mode':
        policy.select_mode('low', risk_confirmed=True)
    elif change == 'path':
        policy.set_game_executable(str(game.parent / 'other' / 'HTGame.exe'))
    else:
        policy.set_paused(True)
    with pytest.raises(PermissionError):
        runtime.prepare_manual_native_deployment(expected_operation_revision=revision)


def test_invalid_native_bundle_is_not_usable_even_with_previously_matching_files(tmp_path, monkeypatch):
    runtime, _policy, _game, old_deploy = setup_runtime(tmp_path, monkeypatch)
    (runtime.root / "native/nte-core.exe").write_bytes(b"changed")
    probe = runtime.tick()
    assert not probe.native_load.files and not probe.core_available
    assert "不匹配" in probe.component_update_detail
    old_deploy.assert_not_called()


def test_partial_upgrade_keeps_previously_owned_unwritten_host_for_cleanup(tmp_path, monkeypatch):
    from src.services.native_plugin_deployment import NativePluginDeployment
    runtime, policy, game, _old_deploy = setup_runtime(tmp_path, monkeypatch)
    old_files = {"d3d12.dll": "a" * 64}
    policy.update_deployment({"loading_method": "native-capture", "game_executable": str(game), "managed_files": old_files})
    partial = NativePluginDeployment(game, game.parent / "d3d12.dll", "", game.parent,
                                     None, {"NTE_Capture.dll": "c" * 64})
    runtime.save_pending_deployment(module.PluginDeploymentPendingCleanup("revoked", deployment=partial))
    assert policy.settings.pending_cleanup
    assert policy.deployment_record["managed_files"] == {**old_files, **partial.managed_files}


def test_selecting_loader_after_d3d_deployment_does_not_change_cleanup_ownership(tmp_path, monkeypatch):
    from src.ui.controllers.mod_loader_controller import equipment_plugin_loading_method_changed
    runtime, policy, game, _old_deploy = setup_runtime(tmp_path, monkeypatch, deployed=False)
    runtime.tick()
    recorded = dict(policy.deployment_record["managed_files"])
    window = SimpleNamespace(
        _equipment_plugin_loading_method_combo=SimpleNamespace(currentData=lambda: "loader"),
        _mod_plugin_loading_service=runtime.loader, work_mode_service=policy,
        work_mode_runtime=runtime, _refresh_equipment_plugin_status=Mock(),
    )
    equipment_plugin_loading_method_changed(window, 1)
    assert policy.deployment_record["loading_method"] == "loader"
    assert policy.deployment_record["deployment_layout"] == "native-capture-v1"
    policy.select_mode("offline")
    legacy_cleanup = Mock(side_effect=AssertionError("cleanup must follow deployed layout"))
    monkeypatch.setattr(module, "cleanup_managed_plugin", legacy_cleanup)
    runtime.cleanup(running=False)
    assert all(not (game.parent / relative).exists() for relative in recorded)
    assert not policy.settings.pending_cleanup
    assert policy.deployment_record == {"loading_method": "loader"}
    runtime.loader.stop_loader.assert_called_once()
    legacy_cleanup.assert_not_called()


def test_offline_cleans_upgraded_native_files_despite_old_deployment_hash(tmp_path, monkeypatch):
    from src.ui.controllers.mod_loader_controller import equipment_plugin_loading_method_changed
    runtime, policy, game, _old_deploy = setup_runtime(tmp_path, monkeypatch, deployed=False)
    runtime.tick()
    window = SimpleNamespace(
        _equipment_plugin_loading_method_combo=SimpleNamespace(currentData=lambda: "loader"),
        _mod_plugin_loading_service=runtime.loader, work_mode_service=policy,
        work_mode_runtime=runtime, _refresh_equipment_plugin_status=Mock(),
    )
    equipment_plugin_loading_method_changed(window, 1)
    policy.select_mode("offline")
    (game.parent / "NTE_Capture.dll").write_bytes(b"changed outside application")
    runtime.cleanup(running=False)
    assert not policy.settings.pending_cleanup
    assert policy.deployment_record == {"loading_method": "loader"}
    assert not (game.parent / "d3d12.dll").exists()
    assert not (game.parent / "NTE_Capture.dll").exists()


def test_d3d_autodeploy_does_not_require_optional_loader_binary(tmp_path, monkeypatch):
    from src.services.mod_plugin_loading_service import ModPluginLoadingService
    runtime, policy, game, _old_deploy = setup_runtime(tmp_path, monkeypatch, deployed=False)
    monkeypatch.delenv('NTE_MOD_LOADER_EXE', raising=False)
    runtime.loader = ModPluginLoadingService(application_root=runtime.root,
        native_workspace_path=runtime.config_dir / 'native-loader',
        operation_guard=policy.require, game_running=lambda: False)
    runtime.tick()
    assert (game.parent / 'd3d12.dll').exists()
    assert len(policy.deployment_record['managed_files']) == 2
    assert runtime.loader.snapshot().phase == 'missing_loader'


def test_auto_sync_toggle_during_native_deployment_does_not_cancel_component_write(tmp_path, monkeypatch):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch, deployed=False)
    deploy = module.deploy_native_plugin

    def toggle_then_deploy(**kwargs):
        policy.set_auto_sync_enabled(False)
        return deploy(**kwargs)

    monkeypatch.setattr(module, "deploy_native_plugin", toggle_then_deploy)
    runtime.tick()
    assert not policy.settings.auto_sync_enabled
    assert (game.parent / "d3d12.dll").is_file()
    assert len(policy.deployment_record["managed_files"]) == 2


@pytest.mark.parametrize("change", ["deployment", "mode_round_trip"])
def test_native_deployment_still_cancels_ownership_or_authority_change(tmp_path, monkeypatch, change):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch, deployed=False)
    deploy = module.deploy_native_plugin

    def change_then_deploy(**kwargs):
        if change == "deployment":
            policy.update_deployment({"loading_method": "loader"})
        else:
            policy.select_mode("offline")
            policy.select_mode("medium", risk_confirmed=True)
            policy.set_cleanup_pending(False)
        return deploy(**kwargs)

    monkeypatch.setattr(module, "deploy_native_plugin", change_then_deploy)
    runtime.tick()
    assert not (game.parent / "d3d12.dll").exists()
    assert "上下文已改变" in runtime.cleanup_detail
