# 验证工作模式保存的真实 Windows 占用恢复、失败保留与先行撤权。
from __future__ import annotations

import ctypes
import errno
import os
import stat
from contextlib import contextmanager
from threading import Event, Thread

import pytest

from src.integrations import work_mode_settings as store_module
from src.services.work_modes.service import WorkModeService


@contextmanager
def _deny_delete_reader(path):
    if os.name != "nt":
        pytest.skip("此行为依赖 Windows 文件共享语义")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # 允许读写但不共享删除，模拟已知会令原子替换报 WinError 5 的短暂读句柄。
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()

    def close():
        nonlocal handle
        if handle is not None:
            assert kernel.CloseHandle(handle)
            handle = None

    try:
        yield close
    finally:
        close()


def test_transient_windows_reader_allows_one_atomic_mode_commit(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    policy = WorkModeService(path)
    policy.select_mode("low", risk_confirmed=True)
    old = path.read_bytes()
    blocked = Event()
    errors, sources = [], []
    replace = os.replace

    def observed_replace(source, target):
        sources.append(source)
        try:
            return replace(source, target)
        except PermissionError as error:
            # 第一次实际系统拒绝时，旧文件完整且内存权限已经撤销。
            errors.append(error.winerror)
            assert path.read_bytes() == old
            assert not policy.allowed("packet_capture")
            blocked.set()
            raise

    with _deny_delete_reader(path) as release:
        def reader():
            blocked.wait(2)
            release()

        thread = Thread(target=reader)
        thread.start()
        try:
            monkeypatch.setattr(store_module.os, "replace", observed_replace)
            policy.select_mode("offline")
        finally:
            blocked.set()
            thread.join(2)
        assert not thread.is_alive()
    assert errors and set(errors) <= {5, 32}
    assert len(set(sources)) == 1  # 只重试原子提交，不重建或重放设置请求。
    assert WorkModeService(path).settings == policy.settings
    assert policy.settings.mode.value == "offline"
    assert not list(tmp_path.glob(".work-mode-*.tmp"))


@pytest.mark.parametrize("obstruction,target", [("reader", "offline"), ("readonly", "developer"), ("readonly", "medium")])
def test_persistent_windows_obstruction_preserves_disk_and_permission_order(
    tmp_path, monkeypatch, obstruction, target,
):
    if os.name != "nt":
        pytest.skip("此行为依赖 Windows 文件共享语义")
    path = tmp_path / "settings.json"
    policy = WorkModeService(path)
    policy.select_mode("low", risk_confirmed=True)
    old, previous = path.read_bytes(), policy.settings
    attempts = []
    replace = os.replace

    def observed_replace(source, destination):
        attempts.append((source, destination))
        return replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", observed_replace)
    try:
        if obstruction == "reader":
            with _deny_delete_reader(path), pytest.raises(PermissionError) as caught:
                policy.select_mode(target, risk_confirmed=target != "offline")
        else:
            path.chmod(stat.S_IREAD)
            with pytest.raises(PermissionError) as caught:
                policy.select_mode(target, risk_confirmed=True)
        assert caught.value.winerror in {5, 32}
        assert 1 < len(attempts) <= 4
        assert len(set(attempts)) == 1
        assert path.read_bytes() == old
        assert policy.settings.mode.value == ("low" if target == "developer" else "offline")
        assert policy.allowed("packet_capture") == (target == "developer")
        assert not policy.allowed("native_load")
        if target == "developer":
            assert policy.settings == previous  # 持久化失败不授予升级权限。
        assert not list(tmp_path.glob(".work-mode-*.tmp"))
    finally:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
    # 解除真实障碍后，由调用方显式重试；未假报上次已保存。
    policy.select_mode(target, risk_confirmed=target != "offline")
    assert WorkModeService(path).settings == policy.settings


@pytest.mark.parametrize("error", [
    OSError(errno.ENOSPC, "disk full"),
    PermissionError(errno.EACCES, "ordinary access denied"),
    OSError(errno.EIO, "device failure"),
])
def test_unrelated_io_error_is_immediate_and_preserves_previous_settings(tmp_path, monkeypatch, error):
    path, other = tmp_path / "settings.json", tmp_path / "other.json"
    policy = WorkModeService(path)
    policy.select_mode("low", risk_confirmed=True)
    other.write_bytes(b"other account remains intact")
    old, previous, attempts = path.read_bytes(), policy.settings, []

    def fail(source, target):
        attempts.append((source, target))
        raise error

    monkeypatch.setattr(store_module.os, "replace", fail)
    with pytest.raises(OSError) as caught:
        policy.select_mode("developer", risk_confirmed=True)
    assert caught.value is error and len(attempts) == 1
    assert path.read_bytes() == old and policy.settings == previous
    assert other.read_bytes() == b"other account remains intact"
    assert not policy.allowed("native_load")
    assert not list(tmp_path.glob(".work-mode-*.tmp"))
