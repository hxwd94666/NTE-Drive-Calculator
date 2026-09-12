# 核对已审核旧组件的精确哈希升级声明及发行保留，拒绝未知文件与无效整包。
import hashlib
import json

import pytest

from src.integrations.native_plugin_bundle import inspect_native_plugin_bundle
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from tests.test_native_component_bundle_build import native_source, prepare
from tests.test_native_plugin_bundle import game_files, make_bundle, write_manifest
from tools.release.game_component_bundle_build import validate_packaged_component_bundle
from tools.release.native_component_bundle_build import native_distribution_manifest


def test_old_manifest_has_no_approved_predecessors(tmp_path):
    root, _payload = make_bundle(tmp_path)
    bundle = inspect_native_plugin_bundle(root)
    assert bundle.ready and dict(bundle.upgrade_from) == {}


def test_predecessor_hashes_are_normalized_and_deeply_immutable(tmp_path):
    root, payload = make_bundle(tmp_path)
    payload["upgrade_from"] = {"d3d12.dll": ["A" * 64, "b" * 64]}
    write_manifest(root, payload)
    bundle = inspect_native_plugin_bundle(root)
    assert bundle.ready
    assert bundle.upgrade_from["d3d12.dll"] == ("a" * 64, "b" * 64)
    with pytest.raises(TypeError):
        bundle.upgrade_from["NTE_Capture.dll"] = ("c" * 64,)
    with pytest.raises(TypeError):
        bundle.upgrade_from["d3d12.dll"][0] = "c" * 64


@pytest.mark.parametrize("value", [
    None, [], {"d3d12.dll": []}, {"d3d12.dll": "a" * 64},
    {"d3d12.dll": ["a" * 64, "A" * 64]}, {"d3d12.dll": ["a" * 63]},
    {"d3d12.dll": ["g" * 64]}, {"d3d12.dll": [12]},
    {"d3d12.dll": [" " + "a" * 64]}, {"../d3d12.dll": ["a" * 64]},
    {"native/d3d12.dll": ["a" * 64]}, {"native\\d3d12.dll": ["a" * 64]},
    {"D3D12.dll": ["a" * 64]}, {"nte-core.exe": ["a" * 64]},
    {"dwmapi.dll": ["a" * 64]}, {"C:/d3d12.dll": ["a" * 64]},
])
def test_invalid_upgrade_declarations_reject_bundle_and_distribution(tmp_path, value):
    root, source, payload = native_source(tmp_path)
    payload["upgrade_from"] = value
    source.write_text(json.dumps(payload), encoding="utf-8")
    assert not inspect_native_plugin_bundle(root).ready
    with pytest.raises(ValueError):
        native_distribution_manifest(payload)


def test_duplicate_upgrade_path_in_json_rejects_bundle(tmp_path):
    root, payload = make_bundle(tmp_path)
    text = json.dumps(payload)
    duplicate = '"d3d12.dll": ["' + "a" * 64 + '"]'
    (root / "component-bundle.json").write_text(
        text[:-1] + ', "upgrade_from": {' + duplicate + ', ' + duplicate + '}}', encoding="utf-8",
    )
    assert not inspect_native_plugin_bundle(root).ready


def test_only_exact_approved_bytes_match_predecessor_without_deployment_record(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    approved = b"reviewed previous native proxy"
    payload["upgrade_from"] = {"d3d12.dll": [hashlib.sha256(approved).hexdigest()]}
    write_manifest(root, payload)
    target = game.parent / "d3d12.dll"
    target.write_bytes(approved)
    result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    item = result.files["d3d12.dll"]
    assert item.matches_predecessor and not item.matches_bundle and not item.matches_record
    assert not result.files_compatible
    assert not result.files["NTE_Capture.dll"].matches_predecessor
    target.write_bytes(b"unreviewed replacement native proxy")
    result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    assert not result.files["d3d12.dll"].matches_predecessor


def test_invalid_bundle_never_authorizes_matching_predecessor(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    payload["upgrade_from"] = {"d3d12.dll": [payload["files"][payload["roles"]["host"]]]}
    write_manifest(root, payload)
    (root / payload["roles"]["core"]).unlink()
    result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    assert not result.bundle_ready and not result.files["d3d12.dll"].matches_predecessor


def test_packaged_manifest_preserves_approved_predecessors_and_rejects_drift(tmp_path):
    root, source, payload = native_source(tmp_path)
    payload["upgrade_from"] = {"d3d12.dll": ["a" * 64], "NTE_Capture.dll": ["b" * 64]}
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = prepare(root)
    bundled = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert bundled["upgrade_from"] == payload["upgrade_from"]
    validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)
    assert inspect_native_plugin_bundle(result.resource_root).ready
    bundled["upgrade_from"]["d3d12.dll"] = ["c" * 64]
    result.manifest_path.write_text(json.dumps(bundled), encoding="utf-8")
    with pytest.raises(ValueError, match="当前已批准"):
        validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)
