# 用合成组件验证原生打包、完整许可、安装器与发布门禁，不构建程序或安装器。
import hashlib
import json

import pytest

from src.integrations.native_plugin_bundle import NATIVE_PLUGIN_CAPABILITIES
from tests.native_plugin_v3_fixture import add_split_roles
from tools.release.game_component_bundle_build import prepare_component_bundle, validate_packaged_component_bundle
from tools.release.native_component_bundle_build import (
    NATIVE_NOTICES, NATIVE_PROGRAMS, NATIVE_ROLE_DESTINATIONS,
    native_component_build_inputs, native_distribution_path,
)


def native_source(tmp_path, *, loader=False):
    root = tmp_path / "source"
    names = {*NATIVE_PROGRAMS, *("third_party/native-capture/" + name for name in NATIVE_NOTICES),
             "third_party/native-capture/core/licenses/example-1.0/LICENSE-MIT"}
    roles = {role: next(key for key in names if native_distribution_path(key) == path)
             for role, path in NATIVE_ROLE_DESTINATIONS.items()}
    if loader:
        names.update("third_party/mod-loader/" + name for name in (
            "bin/nte-mod-loader.exe", "LICENSE", "SOURCE.md", "THIRD_PARTY_LICENSES.md",
            "licenses/MinHook-LICENSE.txt", "licenses/ManualMap-LICENSE.txt",
        ))
        roles.update(loader="third_party/mod-loader/bin/nte-mod-loader.exe",
                     loader_license="third_party/mod-loader/LICENSE", loader_source="third_party/mod-loader/SOURCE.md")
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic fixture " + name).encode())
    payload = {"protocol_version": 1, "layout": "native-capture-v1", "capture_protocol_version": 1,
               "source_commits": {"capture": "a" * 40, "core": "b" * 40},
               "input_digests": {"capture": "c" * 64, "core": "d" * 64},
               "capabilities": sorted(NATIVE_PLUGIN_CAPABILITIES), "roles": roles,
               "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names},
               "file_sizes": {name: (root / name).stat().st_size for name in names}}
    add_split_roles(root, payload, prefix="third_party/native-capture/capture/")
    manifest = root / "third_party/native-capture/component-bundle.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return root, manifest, payload


def prepare(root):
    return prepare_component_bundle(application_root=root, inputs=native_component_build_inputs(root),
                                    output_parent=root / "build")


@pytest.mark.parametrize("loader", [False, True])
def test_native_bundle_preserves_all_notices_identity_and_capabilities(tmp_path, loader):
    root, source, payload = native_source(tmp_path, loader=loader)
    result = prepare(root)
    bundled = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    for field in ("source_commits", "input_digests", "capabilities", "capture_protocol_version", "layout"):
        assert bundled[field] == payload[field]
    assert set(bundled["files"]) == set(bundled["file_sizes"])
    assert len(bundled["files"]) == len(payload["files"])
    assert (result.resource_root / "nte-mod-loader.exe").exists() == loader
    assert (result.resource_root / "native-capture/plugins/NTE_PluginHUD.dll").is_file()
    assert not (result.resource_root / "NTE_Capture.dll").exists()
    validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)
    payload["input_digests"]["capture"] = "e" * 64
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="当前已批准|未通过核对"):
        validate_packaged_component_bundle(result.resource_root, source_manifest_path=source)


@pytest.mark.parametrize("path", ["capture/private.cpp", "capture/source.zip", "core/licenses/example-1.0/code.rs"])
def test_native_manifest_cannot_approve_private_code_or_archives(tmp_path, path):
    root, manifest, payload = native_source(tmp_path)
    key = "third_party/native-capture/" + path
    source = root / key
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"must never publish")
    payload["files"][key] = hashlib.sha256(source.read_bytes()).hexdigest()
    payload["file_sizes"][key] = source.stat().st_size
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="禁止携带源码"):
        prepare(root)
    assert not (root / "build").exists()


def test_omitted_dependency_license_and_undeclared_packaged_members_are_rejected(tmp_path):
    root, source, payload = native_source(tmp_path)
    key = "third_party/native-capture/core/licenses/example-1.0/LICENSE-MIT"
    payload["files"].pop(key)
    payload["file_sizes"].pop(key)
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="依赖许可未全部"):
        prepare(root)
    (root / key).unlink()
    result = prepare(root)
    (result.resource_root / "licenses/native-capture/capture/private.cpp").write_bytes(b"private")
    with pytest.raises(ValueError, match="清单外"):
        validate_packaged_component_bundle(result.resource_root)


def test_installer_and_release_accept_native_layout_without_legacy_mods(tmp_path, monkeypatch):
    import build_installer as installer
    from tools.release import prepare_release as release

    root, _source, _payload = native_source(tmp_path)
    (root / "NOTICE").write_text("fixture notice", encoding="utf-8")
    result = prepare(root)
    monkeypatch.setattr(installer, "ROOT", root)
    monkeypatch.setattr(installer, "APP_INTERNAL", result.resource_root)
    for field in ("APP_EXE", "APP_NTE_CORE", "APP_ANALYSIS_CORE", "APP_ANALYSIS_CORE_MANIFEST",
                  "APP_USER_SCHEMA", "APP_STATIC_DATABASE", "APP_STATIC_MANIFEST",
                  "APP_SHARED_DATABASE_SEED", "APP_SHAPE_BONUS_BASELINE"):
        path = tmp_path / field
        path.write_bytes(b"unrelated app fixture")
        monkeypatch.setattr(installer, field, path)
    # This fixture covers the native bundle, not the separately tested OCR model package.
    monkeypatch.setattr(installer, "validate_packaged_ocr_models", lambda _root: None)
    installer._validate_app_bundle()
    monkeypatch.setattr(release, "ROOT", root)
    standalone = root / "third_party" / "nte-core" / "bin" / "nte-core.exe"
    standalone.parent.mkdir(parents=True, exist_ok=True)
    standalone.write_bytes((root / _payload["roles"]["core"]).read_bytes())
    monkeypatch.setattr(release, "validate_core_capabilities", lambda _core: None)
    release.validate_components()
    standalone.write_bytes(b"different core")
    with pytest.raises(RuntimeError, match="字节不一致"):
        release.validate_components()


@pytest.mark.parametrize("capabilities", [[], ["capture_wait_v1"], ["buff_snapshot_v1"],
                                        ["capture_wait_v1", "buff_snapshot_v1"],
                                        ["capture_wait_v1", "buff_snapshot_v1", "inventory_observed_items_v1"]])
def test_release_checks_actual_packet_capabilities(tmp_path, monkeypatch, capabilities):
    from tools.release import prepare_release as release

    class Client:
        hello_result = {"capabilities": capabilities}

        def __init__(self, *, executable, required_source):
            assert executable == tmp_path / "core.exe"
            assert required_source == "packet"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(release, "NteCoreClient", Client)
    if len(capabilities) == 3:
        release.validate_core_capabilities(tmp_path / "core.exe")
    else:
        with pytest.raises(RuntimeError, match="握手缺少"):
            release.validate_core_capabilities(tmp_path / "core.exe")


def test_release_rejects_core_that_drops_wait_or_buff_capability(monkeypatch, tmp_path):
    from tools.release import prepare_release as release

    class Client:
        hello_result = {"capabilities": ["capture", "inventory", "capture_wait_v1"]}

        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(release, "NteCoreClient", Client)
    with pytest.raises(RuntimeError, match="buff_snapshot_v1"):
        release.validate_core_capabilities(tmp_path / "nte-core.exe")


def test_optional_loader_rejects_missing_license_roles(tmp_path):
    root, source, payload = native_source(tmp_path, loader=True)
    payload["roles"].pop("loader_license")
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="成套声明"):
        prepare(root)
