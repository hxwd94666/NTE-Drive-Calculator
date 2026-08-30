# 构建 Windows 可执行程序的打包脚本。
"""
NTE Drive Calc - PyInstaller 打包脚本

用法:
    python build_exe.py              # 单目录模式（推荐）
    python build_exe.py --onefile    # 单文件模式
"""

import importlib.util
import os
import shutil
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

ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "NTE_Drive_Calc.spec"
PACKAGE_NAME = "NTE_Drive_Calc"
PACKAGE_BUILD_DIR = BUILD / PACKAGE_NAME
PACKAGE_ONEDIR_DIR = DIST / PACKAGE_NAME
PACKAGE_ONEFILE_EXE = DIST / f"{PACKAGE_NAME}.exe"
THIRD_PARTY_DIR = ROOT / "third_party"
SQLITE_SCHEMA_DIR = ROOT / "src" / "storage" / "sqlite" / "schema"
NTE_CORE_ENV = "NTE_CORE_EXE"
MODS_PLUGIN_ENV = "NTE_MODS_PLUGIN_DLL"
MOD_LOADER_ENV = "NTE_MOD_LOADER_EXE"
LEGACY_EQUIPMENT_PLUGIN_ENV = "NTE_EQUIPMENT_PLUGIN_DLL"
STATIC_DATABASE_PATH = ROOT / "data" / "game_static.sqlite3"
STATIC_MANIFEST_PATH = ROOT / "data" / "manifest.json"
PREVIOUS_RELEASE_DATABASE_PATH = BUILD / "previous" / "data" / "game_static.sqlite3"
STATIC_MIGRATION_DATA_DIR = ROOT / "data" / "migrations"
SHARED_DATABASE_SEED_PATH = ROOT / "data" / "app_shared.sqlite3"
MODS_PLUGIN_WORKSPACE_DIR = THIRD_PARTY_DIR / "mods-plugin" / "workspace"
MOD_LOADER_PATH = THIRD_PARTY_DIR / "mod-loader" / "bin" / "nte-mod-loader.exe"
NTE_CORE_RELEASE_FILES = (
    "LICENSE",
    "SOURCE.md",
    "COMPONENT.md",
    "BUILD_VARIANT.md",
    "CLI_PROTOCOL_ZH.md",
    "CLI_PROTOCOL.md",
    "THIRD_PARTY_LICENSES.md",
)

EXPLICIT_WORKSHOP_ARGS = {"--skip-workshop-sync", "--require-workshop-sync", "--prompt-workshop-key"}
SYSTEM_ICU_SHADOW_DLL = "icuuc.dll"
FORBIDDEN_AMBIENT_ICU_DLLS = ("icuuc.dll", "icudt78.dll")


def _is_same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


@contextmanager
def _without_ambient_system_icu_on_path():
    """Prevent build-tool DLLs from shadowing the Windows system ICU runtime."""

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
    """Reject a package that would override Qt's Windows system ICU dependency."""

    if not output.is_dir():
        return
    internal = output / "_internal"
    found = [name for name in FORBIDDEN_AMBIENT_ICU_DLLS if (internal / name).is_file()]
    if found:
        joined = ", ".join(found)
        raise RuntimeError(f"打包产物混入外部 ICU DLL，已拒绝发布：{joined}")


def _running_in_automation() -> bool:
    return build_cli.running_in_automation()


def _choose_workshop_sync_mode() -> tuple[bool, bool]:
    if any(arg in sys.argv for arg in EXPLICIT_WORKSHOP_ARGS):
        return build_cli.choose_build_mode(
            skip_workshop_sync="--skip-workshop-sync" in sys.argv,
            require_workshop_sync="--require-workshop-sync" in sys.argv,
            has_explicit_choice=True,
        )
    return build_cli.choose_build_mode()


skip_workshop_sync, require_workshop_sync = _choose_workshop_sync_mode()


def _sync_workshop_weights_before_build() -> None:
    if skip_workshop_sync:
        build_cli.skip("开发诊断：跳过异环工坊权重发布门禁")
        return
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "game_data" / "sync_recommended_weights.py"),
        "--database", str(STATIC_DATABASE_PATH),
        "--manifest", str(STATIC_MANIFEST_PATH),
        "--reuse-database-if-missing", str(PREVIOUS_RELEASE_DATABASE_PATH),
    ]
    if (
        "--prompt-workshop-key" in sys.argv
        or (require_workshop_sync and "--require-workshop-sync" not in sys.argv)
    ):
        cmd.append("--prompt-key")
    build_cli.run(cmd, ROOT)


_sync_workshop_weights_before_build()

def _remove_package_artifact(path: Path) -> None:
    """Remove only this package's PyInstaller output, never unrelated build worktrees."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


for path in (PACKAGE_BUILD_DIR, PACKAGE_ONEDIR_DIR, PACKAGE_ONEFILE_EXE):
    _remove_package_artifact(path)
if SPEC.exists():
    SPEC.unlink()

onefile = "--onefile" in sys.argv

args = [
    str(ROOT / "main.py"),
    f"--name={PACKAGE_NAME}",
    "--windowed" if "--console" not in sys.argv else "--console",
    "--clean",
    "--noconfirm",
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
args.append(f"--add-data={config_dir}{sep}config")
if assets_dir.exists():
    args.append(f"--add-data={assets_dir}{sep}assets")
if icon_path.exists():
    args.append(f"--icon={icon_path}")


def _append_add_data(src: str | Path, dst: str):
    args.append(f"--add-data={Path(src)}{sep}{dst}")


def _append_add_binary(src: str | Path, dst: str):
    args.append(f"--add-binary={Path(src)}{sep}{dst}")


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


def _nte_core_metadata_directories(executable: Path) -> tuple[Path, ...]:
    """Find release notices for both the source-tree and downloaded layouts."""

    directories = (executable.parent, executable.parent.parent)
    unique: list[Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return tuple(unique)


# 用户数据库首次运行时需要 SQL 结构文件；PyInstaller 不会自动收集非 Python 文件。
if not SQLITE_SCHEMA_DIR.is_dir():
    raise FileNotFoundError(f"SQLite schema 目录不存在：{SQLITE_SCHEMA_DIR}")
_append_add_data(SQLITE_SCHEMA_DIR, "src/storage/sqlite/schema")

# nte-core 是随应用运行的本地组件。正式构建使用仓库内已审计的组件；
# NTE_CORE_EXE 和项目根目录候选仅用于兼容现有开发环境。
nte_core_path = _required_build_file(
    "nte-core.exe",
    os.environ.get(NTE_CORE_ENV),
    THIRD_PARTY_DIR / "nte-core" / "bin" / "nte-core.exe",
    ROOT / "nte-core.exe",
    ROOT / "build_resources" / "nte-core" / "nte-core.exe",
)
_append_add_binary(nte_core_path, ".")

# Release 目录若提供许可证和协议说明，则一并放入安装包，便于审计和再分发。
nte_core_metadata_dirs = _nte_core_metadata_directories(nte_core_path)
for release_name in NTE_CORE_RELEASE_FILES:
    release_file = next(
        (directory / release_name for directory in nte_core_metadata_dirs if (directory / release_name).is_file()),
        None,
    )
    if release_file is not None:
        _append_add_data(release_file, "licenses/nte-core")
if not any((directory / "LICENSE").is_file() for directory in nte_core_metadata_dirs):
    build_cli.warn("nte-core 目录没有 LICENSE；本地测试可继续，正式发布必须使用完整 Release 目录")

# 发行版静态数据库直接随源码仓库维护，确保本地构建和 GitHub Release 使用同一数据集。
static_database_path = _required_build_file("发行版静态数据库", STATIC_DATABASE_PATH)
_append_add_data(static_database_path, "data")
static_manifest_path = _required_build_file("发行版静态数据库清单", STATIC_MANIFEST_PATH)
_append_add_data(static_manifest_path, "data")
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

# 环境配置页会显式部署该 DLL 至用户选择的游戏目录；安装器本身不会修改游戏目录。
mods_plugin_path = _required_build_file(
    "nte-mods-plugin dwmapi.dll",
    os.environ.get(MODS_PLUGIN_ENV),
    os.environ.get(LEGACY_EQUIPMENT_PLUGIN_ENV),
    THIRD_PARTY_DIR / "mods-plugin" / "bin" / "dwmapi.dll",
    ROOT / "dwmapi.dll",
)
_append_add_data(mods_plugin_path, ".")
mod_loader_path = _required_build_file(
    "nte-mod-loader.exe",
    os.environ.get(MOD_LOADER_ENV),
    MOD_LOADER_PATH,
)
_append_add_binary(mod_loader_path, ".")
if not MODS_PLUGIN_WORKSPACE_DIR.is_dir():
    raise FileNotFoundError(f"打包缺少 nte-mods 工作区：{MODS_PLUGIN_WORKSPACE_DIR}")
_append_add_data(MODS_PLUGIN_WORKSPACE_DIR, "plugins")

# 随包携带第三方声明，二进制实际位置可变但许可信息必须可审计。
for notice_path in (
    ROOT / "LICENSE",
    ROOT / "NOTICE",
    THIRD_PARTY_DIR / "vigembus" / "NOTICE.md",
    THIRD_PARTY_DIR / "vigembus" / "LICENSE-BSD-3-Clause.txt",
):
    if notice_path.is_file():
        _append_add_data(notice_path, "licenses")
for notice_path in (
    THIRD_PARTY_DIR / "mods-plugin" / "LICENSE",
    THIRD_PARTY_DIR / "mods-plugin" / "SOURCE.md",
    THIRD_PARTY_DIR / "mods-plugin" / "COMPONENT.md",
):
    if notice_path.is_file():
        _append_add_data(notice_path, "licenses/mods-plugin")
for notice_path in (
    THIRD_PARTY_DIR / "mod-loader" / "LICENSE",
    THIRD_PARTY_DIR / "mod-loader" / "SOURCE.md",
    THIRD_PARTY_DIR / "mod-loader" / "COMPONENT.md",
    THIRD_PARTY_DIR / "mod-loader" / "THIRD_PARTY_LICENSES.md",
):
    if notice_path.is_file():
        _append_add_data(notice_path, "licenses/mod-loader")
mod_loader_dependency_licenses = THIRD_PARTY_DIR / "mod-loader" / "licenses"
if mod_loader_dependency_licenses.is_dir():
    _append_add_data(
        mod_loader_dependency_licenses,
        "licenses/mod-loader/dependencies",
    )


def _find_package_dir(package_name: str) -> Path | None:
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


hidden_imports = [
    "cv2", "cv2.mat_wrapper",
    "numpy", "numpy._core", "numpy.linalg",
    "rapidocr_openvino", "rapidocr_onnxruntime", "onnxruntime",
    "openvino", "openvino.runtime",
    "mss", "keyboard", "pyautogui", "vgamepad",
    "scipy", "scipy.optimize", "scipy.sparse", "scipy.spatial",
    "pydantic", "loguru", "pypinyin",
    "PIL", "PIL.Image",
    "json", "hashlib", "difflib", "re", "copy", "itertools", "collections",
    "pathlib", "logging", "shutil",
    "src.scanner.gamepad_controller",
]

for pkg_name in ("rapidocr_openvino", "rapidocr_onnxruntime"):
    try:
        hidden_imports.extend(collect_submodules(pkg_name))
    except Exception as exc:
        build_cli.warn(f"收集 {pkg_name} hidden imports 失败，按基础 hook 继续: {exc}")

for imp in hidden_imports:
    args.append(f"--hidden-import={imp}")

excludes = [
    # 科学计算/ML（完全不用）
    "matplotlib", "pandas", "torch", "tensorflow", "jupyter", "IPython", "sympy",
    "sklearn",
    # tkinter（用 PySide6）
    "tkinter", "_tkinter",
    # onnxruntime 未使用的 execution provider
    "onnxruntime.transformers",
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
    "PySide6.QtOpenGLWidgets", "PySide6.QtNetwork",
    "PySide6.QtNetworkAuth", "PySide6.QtDBus",
    "PySide6.QtConcurrent",
    # PIL 未使用
    "PIL.ImageTk",
]

for exc in excludes:
    args.append(f"--exclude-module={exc}")

# rapidocr 数据文件（模型和配置）— 优先 openvino，兼容 onnxruntime
for ocr_pkg_name in ("rapidocr_openvino", "rapidocr_onnxruntime"):
    try:
        for src, dst in collect_data_files(ocr_pkg_name):
            _append_add_data(src, dst)
        for src, dst in copy_metadata(ocr_pkg_name):
            _append_add_data(src, dst)
    except Exception:
        build_cli.warn(f"收集 {ocr_pkg_name} 数据文件失败，OCR 包可能未安装，继续打包: {ocr_pkg_name}")

# OpenVINO runtime: complete libs, cache.json, and package metadata.
# A hand-written DLL list is fragile and can miss plugin/data files.
try:
    for src, dst in collect_dynamic_libs("openvino"):
        _append_add_binary(src, dst)
    for src, dst in collect_data_files("openvino", includes=["libs/cache.json"]):
        _append_add_data(src, dst)
    for src, dst in copy_metadata("openvino"):
        _append_add_data(src, dst)
except Exception:
    build_cli.warn("收集 OpenVINO runtime 文件失败，继续打包；若运行 OCR 异常请检查依赖安装")

# ONNX Runtime / DirectML runtime: required when a discrete GPU is available.
try:
    for src, dst in collect_dynamic_libs("onnxruntime"):
        _append_add_binary(src, dst)
    for package_name in ("onnxruntime-directml", "onnxruntime"):
        try:
            for src, dst in copy_metadata(package_name):
                _append_add_data(src, dst)
        except Exception:
            build_cli.warn(f"收集 {package_name} metadata 失败，继续打包")
except Exception:
    build_cli.warn("收集 ONNX Runtime / DirectML 文件失败，继续打包；独显加速可能不可用")

# ViGEmClient.dll（虚拟手柄）
vg_path = _find_package_dir("vgamepad")
if vg_path is not None:
    vigem_dll = vg_path / "win" / "vigem" / "client" / "x64" / "ViGEmClient.dll"
    if vigem_dll.exists():
        args.append(f"--add-binary={vigem_dll}{sep}vgamepad/win/vigem/client/x64")

# UPX 压缩（如果可用）
args.append("--upx-dir=.")

build_cli.info(f"[BUILD] Mode: {'Single File' if onefile else 'Single Dir'}")
with _without_ambient_system_icu_on_path():
    PyInstaller.__main__.run(args)

output = PACKAGE_ONEDIR_DIR
if onefile:
    output = PACKAGE_ONEFILE_EXE

if output.exists():
    _validate_no_ambient_icu_dlls(output)
    size_mb = sum(
        f.stat().st_size for f in output.rglob("*") if f.is_file()
    ) / (1024 * 1024)
    build_cli.ok(f"Build complete: {output}")
    build_cli.info(f"[SIZE] {size_mb:.1f} MB")
else:
    build_cli.fail("Build failed.")
    sys.exit(1)
