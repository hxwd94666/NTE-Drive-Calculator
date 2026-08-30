# 统一账号管理与战报导出使用的当前账号命名边界。

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
        self._ensure_current()
        original_bytes, index = self._read_index()
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
        with UserDataDao(self._user_database_path) as user_dao:
            profile = user_dao.profile()
            if str(profile["account_id"]) != self._account_id:
                raise UserDataError("冻结账号与用户数据库不一致")
            previous_name = normalize_account_name(profile["account_name"])
            user_dao.rename_account(name)
        try:
            self._ensure_current()
            account["name"] = name
            self._write_index_atomic(index)
        except BaseException:
            with UserDataDao(self._user_database_path) as user_dao:
                user_dao.rename_account(previous_name)
            if self._accounts_index_path.is_file():
                current_bytes = self._accounts_index_path.read_bytes()
                if current_bytes != original_bytes:
                    self._write_bytes_atomic(original_bytes)
            raise
        return name

    def _ensure_current(self) -> None:
        if not self._context_is_current():
            raise UserDataError("账号上下文已经变化")

    def _read_index(self) -> tuple[bytes, dict[str, Any]]:
        try:
            raw = self._accounts_index_path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UserDataError("无法读取账号索引") from error
        if not isinstance(value, dict) or not isinstance(value.get("accounts"), list):
            raise UserDataError("账号索引格式无效")
        return raw, value

    def _write_index_atomic(self, value: dict[str, Any]) -> None:
        try:
            raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise UserDataError("账号索引无法序列化") from error
        self._write_bytes_atomic(raw)

    def _write_bytes_atomic(self, raw: bytes) -> None:
        target = self._accounts_index_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        except OSError as error:
            raise UserDataError("无法更新账号索引") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
