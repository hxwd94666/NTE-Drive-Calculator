# 原生组件代理只按清单打包，禁止依赖扫描拾取同名本机代理。
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def declared_native_proxies_only():
    """Keep Qt's Windows imports from collecting workspace game proxies."""
    from PyInstaller.depend import dylib

    original = dylib.exclude_list

    class ProxyExclusions:
        def check_library(self, name):
            return Path(name).name.lower() in {"dwmapi.dll", "d3d12.dll"} or original.check_library(name)

    # This controls discovered dependencies only. Explicit manifest inputs remain
    # in the bundle; Windows supplies Qt's system libraries at runtime.
    dylib.exclude_list = ProxyExclusions()
    try:
        yield
    finally:
        dylib.exclude_list = original
