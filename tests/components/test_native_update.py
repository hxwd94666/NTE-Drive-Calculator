# 用合成组件验证明确更新、依赖顺序、拒绝忙碌和未知结果不重发，不操作游戏。
import hashlib
import shutil

import pytest

from tests.test_native_plugin_bundle import make_bundle, write_manifest
from src.integrations.native_plugin_bundle import HOT_PLUGIN_DEPLOYMENT_PATHS, inspect_native_plugin_bundle
from src.integrations.native_host_control import NativeHostClient, NativeHostError, NativeHostOutcomeUnknown
from src.integrations.components.native_files import update_native_plugins, USER, COMBAT
from src.integrations.components.native_files import deploy_native_component_files
from src.integrations.components.inspection import inspect_deployed_native_plugin


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hot_bundle(tmp_path):
    root, payload = make_bundle(tmp_path)
    old = payload['roles']['capture_plugin']
    payload['files'].pop(old)
    payload['file_sizes'].pop(old)
    for role, relative in HOT_PLUGIN_DEPLOYMENT_PATHS.items():
        source = 'native/' + relative
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('new fixture:' + role).encode())
        payload['roles'][role] = source
        payload['files'][source] = digest(path)
        payload['file_sizes'][source] = path.stat().st_size
    payload.update(layout='native-plugins-v2', plugin_policy='calc-publisher-rsa3072-sha256-v1')
    write_manifest(root, payload)
    assert inspect_native_plugin_bundle(root).ready
    return root, payload


class Host:
    def __init__(self, directory, fail=None):
        self.directory, self.fail = directory, fail
        self.states = {USER: 'loaded', COMBAT: 'loaded'}
        self.actions = []

    def call(self, action):
        if action == 'describe':
            return {'mode': 'calc', 'pluginPolicy': 'calc-publisher-rsa3072-sha256-v1'}
        if action == 'status':
            return {'runtimeDirectory': str(self.directory)}
        assert action == 'list'
        return [{'file': name, 'state': state,
                 'sha256': digest(self.directory / 'plugins' / name) if state == 'loaded' else ''}
                for name, state in self.states.items()]

    def change(self, action, name):
        self.actions.append((action, name))
        if self.fail and self.fail[0] == (action, name):
            raise self.fail[1]
        self.states[name] = 'unloaded' if action == 'disable' else 'loaded'


def scenario(tmp_path, fail=None):
    root, payload = hot_bundle(tmp_path)
    directory = tmp_path / 'game'
    directory.mkdir()
    (directory / 'HTGame.exe').write_bytes(b'fixture')
    for role, relative in HOT_PLUGIN_DEPLOYMENT_PATHS.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / payload['roles'][role], target)
        if role != 'host':
            target.write_bytes(b'old fixture ' + role.encode())
    return root, directory, Host(directory, fail)


def update(root, directory, host):
    return update_native_plugins(application_root=root, directory=directory, game_pid=123,
                                 operation_guard=lambda _: None, client=host)


def test_cold_deployment_includes_both_plugins_and_signatures(tmp_path):
    root, _ = hot_bundle(tmp_path)
    directory = tmp_path / 'game'
    directory.mkdir()
    executable = directory / 'HTGame.exe'
    executable.write_bytes(b'fixture')
    result = deploy_native_component_files(application_root=root, directory_path=directory,
                                           operation_guard=lambda _: None, game_running=lambda: False)
    assert set(result.managed_files) == set(HOT_PLUGIN_DEPLOYMENT_PATHS.values())
    assert inspect_deployed_native_plugin(application_root=root, game_executable_path=executable).files_compatible


def test_update_orders_consumers_before_provider_and_confirms_image(tmp_path):
    root, directory, host = scenario(tmp_path)
    assert update(root, directory, host).state == 'updated'
    assert host.actions == [('disable', COMBAT), ('disable', USER), ('enable', USER), ('enable', COMBAT)]
    assert not list(directory.glob('.nte-update-*'))
    host.actions.clear()
    assert update(root, directory, host).state == 'current'
    assert not host.actions


def test_busy_preserves_disk_and_does_not_stop_provider(tmp_path):
    root, directory, host = scenario(tmp_path, (('disable', COMBAT), NativeHostError('busy')))
    before = (directory / 'plugins' / COMBAT).read_bytes()
    with pytest.raises(NativeHostError, match='busy'):
        update(root, directory, host)
    assert host.actions == [('disable', COMBAT)]
    assert (directory / 'plugins' / COMBAT).read_bytes() == before


def test_unknown_unload_does_not_retry_or_restore_during_live_operation(tmp_path):
    root, directory, host = scenario(tmp_path, (('disable', COMBAT), NativeHostOutcomeUnknown('pending')))
    with pytest.raises(NativeHostOutcomeUnknown):
        update(root, directory, host)
    assert host.actions == [('disable', COMBAT)]
    assert len(list(directory.glob('.nte-update-*'))) == 1


def test_changed_host_requires_game_restart_without_mutation(tmp_path):
    root, directory, host = scenario(tmp_path)
    (directory / 'd3d12.dll').write_bytes(b'other host')
    assert update(root, directory, host).state == 'restart_required'
    assert not host.actions


def test_operation_polls_the_accepted_id_and_never_repeats_mutation():
    client = NativeHostClient.__new__(NativeHostClient)
    calls = []
    replies = iter([{'operationId': 4, 'state': 'queued'}, {'operationId': 5, 'state': 'completed'}])
    client.call = lambda action, plugin='': (calls.append(action), next(replies))[1]
    with pytest.raises(NativeHostOutcomeUnknown):
        client.change('disable', COMBAT)
    assert calls == ['disable', 'operation']


def test_maintenance_blocks_new_connections_and_releases_on_error():
    from src.services.native_session.session import NativeGameSession
    from src.integrations.nte_core_protocol import NteCoreError
    session = NativeGameSession(factory=lambda: pytest.fail('must not connect'), guard=lambda _: None)
    with pytest.raises(ValueError):
        with session.plugin_maintenance():
            with pytest.raises(NteCoreError, match='组件正在更新'):
                session._connect()
            raise ValueError('fixture')
    assert not session._maintenance.is_set()
    session._performance_active = True
    with pytest.raises(NteCoreError, match='请先停止'):
        with session.plugin_maintenance():
            pytest.fail('must not enter while performance owns the pipe')


def test_declared_performance_plugin_is_deployed_and_updated_as_signed_pair(tmp_path):
    from src.integrations.native_plugin_bundle import PERFORMANCE_DEPLOYMENT_PATHS
    root, directory, host = scenario(tmp_path)
    import json
    # Use the actual fixture manifest location rather than a production directory.
    manifest = inspect_native_plugin_bundle(root).manifest_path
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    for role, relative in PERFORMANCE_DEPLOYMENT_PATHS.items():
        source = 'native/' + relative
        path = root / source
        path.write_bytes(('new fixture:' + role).encode())
        payload['roles'][role] = source
        payload['files'][source] = digest(path)
        payload['file_sizes'][source] = path.stat().st_size
        (directory / relative).write_bytes(b'old performance fixture')
    payload['capabilities'].append('performance.trace.v1')
    write_manifest(root, payload)
    bundle = inspect_native_plugin_bundle(root)
    assert bundle.ready
    assert set(PERFORMANCE_DEPLOYMENT_PATHS) <= bundle.deployment_paths.keys()
    host.states['NTE_PluginPerformance.dll'] = 'loaded'
    assert update(root, directory, host).state == 'updated'
    assert host.actions[0] == ('disable', 'NTE_PluginPerformance.dll')
    assert host.actions[-1] == ('enable', 'NTE_PluginPerformance.dll')
    inspection = inspect_deployed_native_plugin(
        application_root=root, game_executable_path=directory / 'HTGame.exe',
    )
    assert inspection.files_compatible
    assert len(inspection.files) == 7
    (directory / PERFORMANCE_DEPLOYMENT_PATHS['performance_signature']).unlink()
    assert not inspect_deployed_native_plugin(
        application_root=root, game_executable_path=directory / 'HTGame.exe',
    ).files_compatible
    payload['roles'].pop('performance_signature')
    write_manifest(root, payload)
    assert not inspect_native_plugin_bundle(root).ready
