# 仅修正本次安装器输出的完整性标签，保留 DACL、工作区和系统临时目录设置。
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import cache
from pathlib import Path
import re
import sys

from tools.release.managed_paths import plain_directory_root, plain_path_stat

MEDIUM = 8192
HIGH = 12288
LABEL_SECURITY_INFORMATION = 0x10


def label_rid(sddl: str) -> int:
    """Windows 将未标记对象视为 Medium；未知标签不作为已校验结果。"""
    labels = re.findall(r"\(ML;[^;]*;[^;]*;;;([^)]*)\)", sddl)
    if not labels:
        if "(ML;" in sddl:
            raise ValueError("安装器完整性标签格式无效。")
        return MEDIUM
    if len(labels) != 1:
        raise ValueError("安装器包含多个完整性标签。")
    aliases = {"LW": 4096, "ME": MEDIUM, "MP": 8448, "HI": 12288, "SI": 16384}
    value = labels[0]
    if value in aliases:
        return aliases[value]
    if re.fullmatch(r"S-1-16-\d+", value):
        return int(value.rsplit("-", 1)[1])
    raise ValueError("安装器包含未知完整性标签。")


class WindowsLabels:
    """只查询 token 或读写 mandatory label，不申请提权、不写审计项或访问授权。"""

    def __init__(self):
        c, w = ctypes, wintypes
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.security = c.WinDLL("advapi32", use_last_error=True)
        declarations = [
            (self.kernel, "GetCurrentProcess", [], w.HANDLE),
            (self.kernel, "CloseHandle", [w.HANDLE], w.BOOL),
            (self.kernel, "LocalFree", [c.c_void_p], c.c_void_p),
            (self.security, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
            (self.security, "GetTokenInformation",
             [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            (self.security, "ConvertSidToStringSidW", [c.c_void_p, c.POINTER(w.LPWSTR)], w.BOOL),
            (self.security, "GetNamedSecurityInfoW",
             [w.LPWSTR, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p,
              c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)], w.DWORD),
            (self.security, "ConvertSecurityDescriptorToStringSecurityDescriptorW",
             [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.c_void_p], w.BOOL),
            (self.security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
             [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p], w.BOOL),
            (self.security, "GetSecurityDescriptorSacl",
             [c.c_void_p, c.POINTER(w.BOOL), c.POINTER(c.c_void_p), c.POINTER(w.BOOL)], w.BOOL),
            (self.security, "SetNamedSecurityInfoW",
             [w.LPWSTR, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p], w.DWORD),
        ]
        for library, name, args, result in declarations:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    def _require(self, success):
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def _free(self, pointer):
        if pointer:
            self.kernel.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))

    def process_rid(self) -> int:
        token = wintypes.HANDLE()
        self._require(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
        try:
            size = wintypes.DWORD()
            self.security.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            self._require(self.security.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)))
            # token 信息中的 SID 指向 buffer 内部；转换完成前保持 buffer 存活。
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            text = wintypes.LPWSTR()
            self._require(self.security.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                return int(text.value.rsplit("-", 1)[1])
            finally:
                self._free(text)
        finally:
            self.kernel.CloseHandle(token)

    def read(self, path: Path) -> str:
        descriptor, sacl = ctypes.c_void_p(), ctypes.c_void_p()
        status = self.security.GetNamedSecurityInfoW(
            str(path), 1, LABEL_SECURITY_INFORMATION, None, None, None,
            ctypes.byref(sacl), ctypes.byref(descriptor))
        if status:
            raise ctypes.WinError(status)
        text = wintypes.LPWSTR()
        try:
            self._require(self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, LABEL_SECURITY_INFORMATION, ctypes.byref(text), None))
            return text.value
        finally:
            self._free(text)
            self._free(descriptor)

    def write(self, path: Path, sddl: str) -> None:
        descriptor, sacl = ctypes.c_void_p(), ctypes.c_void_p()
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        self._require(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None))
        try:
            self._require(self.security.GetSecurityDescriptorSacl(
                descriptor, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted)))
            status = self.security.SetNamedSecurityInfoW(
                str(path), 1, LABEL_SECURITY_INFORMATION, None, None, None, sacl)
            if status:
                raise ctypes.WinError(status)
        finally:
            self._free(descriptor)


@cache
def _api() -> WindowsLabels:
    return WindowsLabels()


def _normal_builder(api: WindowsLabels) -> None:
    if api.process_rid() < MEDIUM:
        raise RuntimeError(
            "构建解释器处于 Low 完整性，安装器发布检查已停止；"
            "请使用普通完整性的构建解释器重试。输出位置不变，未申请提权或修改工作区权限。")


def _normalize(api: WindowsLabels, path: Path, *, directory: bool) -> bool:
    before = api.read(path)
    # 目录只需普通写入；管理员安装器成品不能被显式 Medium 限制执行等级。
    required = MEDIUM if directory else HIGH
    if label_rid(before) >= required:
        return False
    desired = "S:(ML;OICI;NW;;;ME)" if directory else "S:(ML;;NW;;;HI)"
    try:
        api.write(path, desired)
        if label_rid(api.read(path)) != required:
            raise RuntimeError("安装器完整性标签回读不一致。")
    except Exception:
        # 标签修改失败只还原该对象原标签，不改 DACL、所有者、目录位置或其他文件。
        if api.read(path) != before:
            api.write(path, before)
        raise
    return True


def prepare_installer_output(output: Path) -> bool:
    if sys.platform != "win32":
        return False
    api = _api()
    _normal_builder(api)
    # 先检查既有父目录，再创建唯一归本安装器所有的输出目录，不跟随联接。
    parent = plain_directory_root(output.parent)
    output = parent / output.name
    output.mkdir(exist_ok=True)
    plain_directory_root(output)
    return _normalize(api, output, directory=True)


def finalize_installer_artifact(setup: Path) -> bool:
    plain_directory_root(setup.parent)
    info = plain_path_stat(setup)
    if not setup.is_file() or info.st_size == 0:
        raise ValueError("安装器成品必须是非空普通文件。")
    if sys.platform != "win32":
        return False
    api = _api()
    _normal_builder(api)
    return _normalize(api, setup, directory=False)
