# 验证已准备的中风险按固定组件名升级，并拒绝未准备或检查后的目标变化。
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.integrations.components.inspection import inspect_deployed_native_plugin
from src.services.work_modes.runtime import WorkModeRuntime
from src.services.work_modes.service import WorkModeService
from src.integrations.game_component_bundle import inspect_game_component_bundle
from tests.test_native_plugin_bundle import make_bundle, game_files, write_manifest


def setup_upgrade(tmp_path, *, predecessor=True, running=False, prepared=True):
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
    if prepared:
        # 模拟用户此前完成可见准备后保留的自动管理许可，不启动真实同步。
        policy.enable_auto_sync_after_preflight()
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


@pytest.mark.parametrize('predecessor', [True, False])
def test_prepared_medium_upgrades_fixed_components_without_deployment_record(tmp_path, predecessor):
    runtime, policy, game, payload, old = setup_upgrade(tmp_path, predecessor=predecessor)
    runtime._automatic_deploy(False)
    assert (game.parent / 'd3d12.dll').read_bytes() != old
    assert policy.deployment_record['managed_files']['d3d12.dll'] == payload['files'][payload['roles']['host']]
    assert policy.deployment_record['loading_method'] == 'native-capture'
    assert set(policy.deployment_record['managed_files']) == {'d3d12.dll', 'NTE_Capture.dll'}
    assert (game.parent / 'NTE_Capture.dll').read_bytes() == (runtime.root / payload['roles']['capture_plugin']).read_bytes()


def test_unprepared_medium_never_deploys_or_closes_native_session(tmp_path):
    runtime, policy, game, _, old = setup_upgrade(tmp_path, predecessor=False, prepared=False)
    capture = (game.parent / 'NTE_Capture.dll').read_bytes()
    runtime._automatic_deploy(False)
    assert (game.parent / 'd3d12.dll').read_bytes() == old
    assert (game.parent / 'NTE_Capture.dll').read_bytes() == capture
    assert 'managed_files' not in policy.deployment_record
    runtime.native_session.close.assert_not_called()


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
