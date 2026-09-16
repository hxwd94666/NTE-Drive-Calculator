# 用合成临时文件核对原生整包来源、依赖及游戏实际布局，绝不部署真实组件。
import hashlib
import json
import shutil
from unittest.mock import patch

import pytest

from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.native_plugin_bundle import (
    NATIVE_PLUGIN_CAPABILITIES, NATIVE_PLUGIN_DEPLOYMENT_PATHS,
    inspect_native_plugin_bundle, resolve_bundled_native_core,
)
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin


def make_bundle(tmp_path):
    root = tmp_path / "application"
    roles = {
        "host": "native/d3d12.dll", "capture_plugin": "native/NTE_Capture.dll",
        "core": "native/nte-core.exe", "capture_license": "licenses/native-capture/LICENSE",
        "capture_source": "licenses/native-capture/SOURCE.md", "core_license": "licenses/nte-core/LICENSE",
        "core_source": "licenses/nte-core/SOURCE.md",
    }
    for role, relative in roles.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic fixture: " + role).encode())
    payload = {"protocol_version": 1, "layout": "native-capture-v1", "capture_protocol_version": 1,
               "source_commits": {"capture": "a" * 40, "core": "b" * 40},
               "input_digests": {"capture": "c" * 64, "core": "d" * 64},
               "capabilities": sorted(NATIVE_PLUGIN_CAPABILITIES), "roles": roles,
               "files": {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest() for relative in roles.values()},
               "file_sizes": {relative: (root / relative).stat().st_size for relative in roles.values()}}
    (root / "component-bundle.json").write_text(json.dumps(payload), encoding="utf-8")
    return root, payload


def write_manifest(root, payload):
    (root / "component-bundle.json").write_text(json.dumps(payload), encoding="utf-8")


def game_files(tmp_path, root, payload):
    game = tmp_path / "game/HTGame.exe"
    game.parent.mkdir(parents=True)
    game.write_bytes(b"synthetic game fixture")
    for role, relative in NATIVE_PLUGIN_DEPLOYMENT_PATHS.items():
        target = game.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / payload["roles"][role], target)
    return game


def test_new_layout_dispatches_with_exact_declared_capabilities_and_matching_core(tmp_path):
    root, payload = make_bundle(tmp_path)
    result = inspect_game_component_bundle(root)
    assert result.ready and result.layout == "native-capture-v1"
    assert result.native_capabilities == NATIVE_PLUGIN_CAPABILITIES
    assert "combat.context.v1" not in result.native_capabilities
    assert dict(result.file_sizes) == payload["file_sizes"]
    assert resolve_bundled_native_core(root) == root / payload["roles"]["core"]


def test_source_layout_is_detected_without_legacy_roles(tmp_path):
    root, _payload = make_bundle(tmp_path)
    destination = root / "third_party/native-capture/component-bundle.json"
    destination.parent.mkdir(parents=True)
    (root / "component-bundle.json").rename(destination)
    result = inspect_game_component_bundle(root)
    assert result.ready and result.manifest_path == destination
    assert "proxy" not in result.roles and "loader" not in result.roles


def test_missing_new_manifest_never_borrows_old_dlls_or_claims_delivery(tmp_path):
    (tmp_path / "dwmapi.dll").write_bytes(b"legacy fixture")
    (tmp_path / "NTE_Capture.dll").write_bytes(b"legacy fixture")
    assert not inspect_native_plugin_bundle(tmp_path).ready
    assert inspect_game_component_bundle(tmp_path).layout == "legacy-mods-v1"
    assert resolve_bundled_native_core(tmp_path) is None


@pytest.mark.parametrize("role", ["host", "capture_plugin", "core", "capture_license", "capture_source", "core_license", "core_source"])
def test_every_required_program_license_and_source_file_is_checked(tmp_path, role):
    root, payload = make_bundle(tmp_path)
    (root / payload["roles"][role]).unlink()
    assert not inspect_native_plugin_bundle(root).ready
    with pytest.raises(ValueError, match="原生配套 Core"):
        resolve_bundled_native_core(root)


@pytest.mark.parametrize("failure", ["size", "hash", "input_digest", "missing_input", "source_commit", "missing_cap", "duplicate_cap", "path_escape", "capture_protocol"])
def test_invalid_native_contract_is_rejected(tmp_path, failure):
    root, payload = make_bundle(tmp_path)
    if failure == "size": payload["file_sizes"][payload["roles"]["capture_plugin"]] += 1
    elif failure == "hash": payload["files"][payload["roles"]["capture_source"]] = "0" * 64
    elif failure == "input_digest": payload["input_digests"]["capture"] = "unknown"
    elif failure == "missing_input": payload["input_digests"].pop("core")
    elif failure == "source_commit": payload["source_commits"].pop("core")
    elif failure == "missing_cap": payload["capabilities"].remove("inventory.snapshot.v1")
    elif failure == "duplicate_cap": payload["capabilities"].append(payload["capabilities"][0])
    elif failure == "path_escape":
        payload["files"]["../outside.dll"] = "f" * 64
        payload["file_sizes"]["../outside.dll"] = 10
    elif failure == "capture_protocol": payload["capture_protocol_version"] = True
    write_manifest(root, payload)
    assert not inspect_native_plugin_bundle(root).ready


def test_explicit_extra_capabilities_are_retained_without_assuming_them(tmp_path):
    root, payload = make_bundle(tmp_path)
    payload["capabilities"].append("combat.context.v1")
    write_manifest(root, payload)
    result = inspect_native_plugin_bundle(root)
    assert result.ready and "combat.context.v1" in result.native_capabilities


def test_deployed_files_are_distinct_from_bundle_and_do_not_touch_registry(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    old = game.parent / "dwmapi.dll"
    old.write_bytes(b"unrelated legacy file")
    with patch("src.services.deployed_plugin_inspection.mod_workspace_registry_snapshot", side_effect=AssertionError("registry must not be read")):
        result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    assert not result.files_compatible and result.legacy_proxy_present
    assert set(result.files) == set(NATIVE_PLUGIN_DEPLOYMENT_PATHS.values())
    assert all(item.matches_bundle for item in result.files.values())
    assert old.read_bytes() == b"unrelated legacy file"
    assert not any(item.matches_record for item in result.files.values())
    assert not hasattr(result, "pipe_ready") and not hasattr(result, "workspace_registered")
    old.unlink()
    assert inspect_deployed_native_plugin(application_root=root, game_executable_path=game).files_compatible
    (game.parent / "NTE_Capture.dll").write_bytes(b"other capture version")
    assert inspect_native_plugin_bundle(root).ready
    result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    assert not result.files_compatible and not result.files["NTE_Capture.dll"].matches_bundle


def test_record_hash_and_bundle_hash_are_reported_independently(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    target = game.parent / "d3d12.dll"
    target.write_bytes(b"older recorded host")
    result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game,
        recorded_files={"d3d12.dll": hashlib.sha256(target.read_bytes()).hexdigest()})
    assert result.files["d3d12.dll"].matches_record and not result.files["d3d12.dll"].matches_bundle
    assert not result.files_compatible


def test_missing_app_core_prevents_matching_game_files_from_being_ready(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    (root / payload["roles"]["core"]).unlink()
    result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game)
    assert not result.bundle_ready and not result.files_compatible


def test_cached_bundle_avoids_rehashing_the_application_package(tmp_path):
    root, payload = make_bundle(tmp_path)
    game = game_files(tmp_path, root, payload)
    bundle = inspect_native_plugin_bundle(root)
    with patch("src.services.deployed_plugin_inspection.inspect_native_plugin_bundle", side_effect=AssertionError("unexpected duplicate hash")):
        result = inspect_deployed_native_plugin(application_root=root, game_executable_path=game, bundle_inspection=bundle)
    assert result.files_compatible
