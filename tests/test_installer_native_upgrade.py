# 验证原生安装包升级仅移除旧程序目录的误打包代理，不触及游戏与账号数据。
from types import SimpleNamespace

import pytest

import build_installer


@pytest.mark.parametrize("layout", ["native-capture-v1", "native-plugins-v2", "native-plugins-v3", "legacy"])
def test_upgrade_cleans_application_proxy_only_for_native_bundle(tmp_path, monkeypatch, layout):
    monkeypatch.setattr(build_installer, "INSTALLER_DIR", tmp_path / "installer")
    monkeypatch.setattr(build_installer, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(build_installer, "ISS_PATH", tmp_path / "installer" / "fixture.iss")
    monkeypatch.setattr(build_installer, "inspect_game_component_bundle", lambda _path: SimpleNamespace(layout=layout))
    build_installer._write_iss("2.2.4", tmp_path / "driver.exe", True)
    script = build_installer.ISS_PATH.read_text(encoding="utf-8-sig")
    deletes = script.split("[InstallDelete]", 1)[1].split("[Dirs]", 1)[0]
    expected = {"icuuc.dll", "icudt78.dll"} | ({"dwmapi.dll"} if layout != "legacy" else set())
    if layout == "native-plugins-v3":
        expected.update({"d3d12.dll", "NTE_Capture.dll"})
        expected.update(
            "plugins\\" + name + suffix
            for name in ("NTE_PluginUser", "NTE_PluginCombat", "NTE_PluginHUD", "NTE_PluginPerformance")
            for suffix in (".dll", ".dll.sig")
        )
    expected_lines = {
        f'Type: files; Name: "{{app}}\\_internal\\{name}"' for name in expected
    }
    # The retired image directory belongs to the app bundle. Keep the exact
    # deletion allowlist: no broader directory, game path or account cleanup.
    expected_lines.add('Type: filesandordirs; Name: "{app}\\_internal\\assets\\game_ui"')
    assert {line.strip() for line in deletes.splitlines() if line.strip()} == expected_lines
    assert 'CloseApplicationsFilter=NTE_Drive_Calc.exe,nte-mod-loader.exe,nte-core.exe,nte-analysis-core.exe' in script
