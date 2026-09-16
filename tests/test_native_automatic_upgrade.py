# 验证中风险自动接管已核实旧组件，并拒绝未知文件和检查后的目标变化。
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from src.services.work_mode_runtime import WorkModeRuntime
from src.services.work_mode_service import WorkModeService
from src.integrations.game_component_bundle import inspect_game_component_bundle
from tests.test_native_plugin_bundle import make_bundle, game_files, write_manifest


def setup_upgrade(tmp_path, *, predecessor=True, running=False):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    old = b'previous audited capture entry'
    (game.parent / 'd3d12.dll').write_bytes(old)
    if predecessor:
        payload['upgrade_from'] = {'d3d12.dll': [hashlib.sha256(old).hexdigest()]}
        write_manifest(root, payload)
    policy = WorkModeService(tmp_path / 'settings.json')
    policy.select_mode('medium', risk_confirmed=True)
    policy.set_cleanup_pending(False)
    policy.set_game_executable(str(game))
    policy.update_deployment({'loading_method': 'proxy'})
    native = SimpleNamespace(battle_active=False, close=MagicMock())
    runtime = WorkModeRuntime(
        policy=policy, native_session=native,
        loader=SimpleNamespace(snapshot=lambda: SimpleNamespace(phase='stopped')),
        application_root=root, config_dir=tmp_path / 'config', game_running=lambda: running,
    )
    inspection = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    runtime._native_deployed = inspection
    runtime._bundle = inspect_game_component_bundle(root)
    return runtime, policy, game, payload, old


def test_medium_upgrades_audited_predecessor_without_deployment_record(tmp_path):
    runtime, policy, game, payload, old = setup_upgrade(tmp_path)
    runtime._automatic_deploy(False)
    assert (game.parent / 'd3d12.dll').read_bytes() != old
    assert policy.deployment_record['managed_files']['d3d12.dll'] == payload['files'][payload['roles']['host']]
    assert policy.deployment_record['loading_method'] == 'native-capture'


def test_unknown_predecessor_is_not_taken_over(tmp_path):
    runtime, policy, game, _, old = setup_upgrade(tmp_path, predecessor=False)
    runtime._automatic_deploy(False)
    assert (game.parent / 'd3d12.dll').read_bytes() == old
    assert 'managed_files' not in policy.deployment_record


def test_audited_upgrade_waits_for_game_exit(tmp_path):
    runtime, _, game, _, old = setup_upgrade(tmp_path, running=True)
    runtime._automatic_deploy(True)
    assert (game.parent / 'd3d12.dll').read_bytes() == old
    assert '等待游戏退出' in runtime.cleanup_detail


def test_changed_target_after_inspection_cannot_be_adopted(tmp_path):
    runtime, policy, game, _, _ = setup_upgrade(tmp_path)
    target = game.parent / 'd3d12.dll'
    modified = b'another tool replaced this after inspection'
    target.write_bytes(modified)
    before_runtime = hashlib.sha256((game.parent / 'NTE_Capture.dll').read_bytes()).hexdigest()
    runtime._automatic_deploy(False)
    assert target.read_bytes() == modified
    assert hashlib.sha256((game.parent / 'NTE_Capture.dll').read_bytes()).hexdigest() == before_runtime
    assert 'managed_files' not in policy.deployment_record
    assert '变化' in runtime.cleanup_detail
