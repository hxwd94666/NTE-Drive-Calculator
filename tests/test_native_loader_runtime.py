# 验证原生 Loader 从模式入口启动、持久化及退出后按所有权清理。
import pytest
from src.services.mod_plugin_loading_service import (
    ModPluginLoadingService, ModPluginLoadingError, ModPluginLoadingWaiting,
)
from tests.test_native_plugin_runtime import setup_runtime
from tests.test_native_mod_plugin_loading_service import Runtime


def setup_loader(tmp_path, monkeypatch):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch, deployed=False)
    # The helper fixture supplies an executable in this directory.
    launcher = game.parent / 'NTELauncher.exe'
    launcher.write_bytes(b'synthetic launcher')
    monkeypatch.setattr('src.services.mod_plugin_loading_service.game_launcher_executable', lambda _game: launcher)
    monkeypatch.setattr('src.services.mod_plugin_loading_service.game_launcher_candidates', lambda _game: (launcher,))
    process = Runtime()
    service = ModPluginLoadingService(application_root=runtime.root, runtime=process,
        native_workspace_path=runtime.config_dir / 'native-loader',
        operation_guard=policy.require, game_running=lambda: False)
    monkeypatch.setattr(service, '_require_msvc_runtime', lambda: None)
    runtime.loader = service
    policy.update_deployment({'loading_method': 'loader'})
    return runtime, policy, game, process


def test_running_selected_launcher_blocks_loader_before_workspace_changes(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    monkeypatch.setattr('src.services.mod_plugin_loading_service.selected_launcher_running', lambda _path: True)
    with pytest.raises(ModPluginLoadingWaiting, match='启动器'):
        runtime.start_native_loader()
    assert not process.calls
    assert not runtime.loader.native_workspace_path.exists()
    assert not policy.settings.pending_cleanup


def test_secondary_selected_launcher_also_blocks_loader(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    primary = tmp_path / 'NTELauncher.exe'
    secondary = tmp_path / 'NTEGlobalLauncher.exe'
    monkeypatch.setattr('src.services.mod_plugin_loading_service.game_launcher_candidates',
                        lambda _game: (primary, secondary))
    monkeypatch.setattr('src.services.mod_plugin_loading_service.selected_launcher_running',
                        lambda path: path == secondary)
    with pytest.raises(ModPluginLoadingWaiting, match='启动器'):
        runtime.start_native_loader()
    assert not process.calls
    assert not runtime.loader.native_workspace_path.exists()
    assert not policy.settings.pending_cleanup


def test_manual_start_persists_actual_stage_and_offline_cleans_it(tmp_path, monkeypatch):
    runtime, policy, game, process = setup_loader(tmp_path, monkeypatch)
    runtime.start_native_loader()
    record = policy.deployment_record
    assert record['loading_method'] == 'loader'
    assert len(record['native_workspace_files']) == 1
    assert not (game.parent / 'd3d12.dll').exists()
    assert process.calls[0]['payload_load_mode'] == 'loadlibrary'
    assert runtime.tick().native_load.files
    policy.select_mode('offline')
    runtime.cleanup()
    assert policy.deployment_record == {'loading_method': 'loader'}
    assert not policy.settings.pending_cleanup
    assert not (runtime.loader.native_workspace_path / 'NTE_Capture.dll').exists()


@pytest.mark.parametrize('automatic', [False, True])
def test_only_manual_loader_start_retires_old_proxy_on_start_and_repeat(tmp_path, monkeypatch, automatic):
    runtime, _, game, process = setup_loader(tmp_path, monkeypatch)
    target = game.parent / 'dwmapi.dll'
    for contents in (b'old proxy', b'proxy reappeared while loader waiting'):
        target.write_bytes(contents)
        runtime.start_native_loader(automatic=automatic)
        assert target.exists() == automatic
        if automatic:
            assert target.read_bytes() == contents
        assert not list(runtime.config_dir.rglob('dwmapi.dll.bak'))
    assert len(process.calls) == 1


def test_launch_failure_retains_stage_record_for_cleanup(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    process.fail = True
    with pytest.raises(ModPluginLoadingError): runtime.start_native_loader()
    assert policy.settings.pending_cleanup
    assert len(policy.deployment_record['native_workspace_files']) == 1
    runtime.cleanup()
    assert not policy.settings.pending_cleanup


def test_unsupported_loader_keeps_owned_game_files_untouched(tmp_path, monkeypatch):
    runtime, policy, game, process = setup_loader(tmp_path, monkeypatch)
    owned = game.parent / 'd3d12.dll'
    owned.write_bytes(b'owned host')
    policy.update_deployment({'loading_method': 'loader', 'deployment_layout': 'native-capture-v1',
                              'game_executable': str(game), 'managed_files': {'d3d12.dll': 'a' * 64}})
    before = policy.deployment_record
    process.supported = False
    with pytest.raises(ModPluginLoadingError): runtime.start_native_loader()
    assert policy.deployment_record == before
    assert owned.read_bytes() == b'owned host'
    assert not runtime.loader.native_workspace_path.exists()


def test_automatic_start_uses_same_service_and_preserves_loader_selection(tmp_path, monkeypatch):
    runtime, policy, game, process = setup_loader(tmp_path, monkeypatch)
    runtime.tick()
    assert len(process.calls) == 1
    assert policy.deployment_record['loading_method'] == 'loader'
    assert not (game.parent / 'd3d12.dll').exists()
    runtime.tick()
    assert len(process.calls) == 1


def test_stage_cleanup_does_not_require_removed_game_executable(tmp_path, monkeypatch):
    runtime, policy, game, _process = setup_loader(tmp_path, monkeypatch)
    runtime.start_native_loader()
    game.unlink()
    policy.select_mode('offline')
    runtime.cleanup()
    assert policy.deployment_record == {'loading_method': 'loader'}
    assert not policy.settings.pending_cleanup


def test_retry_cleans_pending_stage_before_start_and_does_not_stop_on_next_tick(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    process.fail = True
    with pytest.raises(ModPluginLoadingError): runtime.start_native_loader()
    process.fail = False
    runtime.start_native_loader()
    assert not policy.settings.pending_cleanup
    runtime.tick()
    assert process.phase == 'running'


def test_revocation_after_start_stops_loader_immediately_and_keeps_cleanup(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    start = process.start
    def revoked(**kwargs):
        result = start(**kwargs)
        policy.select_mode('offline')
        return result
    process.start = revoked
    with pytest.raises(PermissionError): runtime.start_native_loader()
    assert process.phase == 'stopped'
    assert policy.settings.pending_cleanup
    assert len(policy.deployment_record['native_workspace_files']) == 1


def test_context_change_during_preflight_never_cleans_d3d(tmp_path, monkeypatch):
    runtime, policy, game, process = setup_loader(tmp_path, monkeypatch)
    owned = game.parent / 'd3d12.dll'
    owned.write_bytes(b'owned host')
    policy.update_deployment({'loading_method': 'loader', 'deployment_layout': 'native-capture-v1',
                              'game_executable': str(game), 'managed_files': {'d3d12.dll': 'a' * 64}})
    process.require_payload_load_mode = lambda _mode: policy.set_game_executable(str(game.parent / "changed-game.exe"))
    with pytest.raises(PermissionError): runtime.start_native_loader()
    assert owned.read_bytes() == b'owned host'
    assert not runtime.loader.native_workspace_path.exists()


def test_switch_during_pending_cleanup_is_preserved_and_cancels_old_start(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    process.fail = True
    with pytest.raises(ModPluginLoadingError): runtime.start_native_loader()
    process.fail = False
    stop = runtime.loader.stop_loader
    def switch():
        stop()
        record = policy.deployment_record
        record['loading_method'] = 'native-capture'
        policy.update_deployment(record)
    monkeypatch.setattr(runtime.loader, 'stop_loader', switch)
    previous_starts = len(process.calls)
    with pytest.raises(PermissionError): runtime.start_native_loader()
    assert policy.deployment_record['loading_method'] == 'native-capture'
    assert len(process.calls) == previous_starts
    assert process.phase == 'stopped'


@pytest.mark.parametrize("automatic", [False, True])
def test_auto_sync_toggle_after_loader_start_keeps_loader_running(tmp_path, monkeypatch, automatic):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    start = process.start

    def toggle(**kwargs):
        result = start(**kwargs)
        policy.set_auto_sync_enabled(False)
        return result

    process.start = toggle
    runtime.start_native_loader(automatic=automatic)
    assert process.phase == "running"
    assert not policy.settings.auto_sync_enabled
    assert not policy.settings.pending_cleanup
    assert policy.deployment_record["native_workspace_files"]


def test_auto_sync_toggle_during_pending_cleanup_allows_loader_retry(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    process.fail = True
    with pytest.raises(ModPluginLoadingError):
        runtime.start_native_loader()
    process.fail = False
    stop = runtime.loader.stop_loader

    def toggle():
        stop()
        policy.set_auto_sync_enabled(False)

    monkeypatch.setattr(runtime.loader, "stop_loader", toggle)
    runtime.start_native_loader()
    assert process.phase == "running"
    assert not policy.settings.auto_sync_enabled
    assert not policy.settings.pending_cleanup


def test_deployment_ownership_change_after_loader_start_still_stops_it(tmp_path, monkeypatch):
    runtime, policy, _game, process = setup_loader(tmp_path, monkeypatch)
    start = process.start

    def change(**kwargs):
        result = start(**kwargs)
        record = policy.deployment_record
        record["ownership_changed"] = True
        policy.update_deployment(record)
        return result

    process.start = change
    with pytest.raises(PermissionError):
        runtime.start_native_loader()
    assert process.phase == "stopped"
    assert policy.settings.pending_cleanup
