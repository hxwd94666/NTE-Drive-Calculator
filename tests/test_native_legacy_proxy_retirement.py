# 验证自动管理保留旧代理，手动部署按显式授权清理旧代理。
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.integrations import legacy_game_proxy as proxy
from src.services.native_plugin_deployment import deploy_native_plugin
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from tests.test_native_automatic_upgrade import setup_upgrade
from tests.test_native_plugin_bundle import game_files, make_bundle


def test_manual_deploy_removes_proxy_without_backup(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    target = game.parent / 'dwmapi.dll'
    target.write_bytes(b'old proxy without ownership record')
    result = deploy_native_plugin(
        application_root=root, game_executable_path=game,
        operation_guard=lambda _: None,
        game_running=lambda: False,
        cleanup_legacy_proxy=True,
    )
    assert not target.exists()
    assert not list((tmp_path / 'backups').rglob('dwmapi.dll.bak'))
    assert set(result.managed_files) == {'d3d12.dll', 'NTE_Capture.dll'}


def test_automatic_deploy_ignores_proxy_when_new_files_match(tmp_path):
    runtime, _, game, payload, _ = setup_upgrade(tmp_path)
    (game.parent / 'd3d12.dll').write_bytes((runtime.root / payload['roles']['host']).read_bytes())
    target = game.parent / 'dwmapi.dll'
    target.write_bytes(b'residual proxy')
    from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
    runtime._native_deployed = inspect_deployed_native_plugin(
        application_root=runtime.root, game_executable_path=game,
    )
    assert runtime._native_deployed.files_compatible
    with patch('src.services.work_mode_runtime.deploy_native_plugin') as deploy:
        runtime._automatic_deploy(False)
    deploy.assert_not_called()
    assert target.read_bytes() == b'residual proxy'
    assert not list(runtime.config_dir.rglob('dwmapi.dll.bak'))


def test_proxy_delete_failure_blocks_new_component_writes(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    target = game.parent / 'dwmapi.dll'
    target.write_bytes(b'old proxy')
    original = Path.unlink
    def blocked(path, *args, **kwargs):
        if path == target:
            raise PermissionError('proxy locked')
        return original(path, *args, **kwargs)
    with patch.object(Path, 'unlink', blocked):
        with patch('src.services.native_plugin_deployment._replace_file') as replace:
            with pytest.raises(PermissionError):
                deploy_native_plugin(
                    application_root=root, game_executable_path=game,
                    operation_guard=lambda _: None,
                    game_running=lambda: False,
                    cleanup_legacy_proxy=True,
                )
            replace.assert_not_called()
    assert target.read_bytes() == b'old proxy'


def test_failed_native_write_does_not_restore_removed_proxy(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    target = game.parent / 'dwmapi.dll'
    target.write_bytes(b'old proxy')
    with patch('src.services.native_plugin_deployment._replace_file', side_effect=OSError('write failed')):
        with pytest.raises(EquipmentPluginDeploymentError):
            deploy_native_plugin(
                application_root=root, game_executable_path=game,
                operation_guard=lambda _: None,
                game_running=lambda: False,
                cleanup_legacy_proxy=True,
            )
    assert not target.exists()
    assert not list((tmp_path / 'backups').rglob('dwmapi.dll.bak'))


def test_running_game_does_not_retire_proxy(tmp_path):
    runtime, _, game, _, _ = setup_upgrade(tmp_path, running=True)
    target = game.parent / 'dwmapi.dll'
    target.write_bytes(b'old proxy')
    runtime._automatic_deploy(True)
    assert target.read_bytes() == b'old proxy'
    assert not list(runtime.config_dir.rglob('dwmapi.dll.bak'))


@pytest.mark.parametrize('name', ['dwmapi.dll', 'dxgi.dll', 'NTE-Platform.dll', 'unrelated.dll'])
def test_automatic_upgrade_and_offline_cleanup_preserve_unrelated_dll(tmp_path, name):
    runtime, policy, game, _, _ = setup_upgrade(tmp_path)
    target = game.parent / name
    target.write_bytes(b'other tool')
    runtime._automatic_deploy(False)
    assert policy.deployment_record['managed_files']
    assert target.read_bytes() == b'other tool'
    policy.select_mode('offline')
    runtime.loader.stop_loader = Mock()
    runtime.cleanup(running=False)
    assert target.read_bytes() == b'other tool'


def test_automatic_upgrade_does_not_inspect_unmanaged_proxy_directory(tmp_path):
    runtime, policy, game, _, _ = setup_upgrade(tmp_path)
    target = game.parent / 'dwmapi.dll'
    target.mkdir()
    runtime._automatic_deploy(False)
    assert target.is_dir() and policy.deployment_record['managed_files']


@pytest.mark.parametrize('change', ['replace', 'game_started', 'revoked'])
@pytest.mark.parametrize('change_at', [2, 3])
def test_changes_before_delete_preserve_game_file(tmp_path, change, change_at):
    game = tmp_path / 'game'
    game.mkdir()
    target = game / 'dwmapi.dll'
    target.write_bytes(b'original')
    checks = 0
    def idle():
        nonlocal checks
        checks += 1
        if checks == change_at:
            if change == 'replace':
                target.write_bytes(b'external replacement')
            else:
                raise PermissionError(change)
    with pytest.raises(OSError):
        proxy.remove_legacy_game_proxy(game_directory=game, require_idle=idle)
    assert target.read_bytes() == (b'external replacement' if change == 'replace' else b'original')


def test_directory_is_not_removed(tmp_path):
    game = tmp_path / 'game'
    (game / 'dwmapi.dll').mkdir(parents=True)
    with pytest.raises(OSError, match='普通文件'):
        proxy.remove_legacy_game_proxy(game_directory=game, require_idle=lambda: None)
    assert (game / 'dwmapi.dll').is_dir()


def test_link_is_not_followed_or_deleted(tmp_path):
    game = tmp_path / 'game'
    game.mkdir()
    outside = tmp_path / 'external.dll'
    outside.write_bytes(b'external file')
    link = game / 'dwmapi.dll'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation unavailable')
    with pytest.raises(OSError, match='普通文件'):
        proxy.remove_legacy_game_proxy(game_directory=game, require_idle=lambda: None)
    assert link.is_symlink() and outside.read_bytes() == b'external file'
