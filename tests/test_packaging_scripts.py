# 测试打包脚本的版本和编码输出。
import codecs
import unittest

import build_installer
from src.app.constants import APP_VERSION


class PackagingScriptTests(unittest.TestCase):
    def test_installer_version_comes_from_app_constants(self):
        self.assertEqual(APP_VERSION, build_installer._read_app_version())

    def test_generated_installer_script_is_utf8_bom_with_chinese_text(self):
        build_installer._write_iss(APP_VERSION, build_installer.VIGEM_BUNDLE_EXE, True)

        data = build_installer.ISS_PATH.read_bytes()
        self.assertTrue(data.startswith(codecs.BOM_UTF8))

        text = data.decode("utf-8-sig")
        self.assertIn("安装程序", text)
        self.assertIn("创建桌面快捷方式", text)
        self.assertNotIn("瀹夎", text)

    def test_replace_core_config_task_updates_runtime_config_and_creates_backup(self):
        build_installer._write_iss(APP_VERSION, build_installer.VIGEM_BUNDLE_EXE, True)

        text = build_installer.ISS_PATH.read_text(encoding="utf-8-sig")

        self.assertIn('DestDir: "{app}\\config"', text)
        self.assertIn("Tasks: replacecoreconfig", text)
        self.assertIn("BackupCoreConfigBeforeReplace", text)


if __name__ == "__main__":
    unittest.main()
