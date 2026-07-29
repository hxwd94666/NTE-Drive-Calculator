# 在人工发布前完成检查、构建并生成安装包校验值。
"""只执行本地发布准备，不创建标签、不推送、也不上传任何发布文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_cli
from src.app.version import __version__
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

build_cli.configure_utf8_console()


INSTALLER_NAME = f"NTE_Drive_Calc_Setup_{__version__}.exe"
INSTALLER_PATH = ROOT / "installer" / "output" / INSTALLER_NAME
STATIC_DATABASE = ROOT / "data" / "game_static.sqlite3"
STATIC_MANIFEST = ROOT / "data" / "manifest.json"
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


def validate_static_database(path: Path = STATIC_DATABASE) -> dict[str, object]:
    """以只读方式检查结构版本、核心表和 SQLite 完整性。"""

    with StaticGameDataDao(path) as dao:
        summary = dao.summary()
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"静态数据库 quick_check 失败：{result!r}")
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
    with StaticGameDataDao(database_path) as dao:
        summary = dao.summary()
    expected = {
        "filename": database_path.name,
        "dataset_id": summary["dataset"]["dataset_id"],
        "schema_version": summary["schema_version"],
        "sha256": sha256(database_path),
    }
    actual = {key: database.get(key) for key in expected}
    actual["sha256"] = str(actual.get("sha256") or "").upper()
    if actual != expected:
        raise RuntimeError(f"静态数据库清单与实际文件不一致：清单={actual}，实际={expected}")
    if database.get("source_payloads_omitted") is not True:
        raise RuntimeError("发行静态数据库清单必须声明已省略原始 payload")
    print(f"[通过] 静态数据库清单：{manifest_path.relative_to(ROOT)}")
    return manifest


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
        "--skip-workshop-sync",
        action="store_true",
        help="构建时跳过工坊权重同步；正式发布应确保静态库已同步。",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        ensure_clean_worktree(allow_dirty=args.allow_dirty)
        ensure_tag_matches_version(args.tag)
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
        validate_static_database()
        validate_static_manifest()
        validate_components()
        if not args.skip_build:
            build_command = [
                sys.executable,
                str(ROOT / "build_installer.py"),
                "--version",
                __version__,
            ]
            build_command.append("--skip-workshop-sync" if args.skip_workshop_sync else "--require-workshop-sync")
            run(build_command)
        if not INSTALLER_PATH.is_file():
            raise RuntimeError(f"找不到安装包：{INSTALLER_PATH}")
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
