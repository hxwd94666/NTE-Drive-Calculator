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

    def test_stats_catalog_is_always_replaced_without_an_installer_choice(self):
        build_installer._write_iss(APP_VERSION, build_installer.VIGEM_BUNDLE_EXE, True)

        text = build_installer.ISS_PATH.read_text(encoding="utf-8-sig")

        self.assertIn('DestDir: "{app}\\config"', text)
        self.assertIn('config\\stats.json"; DestDir: "{app}\\config"; Flags: ignoreversion', text)
        self.assertNotIn("replacecoreconfig", text)
        self.assertNotIn("BackupCoreConfigBeforeReplace", text)
        self.assertIn("game_static.previous.sqlite3", text)
        self.assertIn("FileCopy(OldStaticDatabase, MigrationBackup, False)", text)

    def test_installer_rejects_bundle_missing_runtime_data_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_exe = root / "NTE_Drive_Calc.exe"
            internal = root / "_internal"
            core = internal / "nte-core.exe"
            mods_plugin = internal / "dwmapi.dll"
            mod_set = internal / "plugins/nte-mods.enabled"
            equipment_mod = internal / "plugins/nte-mods/equipment.nte"
            combat_clock_mod = internal / "plugins/nte-mods/combat-clock.nte"
            schema = internal / "src/storage/sqlite/schema/001_user_data.sql"
            static_database = internal / "data/game_static.sqlite3"
            static_manifest = internal / "data/manifest.json"
            shared_database_seed = internal / "data/app_shared.sqlite3"
            shape_bonus_baseline = (
                internal / "data/migrations/shape_bonus_defaults_2.0.2.json"
            )
            app_exe.touch()
            core.parent.mkdir(parents=True)
            core.touch()
            mods_plugin.touch()
            mod_set.parent.mkdir(parents=True)
            mod_set.touch()
            equipment_mod.parent.mkdir(parents=True)
            equipment_mod.touch()
            combat_clock_mod.touch()
            schema.parent.mkdir(parents=True)
            schema.touch()
            static_database.parent.mkdir(parents=True)
            static_database.touch()
            static_manifest.touch()
            shared_database_seed.touch()
            shape_bonus_baseline.parent.mkdir(parents=True)
            shape_bonus_baseline.touch()

            with (
                patch.object(build_installer, "APP_EXE", app_exe),
                patch.object(build_installer, "APP_INTERNAL", internal),
                patch.object(build_installer, "APP_NTE_CORE", core),
                patch.object(build_installer, "APP_MODS_PLUGIN", mods_plugin),
                patch.object(build_installer, "APP_MOD_SET", mod_set),
                patch.object(build_installer, "APP_EQUIPMENT_MOD", equipment_mod),
                patch.object(build_installer, "APP_COMBAT_CLOCK_MOD", combat_clock_mod),
                patch.object(build_installer, "APP_USER_SCHEMA", schema),
                patch.object(build_installer, "APP_STATIC_DATABASE", static_database),
                patch.object(build_installer, "APP_STATIC_MANIFEST", static_manifest),
                patch.object(
                    build_installer,
                    "APP_SHARED_DATABASE_SEED",
                    shared_database_seed,
                ),
                patch.object(
                    build_installer,
                    "APP_SHAPE_BONUS_BASELINE",
                    shape_bonus_baseline,
                ),
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
        self.assertIn('MODS_PLUGIN_ENV = "NTE_MODS_PLUGIN_DLL"', source)
        self.assertIn('LEGACY_EQUIPMENT_PLUGIN_ENV = "NTE_EQUIPMENT_PLUGIN_DLL"', source)
        self.assertIn('THIRD_PARTY_DIR / "mods-plugin" / "bin" / "dwmapi.dll"', source)
        self.assertIn('MODS_PLUGIN_WORKSPACE_DIR = THIRD_PARTY_DIR / "mods-plugin" / "workspace"', source)
        self.assertIn('_append_add_data(MODS_PLUGIN_WORKSPACE_DIR, "plugins")', source)
        self.assertIn('_append_add_data(SQLITE_SCHEMA_DIR, "src/storage/sqlite/schema")', source)
        self.assertIn('_append_add_binary(nte_core_path, ".")', source)
        self.assertIn('"SOURCE.md"', source)
        self.assertIn('ROOT / "NOTICE"', source)
        self.assertIn('STATIC_DATABASE_PATH = ROOT / "data" / "game_static.sqlite3"', source)
        self.assertIn('STATIC_MANIFEST_PATH = ROOT / "data" / "manifest.json"', source)
        self.assertIn('STATIC_MIGRATION_DATA_DIR = ROOT / "data" / "migrations"', source)
        self.assertIn('SHARED_DATABASE_SEED_PATH = ROOT / "data" / "app_shared.sqlite3"', source)
        self.assertIn('_required_build_file("发行版静态数据库", STATIC_DATABASE_PATH)', source)
        self.assertIn('_required_build_file("发行版静态数据库清单", STATIC_MANIFEST_PATH)', source)
        self.assertIn('_append_add_data(static_database_path, "data")', source)
        self.assertIn('_append_add_data(STATIC_MIGRATION_DATA_DIR, "data/migrations")', source)
        self.assertIn('_append_add_data(shared_database_seed_path, "data")', source)

    def test_pyinstaller_cleanup_does_not_delete_unrelated_build_directories(self):
        source = Path("build_exe.py").read_text(encoding="utf-8")

        self.assertIn('PACKAGE_BUILD_DIR = BUILD / PACKAGE_NAME', source)
        self.assertIn('PACKAGE_ONEDIR_DIR = DIST / PACKAGE_NAME', source)
        self.assertIn('PACKAGE_ONEFILE_EXE = DIST / f"{PACKAGE_NAME}.exe"', source)
        self.assertIn('for path in (PACKAGE_BUILD_DIR, PACKAGE_ONEDIR_DIR, PACKAGE_ONEFILE_EXE):', source)
        self.assertNotIn('for path in (DIST, BUILD):', source)

    def test_mouse_scan_runtime_dependencies_are_declared_and_bundled(self):
        project = Path("pyproject.toml").read_text(encoding="utf-8")
        build_source = Path("build_exe.py").read_text(encoding="utf-8")

        for dependency in ("mss", "pyautogui", "opencv-python", "numpy"):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, project)
        for hidden_import in ('"mss"', '"pyautogui"', '"cv2"', '"numpy"'):
            with self.subTest(hidden_import=hidden_import):
                self.assertIn(hidden_import, build_source)

    def test_windows_validator_is_not_part_of_runtime_packaging(self):
        packaging_sources = (
            Path("build_exe.py").read_text(encoding="utf-8"),
            Path("NTE_Drive_Calc.spec").read_text(encoding="utf-8"),
            Path("installer/NTE_Drive_Calc.iss").read_text(encoding="utf-8-sig"),
        )

        for source in packaging_sources:
            with self.subTest(source=source[:40]):
                self.assertNotIn("windows_validation", source)

    def test_installer_prefers_the_organized_vigembus_location(self):
        source = Path("build_installer.py").read_text(encoding="utf-8")

        self.assertIn('THIRD_PARTY_DIR / "vigembus" / "bin"', source)
        self.assertIn("LEGACY_VIGEM_BUNDLE_EXE", source)

    def test_committed_nte_core_binary_has_redistribution_records(self):
        component_dir = Path("third_party/nte-core")

        self.assertTrue((component_dir / "bin" / "nte-core.exe").is_file())
        self.assertTrue((component_dir / "LICENSE").is_file())
        self.assertTrue((component_dir / "SOURCE.md").is_file())

        mods_component_dir = Path("third_party/mods-plugin")
        self.assertTrue((mods_component_dir / "bin" / "dwmapi.dll").is_file())
        self.assertTrue((mods_component_dir / "workspace" / "nte-mods.enabled").is_file())
        self.assertTrue((mods_component_dir / "workspace" / "nte-mods" / "equipment.nte").is_file())
        self.assertTrue((mods_component_dir / "workspace" / "nte-mods" / "combat-clock.nte").is_file())
        self.assertTrue((mods_component_dir / "LICENSE").is_file())
        self.assertTrue((mods_component_dir / "SOURCE.md").is_file())

if __name__ == "__main__":
    unittest.main()
