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


def _admin_builder(api: WindowsLabels) -> None:
    # 发行安装器成品须为 High，防止普通权限进程在构建后篡改；Windows 只允许管理员进程设置该标签。
    if api.process_rid() < HIGH:
        raise RuntimeError(
            "安装器须以管理员身份构建：成品要标记为 High 完整性以防普通权限进程篡改，"
            "而 Windows 只允许管理员进程设置该标签。请以管理员身份重新运行构建；"
            "输出位置不变，未创建目录、未修改任何文件或标签。")


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


ELEVATED_BUILD_ARGUMENT = "--elevated-build"


def builder_is_admin() -> bool:
    """非 Windows 不使用强制标签；Windows 上以当前 token 的完整性等级判断是否为管理员构建。"""
    return sys.platform != "win32" or _api().process_rid() >= HIGH


def request_elevated_build(argv: list[str]) -> bool:
    """经系统 UAC 确认，在新的管理员控制台中重跑同一构建命令；用户取消或启动失败返回 False。

    使用控制台解释器以便查看构建输出；标记参数只防止重复请求，不授予权限，新进程仍按 token 核验。
    """
    import os
    import subprocess
    from src.integrations.runtime.windows_privileges import request_elevated_process

    args = [str(Path(argv[0]).resolve()), *(arg for arg in argv[1:] if arg != ELEVATED_BUILD_ARGUMENT),
            ELEVATED_BUILD_ARGUMENT]
    return request_elevated_process(sys.executable, subprocess.list2cmdline(args), os.getcwd())


def disable_console_quick_edit() -> bool:
    """关闭当前控制台的快速编辑：误点窗口进入“选择”模式会挂起输出，使构建看似卡住。

    仅调整本控制台输入缓冲区的显示交互模式，尽力而为；读取或设置失败返回 False，不影响构建。
    """
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL
    # STD_INPUT_HANDLE=-10；ENABLE_QUICK_EDIT_MODE=0x40 须配合 ENABLE_EXTENDED_FLAGS=0x80 才能清除。
    handle = kernel32.GetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF))
    mode = wintypes.DWORD()
    if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel32.SetConsoleMode(handle, (mode.value & ~0x40) | 0x80))


def prepare_installer_output(output: Path) -> bool:
    if sys.platform != "win32":
        return False
    api = _api()
    _admin_builder(api)
    # 构建入口已准备输出根；首次构建只在已核对的根下补齐 installer 目录。
    parent = output.parent
    try:
        parent = plain_directory_root(parent)
    except FileNotFoundError:
        parent = plain_directory_root(parent.parent) / parent.name
        parent.mkdir(exist_ok=True)
        parent = plain_directory_root(parent)
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
    _admin_builder(api)
    return _normalize(api, setup, directory=False)
