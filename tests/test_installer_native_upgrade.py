# 验证原生安装包升级仅移除旧程序目录的误打包代理，不触及游戏与账号数据。
from types import SimpleNamespace

import pytest

import build_installer


@pytest.mark.parametrize("layout", ["native-capture-v1", "legacy"])
def test_upgrade_cleans_application_proxy_only_for_native_bundle(tmp_path, monkeypatch, layout):
    monkeypatch.setattr(build_installer, "INSTALLER_DIR", tmp_path / "installer")
    monkeypatch.setattr(build_installer, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(build_installer, "ISS_PATH", tmp_path / "installer" / "fixture.iss")
    monkeypatch.setattr(build_installer, "inspect_game_component_bundle", lambda _path: SimpleNamespace(layout=layout))
    build_installer._write_iss("2.2.4", tmp_path / "driver.exe", True)
    script = build_installer.ISS_PATH.read_text(encoding="utf-8-sig")
    deletes = script.split("[InstallDelete]", 1)[1].split("[Dirs]", 1)[0]
    assert ('Type: files; Name: "{app}\\_internal\\dwmapi.dll"' in deletes) == (layout == "native-capture-v1")
    expected = {"icuuc.dll", "icudt78.dll"} | ({"dwmapi.dll"} if layout == "native-capture-v1" else set())
    assert {line.strip() for line in deletes.splitlines() if line.strip()} == {
        f'Type: files; Name: "{{app}}\\_internal\\{name}"' for name in expected
    }
    assert 'Type: filesandordirs' not in script
    assert 'CloseApplicationsFilter=NTE_Drive_Calc.exe,nte-mod-loader.exe,nte-core.exe,nte-analysis-core.exe' in script
