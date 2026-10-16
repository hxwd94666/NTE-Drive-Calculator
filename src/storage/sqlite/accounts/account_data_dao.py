# 管理账号数据与设置的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.storage.sqlite.user_data_support import (
    SYNC_METHODS,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _utc_now,
)
from src.storage.sqlite.protocols import UserDataDaoMixinHost

class AccountDataDaoMixin(UserDataDaoMixinHost):
    """账号档案、同步设置和旧设置副本的持久化入口；只使用当前账号连接。"""

    def profile(self) -> dict[str, Any]:
        profile = self._one("SELECT * FROM database_profile WHERE singleton_id = 1")
        if profile is None:
            raise UserDataError("用户数据库缺少账号信息")
        profile.pop("singleton_id", None)
        return profile

    def rename_account(self, account_name: str) -> None:
        name = str(account_name).strip()
        if not name:
            raise UserDataValidationError("account_name 不能为空")
        # 独立调用仍提交；命名 Service 的外层事务拥有最终提交与失败回滚。
        with self.write_transaction() as connection:
            connection.execute(
                "UPDATE database_profile SET account_name = ?, updated_at_utc = ? WHERE singleton_id = 1",
                (name, _utc_now()),
            )

    def import_legacy_application_settings(
        self, candidates: Mapping[str, Mapping[str, Any]], *,
        check: Callable[[], None] | None = None,
    ) -> None:
        """在同一事务内只填仍缺失的设置组，并写入一次完成标记。"""
        # 候选已由 Service 全部规范化。锁内重检显式组，异常和取消不留半迁移。
        frozen = {str(key): dict(value) for key, value in candidates.items()}
        with self.write_transaction() as connection:
            if check is not None:
                check()
            if self.legacy_application_settings_imported():
                return
            existing = self.list_application_setting_copies()
            for key, value in frozen.items():
                if key not in existing:
                    self._write_application_setting(key, value)
            connection.execute(
                "INSERT INTO application_setting_migration(singleton_id, legacy_imported_at_utc) VALUES (1, ?)",
                (_utc_now(),),
            )
            if check is not None:
                check()

    def list_application_setting_copies(self) -> dict[str, dict[str, Any]]:
        copies: dict[str, dict[str, Any]] = {}
        for row in self._rows(
            "SELECT setting_key, value_json FROM application_setting_copy ORDER BY setting_key"
        ):
            value = _decoded(row["value_json"], {})
            if not isinstance(value, dict):
                raise UserDataError(
                    f"账号设置副本 {row['setting_key']!r} 不是 JSON 对象"
                )
            copies[str(row["setting_key"])] = value
        return copies

    def replace_application_setting_copy(
        self, setting_key: str, value: Mapping[str, Any], *,
        expected_loadout_plans: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        key = str(setting_key).strip()
        if not key:
            raise UserDataValidationError("setting_key 不能为空")
        if not isinstance(value, Mapping):
            raise UserDataValidationError("账号设置副本必须是对象")
        connection = self._db()
        try:
            if expected_loadout_plans:
                connection.execute("BEGIN IMMEDIATE")
                for expected in expected_loadout_plans:
                    character_id = _integer(expected["character_id"], "character_id", minimum=1)
                    row = self._one(
                        "SELECT character_id, current_plan_id, is_archived FROM role_loadout_slot WHERE slot_id = ?",
                        (_integer(expected["slot_id"], "slot_id", minimum=1),),
                    )
                    if (row is None or row["is_archived"]
                            or row["character_id"] != character_id
                            or row["current_plan_id"] != expected["plan_id"]):
                        raise UserDataValidationError("所选配装已变化，请关闭并重新打开倒带推荐后生成。")
            self._write_application_setting(key, value)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _write_application_setting(self, key: str, value: Mapping[str, Any]) -> None:
        self._db().execute(
            """
            INSERT INTO application_setting_copy(setting_key, value_json, updated_at_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at_utc = excluded.updated_at_utc
            """,
            (key, _json(dict(value)), _utc_now()),
        )

    def compare_and_replace_application_setting_copy(
        self, key: str, value: Mapping[str, Any], *, expected: Mapping[str, Any],
        missing_value: Mapping[str, Any], check: Callable[[], None] | None = None,
    ) -> bool:
        """同一写事务内核对编辑基线；并发编辑或账号代次失效时保留原配置。"""
        with self.write_transaction():
            if check is not None:
                check()
            current = self.list_application_setting_copies().get(key, dict(missing_value))
            if current != dict(expected):
                raise UserDataValidationError("配置已变化，请重新打开后修改")
            if current == dict(value):
                return False
            self._write_application_setting(key, value)
            if check is not None:
                check()
        return True

    def patch_application_setting_copy(self, key: str, fields: Mapping[str, Any]) -> None:
        """锁内只合并指定字段；参与外层事务，不覆盖其他功能刚保存的偏好。"""
        with self.write_transaction():
            current = self.list_application_setting_copies().get(key, {})
            self._write_application_setting(key, {**current, **fields})

    def delete_application_setting_copy(self, setting_key: str) -> None:
        self._db().execute(
            "DELETE FROM application_setting_copy WHERE setting_key = ?",
            (str(setting_key).strip(),),
        )
        self._db().commit()

    def legacy_application_settings_imported(self) -> bool:
        return self._one(
            "SELECT singleton_id FROM application_setting_migration WHERE singleton_id = 1"
        ) is not None

    def mark_legacy_application_settings_imported(self) -> None:
        self._db().execute(
            """
            INSERT INTO application_setting_migration(singleton_id, legacy_imported_at_utc)
            VALUES (1, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                legacy_imported_at_utc = excluded.legacy_imported_at_utc
            """,
            (_utc_now(),),
        )
        self._db().commit()

    def get_ui_item_order(self, scope: str) -> list[str]:
        normalized_scope = str(scope).strip()
        if not normalized_scope:
            raise UserDataValidationError("scope 不能为空")
        return [
            str(row["item_key"])
            for row in self._rows(
                """
                SELECT item_key
                FROM ui_item_order
                WHERE scope = ?
                ORDER BY ordinal
                """,
                (normalized_scope,),
            )
        ]

    def replace_ui_item_order(
        self, scope: str, item_keys: Sequence[str | int]
    ) -> list[str]:
        normalized_scope = str(scope).strip()
        if not normalized_scope:
            raise UserDataValidationError("scope 不能为空")
        normalized_keys = [str(item_key).strip() for item_key in item_keys]
        if any(not item_key for item_key in normalized_keys):
            raise UserDataValidationError("item_key 不能为空")
        if len(set(normalized_keys)) != len(normalized_keys):
            raise UserDataValidationError("界面项目顺序不能包含重复项")
        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM ui_item_order WHERE scope = ?",
                (normalized_scope,),
            )
            connection.executemany(
                """
                INSERT INTO ui_item_order(
                    scope, item_key, ordinal, updated_at_utc
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (normalized_scope, item_key, ordinal, now)
                    for ordinal, item_key in enumerate(normalized_keys)
                ],
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        return normalized_keys

    def get_sync_settings(self) -> dict[str, Any]:
        settings = self._one("SELECT * FROM sync_settings WHERE singleton_id = 1")
        if settings is None:
            raise UserDataError("用户数据库缺少同步设置")
        settings.pop("singleton_id", None)
        settings["raw_capture_enabled"] = bool(settings["raw_capture_enabled"])
        settings["auto_start_inventory_sync"] = bool(settings["auto_start_inventory_sync"])
        return settings

    def update_sync_settings(
        self,
        *,
        inventory_sync_method: str | None = None,
        equipment_apply_method: str | None = None,
        capture_device_id: str | None = None,
        raw_capture_enabled: bool | None = None,
        inventory_settle_seconds: float | None = None,
        auto_start_inventory_sync: bool | None = None,
        inventory_snapshot_retention_count: int | None = None,
    ) -> dict[str, Any]:
        current = self.get_sync_settings()
        inventory_method = inventory_sync_method or current["inventory_sync_method"]
        apply_method = equipment_apply_method or current["equipment_apply_method"]
        if inventory_method not in SYNC_METHODS:
            raise UserDataValidationError("inventory_sync_method 必须是 nte_core 或 gamepad")
        if apply_method not in SYNC_METHODS:
            raise UserDataValidationError("equipment_apply_method 必须是 nte_core 或 gamepad")
        device_id = current["capture_device_id"] if capture_device_id is None else str(capture_device_id).strip() or None
        raw_enabled = current["raw_capture_enabled"] if raw_capture_enabled is None else bool(raw_capture_enabled)
        settle = current["inventory_settle_seconds"] if inventory_settle_seconds is None else float(inventory_settle_seconds)
        if settle <= 0:
            raise UserDataValidationError("inventory_settle_seconds 必须大于 0")
        auto_start = (
            current["auto_start_inventory_sync"]
            if auto_start_inventory_sync is None
            else bool(auto_start_inventory_sync)
        )
        retention_count = (
            current["inventory_snapshot_retention_count"]
            if inventory_snapshot_retention_count is None
            else _integer(
                inventory_snapshot_retention_count,
                "inventory_snapshot_retention_count",
                minimum=1,
            )
        )
        self._db().execute(
            """
            UPDATE sync_settings
            SET inventory_sync_method = ?, equipment_apply_method = ?,
                capture_device_id = ?, raw_capture_enabled = ?,
                inventory_settle_seconds = ?, auto_start_inventory_sync = ?,
                inventory_snapshot_retention_count = ?,
                updated_at_utc = ?
            WHERE singleton_id = 1
            """,
            (
                inventory_method,
                apply_method,
                device_id,
                int(raw_enabled),
                settle,
                int(auto_start),
                retention_count,
                _utc_now(),
            ),
        )
        self._db().commit()
        return self.get_sync_settings()
