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
import subprocess
import sys
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
SQLITE_SCHEMA_DIR = ROOT / "src" / "storage" / "sqlite" / "schema"
NTE_CORE_ENV = "NTE_CORE_EXE"
STATIC_DATABASE_ENV = "NTE_GAME_STATIC_DB"
NTE_CORE_RELEASE_FILES = (
    "LICENSE",
    "BUILD_VARIANT.md",
    "CLI_PROTOCOL_ZH.md",
    "CLI_PROTOCOL.md",
    "THIRD_PARTY_LICENSES.md",
)

EXPLICIT_WORKSHOP_ARGS = {"--skip-workshop-sync", "--require-workshop-sync", "--prompt-workshop-key"}


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
        build_cli.skip("普通模式：不更新异环工坊权重")
        return
    cmd = [sys.executable, str(ROOT / "tools" / "sync_workshop_weights.py")]
    if require_workshop_sync:
        cmd.extend(["--prompt-key", "--fallback-normal"])
    else:
        cmd.append("--optional")
    build_cli.run(cmd, ROOT)


_sync_workshop_weights_before_build()

for path in (DIST, BUILD):
    if path.exists():
        shutil.rmtree(path)
if SPEC.exists():
    SPEC.unlink()

onefile = "--onefile" in sys.argv

args = [
    str(ROOT / "main.py"),
    "--name=NTE_Drive_Calc",
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


# 用户数据库首次运行时需要 SQL 结构文件；PyInstaller 不会自动收集非 Python 文件。
if not SQLITE_SCHEMA_DIR.is_dir():
    raise FileNotFoundError(f"SQLite schema 目录不存在：{SQLITE_SCHEMA_DIR}")
_append_add_data(SQLITE_SCHEMA_DIR, "src/storage/sqlite/schema")

# nte-core 是随应用运行的本地组件。开发机可放在项目根目录，自动构建则通过
# NTE_CORE_EXE 指向从合作项目固定 Release 下载的文件。
nte_core_path = _required_build_file(
    "nte-core.exe",
    os.environ.get(NTE_CORE_ENV),
    ROOT / "nte-core.exe",
    ROOT / "build_resources" / "nte-core" / "nte-core.exe",
)
_append_add_binary(nte_core_path, ".")

# Release 目录若提供许可证和协议说明，则一并放入安装包，便于审计和再分发。
for release_name in NTE_CORE_RELEASE_FILES:
    release_file = nte_core_path.parent / release_name
    if release_file.is_file():
        _append_add_data(release_file, "licenses/nte-core")
if not (nte_core_path.parent / "LICENSE").is_file():
    build_cli.warn("nte-core 目录没有 LICENSE；本地测试可继续，正式发布必须使用完整 Release 目录")

# 静态数据库目前尚未接入旧界面：本地发布时可通过环境变量提供，CI 的公开来源
# 确定后再改成强制依赖。只要提供了路径，路径无效就立即终止，避免悄悄漏打包。
configured_static_database = os.environ.get(STATIC_DATABASE_ENV)
static_database_path = _first_existing_file(
    configured_static_database,
    ROOT / "build_resources" / "game_static.sqlite3",
    ROOT / "data" / "game_static.sqlite3",
)
if configured_static_database and static_database_path is None:
    raise FileNotFoundError(
        f"{STATIC_DATABASE_ENV} 指向的静态数据库不存在：{configured_static_database}"
    )
if static_database_path is not None:
    _append_add_data(static_database_path, "data")
    build_cli.info(f"[DATA] 已加入静态数据库：{static_database_path}")
else:
    build_cli.warn("未提供静态数据库；旧界面仍可运行，新数据页面接入前必须补齐发布来源")


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
PyInstaller.__main__.run(args)

output = DIST / "NTE_Drive_Calc"
if onefile:
    output = DIST / "NTE_Drive_Calc.exe"

if output.exists():
    size_mb = sum(
        f.stat().st_size for f in output.rglob("*") if f.is_file()
    ) / (1024 * 1024)
    build_cli.ok(f"Build complete: {output}")
    build_cli.info(f"[SIZE] {size_mb:.1f} MB")
else:
    build_cli.fail("Build failed.")
    sys.exit(1)
