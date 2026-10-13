# 原生组件代理只按清单打包，禁止依赖扫描拾取同名本机代理。
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def declared_native_proxies_only():
    """仅阻止依赖发现误收同名游戏代理；显式清单输入不受影响，退出必恢复原规则。"""
    from PyInstaller.depend import dylib

    original = dylib.exclude_list

    class ProxyExclusions:
        def check_library(self, name):
            return Path(name).name.lower() in {"dwmapi.dll", "d3d12.dll"} or original.check_library(name)

    # 只控制动态发现，不删除清单显式输入；Qt 的系统库由 Windows 提供。
    dylib.exclude_list = ProxyExclusions()
    try:
        yield
    finally:
        dylib.exclude_list = original
