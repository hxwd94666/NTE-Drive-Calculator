# 提供用户数据 SQLite 的连接、迁移与基础工具。
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from .user_data_support import (
    BASE_SCHEMA_VERSION,
    DEFAULT_SCHEMA_PATH,
    USER_MIGRATIONS,
    SCHEMA_VERSION,
    UserDataError,
    _utc_now,
)
from .user_data_migration_backup import backup_before_history_migration
from .transaction_script import execute_transaction_script

class UserDataDaoCore:
    """单个应用账号所拥有数据的读写边界。

    数据库只保存原始游戏 ID，不映射到旧 JSON 名称，也不重复保存静态显示数据。
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        account_id: str | None = None,
        account_name: str | None = None,
        schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        existed = self.database_path.is_file()
        if not existed and not account_id:
            raise UserDataError("创建用户数据库时必须提供 account_id")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection: sqlite3.Connection | None = sqlite3.connect(
                self.database_path, timeout=10.0
            )
        except sqlite3.Error as exc:
            raise UserDataError(f"无法打开用户数据库：{self.database_path}") from exc
        self._connection.row_factory = sqlite3.Row
        self._transaction_serial = 0
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        try:
            if not existed:
                self._initialize(
                    Path(schema_path),
                    account_id=str(account_id),
                    account_name=str(account_name or account_id),
                )
            self._migrate_schema(backup_existing=existed)
            self._validate_schema()
        except BaseException:
            self.close()
            if not existed:
                self.database_path.unlink(missing_ok=True)
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise UserDataError("用户数据库 DAO 已关闭")
        return self._connection

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """在唯一连接上原子提交一项业务；嵌套 DAO 操作只释放保存点，不提前提交外层。"""
        connection = self._db()
        owns_transaction = not connection.in_transaction
        if owns_transaction:
            connection.execute("BEGIN IMMEDIATE")
            savepoint = None
        else:
            self._transaction_serial += 1
            savepoint = f"nte_write_{self._transaction_serial}"
            connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield connection
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            # 验证异常、取消异常和提交失败都保持原事实；内层失败可被外层明确处理。
            if connection.in_transaction:
                if owns_transaction:
                    connection.rollback()
                else:
                    connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    @contextmanager
    def read_consistent_state(self):
        """同一 SQLite 读快照读取关联事实；本方法不提交调用方已有事务。"""
        connection = self._db()
        owns_transaction = not connection.in_transaction
        if owns_transaction:
            connection.execute("BEGIN")
        try:
            yield self
        finally:
            if owns_transaction:
                connection.rollback()

    def _initialize(self, schema_path: Path, *, account_id: str, account_name: str) -> None:
        if not schema_path.is_file():
            raise UserDataError(f"用户数据库结构文件不存在：{schema_path}")
        connection = self._db()
        try:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            now = _utc_now()
            connection.execute(
                "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                (BASE_SCHEMA_VERSION, now),
            )
            connection.execute(
                """
                INSERT INTO database_profile(
                    singleton_id, account_id, account_name, created_at_utc, updated_at_utc
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (account_id, account_name, now, now),
            )
            connection.execute(
                """
                INSERT INTO sync_settings(
                    singleton_id, inventory_sync_method, equipment_apply_method,
                    capture_device_id, raw_capture_enabled,
                    inventory_settle_seconds, updated_at_utc
                ) VALUES (1, 'nte_core', 'nte_core', NULL, 0, 5.0, ?)
                """,
                (now,),
            )
            connection.commit()
            connection.execute("PRAGMA journal_mode = WAL")
        except (OSError, sqlite3.Error) as exc:
            raise UserDataError("无法初始化用户数据库") from exc

    def _migrate_schema(self, *, backup_existing: bool = True) -> None:
        connection = self._db()
        try:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            # 锁冲突只表示暂未读到结构，不证明文件类型无效；保留原异常供上层分类。
            code = getattr(exc, "sqlite_errorcode", 0)
            if isinstance(code, int) and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise UserDataError("用户数据库正忙，读取结构受阻；请稍后重试") from exc
            raise UserDataError("文件不是 NTE 用户数据库") from exc
        version = int(row["version"] or 0) if row is not None else 0
        if version > SCHEMA_VERSION:
            raise UserDataError(
                f"用户数据库结构版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}"
            )
        try:
            for target_version in range(version + 1, SCHEMA_VERSION + 1):
                migration_path = USER_MIGRATIONS.get(target_version)
                if migration_path is None or not migration_path.is_file():
                    raise UserDataError(f"缺少用户数据库迁移脚本：v{target_version}")
                migration_sql = migration_path.read_text(encoding="utf-8")
                if backup_existing and target_version == 45:
                    # 备份守住养成历史迁移边界；更早的 v35 默认值物化仍先走原迁移事务，
                    # 避免把可修复的旧存储形态提前判坏。备份照常完整校验，失败不进入 v45。
                    backup_before_history_migration(self.database_path, target_version - 1)
                rebuilds_foreign_key_parent = migration_sql.startswith(
                    "-- requires-foreign-keys-off"
                )
                if rebuilds_foreign_key_parent:
                    connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    # 等待锁期间另一 owner 可能已完成本次迁移，事务内再次核对后再执行。
                    actual_version = connection.execute(
                        "SELECT MAX(version) FROM schema_migration"
                    ).fetchone()[0]
                    if actual_version >= target_version:
                        connection.rollback()
                        continue
                    self._prepare_migration(connection, target_version)
                    self._execute_migration_script(connection, migration_sql)
                    connection.execute(
                        "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                        (target_version, _utc_now()),
                    )
                    if rebuilds_foreign_key_parent:
                        # 重建父表时暂时关闭外键；提交前检查，拒绝时连同 DDL 和版本标记回滚。
                        # 保留旧版本后，未修复输入再次打开仍须经过同一迁移门禁。
                        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                        if violations:
                            raise UserDataError(
                                f"用户数据库迁移 v{target_version} 产生外键错误"
                            )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    if rebuilds_foreign_key_parent:
                        connection.execute("PRAGMA foreign_keys = ON")
        except (OSError, sqlite3.Error) as exc:
            connection.rollback()
            raise UserDataError("无法升级用户数据库结构") from exc

    @staticmethod
    def _prepare_migration(
        connection: sqlite3.Connection,
        target_version: int,
    ) -> None:
        """在同一迁移事务内修复会阻断后续 DDL 的历史存储形态。"""

        if target_version != 35:
            return
        # v32 通过 ADD COLUMN 引入普通抗性；旧行只在读取时获得 0.2 默认值，
        # 部分 SQLite 版本会在下一次 ALTER TABLE 时把未物化的值判为 NULL。
        # 自赋值保留所有有效值，并把虚拟默认值物化；失败时随 v35 一起回滚。
        connection.execute(
            """
            UPDATE battle_target_condition
            SET resistance_normal = COALESCE(resistance_normal, 0.2)
            """
        )

    @staticmethod
    def _execute_migration_script(connection: sqlite3.Connection, script: str) -> None:
        """在调用方已经开启的事务中逐条执行迁移 SQL，避免 executescript 隐式提交。"""
        # 保留账号 DAO 的错误类型与原异常链；公共执行器不反向依赖账号领域。
        execute_transaction_script(
            connection, script,
            incomplete_error=UserDataError("用户数据库迁移脚本包含未结束的 SQL 语句"),
        )

    def _validate_schema(self) -> None:
        try:
            row = self._db().execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            # 锁冲突只表示暂未读到结构，不证明文件类型无效；保留原异常供上层分类。
            code = getattr(exc, "sqlite_errorcode", 0)
            if isinstance(code, int) and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise UserDataError("用户数据库正忙，读取结构受阻；请稍后重试") from exc
            raise UserDataError("文件不是 NTE 用户数据库") from exc
        version = row["version"] if row is not None else None
        if version != SCHEMA_VERSION:
            raise UserDataError(
                f"不支持的用户数据库结构版本：{version!r}；需要 {SCHEMA_VERSION}"
            )

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self._db().execute(sql, tuple(parameters))]

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self._db().execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row is not None else None
