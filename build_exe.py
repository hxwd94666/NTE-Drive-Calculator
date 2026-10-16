# 构建 Windows 可执行程序的打包脚本。
"""
NTE Drive Calc - PyInstaller 打包脚本

用法:
    python build_exe.py              # 单目录模式（推荐）
    python build_exe.py --onefile    # 单文件模式
    python build_exe.py --output-root build/release-candidate
"""

import argparse
import importlib.util
import hashlib
import json
import os
import shutil
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

import PyInstaller.__main__
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

from tools import build_cli
from tools.release.app_bundle_validation import (
    CONFIG_RELEASE_FILES, CONFIG_RELEASE_DIRECTORIES, validate_app_bundle,
    validate_analysis_component as _validate_analysis_component,
)
from tools.release.onedir_inputs import write_onedir_receipt
from tools.release.ocr_build_inputs import collect_ocr_build_inputs, write_ocr_support_manifest
from tools.release.managed_paths import plain_member_stat
from src.integrations.vision.recognition.ocr.backend_support import load_backend_support
from tools.release.native_component_bundle_build import native_component_build_inputs
from tools.release.pyinstaller_native_dependencies import declared_native_proxies_only
from tools.release.game_component_bundle_build import (
    prepare_component_bundle,
)
from tools.release.runtime_pruning import prune_unused_runtime_binaries, validate_pruned_runtime
from src.integrations.vision.recognition.ocr.model_resources import (
    build_source_ocr_models,
)

from src.integrations.vision.recognition.ocr.language_resources import load_ocr_dictionary, shared_dictionary_path

ROOT = Path(__file__).parent.resolve()
OUTPUT_ROOT = ROOT
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "NTE_Drive_Calc.spec"
PACKAGE_NAME = "NTE_Drive_Calc"
PACKAGE_BUILD_DIR = BUILD / PACKAGE_NAME
PACKAGE_ONEDIR_DIR = DIST / PACKAGE_NAME
PACKAGE_ONEFILE_EXE = DIST / f"{PACKAGE_NAME}.exe"
THIRD_PARTY_DIR = ROOT / "third_party"
SQLITE_SCHEMA_DIR = ROOT / "src" / "storage" / "sqlite" / "schema"
STATIC_DATABASE_PATH = ROOT / "data" / "game_static.sqlite3"
STATIC_MANIFEST_PATH = ROOT / "data" / "manifest.json"
STATIC_MIGRATION_DATA_DIR = ROOT / "data" / "migrations"
SHARED_DATABASE_SEED_PATH = ROOT / "data" / "app_shared.sqlite3"
ANALYSIS_CORE_PATH = THIRD_PARTY_DIR / "analysis-core" / "bin" / "nte-analysis-core.exe"
ANALYSIS_CORE_MANIFEST_PATH = THIRD_PARTY_DIR / "analysis-core" / "component.json"
ANALYSIS_CORE_RELEASE_FILES = ("LICENSE", "SOURCE.md", "THIRD_PARTY_NOTICES.txt")

SYSTEM_ICU_SHADOW_DLL = "icuuc.dll"
FORBIDDEN_AMBIENT_ICU_DLLS = ("icuuc.dll", "icudt78.dll")
FORBIDDEN_RUNTIME_CACHE_NAMES = ("NTE_SDK.bin", "NTE_SDK.checksum")


def _configure_output_root(requested: Path | None) -> None:
    """输出只归本次普通目录；输入根不变，先 lstat 再使用，不抹掉联接身份。"""
    global OUTPUT_ROOT, DIST, BUILD, SPEC
    global PACKAGE_BUILD_DIR, PACKAGE_ONEDIR_DIR, PACKAGE_ONEFILE_EXE
    output = build_cli.select_output_root(ROOT, requested)
    # spec 与构建父目录同样是写入成员；拒绝已有重定向或错误类型后才准备输入。
    for member, predicate in ((output / "dist", stat.S_ISDIR), (output / "build", stat.S_ISDIR),
                              (output / "build/component-bundles", stat.S_ISDIR),
                              (output / f"{PACKAGE_NAME}.spec", stat.S_ISREG)):
        try:
            info = plain_member_stat(ROOT, member)
        except FileNotFoundError:
            continue
        if not predicate(info.st_mode):
            raise ValueError(f"输出成员类型不符：{member}")
    OUTPUT_ROOT = output
    DIST, BUILD, SPEC = output / "dist", output / "build", output / f"{PACKAGE_NAME}.spec"
    PACKAGE_BUILD_DIR = BUILD / PACKAGE_NAME
    PACKAGE_ONEDIR_DIR = DIST / PACKAGE_NAME
    PACKAGE_ONEFILE_EXE = DIST / f"{PACKAGE_NAME}.exe"


def _is_same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


@contextmanager
def _without_ambient_system_icu_on_path():
    """构建期间暂时排除非系统 ICU 来源；无论结果如何都还原调用方 PATH。"""

    original = os.environ.get("PATH")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system32 = system_root / "System32"
    kept: list[str] = []
    removed_count = 0
    for raw_entry in (original or "").split(os.pathsep):
        if not raw_entry:
            continue
        entry = Path(os.path.expandvars(raw_entry)).expanduser()
        shadows_system_icu = (entry / SYSTEM_ICU_SHADOW_DLL).is_file()
        if shadows_system_icu and not _is_same_path(entry, system32):
            removed_count += 1
            continue
        kept.append(raw_entry)

    if removed_count:
        build_cli.info(
            f"[BUILD] 已隔离 {removed_count} 个携带外部 ICU DLL 的 PATH 目录"
        )
    os.environ["PATH"] = os.pathsep.join(kept)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original


def _validate_no_ambient_icu_dlls(output: Path) -> None:
    """Qt 使用系统 ICU；拒绝会覆盖该身份的应用旁置 DLL。"""

    if not output.is_dir():
        return
    internal = output / "_internal"
    found = [name for name in FORBIDDEN_AMBIENT_ICU_DLLS if (internal / name).is_file()]
    if found:
        joined = ", ".join(found)
        raise RuntimeError(f"打包产物混入外部 ICU DLL，已拒绝发布：{joined}")


def _validate_no_runtime_caches(output: Path) -> None:
    """本机 SDK 缓存须由安装后的组件生成，发行输入不继承维护者缓存。"""

    if not output.is_dir():
        return
    found = [
        path.relative_to(output)
        for name in FORBIDDEN_RUNTIME_CACHE_NAMES
        for path in output.rglob(name)
    ]
    if found:
        joined = "、".join(str(path) for path in found)
        raise RuntimeError(f"打包产物混入本机运行时缓存，已拒绝发布：{joined}")


def _running_in_automation() -> bool:
    return build_cli.running_in_automation()


def _package_artifact_stat(path: Path) -> os.stat_result | None:
    """仅承认三项固定输出身份；根、父段和叶子均须为普通路径。"""
    expected = {
        PACKAGE_BUILD_DIR: stat.S_ISDIR,
        PACKAGE_ONEDIR_DIR: stat.S_ISDIR,
        PACKAGE_ONEFILE_EXE: stat.S_ISREG,
    }
    if path not in expected:
        raise ValueError(f"非本包清理目标：{path}")
    # 根缺失不是“产物尚未生成”；缺失只允许发生在已校验根内的成员。
    plain_member_stat(ROOT, ROOT)
    try:
        info = plain_member_stat(ROOT, path)
    except FileNotFoundError:
        return None
    if not expected[path](info.st_mode):
        raise ValueError(f"打包清理目标类型不符：{path}")
    return info


def _preflight_package_cleanup() -> None:
    """先整体检查，避免后一个无效目标导致前一个正常产物已被清理。"""
    for path in (PACKAGE_BUILD_DIR, PACKAGE_ONEDIR_DIR, PACKAGE_ONEFILE_EXE):
        _package_artifact_stat(path)


def _remove_package_artifact(path: Path) -> None:
    """删除前复核确切身份；不清理其他构建任务或重定向路径。"""
    # 静态预检与逐次复核不消除检查后路径被替换的竞态；构建输出须独占。
    info = _package_artifact_stat(path)
    if info is not None:
        if stat.S_ISDIR(info.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()


def _first_existing_file(*candidates: str | Path | None) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if path.is_file():
            return path
    return None


def _required_build_file(label: str, *candidates: str | Path | None) -> Path:
    path = _first_existing_file(*candidates)
    if path is None:
        checked = "、".join(str(candidate) for candidate in candidates if candidate)
        raise FileNotFoundError(f"打包缺少 {label}；已检查：{checked}")
    return path


def _find_package_dir(package_name: str) -> Path | None:
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def main() -> int:
    """显式准备发行输入后构建；普通导入不写暂存、清理产物或启动工具。"""
    parser = argparse.ArgumentParser(description="构建 NTE Drive Calc 可执行程序。")
    parser.add_argument("--output-root", type=Path, help="输出至仓库 build 下的独立子目录；默认仓库根。")
    # 既有 onefile/console 标志仍由原入口消费，不将参数透传给 PyInstaller。
    options, _ = parser.parse_known_args()
    _configure_output_root(options.output_root)
    # 准备组件/OCR 输入也会写 build，因此须先拒绝父目录重定向。
    _preflight_package_cleanup()
    onefile = "--onefile" in sys.argv
    # 与运行时使用同一声明；构建本次冻结，不在收集/fallback 时重读环境。
    ocr_support = load_backend_support()
    # OCR 写入消费本次冻结 profile；父段或清单叶子的联接不能绕过输出隔离。
    ocr_manifest_path = BUILD / "ocr-support-input" / ocr_support.profile / "backend_support.json"
    try:
        ocr_manifest_info = plain_member_stat(ROOT, ocr_manifest_path)
    except FileNotFoundError:
        ocr_manifest_info = None
    if ocr_manifest_info is not None and not stat.S_ISREG(ocr_manifest_info.st_mode):
        raise ValueError("OCR 构建清单输出不是普通文件。")

    args = [
        str(ROOT / "main.py"),
        f"--name={PACKAGE_NAME}",
        "--windowed" if "--console" not in sys.argv else "--console",
        "--clean",
        "--noconfirm",
        # 构建、清理与后验核验共用输出根；源码、资源及 UPX 始终消费脚本根。
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        f"--specpath={OUTPUT_ROOT}",
    ]

    if onefile:
        args.append("--onefile")
    else:
        args.append("--onedir")

    if sys.platform == "win32":
        args.append("--uac-admin")

    config_dir = ROOT / "config"
    assets_dir = ROOT / "assets"
    icon_path = assets_dir / "app_icon.ico"
    sep = ";" if sys.platform == "win32" else ":"
    if assets_dir.exists():
        args.append(f"--add-data={assets_dir}{sep}assets")
    if icon_path.exists():
        args.append(f"--icon={icon_path}")


    def _append_add_data(src: str | Path, dst: str):
        args.append(f"--add-data={Path(src)}{sep}{dst}")


    def _append_add_binary(src: str | Path, dst: str):
        args.append(f"--add-binary={Path(src)}{sep}{dst}")


    # 发行配置采用显式白名单，避免把 config 下的账号数据或运行时 SDK 缓存带入安装包。
    for release_name in CONFIG_RELEASE_FILES:
        release_path = config_dir / release_name
        if not release_path.is_file():
            raise FileNotFoundError(f"打包缺少发行配置文件：{release_path}")
        _append_add_data(release_path, "config")
    for release_name in CONFIG_RELEASE_DIRECTORIES:
        release_path = config_dir / release_name
        if not release_path.is_dir():
            raise FileNotFoundError(f"打包缺少发行配置目录：{release_path}")
        _append_add_data(release_path, f"config/{release_name}")


    # 首次建库与迁移只消费 SQL；保留 shared 等相对目录，不把源码或解释器缓存带入包。
    if not SQLITE_SCHEMA_DIR.is_dir():
        raise FileNotFoundError(f"SQLite schema 目录不存在：{SQLITE_SCHEMA_DIR}")
    schema_files = []
    for source in sorted(SQLITE_SCHEMA_DIR.rglob("*.sql")):
        relative = source.relative_to(SQLITE_SCHEMA_DIR)
        if "__pycache__" in relative.parts:
            continue
        if not stat.S_ISREG(plain_member_stat(ROOT, source).st_mode):
            raise ValueError(f"SQLite schema SQL 资源不是普通文件：{source}")
        schema_files.append((source, relative))
    if not schema_files:
        raise FileNotFoundError(f"SQLite schema 缺少 SQL 资源：{SQLITE_SCHEMA_DIR}")
    for source, relative in schema_files:
        _append_add_data(source, (Path("src/storage/sqlite/schema") / relative.parent).as_posix())


    # 可选显示功能仍按整套组件交付；采样器的字节、清单与许可一起核对。
    presentmon_dir = THIRD_PARTY_DIR / "presentmon"
    presentmon_manifest = json.loads(_required_build_file("PresentMon manifest", presentmon_dir / "component.json").read_text(encoding="utf-8"))
    presentmon_exe = _required_build_file("PresentMon console", presentmon_dir / "PresentMon.exe")
    if hashlib.sha256(presentmon_exe.read_bytes()).hexdigest() != presentmon_manifest["sha256"]:
        raise ValueError("PresentMon 组件哈希不符")
    _append_add_binary(presentmon_exe, "third_party/presentmon")
    for notice in ("component.json", "LICENSE.txt", "SOURCE.md"):
        _append_add_data(_required_build_file(notice, presentmon_dir / notice), "third_party/presentmon")

    # 分析与采集执行物职责独立，清单及许可随分析组件成套加入。
    analysis_core_path = _required_build_file("nte-analysis-core.exe", ANALYSIS_CORE_PATH)
    analysis_manifest_path = _required_build_file(
        "analysis component manifest",
        ANALYSIS_CORE_MANIFEST_PATH,
    )
    _validate_analysis_component(analysis_core_path, analysis_manifest_path)
    _append_add_binary(analysis_core_path, ".")
    _append_add_data(analysis_manifest_path, "analysis-core-meta")
    for analysis_notice in ANALYSIS_CORE_RELEASE_FILES:
        _append_add_data(
            _required_build_file(
                analysis_notice,
                THIRD_PARTY_DIR / "analysis-core" / analysis_notice,
            ),
            "licenses/analysis-core",
        )


    # 发行版静态数据库直接随源码仓库维护，确保本地构建和 GitHub Release 使用同一数据集。
    static_database_path = _required_build_file("发行版静态数据库", STATIC_DATABASE_PATH)
    _append_add_data(static_database_path, "data")
    static_manifest_path = _required_build_file("发行版静态数据库清单", STATIC_MANIFEST_PATH)
    _append_add_data(static_manifest_path, "data")
    from src.integrations.role_catalog_release import resolve_role_catalog, validate_role_assets
    role_catalog = resolve_role_catalog(static_database_path)
    if role_catalog is None:
        raise FileNotFoundError("发行构建需要完整图鉴与统一界面图片，请先晋升 data/role_catalog")
    role_assets = validate_role_assets(role_catalog.asset_root, role_catalog.dataset_id, role_catalog.sha256)
    for relative in ("game_static.sqlite3", "manifest.json", "game_ui/manifest.json",
                     *("game_ui/" + path for path in role_assets["files"])):
        source = role_catalog.database_path.parent / relative
        _append_add_data(source, (Path("data/role_catalog") / Path(relative).parent).as_posix())
    if not STATIC_MIGRATION_DATA_DIR.is_dir():
        raise FileNotFoundError(f"静态数据迁移基线目录不存在：{STATIC_MIGRATION_DATA_DIR}")
    _append_add_data(STATIC_MIGRATION_DATA_DIR, "data/migrations")
    shared_database_seed_path = _required_build_file(
        "公共额外形状默认库",
        SHARED_DATABASE_SEED_PATH,
    )
    _append_add_data(shared_database_seed_path, "data")
    build_cli.info(f"[DATA] 已加入静态数据库：{static_database_path}")
    build_cli.info(f"[DATA] 已加入公共额外形状默认库：{shared_database_seed_path}")


    # 随包携带第三方声明，二进制实际位置可变但许可信息必须可审计。
    for notice_path in (
        ROOT / "LICENSE",
        ROOT / "NOTICE",
        THIRD_PARTY_DIR / "vigembus" / "NOTICE.md",
        THIRD_PARTY_DIR / "vigembus" / "LICENSE-BSD-3-Clause.txt",
    ):
        if notice_path.is_file():
            _append_add_data(notice_path, "licenses")
    component_bundle = prepare_component_bundle(
        application_root=ROOT, inputs=native_component_build_inputs(ROOT),
        output_parent=BUILD / "component-bundles",
    )

    for item in component_bundle.inputs:
        destination = Path(item.destination)
        append = _append_add_binary if destination.name in {"nte-core.exe", "nte-mod-loader.exe"} else _append_add_data
        append(item.source, destination.parent.as_posix())
    _append_add_data(component_bundle.manifest_path, ".")


    hidden_imports = [
        "cv2", "cv2.mat_wrapper",
        "numpy", "numpy._core", "numpy.linalg",
        "mss", "keyboard", "pyautogui", "vgamepad",
        "scipy", "scipy.optimize", "scipy.sparse", "scipy.spatial",
        "pydantic", "loguru", "pypinyin",
        "PIL", "PIL.Image",
        "json", "hashlib", "difflib", "re", "copy", "itertools", "collections",
        "pathlib", "logging", "shutil",
        "src.scanner.gamepad_controller",
    ]

    ocr_inputs = collect_ocr_build_inputs(
        ocr_support, collect_submodules=collect_submodules, collect_data_files=collect_data_files,
        collect_dynamic_libs=collect_dynamic_libs, copy_metadata=copy_metadata,
    )
    hidden_imports.extend(ocr_inputs.hidden_imports)

    for imp in hidden_imports:
        args.append(f"--hidden-import={imp}")

    excludes = [
        # SciPy 自测入口间接带入测试框架及终端高亮，不属于产品运行链；保留其 _testutils 入口。
        "pytest", "_pytest", "pygments",
        # 科学计算/ML（完全不用）
        "matplotlib", "pandas", "torch", "tensorflow", "jupyter", "IPython", "sympy",
        "sklearn",
        # tkinter（用 PySide6）
        "tkinter", "_tkinter",
        # onnxruntime 未使用的 execution provider
        "onnxruntime.transformers",
        # 工坊模板后台按需使用 QtNetwork；保留模块 hook 和其 HTTPS TLS 插件收集。
        # PySide6 未使用子模块
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
        "PySide6.QtVirtualKeyboard", "PySide6.QtWebEngine",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth", "PySide6.QtNfc",
        "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtWebChannel", "PySide6.QtWebSockets",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml",
        "PySide6.QtPrintSupport", "PySide6.QtHelp",
        "PySide6.QtPositioning", "PySide6.QtLocation",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml",
        "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
        "PySide6.Qt3DCore", "PySide6.Qt3DInput",
        "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
        "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtGraphs", "PySide6.QtGrpc",
        "PySide6.QtHttpServer", "PySide6.QtQuick3D",
        "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
        "PySide6.QtSpatialAudio", "PySide6.QtSvgWidgets",
        "PySide6.QtSvg", "PySide6.QtUiTools",
        "PySide6.QtDesigner", "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtNetworkAuth", "PySide6.QtDBus",
        "PySide6.QtConcurrent",
        # PIL 未使用；项目图片入口与发行素材不使用 AVIF。
        "PIL.ImageTk", "PIL.AvifImagePlugin", "PIL._avif",
    ]

    excludes.extend(ocr_inputs.excludes)
    for exc in excludes:
        args.append(f"--exclude-module={exc}")

    # 所选库共享一份模型/字典；排除库不收集配置、DLL 或 metadata。
    load_ocr_dictionary(shared_dictionary_path())
    for source, destination in ocr_inputs.data:
        _append_add_data(source, destination)
    for source, destination in ocr_inputs.binaries:
        _append_add_binary(source, destination)
    for model_path in build_source_ocr_models(support=ocr_support).values():
        _append_add_data(model_path, "assets/ocr/models")
    ocr_manifest = write_ocr_support_manifest(
        ocr_manifest_path, ocr_support,
    )
    _append_add_data(ocr_manifest, "assets/ocr")

    # ViGEmClient.dll（虚拟手柄）
    vg_path = _find_package_dir("vgamepad")
    if vg_path is not None:
        vigem_dll = vg_path / "win" / "vigem" / "client" / "x64" / "ViGEmClient.dll"
        if vigem_dll.exists():
            args.append(f"--add-binary={vigem_dll}{sep}vgamepad/win/vigem/client/x64")

    # UPX 只读取脚本根下的已备工具，不跟随调用方工作目录。
    args.append(f"--upx-dir={ROOT}")
    args.extend(["--upx-exclude=nte-core.exe", "--upx-exclude=nte-mod-loader.exe",
                 "--upx-exclude=NTE_Capture.dll", "--upx-exclude=d3d12.dll"])

    build_cli.info(f"[BUILD] Mode: {'Single File' if onefile else 'Single Dir'}")
    for path in (PACKAGE_BUILD_DIR, PACKAGE_ONEDIR_DIR, PACKAGE_ONEFILE_EXE):
        _remove_package_artifact(path)
    try:
        spec_info = plain_member_stat(ROOT, SPEC)
    except FileNotFoundError:
        spec_info = None
    if spec_info is not None:
        if not stat.S_ISREG(spec_info.st_mode):
            raise ValueError("spec 输出类型已改变。")
        SPEC.unlink()
    with _without_ambient_system_icu_on_path(), declared_native_proxies_only():
        PyInstaller.__main__.run(args)

    output = PACKAGE_ONEDIR_DIR
    if onefile:
        output = PACKAGE_ONEFILE_EXE

    if output.exists():
        if not onefile:
            removed_runtime = prune_unused_runtime_binaries(output / "_internal", support=ocr_support)
            validate_pruned_runtime(output / "_internal", support=ocr_support)
            build_cli.info(
                "[SIZE] 已裁剪可选运行库："
                f"{sum(removed_runtime.values()) / (1024 * 1024):.1f} MiB 展开体积"
            )
            validate_app_bundle(output / f"{PACKAGE_NAME}.exe", output / "_internal")
        _validate_no_ambient_icu_dlls(output)
        _validate_no_runtime_caches(output)
        if not onefile:
            from src.app.version import __version__
            write_onedir_receipt(output, PACKAGE_BUILD_DIR / "onedir-files.json", version=__version__)
        size_mib = build_cli.artifact_size_bytes(output) / (1024 * 1024)
        build_cli.ok(f"Build complete: {output}")
        build_cli.info(f"[SIZE] {size_mib:.1f} MiB")
    else:
        build_cli.fail("Build failed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
