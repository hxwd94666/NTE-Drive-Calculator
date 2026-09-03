# 在人工发布前完成检查、构建并生成安装包校验值。
"""只执行本地发布准备，不创建标签、不推送、也不上传任何发布文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_cli
from tools.game_data.promote_static_release import (
    SIZE_MANIFEST_REQUIRED_SCHEMA_VERSION,
    StaticReleasePromotionError,
    format_size,
    load_local_config,
    validate_database_size,
)
from src.app.version import __version__
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

build_cli.configure_utf8_console()


INSTALLER_NAME = f"NTE_Drive_Calc_Setup_{__version__}.exe"
INSTALLER_PATH = ROOT / "installer" / "output" / INSTALLER_NAME
STATIC_DATABASE = ROOT / "data" / "game_static.sqlite3"
STATIC_MANIFEST = ROOT / "data" / "manifest.json"
GAME_UI_ASSET_ROOT = ROOT / "assets" / "game_ui"
GAME_UI_ASSET_MANIFEST = GAME_UI_ASSET_ROOT / "manifest.json"
APP_INTERNAL = ROOT / "dist" / "NTE_Drive_Calc" / "_internal"
BUNDLED_STATIC_DATABASE = APP_INTERNAL / "data" / "game_static.sqlite3"
BUNDLED_STATIC_MANIFEST = APP_INTERNAL / "data" / "manifest.json"
BUNDLED_GAME_UI_ASSET_ROOT = APP_INTERNAL / "assets" / "game_ui"
BUNDLED_GAME_UI_ASSET_MANIFEST = BUNDLED_GAME_UI_ASSET_ROOT / "manifest.json"
LOCAL_CONFIG_ENV = "NTE_LOCAL_CONFIG"
COMPONENTS = (
    (
        ROOT / "third_party" / "nte-core" / "bin" / "nte-core.exe",
        ROOT / "third_party" / "nte-core" / "COMPONENT.md",
        "当前本机二进制 SHA-256",
    ),
    (
        ROOT / "third_party" / "mods-plugin" / "bin" / "dwmapi.dll",
        ROOT / "third_party" / "mods-plugin" / "COMPONENT.md",
        "`bin/dwmapi.dll` SHA-256",
    ),
    (
        ROOT / "third_party" / "mod-loader" / "bin" / "nte-mod-loader.exe",
        ROOT / "third_party" / "mod-loader" / "COMPONENT.md",
        "`bin/nte-mod-loader.exe` SHA-256",
    ),
)
REQUIRED_COMPONENT_FILES = (
    ROOT / "NOTICE",
    ROOT / "third_party" / "nte-core" / "LICENSE",
    ROOT / "third_party" / "nte-core" / "SOURCE.md",
    ROOT / "third_party" / "mods-plugin" / "LICENSE",
    ROOT / "third_party" / "mods-plugin" / "SOURCE.md",
    ROOT / "third_party" / "mods-plugin" / "workspace" / "nte-mods.enabled",
    ROOT / "third_party" / "mods-plugin" / "workspace" / "nte-mods" / "equipment.nte",
    ROOT / "third_party" / "mods-plugin" / "workspace" / "nte-mods" / "combat-clock.nte",
    ROOT / "third_party" / "mod-loader" / "LICENSE",
    ROOT / "third_party" / "mod-loader" / "SOURCE.md",
    ROOT / "third_party" / "mod-loader" / "THIRD_PARTY_LICENSES.md",
    ROOT / "third_party" / "mod-loader" / "licenses" / "MinHook-LICENSE.txt",
    ROOT / "third_party" / "mod-loader" / "licenses" / "ManualMap-LICENSE.txt",
)


def run(command: Sequence[str]) -> None:
    """在仓库根目录执行命令，失败时立即终止准备流程。"""

    print("+", subprocess.list2cmdline(list(command)))
    subprocess.run(list(command), cwd=ROOT, check=True)


def sha256(path: Path) -> str:
    """流式计算文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def ensure_clean_worktree(*, allow_dirty: bool) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.stdout.strip() and not allow_dirty:
        raise RuntimeError("Git 工作区不干净；请先提交或暂存之外妥善处理所有改动。")
    if result.stdout.strip():
        print("[警告] 已显式允许在非干净工作区执行，仅用于本地验证。")


def ensure_tag_matches_version(tag: str) -> None:
    if tag != __version__:
        raise RuntimeError(f"发布标签 {tag!r} 与应用版本 {__version__!r} 不一致。")


def validate_static_database(
    path: Path = STATIC_DATABASE,
    *,
    allow_size_warning: bool = False,
) -> dict[str, object]:
    """以只读方式检查结构版本、核心表和 SQLite 完整性。"""

    with StaticGameDataDao(path) as dao:
        summary = dao.summary()
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"静态数据库 quick_check 失败：{result!r}")
    try:
        validate_database_size(
            path.stat().st_size,
            allow_size_warning=allow_size_warning,
        )
    except StaticReleasePromotionError as exc:
        raise RuntimeError(str(exc)) from exc
    print(f"[通过] 静态数据库：schema={summary['schema_version']}，dataset={summary['dataset']['dataset_id']}")
    return summary


def validate_static_manifest(
    database_path: Path = STATIC_DATABASE,
    manifest_path: Path = STATIC_MANIFEST,
) -> dict[str, object]:
    """核对机器清单与实际发行数据库。"""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取静态数据库清单：{manifest_path}") from exc
    database = manifest.get("database") if isinstance(manifest, dict) else None
    if not isinstance(database, dict):
        raise RuntimeError("静态数据库清单缺少 database 对象")
    recorded_size = database.get("size_bytes")
    if recorded_size is not None and (
        isinstance(recorded_size, bool) or not isinstance(recorded_size, int)
    ):
        raise RuntimeError("静态数据库清单的 database.size_bytes 必须是整数")
    declared_schema = database.get("schema_version")
    if isinstance(declared_schema, bool) or not isinstance(declared_schema, int):
        raise RuntimeError("静态数据库清单的 database.schema_version 必须是整数")
    with StaticGameDataDao(
        database_path,
        expected_schema_version=declared_schema,
    ) as dao:
        summary = dao.summary()
    expected = {
        "filename": database_path.name,
        "dataset_id": summary["dataset"]["dataset_id"],
        "schema_version": summary["schema_version"],
        "size_bytes": database_path.stat().st_size,
        "sha256": sha256(database_path),
    }
    schema_version = int(summary["schema_version"])
    legacy_size_manifest = (
        schema_version < SIZE_MANIFEST_REQUIRED_SCHEMA_VERSION
        and recorded_size is None
    )
    if legacy_size_manifest:
        expected.pop("size_bytes")
    if (
        schema_version >= SIZE_MANIFEST_REQUIRED_SCHEMA_VERSION
        and recorded_size is None
    ):
        raise RuntimeError("静态数据库清单缺少 database.size_bytes")
    actual = {key: database.get(key) for key in expected}
    actual["sha256"] = str(actual.get("sha256") or "").upper()
    if actual != expected:
        raise RuntimeError(f"静态数据库清单与实际文件不一致：清单={actual}，实际={expected}")
    if database.get("source_payloads_omitted") is not True:
        raise RuntimeError("发行静态数据库清单必须声明已省略原始 payload")
    if legacy_size_manifest:
        print(
            f"[警告] schema v{schema_version} 旧清单缺少 database.size_bytes；"
            f"已按实际文件校验 {format_size(database_path.stat().st_size)}，"
            "schema v30 起将拒绝缺失字段"
        )
    print(f"[通过] 静态数据库清单：{manifest_path.relative_to(ROOT)}")
    return manifest


def validate_game_ui_asset_manifest(
    asset_root: Path,
    manifest_path: Path,
) -> dict[str, object]:
    """核对游戏 UI 资源清单中的路径、字节数、哈希和汇总。"""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取游戏 UI 资源清单：{manifest_path}") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict):
        raise RuntimeError("游戏 UI 资源清单缺少 files 对象")

    declared_paths: set[str] = set()
    total_bytes = 0
    for relative_path, record in files.items():
        pure_path = PurePosixPath(str(relative_path))
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise RuntimeError(f"游戏 UI 资源清单包含越界路径：{relative_path}")
        if not isinstance(record, dict):
            raise RuntimeError(f"游戏 UI 资源记录无效：{relative_path}")
        path = asset_root.joinpath(*pure_path.parts)
        if not path.is_file():
            raise RuntimeError(f"游戏 UI 资源缺失：{relative_path}")
        expected_bytes = record.get("bytes")
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int):
            raise RuntimeError(f"游戏 UI 资源缺少整数 bytes：{relative_path}")
        actual_bytes = path.stat().st_size
        actual_hash = sha256(path)
        expected_hash = str(record.get("sha256") or "").upper()
        if actual_bytes != expected_bytes or actual_hash != expected_hash:
            raise RuntimeError(
                "游戏 UI 资源与清单不一致："
                f"{relative_path}，清单={expected_bytes} bytes/{expected_hash}，"
                f"实际={actual_bytes} bytes/{actual_hash}"
            )
        declared_paths.add(pure_path.as_posix())
        total_bytes += actual_bytes

    actual_image_paths = {
        path.relative_to(asset_root).as_posix()
        for path in asset_root.rglob("*.png")
        if path.is_file()
    }
    if actual_image_paths != declared_paths:
        raise RuntimeError(
            "游戏 UI PNG 集合与清单不一致："
            f"未登记={sorted(actual_image_paths - declared_paths)[:5]}，"
            f"缺失={sorted(declared_paths - actual_image_paths)[:5]}"
        )
    if manifest.get("total_files") != len(declared_paths):
        raise RuntimeError("游戏 UI 资源清单 total_files 不一致")
    if manifest.get("total_bytes") != total_bytes:
        raise RuntimeError("游戏 UI 资源清单 total_bytes 不一致")
    print(
        f"[通过] 游戏 UI 资源：{len(declared_paths)} 个文件，"
        f"{format_size(total_bytes)}"
    )
    return manifest


def validate_packaged_release_artifacts(
    *,
    allow_size_warning: bool = False,
) -> None:
    """复核安装包输入目录中的静态库和游戏 UI 资源没有打包漂移。"""

    validate_static_database(
        BUNDLED_STATIC_DATABASE,
        allow_size_warning=allow_size_warning,
    )
    validate_static_manifest(BUNDLED_STATIC_DATABASE, BUNDLED_STATIC_MANIFEST)
    if sha256(BUNDLED_STATIC_MANIFEST) != sha256(STATIC_MANIFEST):
        raise RuntimeError("安装包输入目录中的静态数据库 manifest 与 data/ 不一致")

    validate_game_ui_asset_manifest(GAME_UI_ASSET_ROOT, GAME_UI_ASSET_MANIFEST)
    validate_game_ui_asset_manifest(
        BUNDLED_GAME_UI_ASSET_ROOT,
        BUNDLED_GAME_UI_ASSET_MANIFEST,
    )
    if sha256(BUNDLED_GAME_UI_ASSET_MANIFEST) != sha256(GAME_UI_ASSET_MANIFEST):
        raise RuntimeError("安装包输入目录中的游戏 UI 资源 manifest 与 assets/ 不一致")


def validate_static_dataset_against_local_config(
    summary: dict[str, object],
    local_config_path: Path,
) -> None:
    """阻止本机已切换新数据集后继续发布旧的正式静态库。"""

    config = load_local_config(local_config_path)
    dataset = summary.get("dataset")
    if not isinstance(dataset, dict):
        raise RuntimeError("静态数据库摘要缺少 dataset")
    actual = str(dataset.get("dataset_id") or "")
    expected = str(config["dataset_id"])
    if actual != expected:
        raise RuntimeError(
            f"本机配置 dataset={expected}，正式静态库 dataset={actual}；"
            "请先完成候选晋升"
        )
    print(f"[通过] 本机静态数据配置：dataset={expected}")


def _recorded_hash(record: Path, label: str) -> str:
    text = record.read_text(encoding="utf-8")
    match = re.search(
        rf"^-\s*{re.escape(label)}：`?([A-Fa-f0-9]{{64}})`?\s*$",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"{record} 未记录 {label}")
    return match.group(1).upper()


def validate_components() -> None:
    """校验随包组件、脚本、来源说明和许可证。"""

    missing = [path for path in REQUIRED_COMPONENT_FILES if not path.is_file()]
    if missing:
        formatted = "\n".join(f"- {path.relative_to(ROOT)}" for path in missing)
        raise RuntimeError(f"第三方组件记录不完整：\n{formatted}")

    for binary, record, label in COMPONENTS:
        if not binary.is_file():
            raise RuntimeError(f"缺少发行组件：{binary.relative_to(ROOT)}")
        expected = _recorded_hash(record, label)
        actual = sha256(binary)
        if actual != expected:
            raise RuntimeError(f"{binary.relative_to(ROOT)} SHA-256 不匹配：记录={expected}，实际={actual}")
        print(f"[通过] {binary.relative_to(ROOT)}：{actual}")


def write_checksum(path: Path) -> Path:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum_path.write_text(
        f"{sha256(path)}  {path.name}\n",
        encoding="ascii",
    )
    return checksum_path


def print_manual_commands(tag: str, installer: Path, notes_file: Path) -> None:
    relative_installer = installer.relative_to(ROOT)
    relative_checksum = installer.with_suffix(installer.suffix + ".sha256").relative_to(ROOT)
    print("\n本地准备完成。确认产物后，由维护者手工执行：")
    print(f"git tag {tag}")
    print(f"git push origin {tag}")
    print(
        "gh release create "
        f'{tag} "{relative_installer}" "{relative_checksum}" '
        f'--title {tag} --notes-file "{notes_file}"'
    )
    print("Mirror 的分发与下载风险由 Mirror 发布链负责，本工具不会上传 Mirror。")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=__version__, help="必须与应用版本完全一致。")
    parser.add_argument(
        "--notes-file",
        type=Path,
        default=Path("RELEASE_NOTES.md"),
        help="仅用于输出 gh release create 命令；本工具不读取或上传它。",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="仅供开发期验证；正式发布前不要使用。",
    )
    parser.add_argument("--skip-tests", action="store_true", help="仅供调试发布工具。")
    parser.add_argument("--skip-build", action="store_true", help="校验已有安装包，不重新构建。")
    parser.add_argument(
        "--local-config",
        type=Path,
        default=Path(os.environ[LOCAL_CONFIG_ENV])
        if os.environ.get(LOCAL_CONFIG_ENV)
        else None,
        help=(
            f"仓库外 local.paths.json；默认读取 {LOCAL_CONFIG_ENV}，"
            "正式发布必须提供"
        ),
    )
    parser.add_argument(
        "--allow-size-warning",
        action="store_true",
        help=(
            "显式放行已审计的 95–96 MiB 仓库内静态库；"
            "不覆盖 96 MiB 项目仓库预算或 GitHub 100 MiB 单文件硬上限"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        ensure_clean_worktree(allow_dirty=args.allow_dirty)
        ensure_tag_matches_version(args.tag)
        if args.local_config is None:
            raise RuntimeError(
                f"正式发布必须通过 --local-config 或 {LOCAL_CONFIG_ENV} "
                "提供本机静态数据配置"
            )
        static_summary = validate_static_database(
            allow_size_warning=args.allow_size_warning,
        )
        validate_static_manifest()
        validate_static_dataset_against_local_config(
            static_summary,
            args.local_config,
        )
        if not args.skip_tests:
            run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"])
        run(
            [
                sys.executable,
                "-X",
                f"pycache_prefix={ROOT / 'build' / 'compile-cache'}",
                "-m",
                "compileall",
                "-q",
                "src",
                "tests",
                "tools",
            ]
        )
        validate_components()
        if not args.skip_build:
            build_command = [
                sys.executable,
                str(ROOT / "build_installer.py"),
                "--version",
                __version__,
            ]
            run(build_command)
        if not INSTALLER_PATH.is_file():
            raise RuntimeError(f"找不到安装包：{INSTALLER_PATH}")
        validate_packaged_release_artifacts(
            allow_size_warning=args.allow_size_warning,
        )
        checksum_path = write_checksum(INSTALLER_PATH)
        print(f"[通过] 安装包：{INSTALLER_PATH}")
        print(f"[通过] SHA-256：{checksum_path}")
        print_manual_commands(args.tag, INSTALLER_PATH, args.notes_file)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
