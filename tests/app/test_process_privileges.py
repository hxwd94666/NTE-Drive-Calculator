# 验证启动按实际完整性等级提权、单次重启和源码/打包参数保真。
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
from unittest.mock import Mock

import pytest

from src.integrations.runtime import windows_privileges as windows
from src.ui.startup import process_privileges as startup


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(startup.sys, "platform", "win32")
    monkeypatch.setattr(startup.sys, "argv", ["main.py", "--gui"])
    probe = Mock(return_value=0x2000)
    restart = Mock(return_value=True)
    monkeypatch.setattr(startup, "current_process_integrity", probe)
    monkeypatch.setattr(startup, "request_admin_restart", restart)
    return probe, restart


@pytest.mark.parametrize("level", [0x3000, 0x4000])
def test_high_integrity_does_not_restart(environment, level):
    probe, restart = environment
    probe.return_value = level
    startup.ensure_process_privileges()
    restart.assert_not_called()


@pytest.mark.parametrize("level", [0x1000, 0x2000, 0x2100])
def test_below_high_requests_one_restart_and_exits_old_process(environment, level):
    probe, restart = environment
    probe.return_value = level
    with pytest.raises(SystemExit) as result:
        startup.ensure_process_privileges()
    assert result.value.code == 0
    restart.assert_called_once_with()


def test_restarted_process_still_checks_its_token(environment, monkeypatch):
    _, restart = environment
    monkeypatch.setattr(startup.sys, "argv", ["main.py", windows.ADMIN_RESTART_ARGUMENT])
    with pytest.raises(startup.StartupPrivilegeError, match="权限等级仍然偏低"):
        startup.ensure_process_privileges()
    restart.assert_not_called()


def test_successful_high_integrity_child_continues(environment, monkeypatch):
    probe, restart = environment
    probe.return_value = 0x3000
    monkeypatch.setattr(startup.sys, "argv", ["main.py", windows.ADMIN_RESTART_ARGUMENT])
    startup.ensure_process_privileges()
    restart.assert_not_called()


@pytest.mark.parametrize("result", [False, OSError("fixture-start-failed")])
def test_cancel_or_restart_failure_stops_startup(environment, result):
    _, restart = environment
    if isinstance(result, Exception):
        restart.side_effect = result
    else:
        restart.return_value = result
    with pytest.raises(startup.StartupPrivilegeError, match="未获得管理员运行权限"):
        startup.ensure_process_privileges()
    restart.assert_called_once_with()


def test_probe_failure_does_not_guess_or_start_elevation(environment):
    probe, restart = environment
    probe.side_effect = OSError("fixture-probe-failed")
    with pytest.raises(startup.StartupPrivilegeError, match="权限等级读取失败"):
        startup.ensure_process_privileges()
    restart.assert_not_called()


def test_non_windows_does_not_read_token_or_restart(environment, monkeypatch):
    probe, restart = environment
    monkeypatch.setattr(startup.sys, "platform", "linux")
    startup.ensure_process_privileges()
    probe.assert_not_called()
    restart.assert_not_called()


@pytest.mark.parametrize("frozen", [False, True])
def test_restart_preserves_interpreter_arguments_and_working_directory(tmp_path, monkeypatch, frozen):
    script = tmp_path / "source tree" / "main.py"
    args = [str(script), "--gui", "--ime-compat", "--fixture-path", 'space and "quote"\\']
    monkeypatch.setattr(windows.sys, "argv", args)
    monkeypatch.setattr(windows.sys, "executable", str(tmp_path / "runtime folder" / "app.exe"))
    monkeypatch.setattr(windows.sys, "frozen", frozen, raising=False)
    monkeypatch.chdir(tmp_path)
    executable, parameters, directory = windows.admin_restart_command()
    expected = args[1:] if frozen else [str(script.resolve()), *args[1:]]
    expected.append(windows.ADMIN_RESTART_ARGUMENT)
    assert executable == windows.sys.executable
    assert parameters == subprocess.list2cmdline(expected)
    assert directory == str(tmp_path)


def test_restart_marker_is_not_duplicated(monkeypatch):
    monkeypatch.setattr(windows.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows.sys, "argv", ["app.exe", windows.ADMIN_RESTART_ARGUMENT, "--gui"])
    _, parameters, _ = windows.admin_restart_command()
    assert parameters == subprocess.list2cmdline(["--gui", windows.ADMIN_RESTART_ARGUMENT])


def test_normal_entry_accepts_hidden_restart_argument(monkeypatch):
    import main
    from src.ui import app
    from src.ui.startup import startup_data_selection
    from src.integrations.distribution import maintenance_cli
    run = Mock()
    monkeypatch.setattr(main.sys, "argv", ["main.py", windows.ADMIN_RESTART_ARGUMENT, "--gui"])
    monkeypatch.setattr(app, "run_gui", run)
    monkeypatch.setattr(maintenance_cli, "run_maintenance", lambda _: False)
    monkeypatch.setattr(startup_data_selection, "choose_first_external_root", lambda: True)
    main.main()
    run.assert_called_once_with()


@pytest.mark.parametrize("return_value, accepted", [(0, False), (5, False), (32, False), (33, True)])
def test_restart_uses_normal_runas_with_pointer_sized_result(monkeypatch, return_value, accepted):
    shell = Mock()
    shell.ShellExecuteW.return_value = return_value
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=shell))
    monkeypatch.setattr(windows, "admin_restart_command", lambda: ("APP.exe", '"ARG with spaces"', "WORKDIR"))
    assert windows.request_admin_restart() is accepted
    shell.ShellExecuteW.assert_called_once_with(None, "runas", "APP.exe", '"ARG with spaces"', "WORKDIR", 1)
    assert shell.ShellExecuteW.restype is wintypes.HINSTANCE


@pytest.mark.parametrize("fail_read", [False, True])
def test_integrity_read_uses_token_query_and_always_closes_handle(monkeypatch, fail_read):
    kernel, security = Mock(), Mock()
    kernel.GetCurrentProcess.return_value = 100
    count, rid = ctypes.c_ubyte(1), wintypes.DWORD(0x2000)
    sid = ctypes.c_ulong(1)
    def open_token(_process, access, out):
        assert access == 8
        ctypes.cast(out, ctypes.POINTER(wintypes.HANDLE))[0] = wintypes.HANDLE(200)
        return True
    def read_token(_token, kind, buffer, _capacity, size):
        assert kind == 25
        ctypes.cast(size, ctypes.POINTER(wintypes.DWORD))[0] = ctypes.sizeof(ctypes.c_void_p) + 16
        if buffer is None:
            return False
        if fail_read:
            return False
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(sid)
        return True
    security.OpenProcessToken.side_effect = open_token
    security.GetTokenInformation.side_effect = read_token
    security.GetSidSubAuthorityCount.return_value = ctypes.pointer(count)
    security.GetSidSubAuthority.return_value = ctypes.pointer(rid)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_: kernel if name == "kernel32" else security)
    if fail_read:
        with pytest.raises(OSError):
            windows.current_process_integrity()
    else:
        assert windows.current_process_integrity() == 0x2000
    kernel.CloseHandle.assert_called_once()
    assert kernel.CloseHandle.call_args.args[0].value == 200


def test_locale_keys_support_missing_patch_fallback_and_named_parameters():
    from src.domain.localization.catalog import make_catalog
    from src.integrations.preferences.locale_files import bundled_text_catalog, read_catalog_file
    from src.integrations.runtime.bundled_resources import bundled_root
    base = bundled_text_catalog()
    patch = make_catalog(read_catalog_file(bundled_root() / "docs/examples/locales/qps-ploc.json"), base=base)
    empty = make_catalog({"format_version": 1, "catalog_id": "nte-ui", "locale": "en-US",
                          "display_name": "fixture", "messages": {}}, base=base)
    for name in ("inspect_failed", "relaunch_failed", "still_low"):
        key = f"startup.privilege.{name}"
        assert patch.render(key) != base.render(key)
        assert empty.render(key) == base.render(key)
