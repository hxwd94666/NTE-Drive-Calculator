# 用假进程结果验证 Loader 正式能力查询、同名替换与旧参数兼容，不执行组件。
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.integrations import mod_loader as loader


def capabilities(**changes):
    result = {
        'schema_version': 1, 'component': 'nte-mod-loader', 'shim_protocol_version': 3,
        'embedded_shim_compatible': True, 'payload_load_modes': ['manualmap', 'loadlibrary'],
        'managed_session': True, 'payload_kinds': ['nte_capture_runtime_v1'],
    }
    result.update(changes)
    return result


def query_result(value=None, *, text=None, code=0):
    return SimpleNamespace(returncode=code, stdout=json.dumps(capabilities() if value is None else value)
                           if text is None else text, stderr='')


def test_query_uses_only_formal_dry_run_contract(tmp_path, monkeypatch):
    path = tmp_path / 'nte-mod-loader.exe'
    run = Mock(return_value=query_result())
    monkeypatch.setattr(loader.subprocess, 'run', run)
    assert loader.probe_mod_loader_capabilities(path) == {'manualmap', 'loadlibrary'}
    args, kwargs = run.call_args
    assert args == ([str(path.resolve()), '--capabilities-json', '--dry-run', '--once'],)
    assert kwargs['timeout'] == 5 and kwargs['check'] is False
    assert kwargs['capture_output'] and kwargs['encoding'] == 'utf-8-sig'
    assert kwargs['cwd'] == str(path.parent.resolve())


@pytest.mark.parametrize('result', [
    query_result(text='old Loader preview only'), query_result(text='[]'), query_result(text='null'),
    query_result(text='{"schema_version":1,"schema_version":1}'),
    query_result(text='x' * 16385), query_result(code=3),
    query_result(capabilities(schema_version=True)), query_result(capabilities(schema_version=2)),
    query_result(capabilities(component='another-component')),
    query_result(capabilities(embedded_shim_compatible=False)),
    query_result(capabilities(shim_protocol_version=1)),
    query_result(capabilities(shim_protocol_version=2)),
    query_result(capabilities(payload_kinds=None)),
    query_result(capabilities(payload_kinds=['old_host'])),
    query_result(capabilities(payload_kinds=['nte_capture_runtime_v1', 'nte_capture_runtime_v1'])),
    query_result(capabilities(payload_kinds=['nte_capture_runtime_v1', 1])),
    query_result(capabilities(managed_session=False)),
    query_result(capabilities(payload_load_modes='loadlibrary')),
    query_result(capabilities(payload_load_modes=['loadlibrary', 'loadlibrary'])),
    query_result(capabilities(payload_load_modes=['loadlibrary', 1])),
])
def test_missing_old_or_malformed_capability_never_passes(tmp_path, monkeypatch, result):
    monkeypatch.setattr(loader.subprocess, 'run', Mock(return_value=result))
    with pytest.raises(loader.ModLoaderRuntimeError, match='配套 Loader'):
        loader.probe_mod_loader_capabilities(tmp_path / 'nte-mod-loader.exe')


@pytest.mark.parametrize('error, expected', [
    (subprocess.TimeoutExpired('synthetic', 5), '未正常完成'),
    (UnicodeError('synthetic'), '未正常完成'), (OSError('synthetic'), '只读能力'),
])
def test_query_environment_failure_is_explicit(tmp_path, monkeypatch, error, expected):
    monkeypatch.setattr(loader.subprocess, 'run', Mock(side_effect=error))
    with pytest.raises(loader.ModLoaderRuntimeError, match=expected):
        loader.probe_mod_loader_capabilities(tmp_path / 'nte-mod-loader.exe')


def test_740_describes_environment_without_claiming_loader_is_old(tmp_path, monkeypatch):
    error = OSError('synthetic non-elevated launch')
    error.winerror = 740
    monkeypatch.setattr(loader.subprocess, 'run', Mock(side_effect=error))
    with pytest.raises(loader.ModLoaderRuntimeError, match='当前运行环境未提权'):
        loader.probe_mod_loader_capabilities(tmp_path / 'nte-mod-loader.exe')


def test_standard_mode_rechecks_same_named_replacement(tmp_path, monkeypatch):
    path = tmp_path / 'nte-mod-loader.exe'
    path.write_bytes(b'old replacement')
    monkeypatch.delenv(loader.MOD_LOADER_ENV, raising=False)
    run = Mock(side_effect=[query_result(), query_result(capabilities(embedded_shim_compatible=False))])
    monkeypatch.setattr(loader.subprocess, 'run', run)
    runtime = loader.ModLoaderRuntime(application_root=tmp_path)
    runtime.require_payload_load_mode('loadlibrary')
    path.write_bytes(b'new replacement')
    with pytest.raises(loader.ModLoaderRuntimeError):
        runtime.require_payload_load_mode('loadlibrary')
    assert run.call_count == 2


def test_declared_manualmap_only_does_not_allow_native_host(tmp_path, monkeypatch):
    (tmp_path / 'nte-mod-loader.exe').write_bytes(b'synthetic')
    monkeypatch.delenv(loader.MOD_LOADER_ENV, raising=False)
    monkeypatch.setattr(loader.subprocess, 'run', Mock(return_value=query_result(capabilities(payload_load_modes=['manualmap']))))
    with pytest.raises(loader.ModLoaderRuntimeError, match='不支持标准采集加载'):
        loader.ModLoaderRuntime(application_root=tmp_path).require_payload_load_mode('loadlibrary')


def test_default_arguments_remain_exact_legacy_shape():
    payload = Path('payload directory/dwmapi.dll')
    assert loader.mod_loader_arguments(payload_path=payload, event_name='Local\\event', owner_pid=123) == (
        f'--dll "{payload}" --monitor-timeout 0 --stop-event "Local\\event" --owner-pid 123')
    assert '--payload-load-mode' not in loader.mod_loader_arguments(payload_path=payload, event_name='e', owner_pid=1)


def test_native_arguments_include_only_explicit_standard_mode():
    args = loader.mod_loader_arguments(payload_path=Path('stage/d3d12.dll'), event_name='e', owner_pid=1,
                                       payload_load_mode='loadlibrary')
    assert '--payload-load-mode loadlibrary' in args
    assert '--capabilities-json' not in args and '--dry-run' not in args


def test_legacy_start_does_not_query_capabilities(tmp_path, monkeypatch):
    if loader.os.name != 'nt':
        pytest.skip('Windows managed runtime boundary')
    for filename in ('nte-mod-loader.exe', 'NTELauncher.exe', 'dwmapi.dll'):
        (tmp_path / filename).write_bytes(b'synthetic')
    monkeypatch.delenv(loader.MOD_LOADER_ENV, raising=False)
    query = Mock(side_effect=AssertionError('legacy must not query'))
    monkeypatch.setattr(loader, 'probe_mod_loader_capabilities', query)
    runtime = loader.ModLoaderRuntime(application_root=tmp_path)
    monkeypatch.setattr(runtime, '_refresh_running_locked', lambda: True)
    assert runtime.start(payload_path=tmp_path / 'dwmapi.dll', launcher_path=tmp_path / 'NTELauncher.exe').phase == 'running'
    query.assert_not_called()


def test_native_start_queries_before_runtime_start(tmp_path, monkeypatch):
    if loader.os.name != 'nt':
        pytest.skip('Windows managed runtime boundary')
    for filename in ('nte-mod-loader.exe', 'NTELauncher.exe', 'd3d12.dll'):
        (tmp_path / filename).write_bytes(b'synthetic')
    monkeypatch.delenv(loader.MOD_LOADER_ENV, raising=False)
    query = Mock(return_value=frozenset({'loadlibrary'}))
    monkeypatch.setattr(loader, 'probe_mod_loader_capabilities', query)
    runtime = loader.ModLoaderRuntime(application_root=tmp_path)
    monkeypatch.setattr(runtime, '_refresh_running_locked', lambda: True)
    assert runtime.start(payload_path=tmp_path / 'd3d12.dll', launcher_path=tmp_path / 'NTELauncher.exe',
                         payload_load_mode='loadlibrary').phase == 'running'
    query.assert_called_once_with((tmp_path / 'nte-mod-loader.exe').resolve())


def test_revocation_during_query_prevents_event_or_process_creation(tmp_path, monkeypatch):
    if loader.os.name != 'nt':
        pytest.skip('Windows managed runtime boundary')
    for filename in ('nte-mod-loader.exe', 'NTELauncher.exe', 'd3d12.dll'):
        (tmp_path / filename).write_bytes(b'synthetic')
    monkeypatch.delenv(loader.MOD_LOADER_ENV, raising=False)
    allowed = [True]
    def query(_path):
        allowed[0] = False
        return frozenset({'loadlibrary'})
    def guard():
        if not allowed[0]:
            raise PermissionError('revoked during query')
    monkeypatch.setattr(loader, 'probe_mod_loader_capabilities', query)
    windows = Mock(side_effect=AssertionError('no event or process creation after revocation'))
    monkeypatch.setattr(loader.ctypes, 'WinDLL', windows)
    runtime = loader.ModLoaderRuntime(application_root=tmp_path)
    with pytest.raises(PermissionError, match='revoked during query'):
        runtime.start(payload_path=tmp_path / 'd3d12.dll', launcher_path=tmp_path / 'NTELauncher.exe',
                      payload_load_mode='loadlibrary', launch_guard=guard)
    windows.assert_not_called()


def test_final_launch_guard_failure_closes_event_and_restores_environment(tmp_path, monkeypatch):
    if loader.os.name != 'nt':
        pytest.skip('Windows managed runtime boundary')
    for filename in ('nte-mod-loader.exe', 'NTELauncher.exe', 'd3d12.dll'):
        (tmp_path / filename).write_bytes(b'synthetic')
    monkeypatch.delenv(loader.MOD_LOADER_ENV, raising=False)
    monkeypatch.setenv(loader.MOD_LOADER_LAUNCHER_ENV, 'previous-launcher')
    monkeypatch.setattr(loader, 'probe_mod_loader_capabilities', lambda _path: frozenset({'loadlibrary'}))
    kernel = SimpleNamespace(CreateEventW=Mock(return_value=44), CloseHandle=Mock(return_value=True))
    shell = SimpleNamespace(ShellExecuteExW=Mock(side_effect=AssertionError('launch must be cancelled')))
    monkeypatch.setattr(loader.ctypes, 'WinDLL', lambda name, **_kwargs: kernel if name == 'kernel32' else shell)
    guard = Mock(side_effect=[None, PermissionError('revoked before ShellExecute')])
    runtime = loader.ModLoaderRuntime(application_root=tmp_path)
    with pytest.raises(PermissionError, match='revoked before ShellExecute'):
        runtime.start(payload_path=tmp_path / 'd3d12.dll', launcher_path=tmp_path / 'NTELauncher.exe',
                      payload_load_mode='loadlibrary', launch_guard=guard)
    kernel.CloseHandle.assert_called_once_with(44)
    shell.ShellExecuteExW.assert_not_called()
    assert loader.os.environ[loader.MOD_LOADER_LAUNCHER_ENV] == 'previous-launcher'
    assert runtime._process_handle is None and runtime._stop_event_handle is None
