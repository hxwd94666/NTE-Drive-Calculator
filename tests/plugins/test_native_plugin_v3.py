# 验证五 DLL 的保护与声明门禁，以及显式更新顺序；不加载或部署真实组件。
import shutil
import hashlib
from copy import deepcopy

import pytest

from src.integrations.components.native_plugin_bundle import inspect_native_plugin_bundle, SPLIT_PLUGIN_LAYOUT
from tests.plugins.native_plugin_v3_fixture import add_split_roles
from tests.plugins.native_plugin_v3_fixture import write_metadata
from tests.plugins.test_native_plugin_bundle import make_bundle
from tests.plugins.test_native_plugin_bundle import write_manifest
from tests.components.test_native_update import Host, update


def split_bundle(tmp_path):
    root, payload = make_bundle(tmp_path)
    metadata = add_split_roles(root, payload)
    write_manifest(root, payload)
    return root, payload, metadata


def test_quick_wheel_requires_signed_protected_training_pair(tmp_path):
    root, payload, metadata = split_bundle(tmp_path)
    payload['capabilities'].append('calc.quick_wheel.v2')
    write_manifest(root, payload)
    assert not inspect_native_plugin_bundle(root).ready
    for role, suffix in [('training_plugin', ''), ('training_signature', '.sig')]:
        relative = 'native/plugins/NTE_PluginTraining.dll' + suffix
        path = root / relative
        path.write_bytes(('synthetic training fixture ' + role).encode())
        payload['roles'][role] = relative
        payload['files'][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        payload['file_sizes'][relative] = path.stat().st_size
    record = deepcopy(metadata['protection']['binaries']['NTE_PluginHUD.dll'])
    record['sha256'] = payload['files'][payload['roles']['training_plugin']]
    record['notice']['component'] = 'NTE_PluginTraining'
    metadata['protection']['binaries']['NTE_PluginTraining.dll'] = record
    write_metadata(root, payload, metadata)
    write_manifest(root, payload)
    checked = inspect_native_plugin_bundle(root)
    assert checked.ready and checked.deployment_paths['training_plugin'].endswith('NTE_PluginTraining.dll')
    (root / payload['roles']['training_signature']).unlink()
    assert not inspect_native_plugin_bundle(root).ready


def test_split_bundle_binds_all_five_dlls_and_keeps_missing_hud_unavailable(tmp_path):
    root, payload, _ = split_bundle(tmp_path)
    result = inspect_native_plugin_bundle(root)
    assert result.ready and result.layout == SPLIT_PLUGIN_LAYOUT
    assert len(result.deployment_paths) == 9
    (root / payload["roles"]["hud_signature"]).unlink()
    assert not inspect_native_plugin_bundle(root).ready


@pytest.mark.parametrize("failure", ["missing_dll", "d3d_profile", "wrong_hash", "missing_notice", "wrong_notice", "input_identity"])
def test_split_bundle_rejects_protection_or_notice_mismatch(tmp_path, failure):
    root, payload, metadata = split_bundle(tmp_path)
    binaries = metadata["protection"]["binaries"]
    if failure == "missing_dll":
        binaries.pop("NTE_PluginPerformance.dll")
    elif failure == "d3d_profile":
        binaries["d3d12.dll"]["profile"] = {
            "name": "whole-file-v1", "options": ["StripDebugInfo", "Pack", "ResourceProtection"]}
    elif failure == "wrong_hash":
        binaries["NTE_PluginHUD.dll"]["sha256"] = "0" * 64
    elif failure == "missing_notice":
        binaries["NTE_PluginUser.dll"].pop("notice")
    elif failure == "wrong_notice":
        binaries["NTE_PluginHUD.dll"]["notice"]["component"] = "NTE_PluginCombat"
    else:
        metadata["sourceTreeSha256"] = "0" * 64
    write_metadata(root, payload, metadata)
    write_manifest(root, payload)
    assert not inspect_native_plugin_bundle(root).ready


def test_split_update_orders_four_plugins_and_records_the_frozen_layout(tmp_path):
    root, payload, _ = split_bundle(tmp_path)
    bundle = inspect_native_plugin_bundle(root)
    directory = tmp_path / "game"
    directory.mkdir()
    plugins = ("NTE_PluginUser.dll", "NTE_PluginCombat.dll", "NTE_PluginHUD.dll", "NTE_PluginPerformance.dll")
    for role, relative in bundle.deployment_paths.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / payload["roles"][role], path)
        if role != "host":
            path.write_bytes(("synthetic old fixture " + role).encode())
    host = Host(directory)
    host.states = dict.fromkeys(plugins, "loaded")
    result = update(root, directory, host)
    assert result.state == "updated" and result.layout == SPLIT_PLUGIN_LAYOUT
    assert host.actions == [("disable", name) for name in reversed(plugins)] + [("enable", name) for name in plugins]
