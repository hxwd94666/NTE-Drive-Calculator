# 统一账号管理与战报导出使用的当前账号命名边界。

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.integrations.accounts.index_file import (
    locked_account_index, read_account_index_bytes, write_account_index_bytes,
)

from src.storage.sqlite.user_data_dao import (
    UserDataDao,
    UserDataError,
    UserDataValidationError,
)


def normalize_account_name(value: object) -> str:
    name = str(value).strip()
    if not name:
        raise UserDataValidationError("account_name 不能为空")
    return name


class AccountNamingService:
    """Keep accounts.json and database_profile.account_name in sync."""

    def __init__(
        self,
        *,
        accounts_index_path: str | Path,
        user_database_path: str | Path,
        account_id: str,
        context_is_current: Callable[[], bool],
    ) -> None:
        self._accounts_index_path = Path(accounts_index_path).resolve()
        self._user_database_path = Path(user_database_path).resolve()
        self._account_id = str(account_id)
        self._context_is_current = context_is_current

    def current_name(self) -> str:
        self._ensure_current()
        with UserDataDao(self._user_database_path) as user_dao:
            profile = user_dao.profile()
        if str(profile["account_id"]) != self._account_id:
            raise UserDataError("冻结账号与用户数据库不一致")
        _raw, index = self._read_index()
        account = next(
            (
                row
                for row in index.get("accounts", ())
                if isinstance(row, dict) and str(row.get("id")) == self._account_id
            ),
            None,
        )
        if account is None:
            raise UserDataError("账号索引中不存在冻结账号")
        return normalize_account_name(account.get("name"))

    def rename(self, value: object) -> str:
        name = normalize_account_name(value)
        # 账号 ID、路径和代次已经冻结；统一按索引锁→DAO 写事务获取锁。
        with locked_account_index():
            self._ensure_current()
            original_bytes, index = self._read_index()
            account = next((row for row in index["accounts"]
                            if isinstance(row, dict) and str(row.get("id")) == self._account_id), None)
            if account is None:
                raise UserDataError("账号索引中不存在冻结账号")
            published: bytes | None = None
            with UserDataDao(self._user_database_path) as user_dao:
                try:
                    with user_dao.write_transaction():
                        if str(user_dao.profile()["account_id"]) != self._account_id:
                            raise UserDataError("冻结账号与用户数据库不一致")
                        user_dao.rename_account(name)
                        self._ensure_current()
                        account["name"] = name
                        raw = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
                        self._write_bytes_atomic(raw)
                        published = raw
                except BaseException as error:
                    # 未由本次发布索引时只回滚 SQLite，绝不用旧整索引覆盖新状态。
                    if published is not None:
                        try:
                            current = read_account_index_bytes(self._accounts_index_path)
                        except BaseException as compensation_error:
                            raise UserDataError("账号命名提交失败，索引补偿未完成；保留现场，请重新核对。") from compensation_error
                        if current != published:
                            raise UserDataError("账号命名补偿冲突：索引已有其他更新，保留当前索引，请重新核对。") from error
                        try:
                            self._write_bytes_atomic(original_bytes)
                        except BaseException as compensation_error:
                            raise UserDataError("账号命名提交失败，索引补偿未完成；保留现场，请重新核对。") from compensation_error
                    raise
        return name

    def _ensure_current(self) -> None:
        if not self._context_is_current():
            raise UserDataError("账号上下文已经变化")

    def _read_index(self) -> tuple[bytes, dict[str, Any]]:
        try:
            raw = read_account_index_bytes(self._accounts_index_path)
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UserDataError("无法读取账号索引") from error
        if not isinstance(value, dict) or not isinstance(value.get("accounts"), list):
            raise UserDataError("账号索引格式无效")
        return raw, value

    def _write_bytes_atomic(self, raw: bytes) -> None:
        try:
            write_account_index_bytes(self._accounts_index_path, raw)
        except OSError as error:
            raise UserDataError("无法更新账号索引") from error
