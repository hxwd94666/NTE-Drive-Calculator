# 测试打包脚本的版本和编码输出。
import codecs
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_installer
from src.app.constants import APP_VERSION


class PackagingScriptTests(unittest.TestCase):
    def test_installer_version_comes_from_app_constants(self):
        self.assertEqual(APP_VERSION, build_installer._read_app_version())

    def test_explicit_external_installer_tools_are_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iscc = root / "ISCC.exe"
            vigem = root / "ViGEmBusSetup_x64.msi"
            iscc.touch()
            vigem.touch()

            self.assertEqual(iscc.resolve(), build_installer._find_iscc(iscc))
            self.assertEqual(
                (vigem.resolve(), False),
                build_installer._find_vigem_installer(vigem),
            )

    def test_installer_tool_paths_can_come_from_external_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "local.paths.json"
            iscc = root / "ISCC.exe"
            config_path.write_text(
                json.dumps({"inno_setup_iscc": str(iscc)}),
                encoding="utf-8",
            )

            config = build_installer._load_local_config(config_path)
            with patch.dict("os.environ", {}, clear=True):
                configured = build_installer._configured_path(
                    None,
                    "INNO_SETUP_ISCC",
                    config,
                    "inno_setup_iscc",
                )

            self.assertEqual(iscc, configured)

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

    def test_installer_rejects_bundle_missing_runtime_data_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_exe = root / "NTE_Drive_Calc.exe"
            internal = root / "_internal"
            core = internal / "nte-core.exe"
            schema = internal / "src/storage/sqlite/schema/001_user_data.sql"
            static_database = internal / "data/game_static.sqlite3"
            app_exe.touch()
            core.parent.mkdir(parents=True)
            core.touch()
            schema.parent.mkdir(parents=True)
            schema.touch()
            static_database.parent.mkdir(parents=True)
            static_database.touch()

            with (
                patch.object(build_installer, "APP_EXE", app_exe),
                patch.object(build_installer, "APP_INTERNAL", internal),
                patch.object(build_installer, "APP_NTE_CORE", core),
                patch.object(build_installer, "APP_USER_SCHEMA", schema),
                patch.object(build_installer, "APP_STATIC_DATABASE", static_database),
            ):
                build_installer._validate_app_bundle()
                static_database.unlink()
                with self.assertRaisesRegex(RuntimeError, "静态数据库"):
                    build_installer._validate_app_bundle()
                static_database.touch()
                core.unlink()
                with self.assertRaisesRegex(RuntimeError, "nte-core"):
                    build_installer._validate_app_bundle()

    def test_pyinstaller_collects_core_schema_and_required_static_database(self):
        source = Path("build_exe.py").read_text(encoding="utf-8")

        self.assertIn('NTE_CORE_ENV = "NTE_CORE_EXE"', source)
        self.assertIn('THIRD_PARTY_DIR / "nte-core" / "bin" / "nte-core.exe"', source)
        self.assertIn('EQUIPMENT_PLUGIN_ENV = "NTE_EQUIPMENT_PLUGIN_DLL"', source)
        self.assertIn('THIRD_PARTY_DIR / "equipment-plugin" / "bin" / "dwmapi.dll"', source)
        self.assertIn('_append_add_data(SQLITE_SCHEMA_DIR, "src/storage/sqlite/schema")', source)
        self.assertIn('_append_add_binary(nte_core_path, ".")', source)
        self.assertIn('"SOURCE.md"', source)
        self.assertIn('ROOT / "NOTICE"', source)
        self.assertIn('STATIC_DATABASE_PATH = ROOT / "data" / "game_static.sqlite3"', source)
        self.assertIn('_required_build_file("发行版静态数据库", STATIC_DATABASE_PATH)', source)
        self.assertIn('_append_add_data(static_database_path, "data")', source)

        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertNotIn("NTE_GAME_STATIC_DB_URL", workflow)

    def test_installer_prefers_the_organized_vigembus_location(self):
        source = Path("build_installer.py").read_text(encoding="utf-8")

        self.assertIn('THIRD_PARTY_DIR / "vigembus" / "bin"', source)
        self.assertIn("LEGACY_VIGEM_BUNDLE_EXE", source)

    def test_release_workflow_uses_the_committed_nte_core_component(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertNotIn("NTE_CORE_RELEASE_TAG", workflow)
        self.assertNotIn("nte-core-windows-x64.zip", workflow)
        self.assertIn("actions/checkout@v7", workflow)

    def test_committed_nte_core_binary_has_redistribution_records(self):
        component_dir = Path("third_party/nte-core")

        self.assertTrue((component_dir / "bin" / "nte-core.exe").is_file())
        self.assertTrue((component_dir / "LICENSE").is_file())
        self.assertTrue((component_dir / "SOURCE.md").is_file())

    def test_release_workflow_supports_manual_release_publish(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("publish_release:", workflow)
        self.assertIn("release_tag:", workflow)
        self.assertIn("github.event.inputs.publish_release", workflow)
        self.assertIn("gh release create $tag", workflow)
        self.assertIn("--target $env:GITHUB_SHA", workflow)


if __name__ == "__main__":
    unittest.main()
