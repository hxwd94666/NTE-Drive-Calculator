# 验证手动清理按固定文件名执行，并保留目录、并发变化与游戏运行门禁。
from __future__ import annotations

import hashlib

import pytest

from src.integrations.components import native_files as deployment
from src.integrations.components import legacy_cleanup as managed_plugin_cleanup
from tests.test_native_plugin_bundle import game_files, make_bundle, write_manifest
from tests.test_native_plugin_runtime import setup_runtime


def _cleanup(root, game, *, recorded=None, running=lambda: False):
    return deployment.cleanup_manual_native_plugin(
        application_root=root, game_executable_path=game,
        managed_files=recorded or {}, game_running=running,
    )


def test_unrecorded_current_and_reviewed_predecessor_are_cleaned(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    old_capture = b'reviewed previous capture'
    (game.parent / 'NTE_Capture.dll').write_bytes(old_capture)
    payload['upgrade_from'] = {
        'NTE_Capture.dll': [hashlib.sha256(old_capture).hexdigest()],
    }
    write_manifest(root, payload)
    unrelated = game.parent / 'other-tool.dll'
    unrelated.write_bytes(b'preserve')

    assert _cleanup(root, game).status == 'cleaned'
    assert not (game.parent / 'd3d12.dll').exists()
    assert not (game.parent / 'NTE_Capture.dll').exists()
    assert unrelated.read_bytes() == b'preserve'


def test_manual_cleanup_removes_exact_names_with_unknown_hash(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    capture = game.parent / 'NTE_Capture.dll'
    capture.write_bytes(b'unknown component')

    result = _cleanup(root, game)
    assert result.status == 'cleaned'
    assert not (game.parent / 'd3d12.dll').exists()
    assert not capture.exists()


def test_manual_cleanup_does_not_require_bundle_and_preserves_other_plugins(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    (root / 'component-bundle.json').unlink()
    plugins = game.parent / 'plugins'
    plugins.mkdir()
    targets = [plugins / f'NTE_Plugin{role}.dll{suffix}'
               for role in ('User', 'Combat', 'HUD', 'Performance') for suffix in ('', '.sig')]
    for target in targets:
        target.write_bytes(b'unknown previous component')
    unrelated = plugins / 'other-tool.dll'
    unrelated.write_bytes(b'preserve')

    assert _cleanup(root, game).status == 'cleaned'
    assert all(not target.exists() for target in targets)
    assert unrelated.read_bytes() == b'preserve'
    assert plugins.is_dir()


def test_recorded_file_may_have_changed_hash(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    capture = game.parent / 'NTE_Capture.dll'
    capture.write_bytes(b'upgraded since record')

    assert _cleanup(root, game, recorded={'NTE_Capture.dll': 'previous digest'}).status == 'cleaned'
    assert not capture.exists() and not (game.parent / 'd3d12.dll').exists()


def test_running_game_and_non_file_target_are_preserved(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    assert _cleanup(root, game, running=lambda: True).status == 'waiting_game_exit'
    capture = game.parent / 'NTE_Capture.dll'
    capture.unlink()
    capture.mkdir()
    assert _cleanup(root, game).status == 'conflict'
    assert (game.parent / 'd3d12.dll').is_file() and capture.is_dir()


def test_file_changed_after_preflight_is_preserved(tmp_path, monkeypatch):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    host = game.parent / 'd3d12.dll'
    original_digest = deployment._digest
    calls = 0

    def change_before_delete(path):
        nonlocal calls
        calls += 1
        if calls == 3:
            host.write_bytes(b'external change')
        return original_digest(path)

    monkeypatch.setattr(deployment, '_digest', change_before_delete)
    assert _cleanup(root, game).status == 'conflict'
    assert host.read_bytes() == b'external change'
    assert (game.parent / 'NTE_Capture.dll').is_file()


@pytest.mark.parametrize('layout', ['native-capture-v1', 'legacy'])
def test_both_manual_cleanup_entry_layouts_remove_recognized_files(tmp_path, monkeypatch, layout):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch)
    record = {'loading_method': 'native-capture'}
    if layout == 'native-capture-v1':
        record.update({'deployment_layout': layout, 'game_executable': str(game)})
    policy.update_deployment(record)
    policy.set_paused(True)
    policy.set_cleanup_pending(True)
    monkeypatch.setattr(managed_plugin_cleanup, 'mod_workspace_registry_snapshot', lambda: (False, None))
    monkeypatch.setattr(managed_plugin_cleanup, 'cleanup_mod_workspace', lambda **_kw: True)

    runtime.cleanup(allow_unrecorded_legacy_workspace=True)

    assert not policy.settings.pending_cleanup
    assert not (game.parent / 'd3d12.dll').exists()
    assert not (game.parent / 'NTE_Capture.dll').exists()


def test_manual_cleanup_unknown_file_clears_pending_after_completion(tmp_path, monkeypatch):
    runtime, policy, game, _ = setup_runtime(tmp_path, monkeypatch)
    capture = game.parent / 'NTE_Capture.dll'
    capture.write_bytes(b'unknown component')
    policy.set_paused(True)
    policy.set_cleanup_pending(True)
    monkeypatch.setattr(managed_plugin_cleanup, 'mod_workspace_registry_snapshot', lambda: (False, None))
    monkeypatch.setattr(managed_plugin_cleanup, 'cleanup_mod_workspace', lambda **_kw: True)

    runtime.cleanup(allow_unrecorded_legacy_workspace=True)

    assert not policy.settings.pending_cleanup
    assert not (game.parent / 'd3d12.dll').exists()
    assert not capture.exists()
