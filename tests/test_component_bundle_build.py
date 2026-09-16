# 验证原生组件构建输入、来源对应和发行目录边界，不运行实际构建。
from dataclasses import replace
import json
import pytest

from tools.release.game_component_bundle_build import prepare_component_bundle, validate_packaged_component_bundle
from tools.release.native_component_bundle_build import native_component_build_inputs
from tests.test_native_component_bundle_build import native_source, prepare


@pytest.mark.parametrize('change', ['missing', 'extra', 'changed', 'destination', 'escape'])
def test_rejects_unapproved_actual_inputs_before_staging(tmp_path, change):
    root, _source, _payload = native_source(tmp_path)
    inputs = native_component_build_inputs(root)
    key = next(iter(inputs))
    if change == 'missing':
        inputs.pop(key)
    elif change == 'extra':
        inputs['third_party/native-capture/private.cpp'] = inputs[key]
    elif change == 'changed':
        inputs[key].source.write_bytes(b'changed')
    elif change == 'destination':
        inputs[key] = replace(inputs[key], destination='other.bin')
    else:
        inputs[key] = replace(inputs[key], destination='../outside.bin')
    with pytest.raises(ValueError):
        prepare_component_bundle(application_root=root, inputs=inputs, output_parent=root/'build')
    assert not (root/'build').exists()


def test_identical_bytes_override_preserves_source_identity(tmp_path):
    root, source, _ = native_source(tmp_path)
    inputs = native_component_build_inputs(root)
    key = next(iter(inputs))
    alternate = tmp_path/'alternate.bin'
    alternate.write_bytes(inputs[key].source.read_bytes())
    inputs[key] = replace(inputs[key], source=alternate)
    result = prepare_component_bundle(application_root=root, inputs=inputs, output_parent=root/'build')
    validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)


@pytest.mark.parametrize('layout', [None, 'legacy-mods-v1'])
def test_build_rejects_retired_layout_even_if_bytes_match(tmp_path, layout):
    root, source, payload = native_source(tmp_path)
    inputs = native_component_build_inputs(root)
    payload['layout'] = layout
    source.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='native-capture-v1'):
        prepare_component_bundle(application_root=root, inputs=inputs, output_parent=root/'build')
    assert not (root/'build').exists()


@pytest.mark.parametrize('relative', ['dwmapi.dll', 'plugins/equipment.nte', 'licenses/mods-plugin/LICENSE', 'licenses/native-capture/extra', 'licenses/native-capture/empty/'])
def test_packaged_bundle_rejects_retired_or_undeclared_members(tmp_path, relative):
    root, source, _ = native_source(tmp_path)
    result = prepare(root)
    path = result.resource_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if relative.endswith('/'):
        path.mkdir()
    else:
        path.write_bytes(b'undeclared')
    with pytest.raises(ValueError):
        validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)


def test_source_identity_must_match_current_approved_bundle(tmp_path):
    root, source, payload = native_source(tmp_path)
    result = prepare(root)
    payload['source_commits']['capture'] = 'f'*40
    source.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError):
        validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)


def test_unrelated_qt_files_are_not_mistaken_for_component_members(tmp_path):
    root, source, _ = native_source(tmp_path)
    result = prepare(root)
    (result.resource_root/'Qt6Core.dll').write_bytes(b'qt fixture')
    validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)
