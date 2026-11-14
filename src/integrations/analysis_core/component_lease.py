# 在单次请求内固定资源路径和字节身份，退出即释放，不缓存跨任务的核验结论。
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import stat

from src.integrations.static_catalog._static_release_pin import WindowsStaticFilePin, _FileInfo


class AnalysisResourceLease:
    """本机 Windows 文件拒写、祖先目录拒改名；仅存活 lease 可复用完整核验。"""

    def __init__(self, paths):
        self.paths = tuple(dict.fromkeys(Path(path).absolute() for path in paths))
        self._stack = None
        self._identities = {}
        self._pins = {}
        self._pin_identities = {}
        self.pinned = False

    def __enter__(self):
        with ExitStack() as stack:
            if os.name == "nt":
                kernel = C.WinDLL("kernel32", use_last_error=True)
                kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
                kernel.CreateFileW.restype = W.HANDLE
                kernel.GetFileInformationByHandle.argtypes = [W.HANDLE, C.POINTER(_FileInfo)]
                kernel.GetFileInformationByHandle.restype = W.BOOL
                kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
                directories = sorted({parent for path in self.paths for parent in path.parents}, key=lambda p: len(p.parts))
                # 逐层从根打开；拒绝重解析点，并持续拒绝 DELETE（目录改名/替换）。
                # 允许目录 READ/WRITE 共享，不阻止同目录的账号/日志等其他文件正常写入。
                for directory in directories:
                    handle = kernel.CreateFileW(str(directory), 0, 3, None, 3, 0x02200000, None)
                    if handle == C.c_void_p(-1).value:
                        raise C.WinError(C.get_last_error())
                    def close(handle=handle):
                        if not kernel.CloseHandle(handle):
                            raise C.WinError(C.get_last_error())
                    stack.callback(close)
                    info = _FileInfo()
                    if not kernel.GetFileInformationByHandle(handle, C.byref(info)):
                        raise C.WinError(C.get_last_error())
                    if not info.attributes & 0x10 or info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        raise OSError("analysis resource directory binding changed")
                for path in self.paths:
                    pin = stack.enter_context(WindowsStaticFilePin(path))
                    self._pins[path] = pin
                    self._pin_identities[path] = pin.metadata_identity
                self.pinned = True
            self._identities = {path: self._identity(path) for path in self.paths}
            self._stack = stack.pop_all()
        return self

    @staticmethod
    def _identity(path):
        path = path.resolve(strict=True)
        info = path.stat()
        return str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino

    def identities(self, paths):
        if self._stack is None:
            raise RuntimeError("analysis resource lease closed")
        # 非 Windows 无拒写 pin，仍每次按原语义重读，不用 mtime 冒充字节保护。
        result = []
        for path in map(lambda item: Path(item).absolute(), paths):
            if self.pinned and self._pins[path].metadata_identity == self._pin_identities[path]:
                result.append(self._identities[path])
            else:
                # 纯时间元数据可不需要写文件数据的权限；仍按原完整身份比较拒绝变化。
                result.append(self._identity(path))
        return tuple(result)

    def __exit__(self, *exception):
        stack, self._stack = self._stack, None
        self.pinned = False
        self._identities.clear()
        self._pins.clear()
        self._pin_identities.clear()
        if stack is not None:
            return stack.__exit__(*exception)


class ComponentLeaseMixin:
    def _init_component_lease(self):
        # 同一 client 的不同线程/上下文不共享租约；重入借用者不负责关闭 owner。
        self._component_lease = ContextVar("analysis_component_lease", default=None)

    @contextmanager
    def verified_session(self, *, extra_paths=(), checkpoint=None):
        from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
        identity = self._component_identity
        active = self._component_lease.get()
        paths = tuple(extra_paths) + tuple(path for path in (identity.executable, identity.manifest_path) if path is not None)
        if active is not None and set(map(Path, paths)) <= set(active[1].paths):
            self._verify_leased_component(checkpoint)
            yield active[1]
            return
        try:
            resources = AnalysisResourceLease(paths)
            resources.__enter__()
        except OSError as error:
            raise NativeAnalysisError('独立分析组件读取失败，请重新部署完整组件并重启应用') from error
        try:
            identity.verify(lambda: self._checkpoint(checkpoint))
            token = self._component_lease.set((identity, resources))
            try:
                # 复用仅限同一冻结 owner 的存活资源租约；退场先回收子进程再释放字节 pin。
                with self.worker_session(checkpoint=checkpoint):
                    yield resources
            finally:
                self._component_lease.reset(token)
        finally:
            resources.__exit__(None, None, None)

    def resource_identities(self, paths):
        active = self._component_lease.get()
        if active is not None and active[1].pinned and set(map(Path, paths)) <= set(active[1].paths):
            return active[1].identities(paths)
        return None

    def _verify_leased_component(self, checkpoint):
        identity = self._component_identity
        active = self._component_lease.get()
        self._checkpoint(checkpoint)
        if active is not None and active[0] == identity and active[1].pinned:
            active[1].identities((identity.executable,))
        else:
            identity.verify(lambda: self._checkpoint(checkpoint))
