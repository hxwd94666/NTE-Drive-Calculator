# 用临时无界面采集整包验证 Loader 单 DLL 目录、能力门禁、写入记录和清理边界。
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations.mod_loader import ModLoaderRuntimeError, ModLoaderRuntimeSnapshot, packaged_mod_loader
from src.integrations.native_plugin_bundle import NATIVE_PLUGIN_DEPLOYMENT_PATHS
from src.services import native_plugin_deployment as files
from src.services.mod_plugin_loading_service import ModPluginLoadingError, ModPluginLoadingService
from tests.test_native_plugin_bundle import make_bundle


class Runtime:
    def __init__(self):
        self.supported = True
        self.mode_checks = []
        self.calls = []
        self.phase = 'stopped'
        self.fail = False

    def require_payload_load_mode(self, mode):
        self.mode_checks.append(mode)
        if not self.supported:
            raise ModLoaderRuntimeError('此 Loader 未声明标准加载能力')

    def snapshot(self, *, payload_path):
        payload = Path(payload_path)
        phase = self.phase if payload.is_file() else 'missing_payload'
        return ModLoaderRuntimeSnapshot(phase, Path('replaceable-loader.exe'), payload)

    def start(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail: raise ModLoaderRuntimeError('synthetic launch failure')
        self.phase = 'running'
        return self.snapshot(payload_path=kwargs['payload_path'])

    def stop(self, **kwargs):
        self.phase = 'stopped'
        return True


@pytest.fixture
def loading(tmp_path, monkeypatch):
    root, payload = make_bundle(tmp_path)
    install = tmp_path / 'game'
    executable = install / 'Client/WindowsNoEditor/HT/Binaries/Win64/HTGame.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'synthetic game')
    (install / 'NTELauncher.exe').write_bytes(b'synthetic launcher')
    runtime = Runtime()
    state = {'allowed': True, 'running': False}
    def guard(_cap):
        if not state['allowed']: raise PermissionError('revoked')
    workspace = root / 'config/native-loader'
    service = ModPluginLoadingService(application_root=root, runtime=runtime,
                                      operation_guard=guard, game_running=lambda: state['running'],
                                      native_workspace_path=workspace)
    monkeypatch.setattr(service, '_require_msvc_runtime', lambda: None)
    args = dict(game_executable_path=executable, writable_workspace_path=workspace)
    return service, runtime, state, args, root, payload


def test_native_snapshot_uses_missing_stage_without_legacy_payload_lookup(loading):
    service, runtime, _state, _args, _root, _payload = loading
    assert service.snapshot().phase == 'missing_payload'
    assert not service.native_workspace_files_compatible
    assert runtime.calls == []


def test_native_first_start_prepares_only_capture_dll_and_uses_standard_mode(loading):
    service, runtime, _state, args, root, payload = loading
    unrelated = args['game_executable_path'].parent / 'dwmapi.dll'
    unrelated.write_bytes(b'other tool')
    result = service.start_loader(**args)
    assert result.native_workspace == service.native_workspace_record
    assert service.native_workspace_files_compatible
    assert runtime.calls[0]['payload_load_mode'] == 'loadlibrary'
    assert runtime.calls[0]['payload_path'] == service.native_workspace_path / 'NTE_Capture.dll'
    relative = NATIVE_PLUGIN_DEPLOYMENT_PATHS['capture_plugin']
    assert relative == 'NTE_Capture.dll'
    assert (service.native_workspace_path / relative).read_bytes() == (root / payload['roles']['capture_plugin']).read_bytes()
    assert service.active_payload_sha256 == service.native_workspace_record.managed_files[relative]
    assert {file.name for file in service.native_workspace_path.iterdir()} == {'NTE_Capture.dll'}
    assert set(service.native_workspace_record.managed_files) == {'NTE_Capture.dll'}
    for deployed_path in NATIVE_PLUGIN_DEPLOYMENT_PATHS.values():
        assert not (args['game_executable_path'].parent / deployed_path).exists()
    assert unrelated.read_bytes() == b'other tool'
    assert not list((root / 'config/native-loader-backups').rglob('dwmapi.dll.bak'))


def test_readonly_support_preflight_and_unsupported_loader_have_no_writes(loading):
    service, runtime, _state, args, root, _payload = loading
    service.require_native_loader_supported()
    assert runtime.mode_checks == ['loadlibrary']
    assert not service.native_workspace_path.exists()
    runtime.supported = False
    with pytest.raises(ModPluginLoadingError, match='标准加载'):
        service.start_loader(**args)
    assert not service.native_workspace_path.exists()
    assert not (root / 'config/native-loader-backups').exists()
    assert runtime.calls == []


def test_running_game_blocks_preparation_and_start(loading):
    service, runtime, state, args, _root, _payload = loading
    state['running'] = True
    with pytest.raises(ModPluginLoadingError): service.start_loader(**args)
    assert not service.native_workspace_path.exists() and runtime.calls == []


def test_native_workspace_cannot_be_redirected_into_game(loading):
    service, runtime, _state, args, root, _payload = loading
    args['writable_workspace_path'] = args['game_executable_path'].parent
    with pytest.raises(ModPluginLoadingError, match='专用运行目录'):
        service.start_loader(**args)
    assert runtime.calls == []


def test_launch_failure_retains_prepared_files_for_root_persistence(loading):
    service, runtime, _state, args, root, _payload = loading
    runtime.fail = True
    with pytest.raises(ModPluginLoadingError): service.start_loader(**args)
    assert len(service.native_workspace_record.managed_files) == 1
    assert service.pending_native_workspace_path == service.native_workspace_path
    assert service.native_workspace_files_compatible


@pytest.mark.parametrize('previous', [False, True])
def test_revoke_preserves_actual_written_and_previous_unchanged_owned_files(loading, previous):
    service, runtime, state, args, _root, _payload = loading
    if previous:
        service.start_loader(**args)
        runtime.stop()
        (service.native_workspace_path / "NTE_Capture.dll").write_bytes(b"previous version")
    replace = files.os.replace
    def revoke(source, target):
        replace(source, target)
        state['allowed'] = False
    with patch.object(files.os, 'replace', side_effect=revoke):
        with pytest.raises(ModPluginLoadingError): service.start_loader(**args)
    expected = {'NTE_Capture.dll'}
    assert set(service.native_workspace_record.managed_files) == expected
    assert len(runtime.calls) == int(previous)


def test_stop_preserves_record_until_explicit_filename_cleanup(loading):
    service, _runtime, state, args, _root, _payload = loading
    service.start_loader(**args)
    record = service.native_workspace_record
    extra = service.native_workspace_path / 'unrelated.txt'
    extra.write_text('keep', encoding='utf-8')
    service.stop_loader()
    assert service.native_workspace_record == record
    state['running'] = True
    assert service.cleanup_native_workspace().status == 'waiting_game_exit'
    assert service.native_workspace_record == record
    state['running'] = False
    assert service.cleanup_native_workspace().status == 'cleaned'
    assert service.native_workspace_record is None and extra.read_text(encoding='utf-8') == 'keep'


def test_upgraded_owned_file_is_cleaned_by_filename(loading):
    service, _runtime, _state, args, _root, _payload = loading
    service.start_loader(**args)
    (service.native_workspace_path / 'NTE_Capture.dll').write_bytes(b'external replacement')
    assert service.cleanup_native_workspace().status == 'cleaned'
    assert service.native_workspace_record is None
    assert not (service.native_workspace_path / 'NTE_Capture.dll').exists()


def test_restore_rejects_outside_layout_record(loading):
    service, _runtime, _state, _args, _root, _payload = loading
    with pytest.raises(ModPluginLoadingError):
        service.restore_native_workspace_record(workspace_path=service.native_workspace_path,
                                                managed_files={'../outside.dll': 'a' * 64})
    assert service.native_workspace_record is None


def test_same_named_loader_replacement_remains_resolvable_without_hash_pin(loading, monkeypatch):
    _service, _runtime, _state, _args, root, _payload = loading
    loader = root / 'nte-mod-loader.exe'
    loader.write_bytes(b'user replacement')
    monkeypatch.delenv('NTE_MOD_LOADER_EXE', raising=False)
    assert packaged_mod_loader(root) == loader.resolve()
    loader.write_bytes(b'newer user replacement')
    assert packaged_mod_loader(root) == loader.resolve()


def test_retaining_new_writes_never_loses_old_cleanup_facts_on_read_failure(loading):
    service, _runtime, _state, args, _root, _payload = loading
    service.start_loader(**args)
    previous = service.native_workspace_record
    relative = 'NTE_Capture.dll'
    new_hash = 'a' * 64
    record = files.NativeComponentFilesDeployment(previous.directory, previous.backup_path, {relative: new_hash})
    with patch('src.services.native_plugin_deployment._digest', side_effect=OSError('synthetic file lock')):
        service._retain_native_workspace(record)
    expected = dict(previous.managed_files)
    expected[relative] = new_hash
    assert service.native_workspace_record.managed_files == expected
    assert service.cleanup_native_workspace().status == 'cleaned'
    assert service.native_workspace_record is None


def test_native_start_passes_live_revocation_guard_into_runtime(loading):
    service, runtime, state, args, _root, _payload = loading
    service.start_loader(**args)
    guard = runtime.calls[0]['launch_guard']
    guard()
    state['allowed'] = False
    with pytest.raises(PermissionError, match='revoked'):
        guard()


@pytest.mark.parametrize('relative', ['d3d12.dll', 'NTE-Platform.dll', 'NTE-DebugTools/plugins/NTE_PluginCombat.dll'])
def test_restore_rejects_non_capture_workspace_ownership(loading, relative):
    service, _runtime, _state, _args, _root, _payload = loading
    with pytest.raises(ModPluginLoadingError, match='无效文件或摘要'):
        service.restore_native_workspace_record(workspace_path=service.native_workspace_path,
                                                managed_files={relative: 'a' * 64})
    assert service.native_workspace_record is None


def test_capture_workspace_does_not_require_or_clean_d3d_host(loading):
    service, _runtime, _state, args, _root, _payload = loading
    service.native_workspace_path.mkdir(parents=True)
    host = service.native_workspace_path / 'd3d12.dll'
    host.write_bytes(b'unowned host remains untouched')
    assert not service.native_workspace_files_compatible
    service.start_loader(**args)
    assert service.native_workspace_files_compatible
    assert set(service.native_workspace_record.managed_files) == {'NTE_Capture.dll'}
    service.stop_loader()
    assert service.cleanup_native_workspace().status == 'cleaned'
    assert host.read_bytes() == b'unowned host remains untouched'
    assert not (service.native_workspace_path / 'NTE_Capture.dll').exists()


def test_current_loader_payload_is_reused_without_a_file_transaction(loading):
    service, runtime, _state, args, root, _payload = loading
    service.start_loader(**args)
    service.stop_loader()
    target = service.native_workspace_path / 'NTE_Capture.dll'
    before = target.stat().st_mtime_ns
    with patch('src.services.native_loader_workspace.deploy_native_component_files', side_effect=AssertionError('current payload must not be rewritten')):
        result = service.start_loader(**args)
    assert target.stat().st_mtime_ns == before
    assert result.native_workspace.backup_path is None
    assert service.native_workspace_files_compatible


def test_old_loader_payload_without_record_is_updated_by_filename(loading):
    service, runtime, _state, args, root, payload = loading
    directory = service.native_workspace_path
    directory.mkdir(parents=True)
    target = directory / 'NTE_Capture.dll'
    target.write_bytes(b'older version without deployment record')
    unrelated = directory / 'keep.txt'
    unrelated.write_text('keep', encoding='utf-8')
    service.start_loader(**args)
    assert target.read_bytes() == (root / payload['roles']['capture_plugin']).read_bytes()
    assert unrelated.read_text(encoding='utf-8') == 'keep'
