# 验证构建输入、安装器覆盖与编码；全部写入隔离夹具，不运行编译或清理。
import codecs
import hashlib
import os
import subprocess
import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import build_installer
from tools.release.game_component_bundle_build import ComponentBuildInput, PreparedComponentBundle
from tools.release.ocr_build_inputs import OcrBuildInputs
from src.integrations.vision.recognition.ocr.backend_support import OcrBackendSupport
from src.app.constants import APP_VERSION


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_installer_text(tmp_path, monkeypatch):
    # 生成文本只属于本次测试；不改工作区安装器脚本，也不运行编译器。
    monkeypatch.setattr(build_installer, "ISS_PATH", tmp_path / "synthetic.iss")


class PackagingScriptTests(unittest.TestCase):
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

    def test_generated_installer_preserves_utf8_bom_and_version_metadata(self):
        build_installer._write_iss(APP_VERSION, build_installer.VIGEM_BUNDLE_EXE, True)

        data = build_installer.ISS_PATH.read_bytes()
        self.assertTrue(data.startswith(codecs.BOM_UTF8))

        text = data.decode("utf-8-sig")
        self.assertIn(f'#define MyAppVersion "{APP_VERSION}"', text)
        self.assertIn(f"VersionInfoVersion={build_installer.windows_numeric_version(APP_VERSION)}", text)
        self.assertIn("VersionInfoTextVersion={#MyAppVersion}", text)
        self.assertIn("VersionInfoProductTextVersion={#MyAppVersion}", text)

    def test_stats_catalog_is_always_replaced_without_an_installer_choice(self):
        build_installer._write_iss(APP_VERSION, build_installer.VIGEM_BUNDLE_EXE, True)

        text = build_installer.ISS_PATH.read_text(encoding="utf-8-sig")

        self.assertIn('DestDir: "{app}\\config"', text)
        self.assertIn('config\\stats.json"; DestDir: "{app}\\config"; Flags: ignoreversion', text)
        self.assertNotIn("replacecoreconfig", text)
        self.assertNotIn("BackupCoreConfigBeforeReplace", text)
        self.assertIn("game_static.previous.sqlite3", text)
        self.assertIn("FileCopy(OldStaticDatabase, MigrationBackup, False)", text)

    def test_installer_overwrites_core_even_when_version_text_is_unchanged(self):
        build_installer._write_iss(APP_VERSION, build_installer.VIGEM_BUNDLE_EXE, True)
        text = build_installer.ISS_PATH.read_text(encoding="utf-8-sig")
        self.assertIn('DestDir: "{app}\\_internal"; Flags: ignoreversion recursesubdirs', text)
        self.assertIn('CloseApplicationsFilter=NTE_Drive_Calc.exe,nte-mod-loader.exe,nte-core.exe', text)

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
                path.touch()  # 只保证合成成员存在，保留已生成组件字节与清单哈希。
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


def test_build_entries_import_without_preparation_cleanup_or_compiler():
    # 冷导入真实根消费者；守卫阻断写入、清理、编译及可选 OCR 库加载。
    code = """
import os, pathlib, shutil, sqlite3, sys
import PyInstaller.__main__
def forbidden(*args, **kwargs):
    raise AssertionError('build import performed a write or external action')
for name in ('mkdir', 'unlink', 'write_bytes', 'write_text'):
    setattr(pathlib.Path, name, forbidden)
for name in ('rmtree', 'copytree', 'copy2'):
    setattr(shutil, name, forbidden)
sqlite3.connect = forbidden
PyInstaller.__main__.run = forbidden
class NoBackendImport:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'openvino', 'onnxruntime', 'rapidocr_openvino', 'rapidocr_onnxruntime'}:
            raise AssertionError('ordinary build import loaded an OCR backend')
sys.meta_path.insert(0, NoBackendImport())
before = dict(os.environ)
import build_exe, build_installer
assert dict(os.environ) == before
print('BUILD_IMPORT_OK: no inputs prepared, cleaned or compiled')
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code], cwd=ROOT,
        env={**os.environ, "NTE_TESTING": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "BUILD_IMPORT_OK" in result.stdout


@pytest.fixture
def executable_inputs(tmp_path, monkeypatch):
    import build_exe as build
    from src.integrations import role_catalog_release

    root = tmp_path / "release inputs"
    fields = {
        "ROOT": "", "OUTPUT_ROOT": "", "DIST": "dist", "BUILD": "build", "SPEC": "NTE_Drive_Calc.spec",
        "PACKAGE_BUILD_DIR": "build/NTE_Drive_Calc", "PACKAGE_ONEDIR_DIR": "dist/NTE_Drive_Calc",
        "PACKAGE_ONEFILE_EXE": "dist/NTE_Drive_Calc.exe", "THIRD_PARTY_DIR": "third_party",
        "SQLITE_SCHEMA_DIR": "src/storage/sqlite/schema",
        "STATIC_DATABASE_PATH": "data/game_static.sqlite3", "STATIC_MANIFEST_PATH": "data/manifest.json",
        "STATIC_MIGRATION_DATA_DIR": "data/migrations", "SHARED_DATABASE_SEED_PATH": "data/app_shared.sqlite3",
        "ANALYSIS_CORE_PATH": "third_party/analysis-core/bin/nte-analysis-core.exe",
        "ANALYSIS_CORE_MANIFEST_PATH": "third_party/analysis-core/component.json",
    }
    for name, relative in fields.items():
        monkeypatch.setattr(build, name, root / relative)
    relatives = [
        "main.py", *(f"config/{name}" for name in build.CONFIG_RELEASE_FILES),
        "config/private-preference.json", "assets/locales/zh-CN.json", "assets/ocr/keys.txt",
        "src/storage/sqlite/schema/001_user_data.sql", "data/game_static.sqlite3", "data/manifest.json",
        "src/storage/sqlite/schema/shared/001_shared_data.sql", "src/storage/sqlite/schema/__init__.py",
        "src/storage/sqlite/schema/__pycache__/__init__.cpython-311.pyc",
        "src/storage/sqlite/schema/__pycache__/not-a-release-resource.sql", "src/storage/sqlite/schema/notes.txt",
        "data/app_shared.sqlite3", "data/migrations/shape-defaults.json",
        "data/role_catalog/game_static.sqlite3", "data/role_catalog/manifest.json",
        "data/role_catalog/game_ui/manifest.json", "data/role_catalog/game_ui/skills/fixture.png",
        "third_party/presentmon/PresentMon.exe", "third_party/presentmon/component.json",
        "third_party/presentmon/LICENSE.txt", "third_party/presentmon/SOURCE.md",
        "third_party/analysis-core/bin/nte-analysis-core.exe", "third_party/analysis-core/component.json",
        *(f"third_party/analysis-core/{name}" for name in build.ANALYSIS_CORE_RELEASE_FILES),
        "LICENSE", "NOTICE", "checked-bundle/nte-core.exe", "checked-bundle/nte-mod-loader.exe",
        "checked-bundle/d3d12.dll", "checked-bundle/component-bundle.json",
        "checked-ocr/config.yaml", "checked-ocr/onnxruntime.dll",
        "build/ocr-support-input/onnx_cpu/backend_support.json",
        *(f"assets/ocr/models/{name}.onnx" for name in ("det", "cls", "rec")),
    ]
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic input")
    for name in build.CONFIG_RELEASE_DIRECTORIES:
        (root / "config" / name).mkdir()
    payload_hash = hashlib.sha256(b"synthetic input").hexdigest()
    (root / "third_party/presentmon/component.json").write_text(json.dumps({"sha256": payload_hash}))
    build.ANALYSIS_CORE_MANIFEST_PATH.write_text(json.dumps({
        "engine": "nte-analysis-core", "engine_version": "0.3.0", "sha256": payload_hash,
        "capabilities": ["battle_page_v1", "main_static_catalog_v1"],
    }))
    role = SimpleNamespace(database_path=root / "data/role_catalog/game_static.sqlite3",
                           asset_root=root / "data/role_catalog/game_ui", dataset_id="synthetic", sha256="fixture")
    monkeypatch.setattr(role_catalog_release, "resolve_role_catalog", lambda _path: role)
    monkeypatch.setattr(role_catalog_release, "validate_role_assets", Mock(return_value={"files": {"skills/fixture.png": {}}}))
    # 上游身份/模型校验复用专项证据；本例只核对正式构建消费者不丢已校验输入。
    inputs = tuple(ComponentBuildInput(root / "checked-bundle" / name, name)
                   for name in ("nte-core.exe", "nte-mod-loader.exe", "d3d12.dll"))
    monkeypatch.setattr(build, "native_component_build_inputs", Mock(return_value=inputs))
    monkeypatch.setattr(build, "prepare_component_bundle", Mock(return_value=PreparedComponentBundle(
        root / "checked-bundle", root / "checked-bundle/component-bundle.json", inputs,
    )))
    support = OcrBackendSupport("onnx_cpu")
    monkeypatch.setattr(build, "load_backend_support", Mock(return_value=support))
    monkeypatch.setattr(build, "collect_ocr_build_inputs", Mock(return_value=OcrBuildInputs(
        ("onnxruntime", "rapidocr_onnxruntime"), ("openvino", "rapidocr_openvino"),
        ((str(root / "checked-ocr/config.yaml"), "rapidocr_onnxruntime"),),
        ((str(root / "checked-ocr/onnxruntime.dll"), "onnxruntime/capi"),),
    )))
    monkeypatch.setattr(build, "build_source_ocr_models", Mock(return_value={
        name: root / f"assets/ocr/models/{name}.onnx" for name in ("det", "cls", "rec")
    }))
    monkeypatch.setattr(build, "write_ocr_support_manifest", Mock(side_effect=lambda path, _support: path))
    monkeypatch.setattr(build, "shared_dictionary_path", lambda: root / "assets/ocr/keys.txt")
    monkeypatch.setattr(build, "load_ocr_dictionary", Mock())
    monkeypatch.setattr(build, "_find_package_dir", lambda _name: None)
    monkeypatch.setattr(build, "_remove_package_artifact", Mock())
    outside = tmp_path / "caller cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)
    return build, root, support


@pytest.mark.parametrize("mode", ["onedir", "onefile", "isolated-onedir", "isolated-onefile",
                                 "bad-hash", "missing-page-capability", "missing-schema", "empty-schema"])
def test_executable_main_freezes_root_resources_and_rejects_invalid_analysis(executable_inputs, monkeypatch, mode):
    build, root, support = executable_inputs
    build_mode = mode.removeprefix("isolated-")
    output = root / "build/isolated output" if mode.startswith("isolated-") else root
    if output != root:
        output.mkdir()
        (root / "dist").mkdir()
        (root / "dist/keep-old-release.exe").write_bytes(b"old distribution retained")
    monkeypatch.setattr(sys, "argv", [str(ROOT / "build_exe.py"),
        *(["--output-root", str(output)] if output != root else []),
        *(["--onefile", "--console"] if build_mode == "onefile" else [])])
    schema_failure = mode in {"missing-schema", "empty-schema"}
    if schema_failure:
        directory = root / mode
        if mode == "empty-schema":
            directory.mkdir()
        monkeypatch.setattr(build, "SQLITE_SCHEMA_DIR", directory)
    if mode in {"bad-hash", "missing-page-capability"}:
        manifest = json.loads(build.ANALYSIS_CORE_MANIFEST_PATH.read_text())
        if mode == "bad-hash":
            manifest["sha256"] = "0" * 64
        else:
            manifest["capabilities"].remove("battle_page_v1")
        build.ANALYSIS_CORE_MANIFEST_PATH.write_text(json.dumps(manifest))
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    before_path = os.environ.get("PATH")

    class BuildIntercepted(Exception):
        pass

    compiler = Mock(side_effect=BuildIntercepted)
    monkeypatch.setattr(build.PyInstaller.__main__, "run", compiler)

    def forbidden(*args, **kwargs):
        raise AssertionError("synthetic build entry attempted a write, cleanup or process")

    # 替身只截获输入/编译边界；真实清理函数体、复制、删除及外部进程均不执行。
    with monkeypatch.context() as guard:
        for name in ("mkdir", "unlink", "write_bytes", "write_text"):
            guard.setattr(Path, name, forbidden)
        for name in ("rmtree", "copytree", "copy2"):
            guard.setattr(build.shutil, name, forbidden)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        if schema_failure:
            with pytest.raises(FileNotFoundError, match="SQLite schema"):
                build.main()
        elif mode in {"bad-hash", "missing-page-capability"}:
            with pytest.raises(RuntimeError, match="能力或哈希不匹配"):
                build.main()
        else:
            with pytest.raises(BuildIntercepted):
                build.main()
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert os.environ.get("PATH") == before_path
    if schema_failure or mode in {"bad-hash", "missing-page-capability"}:
        compiler.assert_not_called()
        build._remove_package_artifact.assert_not_called()
        return
    args = compiler.call_args.args[0]
    assert args[0] == str(root / "main.py")
    for name, path in (("distpath", output / "dist"), ("workpath", output / "build"), ("specpath", output), ("upx-dir", root)):
        assert f"--{name}={path}" in args
    assert build.OUTPUT_ROOT == output and build.SPEC == output / "NTE_Drive_Calc.spec"
    assert f"--{build_mode}" in args
    assert ("--console" in args) == (build_mode == "onefile")
    separator = ";" if sys.platform == "win32" else ":"
    data = {tuple(arg.split("=", 1)[1].rsplit(separator, 1)) for arg in args if arg.startswith("--add-data=")}
    for relative, destination in (
        ("assets", "assets"),
        ("data/game_static.sqlite3", "data"), ("data/manifest.json", "data"), ("data/migrations", "data/migrations"),
        ("data/app_shared.sqlite3", "data"), ("data/role_catalog/game_ui/skills/fixture.png", "data/role_catalog/game_ui/skills"),
        ("checked-bundle/component-bundle.json", "."), ("checked-bundle/d3d12.dll", "."),
        ("third_party/analysis-core/component.json", "analysis-core-meta"),
        *((f"config/{name}", "config") for name in build.CONFIG_RELEASE_FILES),
        *((f"config/{name}", f"config/{name}") for name in build.CONFIG_RELEASE_DIRECTORIES),
        *((f"assets/ocr/models/{name}.onnx", "assets/ocr/models") for name in ("det", "cls", "rec")),
    ):
        assert (str(root / relative), destination) in data
    schema_data = {(source, destination) for source, destination in data
                   if Path(source).is_relative_to(build.SQLITE_SCHEMA_DIR)}
    assert schema_data == {
        (str(build.SQLITE_SCHEMA_DIR / "001_user_data.sql"), "src/storage/sqlite/schema"),
        (str(build.SQLITE_SCHEMA_DIR / "shared/001_shared_data.sql"), "src/storage/sqlite/schema/shared"),
    }
    assert all(Path(source).suffix == '.sql' and '__pycache__' not in Path(source).parts
               for source, _destination in schema_data)
    generated_manifest = output / "build/ocr-support-input/onnx_cpu/backend_support.json"
    assert (str(generated_manifest), "assets/ocr") in data
    assert not any("private-preference.json" in source for source, _destination in data)
    assert "--hidden-import=onnxruntime" in args and "--exclude-module=openvino" in args
    assert "--hidden-import=openvino" not in args
    for package in ("mss", "pyautogui", "cv2", "numpy"):
        assert f"--hidden-import={package}" in args
    for package in ("pytest", "_pytest", "pygments"):
        assert f"--exclude-module={package}" in args
    assert "--exclude-module=scipy" not in args and "--exclude-module=scipy._lib._testutils" not in args
    build.load_backend_support.assert_called_once_with()
    assert build.collect_ocr_build_inputs.call_args.args == (support,)
    build.build_source_ocr_models.assert_called_once_with(support=support)
    build.write_ocr_support_manifest.assert_called_once_with(generated_manifest, support)
    assert build.prepare_component_bundle.call_args.kwargs['output_parent'] == output / 'build/component-bundles'
    assert build.prepare_component_bundle.call_args.kwargs['application_root'] == root
    build.native_component_build_inputs.assert_called_once_with(root)
    assert [call.args[0] for call in build._remove_package_artifact.call_args_list] == [
        output / "build/NTE_Drive_Calc", output / "dist/NTE_Drive_Calc", output / "dist/NTE_Drive_Calc.exe",
    ]


@pytest.mark.parametrize('isolated', [False, True])
def test_installer_output_root_covers_app_stage_iss_and_child_build(tmp_path, monkeypatch, isolated):
    build = build_installer
    source = tmp_path / 'source'
    source.mkdir()
    output = source / 'build/isolated output' if isolated else source
    # 只将输出全局量加入自动恢复；源工具、图标与清单仍绑定源码根。
    output_fields = ('OUTPUT_ROOT', 'DIST_APP', 'APP_EXE', 'APP_INTERNAL', 'APP_NTE_CORE',
        'APP_ANALYSIS_CORE', 'APP_ANALYSIS_CORE_MANIFEST', 'APP_USER_SCHEMA', 'APP_STATIC_DATABASE',
        'APP_STATIC_MANIFEST', 'APP_SHARED_DATABASE_SEED', 'APP_SHAPE_BONUS_BASELINE',
        'INSTALLER_DIR', 'OUTPUT_DIR', 'INSTALL_STAGE_INTERNAL', 'ISS_PATH')
    for name in output_fields:
        monkeypatch.setattr(build, name, getattr(build, name))
    monkeypatch.setattr(build, 'ROOT', source)
    icon = source / 'assets/app_icon.ico'
    icon.parent.mkdir()
    icon.write_bytes(b'synthetic icon')
    monkeypatch.setattr(build, 'APP_ICON', icon)
    runner, validate, stage = Mock(), Mock(), Mock(return_value=[])
    monkeypatch.setattr(build, '_run', runner)
    monkeypatch.setattr(build, '_app_process_running', Mock(return_value=False))
    monkeypatch.setattr(build, '_validate_app_bundle', validate)
    monkeypatch.setattr(build, 'prepare_installer_asset_stage', stage)
    manifest = source / 'third_party/source-component.json'
    manifest_reader = Mock(return_value=manifest)
    monkeypatch.setattr(build, 'source_component_manifest', manifest_reader)
    bundle_gate, ocr_gate = Mock(), Mock()
    monkeypatch.setattr(build, 'validate_packaged_component_bundle', bundle_gate)
    monkeypatch.setattr(build, 'validate_packaged_ocr_models', ocr_gate)
    monkeypatch.setattr(build, 'inspect_game_component_bundle', Mock(return_value=SimpleNamespace(layout='synthetic')))
    driver = source / 'third_party/synthetic-driver.exe'
    monkeypatch.setattr(build, '_find_vigem_installer', Mock(return_value=(driver, True)))
    # 文本生成是真实入口；mock 子构建/暂存/身份门禁，仅核此消费者的路径接线。
    monkeypatch.setattr(sys, 'argv', ['build_installer.py', '--generate-only',
        *(['--output-root', str(output)] if isolated else [])])
    forbidden = Mock(side_effect=AssertionError('no compiler or external process'))
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert build.main() == 0
    runner.assert_called_once_with([sys.executable, str(source / 'build_exe.py'),
        *(['--output-root', str(output)] if isolated else [])])
    validate.assert_called_once_with()
    for name in output_fields:
        assert getattr(build, name).is_relative_to(output), name
    assert build.APP_ICON == icon and build.DIST_APP == output / 'dist/NTE_Drive_Calc'
    stage.assert_called_once_with(build.APP_INTERNAL, output / 'build/installer-stage/_internal', output)
    manifest_reader.assert_called_once_with(source)
    bundle_gate.assert_called_once_with(build.INSTALL_STAGE_INTERNAL, source_manifest_path=manifest)
    ocr_gate.assert_called_once_with(build.INSTALL_STAGE_INTERNAL)
    text = build.ISS_PATH.read_text(encoding='utf-8-sig')
    assert f'OutputDir={build.OUTPUT_DIR}' in text and f'SetupIconFile={icon}' in text
    assert f'Source: "{build.APP_EXE}"' in text and str(build.INSTALL_STAGE_INTERNAL) in text
    forbidden.assert_not_called()


@pytest.mark.parametrize('entry', ['build_exe', 'build_installer'])
def test_output_root_rejects_source_parent_traversal_and_reparse_before_writes(tmp_path, monkeypatch, entry):
    from importlib import import_module
    from tests.release.test_package_cleanup import _junction

    build = import_module(entry)
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setattr(build, 'ROOT', source)
    before = build.OUTPUT_ROOT
    for invalid in (source, source / 'src', source / 'build', source / 'build/../src', tmp_path / 'outside'):
        with pytest.raises(ValueError):
            build._configure_output_root(invalid)
    outside = tmp_path / 'other-owner'
    outside.mkdir()
    sentinel = outside / 'keep'
    sentinel.write_bytes(b'other owner unchanged')
    (source / 'build').mkdir()
    link = source / 'build/redirect'
    _junction(link, outside, tmp_path)
    try:
        with pytest.raises(ValueError, match='重解析点'):
            build._configure_output_root(link / 'new-output')
        assert not (outside / 'new-output').exists()
        assert sentinel.read_bytes() == b'other owner unchanged' and build.OUTPUT_ROOT == before
    finally:
        link.rmdir() if os.name == 'nt' else link.unlink()  # 仅移除本用例的联接本身。


@pytest.mark.parametrize('member', ['component-bundles', 'ocr-profile'])
def test_executable_output_root_rejects_redirected_preparation_members(executable_inputs, tmp_path, monkeypatch, member):
    from tests.release.test_package_cleanup import _junction

    build, root, support = executable_inputs
    output = root / 'build/isolated output'
    output.mkdir()
    # 单独覆盖冻结 profile 父段，避免只核 ocr-support-input 顶层而漏掉子联接。
    relative = 'component-bundles' if member == 'component-bundles' else f'ocr-support-input/{support.profile}'
    link = output / 'build' / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / 'other-owner'
    outside.mkdir()
    sentinel = outside / 'keep'
    sentinel.write_bytes(b'no preparation writes')
    _junction(link, outside, tmp_path)
    monkeypatch.setattr(sys, 'argv', ['build_exe.py', '--output-root', str(output)])
    compiler = Mock(side_effect=AssertionError('no compiler'))
    monkeypatch.setattr(build.PyInstaller.__main__, 'run', compiler)
    try:
        with pytest.raises(ValueError, match='重解析点'):
            build.main()
        build.prepare_component_bundle.assert_not_called()
        build.write_ocr_support_manifest.assert_not_called()
        build._remove_package_artifact.assert_not_called()
        compiler.assert_not_called()
        assert sentinel.read_bytes() == b'no preparation writes'
        assert sorted(path.name for path in outside.iterdir()) == ['keep']
    finally:
        link.rmdir() if os.name == 'nt' else link.unlink()

