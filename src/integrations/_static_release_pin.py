# 在Windows本机规则文件上持有拒写拒替换的短生命周期读取pin。
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import stat


class _FileInfo(C.Structure):
    _fields_ = [("attributes", W.DWORD), ("creation", W.FILETIME), ("access", W.FILETIME),
                ("write", W.FILETIME), ("volume", W.DWORD), ("size_high", W.DWORD),
                ("size_low", W.DWORD), ("links", W.DWORD), ("index_high", W.DWORD), ("index_low", W.DWORD)]


class WindowsStaticFilePin:
    """pin跨越hash/open/query；文件ID只用于对象绑定，绝不替代SHA。"""

    def __init__(self, path: Path):
        if os.name != "nt":
            raise OSError("正式静态字节读取需要Windows本机文件pin")
        self.path = Path(os.path.abspath(path))
        for member in reversed((self.path, *self.path.parents)):
            info = member.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError("正式静态读取路径含重解析点")
        if not self.path.is_file() or self.path.resolve() != self.path:
            raise OSError("正式静态读取需要明确的本机规则文件")
        self._kernel = C.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("CreateFileW", [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            ("CloseHandle", [W.HANDLE], W.BOOL),
            ("GetDriveTypeW", [W.LPCWSTR], W.UINT),
            ("GetFileInformationByHandle", [W.HANDLE, C.POINTER(_FileInfo)], W.BOOL),
            ("SetFilePointerEx", [W.HANDLE, C.c_longlong, C.POINTER(C.c_longlong), W.DWORD], W.BOOL),
            ("ReadFile", [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
        ):
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result
        if self._kernel.GetDriveTypeW(self.path.anchor) != 3:
            raise OSError("正式静态读取需要固定本机磁盘")
        self._handle = None
        handle = self._kernel.CreateFileW(str(self.path), 0x80000000, 1, None, 3, 0x80, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        self._handle = handle
        try:
            info = self._info()
            if info.attributes & (0x10 | stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise OSError("正式静态读取需要非重解析的规则文件")
        except BaseException:
            self.close()
            raise

    def _info(self):
        info = _FileInfo()
        if self._handle is None or not self._kernel.GetFileInformationByHandle(self._handle, C.byref(info)):
            raise OSError("静态文件pin已关闭或对象身份读取失败")
        return info

    @property
    def identity(self) -> tuple[int, int]:
        info = self._info()
        return info.volume, info.index_high << 32 | info.index_low

    @property
    def size(self) -> int:
        info = self._info()
        return info.size_high << 32 | info.size_low

    def sha256(self) -> tuple[str, bytes]:
        if not self._kernel.SetFilePointerEx(self._handle, 0, None, 0):
            raise C.WinError(C.get_last_error())
        digest, header = hashlib.sha256(), b""
        buffer = C.create_string_buffer(262144)
        while True:
            count = W.DWORD()
            if not self._kernel.ReadFile(self._handle, buffer, len(buffer), C.byref(count), None):
                raise C.WinError(C.get_last_error())
            if not count.value:
                return digest.hexdigest(), header
            block = buffer.raw[:count.value]
            if not header:
                header = block[:100]
            digest.update(block)

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._kernel.CloseHandle(handle):
                raise C.WinError(C.get_last_error())

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()
