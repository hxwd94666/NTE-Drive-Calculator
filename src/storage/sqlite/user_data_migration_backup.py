# 在账号结构升级前生成不随默认导出携带的一致性 SQLite 备份。
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

from src.storage.sqlite.user_data_support import UserDataError

MIGRATION_BACKUP_DIRECTORY = ".migration-backups"


def backup_before_history_migration(database_path: Path, version: int) -> Path:
    """只操作确切账号库，备份提交前完成校验，不覆盖或清理旧备份。"""
    # 更早版本可能已按原事务完成升级；本备份失败仅保证尚未进入养成历史迁移。
    root = database_path.parent.resolve()
    directory = root / MIGRATION_BACKUP_DIRECTORY
    if directory.resolve().parent != root:
        raise UserDataError("账号升级备份目录归属异常")
    temporary: Path | None = None
    try:
        directory.mkdir(exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="pending-", suffix=".sqlite3", dir=directory)
        os.close(descriptor)
        temporary = Path(name)
        deadline = monotonic() + 30.0

        def progress(_status: int, _remaining: int, _total: int) -> None:
            if monotonic() > deadline:
                raise UserDataError("账号升级备份超时，尚未进行养成历史结构升级")

        with closing(sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise UserDataError("账号升级备份完整性校验失败")
                backed_version = destination.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
                if backed_version != version:
                    raise UserDataError("账号结构在升级备份期间改变，请重新打开账号")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = directory / f"user-data-v{version}-{stamp}-{uuid4().hex}.sqlite3"
        os.replace(temporary, target)
        temporary = None
        return target
    except (OSError, sqlite3.Error) as error:
        raise UserDataError("账号升级备份失败，尚未进行养成历史结构升级") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
