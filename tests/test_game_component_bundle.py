# 验证组件检查仅接受原生包，旧布局和缺失原生包不会触发兼容回退。
import json
import pytest

from src.integrations.game_component_bundle import inspect_game_component_bundle
from tests.test_native_plugin_bundle import make_bundle, write_manifest


@pytest.mark.parametrize('layout', [None, 'legacy-mods-v1', 'unknown-layout'])
def test_retired_or_missing_layout_is_rejected(tmp_path, layout):
    root, payload = make_bundle(tmp_path)
    if layout is None:
        payload.pop('layout')
    else:
        payload['layout'] = layout
    write_manifest(root, payload)
    assert not inspect_game_component_bundle(root).ready


def test_legacy_source_manifest_is_never_selected(tmp_path):
    root, payload = make_bundle(tmp_path)
    (root / 'component-bundle.json').unlink()
    old = root / 'third_party/mods-plugin/component-bundle.json'
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(payload), encoding='utf-8')
    result = inspect_game_component_bundle(root)
    assert not result.ready
    assert result.manifest_path != old


def test_broken_native_source_does_not_fall_back_to_other_manifest(tmp_path):
    root, _payload = make_bundle(tmp_path)
    native = root / 'third_party/native-capture/component-bundle.json'
    native.parent.mkdir(parents=True)
    native.write_text('{broken', encoding='utf-8')
    assert not inspect_game_component_bundle(root).ready


def test_all_declared_files_are_checked(tmp_path):
    root, payload = make_bundle(tmp_path)
    assert inspect_game_component_bundle(root).ready
    (root / payload['roles']['capture_source']).write_text('changed', encoding='utf-8')
    assert not inspect_game_component_bundle(root).ready


@pytest.mark.parametrize('field', ['source_commits', 'roles', 'input_digests'])
def test_missing_identity_or_roles_is_rejected(tmp_path, field):
    root, payload = make_bundle(tmp_path)
    payload.pop(field)
    write_manifest(root, payload)
    assert not inspect_game_component_bundle(root).ready


def test_duplicate_json_key_is_rejected(tmp_path):
    root, _ = make_bundle(tmp_path)
    (root / 'component-bundle.json').write_text('{"layout":"native-capture-v1","layout":"native-capture-v1"}', encoding='utf-8')
    assert not inspect_game_component_bundle(root).ready
