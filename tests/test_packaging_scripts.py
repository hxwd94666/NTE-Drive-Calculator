# 测试打包脚本的版本和编码输出。
import codecs
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_installer
from src.app.constants import APP_VERSION
from src.integrations.game_component_bundle import inspect_game_component_bundle


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
        from contextlib import ExitStack
        from tests.test_native_component_bundle_build import native_source, prepare

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root, _manifest, _payload = native_source(Path(temporary))
            bundle = prepare(root)
            internal = bundle.resource_root
            stack.enter_context(patch.object(build_installer, "ROOT", root))
            stack.enter_context(patch.object(build_installer, "APP_INTERNAL", internal))
            required = {
                "APP_EXE": root / "NTE_Drive_Calc.exe",
                "APP_NTE_CORE": internal / "nte-core.exe",
                "APP_ANALYSIS_CORE": internal / "nte-analysis-core.exe",
                "APP_ANALYSIS_CORE_MANIFEST": internal / "analysis-core-meta/component.json",
                "APP_USER_SCHEMA": internal / "src/storage/sqlite/schema/001_user_data.sql",
                "APP_STATIC_DATABASE": internal / "data/game_static.sqlite3",
                "APP_STATIC_MANIFEST": internal / "data/manifest.json",
                "APP_SHARED_DATABASE_SEED": internal / "data/app_shared.sqlite3",
                "APP_SHAPE_BONUS_BASELINE": internal / "data/migrations/shape_bonus_defaults_2.0.2.json",
            }
            for field, path in required.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()  # Preserve the synthetic component bytes and manifest hashes.
                stack.enter_context(patch.object(build_installer, field, path))
            stack.enter_context(
                patch.object(build_installer, "validate_packaged_ocr_models")
            )
            build_installer._validate_app_bundle()
            for field, message in (("APP_STATIC_DATABASE", "静态数据库"), ("APP_NTE_CORE", "nte-core")):
                path = required[field]
                contents = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(RuntimeError, message):
                    build_installer._validate_app_bundle()
                path.write_bytes(contents)
                build_installer._validate_app_bundle()

    def test_pyinstaller_collects_component_bundle_schema_and_static_database(self):
        source = Path("build_exe.py").read_text(encoding="utf-8")

        self.assertIn(
            "from tools.release.native_component_bundle_build import "
            "native_component_build_inputs",
            source,
        )
        self.assertIn("component_bundle = prepare_component_bundle(", source)
        self.assertIn("inputs=native_component_build_inputs(ROOT)", source)
        self.assertIn("_append_add_data(component_bundle.manifest_path, \".\")", source)
        self.assertIn(
            'validate_packaged_component_bundle(output / "_internal")',
            source,
        )
        self.assertIn('ANALYSIS_CORE_PATH = THIRD_PARTY_DIR / "analysis-core"', source)
        self.assertIn('"battle_page_v1" not in capabilities', source)
        self.assertIn('_append_add_data(SQLITE_SCHEMA_DIR, "src/storage/sqlite/schema")', source)
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
        installer_source = Path("build_installer.py").read_text(encoding="utf-8")
        self.assertNotIn("workshop-sync", source)
        self.assertNotIn("workshop-sync", installer_source)

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

    def test_rapidocr_models_are_bundled_once_and_validated(self):
        build_source = Path("build_exe.py").read_text(encoding="utf-8")
        installer_source = Path("build_installer.py").read_text(encoding="utf-8")
        release_source = Path("tools/release/prepare_release.py").read_text(encoding="utf-8")

        self.assertIn('excludes=["models/*"]', build_source)
        self.assertIn('"assets/ocr/models"', build_source)
        self.assertIn("build_source_ocr_models().values()", build_source)
        self.assertIn("validate_packaged_ocr_models", build_source)
        self.assertIn("validate_packaged_ocr_models(APP_INTERNAL)", installer_source)
        self.assertIn("validate_packaged_ocr_models(APP_INTERNAL)", release_source)

    def test_windows_validator_is_not_part_of_runtime_packaging(self):
        packaging_sources = (
            Path("build_exe.py").read_text(encoding="utf-8"),
            Path("installer/NTE_Drive_Calc.iss").read_text(encoding="utf-8-sig"),
        )

        for source in packaging_sources:
            with self.subTest(source=source[:40]):
                self.assertNotIn("windows_validation", source)

    def test_installer_prefers_the_organized_vigembus_location(self):
        source = Path("build_installer.py").read_text(encoding="utf-8")

        self.assertIn('THIRD_PARTY_DIR / "vigembus" / "bin"', source)
        self.assertIn("LEGACY_VIGEM_BUNDLE_EXE", source)

    def test_committed_native_bundle_has_verified_files_and_redistribution_records(self):
        inspection = inspect_game_component_bundle(Path.cwd())

        self.assertTrue(inspection.ready, inspection.issues)
        self.assertEqual("native-capture-v1", inspection.layout)
        self.assertEqual(
            "third_party/native-capture/capture/d3d12.dll",
            inspection.roles["host"],
        )
        self.assertEqual(
            "third_party/native-capture/capture/NTE_Capture.dll",
            inspection.roles["capture_plugin"],
        )
        self.assertEqual(
            "third_party/native-capture/core/nte-core.exe",
            inspection.roles["core"],
        )
        for role in (
            "capture_license",
            "capture_source",
            "core_license",
            "core_source",
            "loader_license",
            "loader_source",
        ):
            with self.subTest(role=role):
                self.assertIn(role, inspection.roles)
                self.assertTrue(Path(inspection.roles[role]).is_file())

if __name__ == "__main__":
    unittest.main()
