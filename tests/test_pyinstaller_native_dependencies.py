# 验证清单代理不被依赖扫描重复发现，且构建后恢复 PyInstaller 规则。
import unittest
from unittest.mock import patch

from PyInstaller.depend import bindepend, dylib
from tools.release.pyinstaller_native_dependencies import declared_native_proxies_only


class NativeDependencyTests(unittest.TestCase):
    def test_discovery_skips_workspace_proxy_but_keeps_declared_binary(self):
        declared = [("d3d12.dll", "C:/approved/d3d12.dll", "BINARY")]
        with patch.object(bindepend, "get_imports", return_value={
            ("dwmapi.dll", "C:/workspace/dwmapi.dll"),
        }), declared_native_proxies_only():
            self.assertFalse(dylib.include_library("C:/workspace/DWMAPI.dll"))
            result = bindepend.binary_dependency_analysis(declared)
        self.assertEqual(result, declared)

    def test_other_rules_and_restoration_survive_exception(self):
        original = dylib.exclude_list
        before = dylib.include_library("C:/approved/nte-core.exe")
        with self.assertRaises(RuntimeError):
            with declared_native_proxies_only():
                self.assertEqual(dylib.include_library("C:/approved/nte-core.exe"), before)
                raise RuntimeError("build failure")
        self.assertIs(dylib.exclude_list, original)
