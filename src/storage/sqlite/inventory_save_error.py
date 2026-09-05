# 保留背包保存失败的 SQLite 诊断，并限制公开信息为错误码和结构摘要。
from __future__ import annotations

import re
import sqlite3

from .user_data_support import UserDataError


_CODE_CATEGORIES = {
    sqlite3.SQLITE_BUSY: "BUSY",
    sqlite3.SQLITE_LOCKED: "BUSY",
    sqlite3.SQLITE_READONLY: "READONLY",
    sqlite3.SQLITE_PERM: "PERMISSION",
    sqlite3.SQLITE_AUTH: "PERMISSION",
    sqlite3.SQLITE_FULL: "FULL",
    sqlite3.SQLITE_CORRUPT: "CORRUPT",
    sqlite3.SQLITE_NOTADB: "CORRUPT",
    sqlite3.SQLITE_IOERR: "IO",
    sqlite3.SQLITE_CANTOPEN: "OPEN",
    sqlite3.SQLITE_CONSTRAINT: "CONSTRAINT",
    sqlite3.SQLITE_SCHEMA: "SCHEMA",
}
_FIXED_MESSAGES = {
    "database is locked": "BUSY",
    "database table is locked": "BUSY",
    "attempt to write a readonly database": "READONLY",
    "authorization denied": "PERMISSION",
    "access permission denied": "PERMISSION",
    "database or disk is full": "FULL",
    "database disk image is malformed": "CORRUPT",
    "file is not a database": "CORRUPT",
    "disk I/O error": "IO",
    "unable to open database file": "OPEN",
    "FOREIGN KEY constraint failed": "CONSTRAINT",
    "database schema has changed": "SCHEMA",
    "cannot start a transaction within a transaction": "TRANSACTION",
    "cannot commit - no transaction is active": "TRANSACTION",
}
_STRUCTURE_MESSAGE = re.compile(
    r"(?:(?:no such table|no such column): [A-Za-z_][A-Za-z0-9_.]*"
    r"|table [A-Za-z_][A-Za-z0-9_]* has no column named [A-Za-z_][A-Za-z0-9_]*"
    r"|(?:UNIQUE|NOT NULL|CHECK) constraint failed: "
    r"[A-Za-z_][A-Za-z0-9_.]*(?:, [A-Za-z_][A-Za-z0-9_.]*)*)"
)


def _sqlite_diagnostics(error: sqlite3.Error) -> dict[str, object]:
    # 不输出任意 SQLite 消息：触发器、自定义函数或绑定错误可能夹带业务值和路径。
    message = str(error)
    safe_message = (
        message
        if message in _FIXED_MESSAGES or _STRUCTURE_MESSAGE.fullmatch(message)
        else "SQLite 原始消息未公开；请结合错误码与失败阶段定位"
    )
    fields: dict[str, object] = {
        "sqlite_exception_type": type(error).__name__,
        "sqlite_message": safe_message,
    }
    code = getattr(error, "sqlite_errorcode", None)
    name = getattr(error, "sqlite_errorname", None)
    if isinstance(code, int):
        fields["sqlite_errorcode"] = code
    if isinstance(name, str) and re.fullmatch(r"SQLITE_[A-Z0-9_]+", name):
        fields["sqlite_errorname"] = name
    return fields


class InventorySnapshotSaveError(UserDataError):
    """快照事务失败；原始异常仅通过异常链保留，展示与日志消费安全诊断。"""

    def __init__(
        self,
        error: sqlite3.Error,
        *,
        stage: str,
        rollback_error: sqlite3.Error | None,
    ) -> None:
        details = _sqlite_diagnostics(error)
        code = details.get("sqlite_errorcode")
        category = _CODE_CATEGORIES.get(code & 0xFF) if isinstance(code, int) else None
        message = str(error)
        if category is None:
            category = _FIXED_MESSAGES.get(message)
        if category is None and _STRUCTURE_MESSAGE.fullmatch(message):
            category = "CONSTRAINT" if "constraint failed:" in message else "SCHEMA"
        self.error_code = f"SNAPSHOT_SAVE_{category or 'FAILED'}"
        self.diagnostics: dict[str, object] = {
            "save_error_code": self.error_code,
            "save_stage": stage,
            **details,
            "rollback_status": "failed" if rollback_error is not None else "succeeded",
        }
        if rollback_error is not None:
            self.diagnostics["rollback_error"] = _sqlite_diagnostics(rollback_error)
        sqlite_name = details.get("sqlite_errorname", "SQLite 错误名未提供")
        sqlite_code = details.get("sqlite_errorcode", "未知")
        rollback_detail = (
            f"；回滚失败：{self.diagnostics['rollback_error']}"
            if rollback_error is not None else "；回滚完成"
        )
        super().__init__(
            f"无法导入背包快照：{self.error_code}；阶段={stage}；"
            f"{details['sqlite_exception_type']} / {sqlite_name} ({sqlite_code})；"
            f"{details['sqlite_message']}{rollback_detail}"
        )
