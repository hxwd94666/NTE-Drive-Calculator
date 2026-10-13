# 验证帧采样在游戏切换、启动失败、停止和宿主退出时的资源归属。
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from src.integrations.presentmon_lifetime import CollectorJob, _kernel, stop_session
from src.integrations.presentmon_metrics import PresentMon

NTE_TEST_TIER = 'core'
MODULE = 'src.integrations.presentmon_metrics'


def _process_creation(kernel, handle):
    """读取保留句柄的创建时间；不会因 PID 复用认错测试进程。"""
    times = [W.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(C.byref(value) for value in times)):
        raise C.WinError(C.get_last_error())
    return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime


def _finish_owned_process(kernel, handle, expected_creation):
    """失败判定之后才收尾本次已核对身份的进程，始终使用同一保留句柄。"""
    if _process_creation(kernel, handle) != expected_creation:
        raise AssertionError('owned process creation identity changed')
    state = kernel.WaitForSingleObject(handle, 0)
    if state == 258:
        terminate = kernel.TerminateProcess
        terminate.argtypes, terminate.restype = [W.HANDLE, W.UINT], W.BOOL
        if not terminate(handle, 99) and kernel.WaitForSingleObject(handle, 0) != 0:
            raise C.WinError(C.get_last_error())
    elif state != 0:
        raise C.WinError(C.get_last_error())
    if kernel.WaitForSingleObject(handle, 3000) != 0:
        raise AssertionError('owned process cleanup did not reach physical termination')

class PresentMonLifetimeTests(unittest.TestCase):
    def source(self, reader=lambda: None):
        source = PresentMon(Path('unused'), reader)
        source._process = Mock(stdout=None, stderr=None)
        source._process.poll.return_value = None
        process = source._process
        def finish(timeout):
            process.poll.return_value = 0
            return 0
        process.wait.side_effect = finish
        source._pid = 42
        source._session = 'NTE-Calc-v2-123-456-' + 'a' * 32
        return source

    def test_game_exit_stops_live_collector_without_waiting_for_presentmon(self):
        source = self.source()
        process = source._process
        with patch(MODULE + '.stop_session') as stop:
            metrics = source.sample()
        self.assertEqual(stop.call_count, 2)
        process.wait.assert_called()
        self.assertIsNone(source._process)
        self.assertIsNone(metrics['fps_milli'])
        self.assertEqual(metrics['frame_error'], '等待游戏')

    def test_new_game_stops_old_collector_and_discards_old_window(self):
        source = self.source(lambda: 43)
        source._window.add({'TimeInSeconds': '1', 'msBetweenPresents': '10', 'SwapChainAddress': 'a'}, 1)
        with patch(MODULE + '.stop_session') as stop:
            metrics = source.sample()  # Missing executable prevents a real new launch.
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(source._pid, 43)
        self.assertIsNone(metrics['fps_milli'])
        self.assertFalse(source._window.frames)

    def test_unverifiable_game_stops_instead_of_keeping_stale_pid(self):
        source = self.source(Mock(side_effect=PermissionError))
        with patch(MODULE + '.stop_session') as stop:
            metrics = source.sample()
        self.assertEqual(stop.call_count, 2)
        self.assertIn('已停止', metrics['frame_error'])
        self.assertIsNone(source._process)

    def test_close_reaps_etw_even_after_child_exits_and_is_idempotent(self):
        source = self.source()
        source._process.poll.return_value = 0
        job = source._job = Mock()
        with patch(MODULE + '.stop_session') as stop, patch(MODULE + '.subprocess.Popen') as spawn:
            source.close()
            source.close()
        self.assertEqual(stop.call_count, 2)
        spawn.assert_not_called()
        job.close.assert_called_once()

    def test_hung_collector_is_killed_and_job_closed_when_etw_stop_fails(self):
        source = self.source()
        process = source._process
        process.wait.side_effect = [subprocess.TimeoutExpired('collector', 1), 0]
        job = source._job = Mock()
        with patch(MODULE + '.stop_session', side_effect=PermissionError):
            source.close()
        process.kill.assert_called_once()
        job.close.assert_called_once()

    def test_failed_job_attachment_does_not_leave_started_child(self):
        source = PresentMon(Path('unused'), lambda: 42)
        source._verified = source._reaped = True
        process = Mock(stdout=None, stderr=None)
        process.poll.return_value = 0
        job = Mock()
        job.attach.side_effect = OSError('job denied')
        with patch.object(Path, 'is_file', return_value=True), \
                patch(MODULE + '.session_name', return_value='NTE-Calc-v2-123-456-' + 'a' * 32), \
                patch(MODULE + '.CollectorJob', return_value=job), \
                patch(MODULE + '.subprocess.Popen', return_value=process), \
                patch(MODULE + '.stop_session'):
            metrics = source.sample()
        self.assertIsNone(source._process)
        job.close.assert_called_once()
        self.assertIn('启动失败', metrics['frame_error'])

    def test_stop_rejects_other_tools_sessions(self):
        for name in ('PresentMon', 'OtherMonitor-123', '', 'NTE-Calc-v2-invalid'):
            with self.assertRaises(ValueError):
                stop_session(name)

    @unittest.skipUnless(os.name == 'nt', 'Windows job API')
    def test_windows_job_close_really_terminates_owned_child(self):
        job = CollectorJob()
        child = subprocess.Popen([sys._base_executable, '-c', 'import time; time.sleep(30)'],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            job.attach(child)
            job.close()
            self.assertIsNotNone(child.wait(timeout=3))
        finally:
            job.close()
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)

    @unittest.skipUnless(os.name == 'nt', 'Windows job API')
    def test_abrupt_owner_exit_really_terminates_collector(self):
        # venv 的 python.exe 可能只是另有 Job 的启动器；合成 collector 必须是实际单进程。
        code = """
import ctypes as C
from ctypes import wintypes as W
import os, subprocess, sys
from src.integrations.presentmon_lifetime import CollectorJob, _kernel, _process_identity


def main():
    job = CollectorJob()
    child = subprocess.Popen(
        [sys._base_executable, '-c', 'import os, time; print(os.getpid(), flush=True); time.sleep(30)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    job.attach(child)
    real_pid = int(child.stdout.readline())
    kernel = _kernel()
    member = W.BOOL()
    check = kernel.IsProcessInJob
    check.argtypes, check.restype = [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)], W.BOOL
    if not check(int(child._handle), job._handle, C.byref(member)):
        raise C.WinError(C.get_last_error())
    print(child.pid, _process_identity(child.pid), real_pid, int(member.value), flush=True)
    if sys.stdin.read(1) != 'x':
        os._exit(2)
    os._exit(0)


if __name__ == '__main__':
    main()
"""
        kernel = _kernel()
        owner = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        owner_creation = child_creation = child_handle = None
        child_verified = False
        try:
            owner_creation = _process_creation(kernel, int(owner._handle))
            ready = Queue()
            reader = Thread(target=lambda: ready.put(owner.stdout.readline()), daemon=True)
            reader.start()
            child_pid, child_creation, real_pid, member = map(int, ready.get(timeout=5).split())
            self.assertEqual(real_pid, child_pid, 'collector fixture is a launcher, not the worker')
            self.assertEqual(member, 1, 'actual collector is not attached to CollectorJob')
            child_handle = kernel.OpenProcess(0x1000 | 0x100000 | 1, False, child_pid)
            if not child_handle:
                raise C.WinError(C.get_last_error())
            self.assertEqual(_process_creation(kernel, child_handle), child_creation)
            child_verified = True
            self.assertEqual(kernel.WaitForSingleObject(child_handle, 0), 258,
                             'collector must be live before the owner exits')
            _, stderr = owner.communicate(input='x', timeout=5)
            self.assertEqual(owner.returncode, 0, stderr)
            # owner 的退出/管道 EOF 不等于 collector 已物理终止；只认同一进程句柄的信号。
            state = kernel.WaitForSingleObject(child_handle, 3000)
            self.assertEqual(state, 0, f'collector termination not confirmed: wait={state}')
        finally:
            try:
                if owner.poll() is None:
                    if owner_creation is None:
                        owner.kill()  # Popen 自己保留的句柄仍只指向本次启动的 owner。
                    else:
                        _finish_owned_process(kernel, int(owner._handle), owner_creation)
                owner.wait(timeout=3)
            finally:
                try:
                    if child_handle and child_verified:
                        _finish_owned_process(kernel, child_handle, child_creation)
                finally:
                    if child_handle:
                        kernel.CloseHandle(child_handle)
                    for stream in (owner.stdin, owner.stdout, owner.stderr):
                        if stream is not None:
                            stream.close()
                    owner._handle.Close()
