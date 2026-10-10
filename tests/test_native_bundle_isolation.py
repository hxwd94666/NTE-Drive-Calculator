# 验证游戏代理隔离后仍能按正式清单部署，且拒绝污染 Calc 依赖目录。
import json

import pytest

from src.integrations.native_plugin_bundle import inspect_native_plugin_bundle, resolve_bundled_native_core
from src.services.native_loader_workspace import prepare_native_loader_workspace, inspect_native_loader_workspace
from src.services.native_plugin_deployment import deploy_native_component_files
from tests.test_native_component_bundle_build import native_source, prepare
from tools.release.game_component_bundle_build import validate_packaged_component_bundle


@pytest.mark.parametrize("loader", [False, True])
def test_isolated_bundle_preserves_game_layout_and_component_identity(tmp_path, loader):
    root, source, payload = native_source(tmp_path, loader=loader)
    result = prepare(root)
    package = result.resource_root
    bundle = inspect_native_plugin_bundle(package)
    assert bundle.ready
    assert not (package / "d3d12.dll").exists()
    assert not (package / "plugins").exists()
    assert bundle.roles["host"] == "native-capture/d3d12.dll"
    assert resolve_bundled_native_core(package) == package / "nte-core.exe"
    for role, relative in bundle.deployment_paths.items():
        assert bundle.roles[role] == "native-capture/" + relative
        assert (package / bundle.roles[role]).read_bytes() == (root / payload["roles"][role]).read_bytes()
    validate_packaged_component_bundle(package, source_manifest_path=source)

    game = tmp_path / "game"
    deployed = deploy_native_component_files(
        application_root=package, directory_path=game,
        operation_guard=lambda _capability: None, game_running=lambda: False,
    )
    assert set(deployed.managed_files) == set(bundle.deployment_paths.values())
    workspace = tmp_path / "loader"
    prepare_native_loader_workspace(
        application_root=package, workspace_path=workspace,
        operation_guard=lambda _capability: None, game_running=lambda: False,
    )
    assert inspect_native_loader_workspace(application_root=package, workspace_path=workspace).files_compatible
    for relative in deployed.managed_files:
        assert (game / relative).read_bytes() == (workspace / relative).read_bytes()
    # Removing a declared host must still fail closed, even though Core exists.
    (package / bundle.roles["host"]).unlink()
    assert (package / "nte-core.exe").is_file()
    with pytest.raises(ValueError, match="缺少文件"):
        resolve_bundled_native_core(package)


@pytest.mark.parametrize("relative", [
    "d3d12.dll", "dwmapi.dll", "NTE_Capture.dll", "plugins/NTE_PluginHUD.dll",
    "native-capture/extra.dll", "native-capture/plugins/extra.dll",
])
def test_package_rejects_unisolated_proxies_and_undeclared_payload(tmp_path, relative):
    root, _source, _payload = native_source(tmp_path)
    package = prepare(root).resource_root
    path = package / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unexpected game component")
    with pytest.raises(ValueError):
        validate_packaged_component_bundle(package)


def test_package_rejects_manifest_that_moves_host_back_to_dll_search_root(tmp_path):
    root, _source, _payload = native_source(tmp_path)
    result = prepare(root)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    old = payload["roles"]["host"]
    assert old != "d3d12.dll"
    (result.resource_root / old).rename(result.resource_root / "d3d12.dll")
    for field in ("files", "file_sizes"):
        payload[field]["d3d12.dll"] = payload[field].pop(old)
    payload["roles"]["host"] = "d3d12.dll"
    result.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="布局"):
        validate_packaged_component_bundle(result.resource_root)
