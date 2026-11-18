# 验证安装器只由管理员构建：目录保持普通完整性、成品标记为 High，并保留失败回退。
import hashlib
import subprocess
from pathlib import Path
import sys

import pytest

from tools.release import installer_integrity as integrity

NTE_TEST_TIER = "core"


class Labels:
    # 默认模拟管理员（High）构建进程；普通权限的拒绝路径单独覆盖。
    def __init__(self, current="S:(ML;;NW;;;LW)", process=12288):
        self.current, self.process, self.writes = current, process, []

    def process_rid(self):
        return self.process

    def read(self, path):
        return self.current

    def write(self, path, value):
        self.writes.append((path, value))
        self.current = value


@pytest.mark.parametrize("sddl,rid", [("", 8192), ("S:(ML;ID;NW;;;LW)", 4096),
                                     ("S:(ML;OICI;NW;;;ME)", 8192),
                                     ("S:(ML;;NW;;;S-1-16-12288)", 12288)])
def test_label_semantics(sddl, rid):
    assert integrity.label_rid(sddl) == rid


def test_unknown_label_fails_closed():
    with pytest.raises(ValueError):
        integrity.label_rid("S:(ML;;NW;;;unexpected)")


@pytest.mark.parametrize("process", [4096, 8192])
def test_non_admin_builder_does_not_change_or_create_output(tmp_path, monkeypatch, process):
    api = Labels(process=process)
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="管理员身份构建"):
        integrity.prepare_installer_output(output)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match="管理员身份构建"):
        integrity.finalize_installer_artifact(setup)
    assert not output.exists()
    assert not api.writes and setup.read_bytes() == b"fixture"


@pytest.mark.parametrize("parent_exists", [False, True])
def test_output_keeps_path_and_limits_changes_to_owned_directory(tmp_path, monkeypatch, parent_exists):
    api = Labels()
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    output = tmp_path / "installer" / "output"
    if parent_exists:
        output.parent.mkdir()
    assert integrity.prepare_installer_output(output)
    assert api.writes == [(output, "S:(ML;OICI;NW;;;ME)")]
    assert output.is_dir()


@pytest.mark.parametrize("sddl", ["S:(ML;;NW;;;HI)", "S:(ML;;NW;;;SI)"])
def test_high_or_higher_artifact_is_not_relabelled(tmp_path, monkeypatch, sddl):
    api = Labels(current=sddl)
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    assert not integrity.finalize_installer_artifact(setup)
    assert not api.writes


@pytest.mark.parametrize("sddl", ["", "S:(ML;;NW;;;LW)", "S:(ML;ID;NW;;;ME)"])
def test_installer_artifact_must_not_cap_administrator_process_at_medium(tmp_path, monkeypatch, sddl):
    api = Labels(current=sddl)
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    assert integrity.finalize_installer_artifact(setup)
    assert api.writes == [(setup, "S:(ML;;NW;;;HI)")]
    assert setup.read_bytes() == b"fixture"


def test_failed_readback_restores_only_original_label(tmp_path, monkeypatch):
    api = Labels()
    original = api.current
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)

    def write(path, value):
        api.writes.append((path, value))
        api.current = "S:(ML;;NW;;;ME)" if len(api.writes) == 1 else value

    monkeypatch.setattr(api, "write", write)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match="回读不一致"):
        integrity.finalize_installer_artifact(setup)
    assert api.current == original
    assert api.writes[-1] == (setup, original)
    assert setup.read_bytes() == b"fixture"


def test_builder_does_not_report_complete_after_final_check_failure(tmp_path, monkeypatch, capsys):
    import build_installer as builder

    setup = tmp_path / "NTE_Drive_Calc_Setup_1.0.0.exe"
    setup.write_bytes(b"fixture")
    monkeypatch.setattr(sys, "argv", ["build_installer.py", "--version", "1.0.0", "--skip-app-build"])
    monkeypatch.setattr(builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(builder, "builder_is_admin", lambda: True)  # 不在测试中触发真实 UAC。
    monkeypatch.setattr(builder, "_configure_output_root", lambda *_: None)
    monkeypatch.setattr(builder, "prepare_installer_output", lambda *_: False)
    monkeypatch.setattr(builder, "_ensure_app_bundle", lambda **_: None)
    monkeypatch.setattr(builder, "prepare_installer_asset_stage", lambda *_: [])
    monkeypatch.setattr(builder, "validate_packaged_component_bundle", lambda *_, **__: None)
    monkeypatch.setattr(builder, "validate_packaged_ocr_models", lambda *_: None)
    monkeypatch.setattr(builder, "_find_vigem_installer", lambda *_: (Path("fixture.exe"), True))
    monkeypatch.setattr(builder, "_write_iss", lambda **_: None)
    monkeypatch.setattr(builder, "_find_iscc", lambda *_: Path("ISCC.exe"))
    monkeypatch.setattr(builder, "_run", lambda *_: None)

    def reject(_):
        raise RuntimeError("fixture integrity verification failed")

    monkeypatch.setattr(builder, "finalize_installer_artifact", reject)
    assert builder.main() == 1
    output = capsys.readouterr().out
    assert "fixture integrity verification failed" in output
    assert "Installer complete:" not in output


@pytest.mark.parametrize("accepted,expected", [(True, 0), (False, 1)])
def test_non_admin_build_requests_uac_before_any_build_step(monkeypatch, capsys, accepted, expected):
    import build_installer as builder

    requested, touched = [], []
    monkeypatch.setattr(sys, "argv", ["build_installer.py", "--version", "1.0.0", "--skip-app-build"])
    monkeypatch.setattr(builder, "builder_is_admin", lambda: False)
    monkeypatch.setattr(builder, "request_elevated_build", lambda argv: requested.append(list(argv)) or accepted)
    monkeypatch.setattr(builder, "_configure_output_root", lambda *_: touched.append("output"))
    monkeypatch.setattr(builder, "prepare_installer_output", lambda *_: touched.append("prepare"))
    assert builder.main() == expected
    # 先请求一次系统确认；同意则交给管理员窗口，取消则明确失败，两种情况都不触碰输出目录。
    assert requested == [["build_installer.py", "--version", "1.0.0", "--skip-app-build"]]
    assert touched == []
    output = capsys.readouterr().out
    assert ("新打开的管理员窗口" in output) if accepted else ("已取消" in output)


def test_elevated_build_command_keeps_arguments_and_adds_marker_once(tmp_path, monkeypatch):
    from src.integrations.runtime import windows_privileges

    captured = []
    monkeypatch.setattr(windows_privileges, "request_elevated_process",
                        lambda *call: captured.append(call) or True)
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "build_installer.py"
    argv = [str(script), "--version", "1.0 beta", integrity.ELEVATED_BUILD_ARGUMENT]
    assert integrity.request_elevated_build(argv)
    executable, parameters, directory = captured[0]
    assert executable == sys.executable and directory == str(tmp_path)
    assert parameters == subprocess.list2cmdline(
        [str(script.resolve()), "--version", "1.0 beta", integrity.ELEVATED_BUILD_ARGUMENT])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory label API")
def test_windows_same_directory_new_and_existing_artifact_keeps_bytes(tmp_path):
    api = integrity.WindowsLabels()
    if api.process_rid() < integrity.HIGH:
        # 非管理员环境核对真实拒绝：不创建输出目录、不改动已有成品，而不是跳过。
        output = tmp_path / "output"
        with pytest.raises(RuntimeError, match="管理员身份构建"):
            integrity.prepare_installer_output(output)
        assert not output.exists()
        return
    parent = tmp_path / "installer"
    parent.mkdir()
    parent_before = api.read(parent)
    output = parent / "output"
    output.mkdir()
    api.write(output, "S:(ML;OICI;NW;;;LW)")
    setup = output / "setup.exe"
    setup.write_bytes(b"isolated installer bytes")
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    assert integrity.label_rid(api.read(setup)) < integrity.MEDIUM
    assert integrity.prepare_installer_output(output)
    integrity.finalize_installer_artifact(setup)
    assert integrity.label_rid(api.read(setup)) == integrity.HIGH
    next_setup = output / "next.exe"
    next_setup.write_bytes(b"next build")
    assert integrity.label_rid(api.read(next_setup)) == integrity.MEDIUM
    assert integrity.finalize_installer_artifact(next_setup)
    assert integrity.label_rid(api.read(next_setup)) == integrity.HIGH
    assert hashlib.sha256(setup.read_bytes()).hexdigest() == digest
    assert api.read(parent) == parent_before


def test_iscc_is_found_in_default_program_files_install(tmp_path, monkeypatch):
    import build_installer as builder

    # Inno Setup 默认安装到 Program Files (x86) 且不改 PATH，发现逻辑须覆盖该位置。
    iscc = tmp_path / "x86" / "Inno Setup 6" / "ISCC.exe"
    iscc.parent.mkdir(parents=True)
    iscc.write_bytes(b"")
    monkeypatch.delenv("INNO_SETUP_ISCC", raising=False)
    monkeypatch.setattr(builder.shutil, "which", lambda _name: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "x64"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert builder._find_iscc() == iscc


def test_missing_iscc_stops_before_building_the_app(tmp_path, monkeypatch, capsys):
    import build_installer as builder

    built = []
    monkeypatch.setattr(sys, "argv", ["build_installer.py", "--version", "1.0.0"])
    monkeypatch.setattr(builder, "builder_is_admin", lambda: True)
    monkeypatch.setattr(builder, "_configure_output_root", lambda *_: None)
    monkeypatch.setattr(builder, "prepare_installer_output", lambda *_: False)
    monkeypatch.setattr(builder, "_find_iscc", lambda *_: None)
    monkeypatch.setattr(builder, "_ensure_app_bundle", lambda **_: built.append(True))
    # 缺编译器时在耗时的应用打包之前就返回 2，不再打包完成后才提示。
    assert builder.main() == 2
    assert built == []
    assert "Inno Setup compiler was not found" in capsys.readouterr().out


def test_iscc_prefers_newest_supported_major_version(tmp_path, monkeypatch):
    import build_installer as builder

    # 不写死大版本：同时装有 5/6/7 时取最高的受支持版本，低于 6 的忽略。
    for version, root in ((5, "x86"), (6, "x86"), (7, "x64")):
        folder = tmp_path / root / f"Inno Setup {version}"
        folder.mkdir(parents=True)
        (folder / "ISCC.exe").write_bytes(b"")
    monkeypatch.delenv("INNO_SETUP_ISCC", raising=False)
    monkeypatch.setattr(builder.shutil, "which", lambda _name: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "x64"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert builder._find_iscc() == tmp_path / "x64" / "Inno Setup 7" / "ISCC.exe"
    (tmp_path / "x64" / "Inno Setup 7" / "ISCC.exe").unlink()
    assert builder._find_iscc() == tmp_path / "x86" / "Inno Setup 6" / "ISCC.exe"


@pytest.mark.parametrize("readable", [True, False])
def test_elevated_console_disables_quick_edit_without_failing_build(monkeypatch, readable):
    from unittest.mock import Mock
    import ctypes
    from ctypes import wintypes

    # 误点控制台进入“选择”模式会挂起输出；只清快速编辑位并保留其余模式，读取失败时安全放弃。
    kernel = Mock()
    kernel.GetStdHandle.return_value = 7
    def get_mode(_handle, out):
        ctypes.cast(out, ctypes.POINTER(wintypes.DWORD))[0] = 0x1F7
        return readable
    kernel.GetConsoleMode.side_effect = get_mode
    kernel.SetConsoleMode.return_value = True
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity.ctypes, "WinDLL", Mock(return_value=kernel))
    assert integrity.disable_console_quick_edit() is readable
    if readable:
        kernel.SetConsoleMode.assert_called_once_with(7, (0x1F7 & ~0x40) | 0x80)
    else:
        kernel.SetConsoleMode.assert_not_called()
