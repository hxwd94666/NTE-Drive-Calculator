# 仅枚举明确账号与配置域，拒绝链接和冲突身份，数据库操作交给存储层。
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

from src.storage.sqlite.runtime_data_snapshot import backup_database
from .lock import reject_links
from .metadata import DistributionError


def account_databases(root: Path) -> list[tuple[Path, str]]:
    index = root / "accounts" / "accounts.json"
    reject_links(index)
    if not index.exists():
        if (root / "accounts").exists() and any((root / "accounts").iterdir()):
            raise DistributionError("账号目录已有内容但索引缺失；请恢复索引，不创建替代账号。")
        return []
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
        rows = data["accounts"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty accounts")
        ids = [row["id"] for row in rows]
        if (any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) for key in ids)
                or len({key.casefold() for key in ids}) != len(ids) or data["active_account_id"] not in ids):
            raise ValueError("invalid account identity")
    except (ValueError, KeyError, TypeError) as error:
        raise DistributionError("账号索引损坏或身份冲突；请恢复原数据，不创建默认账号。") from error
    result = []
    for key in ids:
        path = root / "accounts" / key / "user_data.sqlite3"
        reject_links(path)
        if not path.is_file():
            raise DistributionError("账号索引引用的数据库缺失，请恢复原数据。")
        result.append((path, key))
    return result


def configuration_files(root: Path) -> list[Path]:
    """仅运行配置与索引，不将发行静态库、日志或缓存纳入恢复点。"""
    result = []
    directories = [root / "config"]
    for database, _key in account_databases(root):
        directories.append(database.parent / "config")
    for directory in directories:
        reject_links(directory)
        if directory.exists():
            for path in directory.rglob("*"):
                reject_links(path)
                if directory == root / "config" and path.relative_to(directory).parts[0] == "native-loader":
                    continue
                if path.is_file():
                    result.append(path)
    index = root / "accounts" / "accounts.json"
    if index.exists():
        result.append(index)
    if len(result) > 10000 or sum(path.stat().st_size for path in result) > 64 * 1024 * 1024:
        raise DistributionError("配置恢复点超出有界大小，请先检查配置目录。")
    return result


def copy_snapshot_file(source: Path, destination: Path, *, database: bool = False) -> None:
    reject_links(source)
    reject_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if database:
        backup_database(source, destination)
    else:
        shutil.copyfile(source, destination)
