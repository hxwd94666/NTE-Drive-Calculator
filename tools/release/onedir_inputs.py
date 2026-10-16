# 冻结干净目录构建的精确发行白名单，ZIP 只消费该次构建的未变字节。
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
from uuid import uuid4

from tools.release.app_bundle_validation import CONFIG_RELEASE_DIRECTORIES, CONFIG_RELEASE_FILES
from tools.release.managed_paths import iter_plain_tree, plain_directory_root, plain_member_stat


APP_EXE = "NTE_Drive_Calc.exe"
FORBIDDEN_DIRECTORIES = frozenset({
    "userdata", "accounts", "logs", "scanned_images", "screenshots", "captures", "maintenance", "__pycache__", ".git",
})
FORBIDDEN_SUFFIXES = frozenset({".pdb", ".pcap", ".pcapng", ".dmp", ".log", ".rs", ".cpp", ".hpp", ".c", ".h"})
FORBIDDEN_NAMES = frozenset({
    "user_data.sqlite3", "accounts.json", "nte_sdk.bin", "nte_sdk.checksum", ".env", "local.paths.json",
    "token", "credentials", "cdk.txt", "update_config.json", "hotkeys.json", "guide_seen.json",
})


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 成员重复")
        result[key] = value
    return result


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_release_payload_path(relative: str) -> None:
    """运行数据拒绝进入输入 receipt；业务源码只允许构建显式收集的 SQL。"""
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or (parts[0] != "_internal" and relative != APP_EXE):
        raise ValueError(f"不是目录构建发行成员：{relative}")
    lowered = [part.casefold() for part in parts]
    if any(part in FORBIDDEN_DIRECTORIES for part in lowered):
        raise ValueError(f"发行包含运行目录：{relative}")
    if (path.suffix.casefold() in FORBIDDEN_SUFFIXES or lowered[-1] in FORBIDDEN_NAMES
            or lowered[-1].endswith(("-wal", "-shm"))):
        raise ValueError(f"发行包含私有源码、调试、账号或运行数据：{relative}")
    if relative.startswith("_internal/src/") and not (relative.startswith("_internal/src/storage/sqlite/schema/") and path.suffix == ".sql"):
        raise ValueError(f"发行源码资源不在 SQL 白名单：{relative}")
    if relative.startswith("_internal/config/"):
        tail = parts[2:]
        if not (len(tail) == 1 and tail[0] in CONFIG_RELEASE_FILES or tail[0] in CONFIG_RELEASE_DIRECTORIES):
            raise ValueError(f"发行配置不在默认资源白名单：{relative}")
    if path.suffix.casefold() in {".sqlite", ".sqlite3", ".db"} and relative not in {
        "_internal/data/game_static.sqlite3", "_internal/data/app_shared.sqlite3", "_internal/data/role_catalog/game_static.sqlite3",
    }:
        raise ValueError(f"发行数据库不在静态白名单：{relative}")


def inventory_onedir(app_dir: Path) -> list[dict]:
    root = plain_directory_root(app_dir)
    files, names = [], set()
    for path, info in iter_plain_tree(root):
        if path == root:
            continue
        relative = path.relative_to(root).as_posix()
        # 空的用户目录也标记输入污染，不能被 ZIP 枚举忽略。
        if stat.S_ISDIR(info.st_mode):
            if any(part.casefold() in FORBIDDEN_DIRECTORIES for part in PurePosixPath(relative).parts):
                raise ValueError(f"发行包含运行目录：{relative}")
            if relative.split("/", 1)[0] != "_internal":
                raise ValueError(f"发行包根目录不在白名单：{relative}")
            continue
        check_release_payload_path(relative)
        if relative.casefold() in names:
            raise ValueError("发行成员大小写身份冲突")
        names.add(relative.casefold())
        files.append({"path": relative, "size": info.st_size, "sha256": sha256(path)})
    if APP_EXE.casefold() not in names or not any(row["path"].startswith("_internal/") for row in files):
        raise ValueError("目录构建缺少主程序或运行时")
    return sorted(files, key=lambda row: row["path"])


def write_onedir_receipt(app_dir: Path, receipt_path: Path, *, version: str) -> dict:
    """仅构建成功后冻结；receipt 位于 build，绝不作为 runtime 或用户数据文件。"""
    files = inventory_onedir(app_dir)
    parent = plain_directory_root(receipt_path.parent)
    if receipt_path.absolute().is_relative_to(app_dir.absolute()):
        raise ValueError("构建 receipt 必须位于发行目录外")
    try:
        existing = plain_member_stat(parent, receipt_path.absolute())
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise ValueError("构建 receipt 不是普通文件")
    value = {"schema_version": 1, "product": "nte-drive-calc", "version": version,
             "build_id": str(uuid4()), "architecture": "x64", "files": files}
    temporary = parent / f".onedir-{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary.replace(receipt_path)
    finally:
        temporary.unlink(missing_ok=True)
    return value


def verify_onedir_receipt(app_dir: Path, receipt_path: Path, *, version: str) -> dict:
    plain_member_stat(plain_directory_root(receipt_path.parent), receipt_path.absolute())
    if receipt_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("构建 receipt 超出大小限制")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
            or receipt.get("product") != "nte-drive-calc" or receipt.get("architecture") != "x64"
            or receipt.get("version") != version or not isinstance(receipt.get("build_id"), str)
            or not receipt["build_id"]):
        raise ValueError("目录构建身份或版本不匹配，请重新构建")
    if receipt.get("files") != inventory_onedir(app_dir):
        raise ValueError("目录产物与构建白名单不一致，请使用未运行且未改动的干净 onedir")
    return receipt
