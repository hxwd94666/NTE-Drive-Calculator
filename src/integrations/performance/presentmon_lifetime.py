# 约束帧采样子进程归属，直接关闭自己的 ETW 会话并回收失主会话。
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import re
from uuid import uuid4


class _Wnode(C.Structure):
    _fields_ = [('BufferSize', W.ULONG), ('ProviderId', W.ULONG),
                ('HistoricalContext', C.c_ulonglong), ('TimeStamp', C.c_longlong),
                ('Guid', C.c_byte * 16), ('ClientContext', W.ULONG), ('Flags', W.ULONG)]


class _TraceProperties(C.Structure):
    _fields_ = [('Wnode', _Wnode), *[(name, W.ULONG) for name in (
        'BufferSize', 'MinimumBuffers', 'MaximumBuffers', 'MaximumFileSize',
        'LogFileMode', 'FlushTimer', 'EnableFlags', 'AgeLimit', 'NumberOfBuffers',
        'FreeBuffers', 'EventsLost', 'BuffersWritten', 'LogBuffersLost', 'RealTimeBuffersLost')],
        ('LoggerThreadId', W.HANDLE), ('LogFileNameOffset', W.ULONG), ('LoggerNameOffset', W.ULONG),
        ('Name', W.WCHAR * 1024), ('FileName', W.WCHAR * 1024)]


class _JobBasic(C.Structure):
    _fields_ = [('ProcessTime', C.c_longlong), ('JobTime', C.c_longlong),
                ('LimitFlags', W.DWORD), ('MinimumWorkingSet', C.c_size_t),
                ('MaximumWorkingSet', C.c_size_t), ('ActiveProcessLimit', W.DWORD),
                ('Affinity', C.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class _JobLimits(C.Structure):
    _fields_ = [('Basic', _JobBasic), ('IoCounters', C.c_ulonglong * 6),
                ('ProcessMemoryLimit', C.c_size_t), ('JobMemoryLimit', C.c_size_t),
                ('PeakProcessMemoryUsed', C.c_size_t), ('PeakJobMemoryUsed', C.c_size_t)]


def _kernel():
    api = C.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
        ('CloseHandle', [W.HANDLE], W.BOOL),
        ('GetProcessTimes', [W.HANDLE, *([C.POINTER(W.FILETIME)] * 4)], W.BOOL),
        ('WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD),
        ('CreateJobObjectW', [C.c_void_p, W.LPCWSTR], W.HANDLE),
        ('SetInformationJobObject', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
        ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
    ):
        fn = getattr(api, name)
        fn.argtypes, fn.restype = args, result
    return api


def _process_identity(pid):
    """用创建时间识别活进程；确认退出返回 None，权限不足不推断死亡。"""
    api = _kernel()
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        error = C.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return None
        raise C.WinError(error)
    try:
        state = api.WaitForSingleObject(handle, 0)
        if state == 0:
            return None
        if state != 258:
            raise C.WinError(C.get_last_error())
        times = [W.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(C.byref(t) for t in times)):
            raise C.WinError(C.get_last_error())
        return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    finally:
        api.CloseHandle(handle)


_OWNED = re.compile(r'NTE-Calc-v2-(\d+)-(\d+)-[a-f0-9]{32}\Z')


def session_name():
    identity = _process_identity(os.getpid()) if os.name == 'nt' else 0
    return f'NTE-Calc-v2-{os.getpid()}-{identity}-{uuid4().hex}'


def _properties():
    value = _TraceProperties()
    value.Wnode.BufferSize = C.sizeof(value)
    value.LoggerNameOffset = _TraceProperties.Name.offset
    value.LogFileNameOffset = _TraceProperties.FileName.offset
    return value


def stop_session(name):
    """仅停止本产品命名的会话，不另起采集器充当停止命令。"""
    if not _OWNED.fullmatch(name):
        raise ValueError('not an owned PresentMon session')
    if os.name != 'nt':
        return
    api = C.WinDLL('advapi32', use_last_error=True)
    fn = api.ControlTraceW
    fn.argtypes = [C.c_ulonglong, W.LPCWSTR, C.POINTER(_TraceProperties), W.ULONG]
    fn.restype = W.ULONG
    value = _properties()
    status = fn(0, name, C.byref(value), 1)  # EVENT_TRACE_CONTROL_STOP
    if status not in (0, 4201):  # Already absent is also a completed stop.
        raise C.WinError(status)


def reap_abandoned_sessions():
    """只回收本产品命名空间内、且 owner 已确认退出或被 PID 复用的会话。"""
    if os.name != 'nt':
        return
    api = C.WinDLL('advapi32', use_last_error=True)
    fn = api.QueryAllTracesW
    fn.argtypes = [C.POINTER(C.POINTER(_TraceProperties)), W.ULONG, C.POINTER(W.ULONG)]
    fn.restype = W.ULONG
    values = [_properties() for _ in range(128)]
    pointers = (C.POINTER(_TraceProperties) * len(values))(*(C.pointer(v) for v in values))
    count = W.ULONG()
    if fn(pointers, len(values), C.byref(count)) != 0:
        return  # 枚举被拒绝或不完整时，不推断任何会话的归属。
    for value in values[:count.value]:
        match = _OWNED.fullmatch(value.Name)
        if not match:
            continue
        try:
            if _process_identity(int(match[1])) != int(match[2]):
                stop_session(value.Name)
        except OSError:
            continue


class CollectorJob:
    """不可继承的 Windows Job；owner 退出后由系统结束已绑定的采集器。"""
    def __init__(self):
        self._handle = None
        if os.name != 'nt':
            return
        self._api = _kernel()
        self._handle = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise C.WinError(C.get_last_error())
        limits = _JobLimits()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(self._handle, 9, C.byref(limits), C.sizeof(limits)):
            error = C.get_last_error()
            self.close()
            raise C.WinError(error)

    def attach(self, process):
        if self._handle and not self._api.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise C.WinError(C.get_last_error())

    def close(self):
        handle, self._handle = self._handle, None
        if handle:
            self._api.CloseHandle(handle)
