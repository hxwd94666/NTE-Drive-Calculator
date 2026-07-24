# 提供按账号存储设置、背包快照和装配方案的 SQLite 数据层。
"""按账号存储设置、背包快照和装配方案的 SQLite 数据层。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    make_virtual_equipment_assignment,
)

SCHEMA_VERSION = 10
BASE_SCHEMA_VERSION = 1
DEFAULT_SCHEMA_PATH = Path(__file__).with_name("schema") / "001_user_data.sql"
USER_MIGRATIONS = {
    2: Path(__file__).with_name("schema") / "003_user_data_v2.sql",
    3: Path(__file__).with_name("schema") / "004_user_data_v3.sql",
    4: Path(__file__).with_name("schema") / "005_user_data_v4.sql",
    5: Path(__file__).with_name("schema") / "006_user_data_v5.sql",
    6: Path(__file__).with_name("schema") / "007_user_data_v6.sql",
    7: Path(__file__).with_name("schema") / "008_user_data_v7.sql",
    8: Path(__file__).with_name("schema") / "009_user_data_v8.sql",
    9: Path(__file__).with_name("schema") / "010_user_data_v9.sql",
    10: Path(__file__).with_name("schema") / "011_user_data_v10.sql",
}
SYNC_METHODS = frozenset({"nte_core", "gamepad"})
SNAPSHOT_SOURCES = frozenset({"nte_core", "gamepad", "import"})
DEFAULT_SNAPSHOT_RETENTION_COUNT = 20
ALLOCATION_STRATEGIES = frozenset({"role_priority", "drive_priority", "global_optimal"})
SUIT_REQUIREMENT_MODES = frozenset({"none", "two_piece", "four_piece"})


class UserDataError(RuntimeError):
    """用户数据库无效或版本不兼容。"""


class UserDataValidationError(UserDataError, ValueError):
    """传入的 nte-core 或应用数据格式不正确。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decoded(value: str | None, default: Any) -> Any:
    return json.loads(value) if value is not None else default


def _mark_duplicate_modules(items: Sequence[dict[str, Any]]) -> None:
    """标记游戏筛选器无法区分的重复驱动。

    自动装配只能按形状、品质和副词条名称筛选，不能按实际词条数值定位。
    因此同一完整快照中这些字段相同的驱动属于同一重复组。该标记是快照
    派生数据，不修改 nte-core 传入的任何官方字段。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("kind") != "module":
            continue
        signature = _json({
            "geometry": str(item.get("geometry") or ""),
            "quality": str(item.get("quality") or "").casefold(),
            "sub_property_ids": sorted(
                str(stat.get("property_id") or "")
                for stat in item.get("sub_stats") or []
                if isinstance(stat, Mapping) and stat.get("property_id")
            ),
        })
        groups.setdefault(signature, []).append(item)

    group_number = 1
    for signature in sorted(groups):
        group = groups[signature]
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (int(item["uid"]["slot"]), int(item["uid"]["serial"])))
        group_id = f"drive_dup_{group_number:03d}"
        group_number += 1
        for index, item in enumerate(group, start=1):
            item["is_duplicate_drive"] = True
            item["duplicate_group_id"] = group_id
            item["duplicate_index"] = index
            item["duplicate_count"] = len(group)
            item["duplicate_signature"] = signature


def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UserDataValidationError(f"{label} 必须是对象")
    return dict(value)


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserDataValidationError(f"{label} 必须是整数")
    if minimum is not None and value < minimum:
        raise UserDataValidationError(f"{label} 不能小于 {minimum}")
    return value


class UserDataDao:
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
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        try:
            if not existed:
                self._initialize(
                    Path(schema_path),
                    account_id=str(account_id),
                    account_name=str(account_name or account_id),
                )
            self._migrate_schema()
            self._validate_schema()
        except BaseException:
            self.close()
            if not existed:
                self.database_path.unlink(missing_ok=True)
            raise

    def __enter__(self) -> "UserDataDao":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
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

    def _migrate_schema(self) -> None:
        connection = self._db()
        try:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
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
                connection.execute("BEGIN IMMEDIATE")
                self._execute_migration_script(connection, migration_sql)
                connection.execute(
                    "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                    (target_version, _utc_now()),
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            connection.rollback()
            raise UserDataError("无法升级用户数据库结构") from exc

    @staticmethod
    def _execute_migration_script(connection: sqlite3.Connection, script: str) -> None:
        """在调用方已经开启的事务中逐条执行迁移 SQL，避免 executescript 隐式提交。"""

        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                if statement.strip():
                    connection.execute(statement)
                statement = ""
        if statement.strip():
            raise UserDataError("用户数据库迁移脚本包含未结束的 SQL 语句")

    def _validate_schema(self) -> None:
        try:
            row = self._db().execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
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
        self._db().execute(
            "UPDATE database_profile SET account_name = ?, updated_at_utc = ? WHERE singleton_id = 1",
            (name, _utc_now()),
        )
        self._db().commit()

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
        self, setting_key: str, value: Mapping[str, Any]
    ) -> None:
        key = str(setting_key).strip()
        if not key:
            raise UserDataValidationError("setting_key 不能为空")
        if not isinstance(value, Mapping):
            raise UserDataValidationError("账号设置副本必须是对象")
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
        self._db().commit()

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

    @staticmethod
    def _preference_text(value: Any, label: str, *, required: bool = False) -> str | None:
        if value is None and not required:
            return None
        text = str(value or "").strip()
        if not text:
            if required:
                raise UserDataValidationError(f"{label} 不能为空")
            return None
        return text

    @staticmethod
    def _preference_number(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise UserDataValidationError(f"{label} 必须是有限数值")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise UserDataValidationError(f"{label} 必须是有限数值") from exc
        if not math.isfinite(number):
            raise UserDataValidationError(f"{label} 必须是有限数值")
        return number

    @classmethod
    def _validated_optimization_characters(
        cls, characters: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not isinstance(characters, Sequence) or isinstance(characters, (str, bytes)):
            raise UserDataValidationError("characters 必须是角色偏好列表")
        normalized: list[dict[str, Any]] = []
        seen_character_ids: set[int] = set()
        seen_ordinals: set[int] = set()
        for index, value in enumerate(characters):
            row = _plain_object(value, f"characters[{index}]")
            character_id = _integer(row.get("character_id"), f"characters[{index}].character_id", minimum=1)
            ordinal = _integer(row.get("ordinal", index), f"characters[{index}].ordinal", minimum=0)
            priority_group = _integer(
                row.get("priority_group", 0), f"characters[{index}].priority_group", minimum=0
            )
            if character_id in seen_character_ids:
                raise UserDataValidationError("characters 不能包含重复 character_id")
            if ordinal in seen_ordinals:
                raise UserDataValidationError("characters 不能包含重复 ordinal")
            seen_character_ids.add(character_id)
            seen_ordinals.add(ordinal)
            suit_requirement_mode = str(row.get("suit_requirement_mode", "none")).strip()
            if suit_requirement_mode not in SUIT_REQUIREMENT_MODES:
                raise UserDataValidationError("suit_requirement_mode 必须是 none、two_piece 或 four_piece")
            target_suit_id = cls._preference_text(row.get("target_suit_id"), "target_suit_id")
            if suit_requirement_mode != "none" and target_suit_id is None:
                raise UserDataValidationError(
                    "two_piece 或 four_piece 套装约束必须提供 target_suit_id"
                )
            weights_source = row.get("property_weights", {})
            if not isinstance(weights_source, Mapping):
                raise UserDataValidationError(f"characters[{index}].property_weights 必须是对象")
            weights: dict[str, float] = {}
            for property_id, weight in weights_source.items():
                normalized_property_id = cls._preference_text(property_id, "property_id", required=True)
                weights[normalized_property_id] = cls._preference_number(weight, f"{normalized_property_id} weight")

            priorities_source = row.get("substat_priorities", [])
            if not isinstance(priorities_source, Sequence) or isinstance(priorities_source, (str, bytes)):
                raise UserDataValidationError(f"characters[{index}].substat_priorities 必须是列表")
            priorities: list[str] = []
            for property_id in priorities_source:
                normalized_property_id = cls._preference_text(property_id, "substat property_id", required=True)
                if normalized_property_id in priorities:
                    raise UserDataValidationError("substat_priorities 不能包含重复 property_id")
                priorities.append(normalized_property_id)

            limits_source = row.get("property_limits", {})
            if not isinstance(limits_source, Mapping):
                raise UserDataValidationError(f"characters[{index}].property_limits 必须是对象")
            limits: dict[str, dict[str, float | None]] = {}
            for property_id, bounds in limits_source.items():
                normalized_property_id = cls._preference_text(property_id, "property_id", required=True)
                bound_row = _plain_object(bounds, f"{normalized_property_id} limit")
                minimum = bound_row.get("minimum")
                maximum = bound_row.get("maximum")
                if minimum is None and maximum is None:
                    raise UserDataValidationError(f"{normalized_property_id} 至少需要 minimum 或 maximum")
                minimum_number = cls._preference_number(minimum, f"{normalized_property_id} minimum") if minimum is not None else None
                maximum_number = cls._preference_number(maximum, f"{normalized_property_id} maximum") if maximum is not None else None
                if minimum_number is not None and maximum_number is not None and minimum_number > maximum_number:
                    raise UserDataValidationError(f"{normalized_property_id} minimum 不能大于 maximum")
                limits[normalized_property_id] = {"minimum": minimum_number, "maximum": maximum_number}

            normalized.append({
                "character_id": character_id,
                "ordinal": ordinal,
                "priority_group": priority_group,
                "target_suit_id": target_suit_id,
                "suit_requirement_mode": suit_requirement_mode,
                "core_main_property_id": cls._preference_text(row.get("core_main_property_id"), "core_main_property_id"),
                "property_weights": weights,
                "substat_priorities": priorities,
                "property_limits": limits,
            })
        return sorted(normalized, key=lambda row: row["ordinal"])

    def _optimization_version(self, profile_version_id: int) -> dict[str, Any] | None:
        version = self._one(
            """
            SELECT profile_version_id, profile_id, version_number, allocation_strategy, created_at_utc
            FROM optimization_preference_version WHERE profile_version_id = ?
            """,
            (profile_version_id,),
        )
        if version is None:
            return None
        characters = self._rows(
            """
            SELECT character_id, ordinal, priority_group, target_suit_id,
                   suit_requirement_mode, core_main_property_id
            FROM optimization_preference_character
            WHERE profile_version_id = ? ORDER BY ordinal
            """,
            (profile_version_id,),
        )
        for character in characters:
            character_id = character["character_id"]
            character["property_weights"] = {
                row["property_id"]: row["weight"]
                for row in self._rows(
                    """SELECT property_id, weight FROM optimization_preference_property_weight
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY property_id""",
                    (profile_version_id, character_id),
                )
            }
            character["substat_priorities"] = [
                row["property_id"]
                for row in self._rows(
                    """SELECT property_id FROM optimization_preference_substat_priority
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY ordinal""",
                    (profile_version_id, character_id),
                )
            ]
            character["property_limits"] = {
                row["property_id"]: {"minimum": row["minimum_value"], "maximum": row["maximum_value"]}
                for row in self._rows(
                    """SELECT property_id, minimum_value, maximum_value FROM optimization_preference_property_limit
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY property_id""",
                    (profile_version_id, character_id),
                )
            }
        version["characters"] = characters
        return version

    @staticmethod
    def _optimization_strategy(value: Any) -> str:
        strategy = str(value).strip()
        if strategy not in ALLOCATION_STRATEGIES:
            raise UserDataValidationError("allocation_strategy 无效")
        return strategy

    @staticmethod
    def _insert_optimization_profile_version(
        connection: sqlite3.Connection,
        profile_id: int,
        allocation_strategy: str,
        characters: Sequence[Mapping[str, Any]],
    ) -> int:
        """在调用方事务内追加一个不可变偏好版本。"""

        next_version = int(connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS version_number FROM optimization_preference_version WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()["version_number"])
        cursor = connection.execute(
            """INSERT INTO optimization_preference_version(profile_id, version_number, allocation_strategy, created_at_utc)
               VALUES (?, ?, ?, ?)""",
            (profile_id, next_version, allocation_strategy, _utc_now()),
        )
        profile_version_id = int(cursor.lastrowid)
        for character in characters:
            connection.execute(
                """INSERT INTO optimization_preference_character(
                       profile_version_id, character_id, ordinal, priority_group,
                       target_suit_id, suit_requirement_mode, core_main_property_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_version_id, character["character_id"], character["ordinal"],
                    character["priority_group"], character["target_suit_id"],
                    character["suit_requirement_mode"], character["core_main_property_id"],
                ),
            )
            connection.executemany(
                """INSERT INTO optimization_preference_property_weight(
                       profile_version_id, character_id, property_id, weight
                   ) VALUES (?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, weight)
                 for property_id, weight in character["property_weights"].items()],
            )
            connection.executemany(
                """INSERT INTO optimization_preference_substat_priority(
                       profile_version_id, character_id, property_id, ordinal
                   ) VALUES (?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, ordinal)
                 for ordinal, property_id in enumerate(character["substat_priorities"])],
            )
            connection.executemany(
                """INSERT INTO optimization_preference_property_limit(
                       profile_version_id, character_id, property_id, minimum_value, maximum_value
                   ) VALUES (?, ?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, limit["minimum"], limit["maximum"])
                 for property_id, limit in character["property_limits"].items()],
            )
        return profile_version_id

    def create_optimization_profile(
        self,
        name: str,
        *,
        allocation_strategy: str,
        characters: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """创建优化偏好档案及其不可变的第一个版本。"""

        profile_name = self._preference_text(name, "name", required=True)
        strategy = self._optimization_strategy(allocation_strategy)
        normalized_characters = self._validated_optimization_characters(characters)
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            cursor = connection.execute(
                """INSERT INTO optimization_preference_profile(name, is_active, created_at_utc, updated_at_utc)
                   VALUES (?, 1, ?, ?)""",
                (profile_name, now, now),
            )
            profile_id = int(cursor.lastrowid)
            self._insert_optimization_profile_version(
                connection, profile_id, strategy, normalized_characters
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise UserDataValidationError(f"优化偏好档案已存在：{profile_name}") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法创建优化偏好档案") from exc
        except BaseException:
            connection.rollback()
            raise
        profile = self.get_optimization_profile(profile_id)
        assert profile is not None
        return profile

    def create_optimization_profile_version(
        self,
        profile_id: int,
        *,
        allocation_strategy: str,
        characters: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """以新版本保存偏好；既有版本永不被编辑覆盖。"""

        raw_profile_id = _integer(profile_id, "profile_id", minimum=1)
        strategy = self._optimization_strategy(allocation_strategy)
        normalized_characters = self._validated_optimization_characters(characters)
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            profile = connection.execute(
                "SELECT profile_id FROM optimization_preference_profile WHERE profile_id = ? AND is_active = 1",
                (raw_profile_id,),
            ).fetchone()
            if profile is None:
                raise UserDataValidationError("优化偏好档案不存在或已停用")
            profile_version_id = self._insert_optimization_profile_version(
                connection, raw_profile_id, strategy, normalized_characters
            )
            connection.execute(
                "UPDATE optimization_preference_profile SET updated_at_utc = ? WHERE profile_id = ?",
                (_utc_now(), raw_profile_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存优化偏好版本") from exc
        except BaseException:
            connection.rollback()
            raise
        version = self._optimization_version(profile_version_id)
        assert version is not None
        return version

    def get_optimization_profile(
        self, profile_id: int, *, version_number: int | None = None
    ) -> dict[str, Any] | None:
        """读取档案和指定或最新的不可变偏好版本。"""

        raw_profile_id = _integer(profile_id, "profile_id", minimum=1)
        profile = self._one(
            """SELECT profile_id, name, is_active, created_at_utc, updated_at_utc
               FROM optimization_preference_profile WHERE profile_id = ?""",
            (raw_profile_id,),
        )
        if profile is None:
            return None
        profile["is_active"] = bool(profile["is_active"])
        if version_number is None:
            version_row = self._one(
                """SELECT profile_version_id FROM optimization_preference_version
                   WHERE profile_id = ? ORDER BY version_number DESC LIMIT 1""",
                (raw_profile_id,),
            )
        else:
            raw_version_number = _integer(version_number, "version_number", minimum=1)
            version_row = self._one(
                """SELECT profile_version_id FROM optimization_preference_version
                   WHERE profile_id = ? AND version_number = ?""",
                (raw_profile_id, raw_version_number),
            )
        profile["version"] = self._optimization_version(version_row["profile_version_id"]) if version_row else None
        return profile

    def list_optimization_profiles(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """按最近更新顺序列出账号自己的优化偏好档案。"""

        rows = self._rows(
            """SELECT profile_id FROM optimization_preference_profile
               WHERE is_active = 1 OR ? ORDER BY updated_at_utc DESC, profile_id DESC""",
            (int(include_inactive),),
        )
        return [profile for row in rows if (profile := self.get_optimization_profile(row["profile_id"])) is not None]

    def deactivate_optimization_profile(self, profile_id: int) -> bool:
        """停用档案但保留所有版本，避免历史计算失去可追溯的偏好引用。"""

        raw_profile_id = _integer(profile_id, "profile_id", minimum=1)
        cursor = self._db().execute(
            """UPDATE optimization_preference_profile SET is_active = 0, updated_at_utc = ?
               WHERE profile_id = ? AND is_active = 1""",
            (_utc_now(), raw_profile_id),
        )
        self._db().commit()
        return cursor.rowcount > 0

    @classmethod
    def _validated_character_weight_rows(
        cls, properties: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(properties, Sequence) or isinstance(properties, (str, bytes)):
            raise UserDataValidationError("角色词条权重必须是列表")
        normalized = []
        seen_ids: set[str] = set()
        for ordinal, source in enumerate(properties):
            row = _plain_object(source, f"properties[{ordinal}]")
            property_id = cls._preference_text(
                row.get("property_id"), "property_id", required=True
            )
            if property_id in seen_ids:
                raise UserDataValidationError("角色词条权重不能包含重复 property_id")
            seen_ids.add(property_id)
            weight = cls._preference_number(row.get("weight", 0), f"{property_id} weight")
            main_weight = cls._preference_number(
                row.get("main_weight", 0), f"{property_id} main_weight"
            )
            if weight < 0 or main_weight < 0:
                raise UserDataValidationError("角色词条权重不能小于 0")
            normalized.append({
                "property_id": property_id,
                "weight": weight,
                "main_weight": main_weight,
                "ordinal": ordinal,
            })
        return normalized

    def get_character_weight_preferences(self, character_id: int) -> dict[str, Any] | None:
        """读取账号从静态推荐复制后可独立编辑的角色权重。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        seed = self._one(
            """SELECT character_id, source_dataset_id, source_kind,
                      seeded_at_utc, updated_at_utc
               FROM character_weight_preference_seed WHERE character_id = ?""",
            (raw_character_id,),
        )
        if seed is None:
            return None
        properties = self._rows(
            """SELECT property_id, weight, main_weight, ordinal
               FROM character_weight_preference_property
               WHERE character_id = ? ORDER BY ordinal""",
            (raw_character_id,),
        )
        seed["properties"] = properties
        seed["property_weights"] = {
            row["property_id"]: float(row["weight"])
            for row in properties if float(row["weight"]) > 0
        }
        seed["main_property_weights"] = {
            row["property_id"]: float(row["main_weight"])
            for row in properties if float(row["main_weight"]) > 0
        }
        return seed

    def seed_character_weight_preferences(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
        source_dataset_id: str,
        source_kind: str,
    ) -> dict[str, Any]:
        """首次复制静态推荐；已存在的账号编辑永不被新版静态库覆盖。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        dataset_id = self._preference_text(
            source_dataset_id, "source_dataset_id", required=True
        )
        normalized_source_kind = self._preference_text(
            source_kind, "source_kind", required=True
        )
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM character_weight_preference_seed WHERE character_id = ?",
                (raw_character_id,),
            ).fetchone()
            if existing is None:
                now = _utc_now()
                connection.execute(
                    """INSERT INTO character_weight_preference_seed(
                           character_id, source_dataset_id, source_kind,
                           seeded_at_utc, updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (raw_character_id, dataset_id, normalized_source_kind, now, now),
                )
                connection.executemany(
                    """INSERT INTO character_weight_preference_property(
                           character_id, property_id, weight, main_weight, ordinal
                       ) VALUES (?, ?, ?, ?, ?)""",
                    [
                        (
                            raw_character_id, row["property_id"], row["weight"],
                            row["main_weight"], row["ordinal"],
                        )
                        for row in rows
                    ],
                )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法初始化角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_weight_preferences(raw_character_id)
        assert result is not None
        return result

    def save_character_weight_preferences(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """保存账号角色权重，不修改静态推荐或既有计算版本。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM character_weight_preference_seed WHERE character_id = ?",
                (raw_character_id,),
            ).fetchone() is None:
                raise UserDataValidationError("角色词条权重尚未从静态库初始化")
            connection.execute(
                "DELETE FROM character_weight_preference_property WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """INSERT INTO character_weight_preference_property(
                       character_id, property_id, weight, main_weight, ordinal
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        raw_character_id, row["property_id"], row["weight"],
                        row["main_weight"], row["ordinal"],
                    )
                    for row in rows
                ],
            )
            connection.execute(
                "UPDATE character_weight_preference_seed SET updated_at_utc = ? WHERE character_id = ?",
                (_utc_now(), raw_character_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_weight_preferences(raw_character_id)
        assert result is not None
        return result

    def refresh_unmodified_character_weight_preferences(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
        source_dataset_id: str,
        source_kind: str,
    ) -> dict[str, Any] | None:
        """Refresh a never-edited default copy without overwriting account edits."""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        dataset_id = self._preference_text(
            source_dataset_id, "source_dataset_id", required=True
        )
        normalized_source_kind = self._preference_text(
            source_kind, "source_kind", required=True
        )
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            seed = connection.execute(
                """SELECT source_kind, seeded_at_utc, updated_at_utc
                   FROM character_weight_preference_seed
                   WHERE character_id = ?""",
                (raw_character_id,),
            ).fetchone()
            if (
                seed is None
                or str(seed["source_kind"]) != "default"
                or str(seed["seeded_at_utc"]) != str(seed["updated_at_utc"])
            ):
                connection.rollback()
                return None
            connection.execute(
                "DELETE FROM character_weight_preference_property WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """INSERT INTO character_weight_preference_property(
                       character_id, property_id, weight, main_weight, ordinal
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        raw_character_id, row["property_id"], row["weight"],
                        row["main_weight"], row["ordinal"],
                    )
                    for row in rows
                ],
            )
            connection.execute(
                """UPDATE character_weight_preference_seed
                   SET source_dataset_id = ?, source_kind = ?, updated_at_utc = ?
                   WHERE character_id = ?""",
                (dataset_id, normalized_source_kind, _utc_now(), raw_character_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法刷新未修改的角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        return self.get_character_weight_preferences(raw_character_id)

    def get_character_shape_bonus_preferences(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        """读取账号对官方额外形状标签和加成的覆写。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        record = self._one(
            """SELECT character_id, shape_label, updated_at_utc
               FROM character_shape_bonus_preference WHERE character_id = ?""",
            (raw_character_id,),
        )
        if record is None:
            return None
        properties = self._rows(
            """SELECT property_id, display_value, ordinal
               FROM character_shape_bonus_preference_property
               WHERE character_id = ? ORDER BY ordinal""",
            (raw_character_id,),
        )
        record["properties"] = properties
        record["property_values"] = {
            str(row["property_id"]): float(row["display_value"])
            for row in properties
        }
        return record

    def save_character_shape_bonus_preferences(
        self,
        character_id: int,
        *,
        shape_label: str,
        property_values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """保存账号角色的额外形状覆写，不改动发行版静态数据。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        label = str(shape_label or "").strip()
        if len(label) > 100:
            raise UserDataValidationError("额外形状标签不能超过 100 个字符")
        if not isinstance(property_values, Mapping):
            raise UserDataValidationError("额外形状加成必须是对象")
        rows = []
        for ordinal, (raw_property_id, raw_value) in enumerate(property_values.items()):
            property_id = str(raw_property_id or "").strip()
            if not property_id:
                raise UserDataValidationError("额外形状加成 property_id 不能为空")
            value = self._preference_number(raw_value, "额外形状加成数值")
            if value < 0:
                raise UserDataValidationError("额外形状加成数值不能小于 0")
            rows.append((raw_character_id, property_id, value, ordinal))
        if len({row[1] for row in rows}) != len(rows):
            raise UserDataValidationError("额外形状加成不能包含重复属性")
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO character_shape_bonus_preference(
                    character_id, shape_label, updated_at_utc
                ) VALUES (?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    shape_label = excluded.shape_label,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (raw_character_id, label, _utc_now()),
            )
            connection.execute(
                "DELETE FROM character_shape_bonus_preference_property WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """
                INSERT INTO character_shape_bonus_preference_property(
                    character_id, property_id, display_value, ordinal
                ) VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存角色额外形状加成") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_shape_bonus_preferences(raw_character_id)
        assert result is not None
        return result

    def get_character_profile(self, character_id: int) -> dict[str, Any] | None:
        """读取一个只含官方 ID 指针和账号养成状态的角色档案。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        profile = self._one(
            """
            SELECT character_id, character_level, breakthrough_stage,
                   awakening_level, fork_id, fork_level,
                   fork_refinement_level, selected_skill_id, ordinal,
                   is_active, created_at_utc, updated_at_utc
            FROM character_profile WHERE character_id = ?
            """,
            (raw_character_id,),
        )
        if profile is None:
            return None
        profile["is_active"] = bool(profile["is_active"])
        profile["skill_levels"] = {
            row["skill_id"]: int(row["skill_level"])
            for row in self._rows(
                """SELECT skill_id, skill_level FROM character_profile_skill
                   WHERE character_id = ? ORDER BY skill_id""",
                (raw_character_id,),
            )
        }
        return profile

    def list_character_profiles(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """按用户角色页顺序列出账号角色指针。"""

        rows = self._rows(
            """SELECT character_id FROM character_profile
               WHERE is_active = 1 OR ? ORDER BY ordinal, character_id""",
            (int(include_inactive),),
        )
        return [
            profile
            for row in rows
            if (profile := self.get_character_profile(row["character_id"])) is not None
        ]

    def save_character_profile(
        self,
        *,
        character_id: int,
        character_level: int,
        breakthrough_stage: int,
        awakening_level: int,
        fork_id: str | None,
        fork_level: int | None,
        fork_refinement_level: int | None,
        selected_skill_id: str | None = None,
        skill_levels: Mapping[str, int] | None = None,
        ordinal: int = 0,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """原子保存角色指针；角色、弧盘和技能详情仍由官方静态库解析。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        raw_level = _integer(character_level, "character_level", minimum=1)
        if raw_level > 80:
            raise UserDataValidationError("character_level 不能大于 80")
        raw_breakthrough = _integer(breakthrough_stage, "breakthrough_stage", minimum=0)
        if raw_breakthrough > 6:
            raise UserDataValidationError("breakthrough_stage 不能大于 6")
        raw_awakening = _integer(awakening_level, "awakening_level", minimum=0)
        if raw_awakening > 6:
            raise UserDataValidationError("awakening_level 不能大于 6")
        raw_ordinal = _integer(ordinal, "ordinal", minimum=0)
        raw_fork_id = self._preference_text(fork_id, "fork_id")
        if raw_fork_id is None:
            raw_fork_level = None
            raw_refinement = None
        else:
            raw_fork_level = _integer(fork_level, "fork_level", minimum=1)
            if raw_fork_level > 80:
                raise UserDataValidationError("fork_level 不能大于 80")
            raw_refinement = _integer(
                fork_refinement_level, "fork_refinement_level", minimum=1
            )
            if raw_refinement > 5:
                raise UserDataValidationError("fork_refinement_level 不能大于 5")
        raw_selected_skill = self._preference_text(selected_skill_id, "selected_skill_id")
        normalized_skills: dict[str, int] = {}
        for skill_id, skill_level in dict(skill_levels or {}).items():
            raw_skill_id = self._preference_text(skill_id, "skill_id", required=True)
            normalized_skills[raw_skill_id] = _integer(
                skill_level, f"{raw_skill_id} skill_level", minimum=1
            )
        if raw_selected_skill and raw_selected_skill not in normalized_skills:
            raise UserDataValidationError("selected_skill_id 必须存在于 skill_levels")

        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO character_profile(
                    character_id, character_level, breakthrough_stage,
                    awakening_level, fork_id, fork_level,
                    fork_refinement_level, selected_skill_id, ordinal,
                    is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    character_level = excluded.character_level,
                    breakthrough_stage = excluded.breakthrough_stage,
                    awakening_level = excluded.awakening_level,
                    fork_id = excluded.fork_id,
                    fork_level = excluded.fork_level,
                    fork_refinement_level = excluded.fork_refinement_level,
                    selected_skill_id = excluded.selected_skill_id,
                    ordinal = excluded.ordinal,
                    is_active = excluded.is_active,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    raw_character_id, raw_level, raw_breakthrough, raw_awakening,
                    raw_fork_id, raw_fork_level, raw_refinement,
                    raw_selected_skill, raw_ordinal, int(bool(is_active)), now, now,
                ),
            )
            connection.execute(
                "DELETE FROM character_profile_skill WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """INSERT INTO character_profile_skill(character_id, skill_id, skill_level)
                   VALUES (?, ?, ?)""",
                [
                    (raw_character_id, skill_id, skill_level)
                    for skill_id, skill_level in normalized_skills.items()
                ],
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise UserDataValidationError("角色页顺序或养成指针无效") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存角色养成指针") from exc
        profile = self.get_character_profile(raw_character_id)
        assert profile is not None
        return profile

    @staticmethod
    def _snapshot_payload(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        original = dict(snapshot)
        if original.get("method") == "event.inventory.snapshot":
            payload = _plain_object(original.get("params"), "snapshot params")
        else:
            payload = original
        return original, payload

    @staticmethod
    def _validated_item(item: Any, index: int) -> tuple[dict[str, Any], int, int]:
        row = _plain_object(item, f"items[{index}]")
        uid = _plain_object(row.get("uid"), f"items[{index}].uid")
        serial = _integer(uid.get("serial"), f"items[{index}].uid.serial", minimum=0)
        slot = _integer(uid.get("slot"), f"items[{index}].uid.slot", minimum=0)
        kind = row.get("kind")
        if kind not in ("module", "core"):
            raise UserDataValidationError(f"items[{index}].kind 必须是 module 或 core")
        if not isinstance(row.get("item_id"), str) or not row["item_id"]:
            raise UserDataValidationError(f"items[{index}].item_id 不能为空")
        for field in ("level", "max_level"):
            _integer(row.get(field), f"items[{index}].{field}", minimum=0)
        if row["max_level"] < row["level"]:
            raise UserDataValidationError(f"items[{index}].max_level 不能低于 level")
        for field in ("locked", "equipped"):
            if not isinstance(row.get(field), bool):
                raise UserDataValidationError(f"items[{index}].{field} 必须是布尔值")
        character_id = row.get("equipped_character_id")
        if character_id is not None:
            _integer(character_id, f"items[{index}].equipped_character_id", minimum=1)
        return row, serial, slot

    @staticmethod
    def _validated_stats(item: Mapping[str, Any], item_index: int) -> list[tuple[str, int, dict[str, Any]]]:
        validated: list[tuple[str, int, dict[str, Any]]] = []
        for source_field, stat_group in (("main_stats", "main"), ("sub_stats", "sub")):
            stats = item.get(source_field, [])
            if not isinstance(stats, list):
                raise UserDataValidationError(f"items[{item_index}].{source_field} 必须是数组")
            for ordinal, raw_stat in enumerate(stats):
                stat = _plain_object(raw_stat, f"items[{item_index}].{source_field}[{ordinal}]")
                if not isinstance(stat.get("property_id"), str) or not stat["property_id"]:
                    raise UserDataValidationError("背包词条 property_id 不能为空")
                value = stat.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise UserDataValidationError("背包词条 value 必须是数字")
                if not isinstance(stat.get("percent"), bool):
                    raise UserDataValidationError("背包词条 percent 必须是布尔值")
                validated.append((stat_group, ordinal, stat))
        return validated

    def import_inventory_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        source: str = "nte_core",
        protocol_version: int | None = None,
    ) -> int:
        if source not in SNAPSHOT_SOURCES:
            raise UserDataValidationError(f"不支持的背包数据来源：{source}")
        original, payload = self._snapshot_payload(snapshot)
        if payload.get("complete") is not True:
            raise UserDataValidationError("只有完整背包快照才能设为当前数据")
        items = payload.get("items")
        if not isinstance(items, list):
            raise UserDataValidationError("快照 items 必须是数组")
        declared_count = _integer(payload.get("item_count"), "snapshot item_count", minimum=0)
        if declared_count != len(items):
            raise UserDataValidationError(
                f"快照 item_count 为 {declared_count}，但 items 实际有 {len(items)} 条"
            )
        generation = payload.get("generation")
        if generation is not None:
            generation = _integer(generation, "snapshot generation", minimum=0)
        sequence = payload.get("sequence")
        if sequence is not None:
            sequence = _integer(sequence, "snapshot sequence", minimum=0)
        observed_ms = payload.get("observed_at_unix_ms")
        if observed_ms is not None:
            observed_ms = _integer(observed_ms, "snapshot observed_at_unix_ms", minimum=0)
            captured_at = datetime.fromtimestamp(observed_ms / 1000, timezone.utc).isoformat(timespec="milliseconds")
        else:
            captured_at = _utc_now()

        normalized_items: list[tuple[dict[str, Any], int, int, list[tuple[str, int, dict[str, Any]]]]] = []
        seen_uids: set[tuple[int, int]] = set()
        for index, raw_item in enumerate(items):
            item, serial, slot = self._validated_item(raw_item, index)
            uid = (serial, slot)
            if uid in seen_uids:
                raise UserDataValidationError(f"背包 UID 重复：serial={serial}, slot={slot}")
            seen_uids.add(uid)
            normalized_items.append((item, serial, slot, self._validated_stats(item, index)))
        _mark_duplicate_modules([item for item, _serial, _slot, _stats in normalized_items])

        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO inventory_snapshot(
                    source, generation, sequence, observed_at_unix_ms,
                    captured_at_utc, complete, declared_item_count,
                    stored_item_count, protocol_version, raw_snapshot_json,
                    is_current, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0, ?)
                """,
                (
                    source, generation, sequence, observed_ms, captured_at,
                    declared_count, len(normalized_items), protocol_version,
                    _json(original), now,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            for item, serial, slot, stats in normalized_items:
                connection.execute(
                    """
                    INSERT INTO inventory_item(
                        snapshot_id, uid_serial, uid_slot, kind, item_id, suit_id,
                        geometry, grid_count, quality, level, max_level, locked,
                        equipped, equipped_character_uid_json, equipped_character_id,
                        names_json, suit_names_json, raw_item_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id, serial, slot, item["kind"], item["item_id"],
                        item.get("suit_id"), item.get("geometry"), item.get("grid"),
                        item.get("quality"), item["level"], item["max_level"],
                        int(item["locked"]), int(item["equipped"]),
                        _json(item.get("equipped_character_uid"))
                        if item.get("equipped_character_uid") is not None else None,
                        item.get("equipped_character_id"), _json(item.get("names") or {}),
                        _json(item.get("suit_names") or {}), _json(item),
                    ),
                )
                for stat_group, ordinal, stat in stats:
                    connection.execute(
                        """
                        INSERT INTO inventory_item_stat(
                            snapshot_id, uid_serial, uid_slot, stat_group, ordinal,
                            property_id, value, is_percent, names_json, raw_stat_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id, serial, slot, stat_group, ordinal,
                            stat["property_id"], float(stat["value"]),
                            int(stat["percent"]), _json(stat.get("names") or {}),
                            _json(stat),
                        ),
                    )
                character_id = item.get("equipped_character_id")
                character_uid = item.get("equipped_character_uid")
                if character_id is not None and isinstance(character_uid, Mapping):
                    try:
                        character_slot = _integer(character_uid.get("slot"), "equipped_character_uid.slot", minimum=1)
                        character_serial = _integer(character_uid.get("serial"), "equipped_character_uid.serial", minimum=1)
                    except UserDataValidationError:
                        # 背包条目仍完整保存；不把不合法的角色实例写入可执行映射。
                        continue
                    connection.execute(
                        """
                        INSERT INTO character_instance_mapping(
                            character_id, uid_slot, uid_serial, source,
                            first_seen_snapshot_id, last_seen_snapshot_id,
                            created_at_utc, updated_at_utc
                        ) VALUES (?, ?, ?, 'snapshot', ?, ?, ?, ?)
                        ON CONFLICT(character_id, uid_slot, uid_serial) DO UPDATE SET
                            last_seen_snapshot_id = excluded.last_seen_snapshot_id,
                            updated_at_utc = excluded.updated_at_utc
                        """,
                        (
                            character_id, character_slot, character_serial,
                            snapshot_id, snapshot_id, now, now,
                        ),
                    )
            connection.execute("UPDATE inventory_snapshot SET is_current = 0 WHERE is_current = 1")
            connection.execute(
                "UPDATE inventory_snapshot SET is_current = 1 WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            connection.commit()
            return snapshot_id
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法导入背包快照") from exc

    def list_inventory_snapshots(self) -> list[dict[str, Any]]:
        rows = self._rows(
            """
            SELECT snapshot_id, source, generation, sequence, observed_at_unix_ms,
                   captured_at_utc, complete, declared_item_count, stored_item_count,
                   protocol_version, is_current, created_at_utc
            FROM inventory_snapshot
            ORDER BY captured_at_utc DESC, snapshot_id DESC
            """
        )
        for row in rows:
            row["complete"] = bool(row["complete"])
            row["is_current"] = bool(row["is_current"])
        return rows

    def prune_inventory_snapshots(
        self,
        *,
        retain_recent: int | None = None,
    ) -> dict[str, Any]:
        """安全删除未受保护的历史稳定快照。

        始终保留当前快照、已保存装配方案引用的快照，以及按时间最近的若干份。
        删除依靠外键级联清理对应的背包物品和词条；不会修改任何装配方案。
        """

        if retain_recent is None:
            retain_recent = self.get_sync_settings()[
                "inventory_snapshot_retention_count"
            ]
        raw_retain_recent = _integer(
            retain_recent, "retain_recent", minimum=1
        )
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            all_rows = connection.execute(
                "SELECT snapshot_id FROM inventory_snapshot"
            ).fetchall()
            total_before = len(all_rows)
            current_rows = connection.execute(
                "SELECT snapshot_id FROM inventory_snapshot WHERE is_current = 1"
            ).fetchall()
            current_snapshot_ids = {
                int(row["snapshot_id"]) for row in current_rows
            }
            referenced_rows = connection.execute(
                """
                SELECT DISTINCT source_snapshot_id AS snapshot_id
                FROM loadout_plan
                WHERE source_snapshot_id IS NOT NULL
                """
            ).fetchall()
            referenced_snapshot_ids = {
                int(row["snapshot_id"]) for row in referenced_rows
            }
            job_rows = connection.execute(
                "SELECT DISTINCT source_snapshot_id AS snapshot_id FROM equipment_apply_job"
            ).fetchall()
            job_snapshot_ids = {int(row["snapshot_id"]) for row in job_rows}
            recent_rows = connection.execute(
                """
                SELECT snapshot_id
                FROM inventory_snapshot
                ORDER BY captured_at_utc DESC, snapshot_id DESC
                LIMIT ?
                """,
                (raw_retain_recent,),
            ).fetchall()
            recent_snapshot_ids = {
                int(row["snapshot_id"]) for row in recent_rows
            }
            protected_ids = (
                current_snapshot_ids
                | referenced_snapshot_ids
                | job_snapshot_ids
                | recent_snapshot_ids
            )
            deleted_snapshot_ids = sorted(
                int(row["snapshot_id"])
                for row in all_rows
                if int(row["snapshot_id"]) not in protected_ids
            )
            if deleted_snapshot_ids:
                placeholders = ", ".join("?" for _ in deleted_snapshot_ids)
                connection.execute(
                    f"DELETE FROM inventory_snapshot WHERE snapshot_id IN ({placeholders})",
                    deleted_snapshot_ids,
                )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法清理历史背包快照") from exc

        return {
            "retain_recent": raw_retain_recent,
            "total_before": total_before,
            "total_after": total_before - len(deleted_snapshot_ids),
            "deleted_snapshot_ids": deleted_snapshot_ids,
            "deleted_snapshot_count": len(deleted_snapshot_ids),
            "current_snapshot_ids": sorted(current_snapshot_ids),
            "referenced_snapshot_ids": sorted(referenced_snapshot_ids),
            "job_snapshot_ids": sorted(job_snapshot_ids),
            "recent_snapshot_ids": sorted(recent_snapshot_ids),
        }

    @staticmethod
    def _character_uid(value: Mapping[str, Any], label: str = "character_uid") -> dict[str, int]:
        return {
            "slot": _integer(value.get("slot"), f"{label}.slot", minimum=1),
            "serial": _integer(value.get("serial"), f"{label}.serial", minimum=1),
        }

    def upsert_character_instance_mapping(
        self, character_id: int, character_uid: Mapping[str, Any], *, source: str = "manual"
    ) -> dict[str, Any]:
        if source not in {"snapshot", "manual"}:
            raise UserDataValidationError("角色实例映射 source 必须是 snapshot 或 manual")
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        uid = self._character_uid(character_uid)
        now = _utc_now()
        try:
            self._db().execute(
                """
                INSERT INTO character_instance_mapping(
                    character_id, uid_slot, uid_serial, source,
                    first_seen_snapshot_id, last_seen_snapshot_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                ON CONFLICT(character_id, uid_slot, uid_serial) DO UPDATE SET
                    source = excluded.source, updated_at_utc = excluded.updated_at_utc
                """,
                (raw_character_id, uid["slot"], uid["serial"], source, now, now),
            )
            self._db().commit()
        except sqlite3.Error as exc:
            self._db().rollback()
            raise UserDataError("无法保存角色实例映射") from exc
        return self.list_character_instance_mappings(raw_character_id)[0]

    def list_character_instance_mappings(self, character_id: int | None = None) -> list[dict[str, Any]]:
        where = "" if character_id is None else "WHERE character_id = ?"
        parameters = () if character_id is None else (_integer(character_id, "character_id", minimum=1),)
        return self._rows(
            f"""SELECT character_id, uid_slot, uid_serial, source, first_seen_snapshot_id,
                       last_seen_snapshot_id, created_at_utc, updated_at_utc
                FROM character_instance_mapping {where}
                ORDER BY character_id, updated_at_utc DESC, uid_slot, uid_serial""",
            parameters,
        )

    def list_observed_character_ids(self) -> list[int]:
        """Return actual character IDs ordered by their latest account evidence."""

        rows = self._rows(
            """
            SELECT equipped_character_id AS character_id,
                   MAX(snapshot_id) AS last_seen_snapshot_id
            FROM inventory_item
            WHERE equipped = 1 AND equipped_character_id IS NOT NULL
            GROUP BY equipped_character_id
            ORDER BY last_seen_snapshot_id DESC, character_id
            """
        )
        result = [int(row["character_id"]) for row in rows]
        for row in self._rows(
            """
            SELECT character_id, MAX(updated_at_utc) AS last_seen_at
            FROM character_instance_mapping
            GROUP BY character_id
            ORDER BY last_seen_at DESC, character_id
            """
        ):
            character_id = int(row["character_id"])
            if character_id not in result:
                result.append(character_id)
        return result

    def create_equipment_apply_job(
        self, source_snapshot_id: int, prepared_roles: Sequence[Mapping[str, Any]]
    ) -> int:
        raw_snapshot_id = _integer(source_snapshot_id, "source_snapshot_id", minimum=1)
        if self.inventory_snapshot_summary(raw_snapshot_id) is None:
            raise UserDataValidationError("装配任务引用的稳定背包快照不存在")
        if not prepared_roles:
            raise UserDataValidationError("装配任务至少需要一个角色")
        now = _utc_now()
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO equipment_apply_job(source_snapshot_id, status, created_at_utc) VALUES (?, 'prepared', ?)",
                (raw_snapshot_id, now),
            )
            job_id = int(cursor.lastrowid)
            for ordinal, raw_role in enumerate(prepared_roles):
                role = _plain_object(raw_role, f"prepared_roles[{ordinal}]")
                uid = self._character_uid(_plain_object(role.get("character_uid"), "character_uid"))
                role_name = str(role.get("role_name") or "").strip()
                if not role_name:
                    raise UserDataValidationError("装配任务角色名称不能为空")
                plan_id = _integer(role.get("plan_id"), "plan_id", minimum=1)
                connection.execute(
                    """INSERT INTO equipment_apply_job_item(
                        job_id, ordinal, role_name, character_id, character_uid_json, plan_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                    (job_id, ordinal, role_name, _integer(role.get("character_id"), "character_id", minimum=1), _json(uid), plan_id),
                )
            connection.execute(
                "INSERT INTO equipment_apply_job_log(job_id, created_at_utc, level, message) VALUES (?, ?, 'info', ?)",
                (job_id, now, "任务已创建，等待执行"),
            )
            connection.commit()
            return job_id
        except (sqlite3.Error, UserDataValidationError) as exc:
            connection.rollback()
            if isinstance(exc, UserDataValidationError):
                raise
            raise UserDataError("无法创建一键装配任务") from exc

    def get_equipment_apply_job(self, job_id: int) -> dict[str, Any] | None:
        raw_job_id = _integer(job_id, "job_id", minimum=1)
        job = self._one("SELECT * FROM equipment_apply_job WHERE job_id = ?", (raw_job_id,))
        if job is None:
            return None
        items = self._rows("SELECT * FROM equipment_apply_job_item WHERE job_id = ? ORDER BY ordinal", (raw_job_id,))
        for item in items:
            item["character_uid"] = _decoded(item.pop("character_uid_json"), {})
        job["items"] = items
        job["logs"] = self._rows("SELECT * FROM equipment_apply_job_log WHERE job_id = ? ORDER BY log_id", (raw_job_id,))
        return job

    def latest_resumable_equipment_apply_job(self) -> dict[str, Any] | None:
        row = self._one("SELECT job_id FROM equipment_apply_job WHERE status IN ('prepared', 'running', 'failed') ORDER BY job_id DESC LIMIT 1")
        return self.get_equipment_apply_job(int(row["job_id"])) if row else None

    def reset_failed_equipment_apply_job_items(self, job_id: int) -> None:
        raw_job_id = _integer(job_id, "job_id", minimum=1)
        now = _utc_now()
        self._db().execute("UPDATE equipment_apply_job_item SET status = 'pending', last_error = NULL WHERE job_id = ? AND status = 'failed'", (raw_job_id,))
        self._db().execute("UPDATE equipment_apply_job SET status = 'prepared', last_error = NULL WHERE job_id = ?", (raw_job_id,))
        self._db().execute("INSERT INTO equipment_apply_job_log(job_id, created_at_utc, level, message) VALUES (?, ?, 'info', ?)", (raw_job_id, now, "失败角色已重置，等待重试"))
        self._db().commit()

    def mark_equipment_apply_job_item(self, job_item_id: int, *, status: str, error: str | None = None, before_snapshot_id: int | None = None, after_snapshot_id: int | None = None, verified: bool = True) -> None:
        if status not in {"running", "succeeded", "failed"}:
            raise UserDataValidationError("装配任务项状态无效")
        raw_item_id = _integer(job_item_id, "job_item_id", minimum=1)
        item = self._one("SELECT job_id, role_name FROM equipment_apply_job_item WHERE job_item_id = ?", (raw_item_id,))
        if item is None:
            raise UserDataValidationError("装配任务项不存在")
        now = _utc_now()
        connection = self._db()
        connection.execute("BEGIN IMMEDIATE")
        if status == "running":
            connection.execute("UPDATE equipment_apply_job_item SET status = 'running', attempt_count = attempt_count + 1, started_at_utc = ?, last_error = NULL WHERE job_item_id = ?", (now, raw_item_id))
            connection.execute("UPDATE equipment_apply_job SET status = 'running', started_at_utc = COALESCE(started_at_utc, ?), last_error = NULL WHERE job_id = ?", (now, item["job_id"]))
            message, level = f"开始处理角色 [{item['role_name']}]", "info"
        elif status == "succeeded":
            connection.execute("UPDATE equipment_apply_job_item SET status = 'succeeded', before_snapshot_id = ?, after_snapshot_id = ?, completed_at_utc = ?, last_error = NULL WHERE job_item_id = ?", (before_snapshot_id, after_snapshot_id, now, raw_item_id))
            message, level = (
                (f"角色 [{item['role_name']}] 装配已确认", "info")
                if verified
                else (f"角色 [{item['role_name']}] 装配指令已下发，待下次稳定背包同步更新", "info")
            )
        else:
            connection.execute("UPDATE equipment_apply_job_item SET status = 'failed', completed_at_utc = ?, last_error = ? WHERE job_item_id = ?", (now, str(error or "未知错误"), raw_item_id))
            connection.execute("UPDATE equipment_apply_job SET status = 'failed', last_error = ? WHERE job_id = ?", (str(error or "未知错误"), item["job_id"]))
            message, level = f"角色 [{item['role_name']}] 失败：{error or '未知错误'}", "error"
        connection.execute("INSERT INTO equipment_apply_job_log(job_id, job_item_id, created_at_utc, level, message) VALUES (?, ?, ?, ?, ?)", (item["job_id"], raw_item_id, now, level, message))
        connection.commit()

    def complete_equipment_apply_job_if_done(self, job_id: int) -> bool:
        raw_job_id = _integer(job_id, "job_id", minimum=1)
        remaining = self._one("SELECT COUNT(*) AS count FROM equipment_apply_job_item WHERE job_id = ? AND status != 'succeeded'", (raw_job_id,))
        if int(remaining["count"]) != 0:
            return False
        now = _utc_now()
        self._db().execute("UPDATE equipment_apply_job SET status = 'completed', completed_at_utc = ?, last_error = NULL WHERE job_id = ?", (now, raw_job_id))
        self._db().execute("INSERT INTO equipment_apply_job_log(job_id, created_at_utc, level, message) VALUES (?, ?, 'info', ?)", (raw_job_id, now, "全部角色装配任务已完成"))
        self._db().commit()
        return True

    def current_inventory_summary(self) -> dict[str, Any] | None:
        snapshot_id = self.current_inventory_snapshot_id()
        return self.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None

    def current_inventory_snapshot_id(self) -> int | None:
        """返回计算可用库存，优先抓包稳定快照，再回退全量视觉扫描。"""

        row = self._one(
            """
            SELECT snapshot_id
            FROM inventory_snapshot
            WHERE complete = 1 AND source IN ('nte_core', 'gamepad')
            ORDER BY CASE source WHEN 'nte_core' THEN 0 ELSE 1 END,
                     captured_at_utc DESC, snapshot_id DESC
            LIMIT 1
            """
        )
        return int(row["snapshot_id"]) if row is not None else None

    def inventory_snapshot_summary(self, snapshot_id: int) -> dict[str, Any] | None:
        """读取指定不可变快照的摘要，供计算任务固定输入版本。"""

        raw_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        row = self._one(
            """
            SELECT s.snapshot_id, s.source, s.complete, s.generation, s.sequence,
                   s.observed_at_unix_ms, s.captured_at_utc,
                   s.declared_item_count, s.stored_item_count,
                   SUM(CASE WHEN i.kind = 'module' THEN 1 ELSE 0 END) AS module_count,
                   SUM(CASE WHEN i.kind = 'core' THEN 1 ELSE 0 END) AS core_count,
                   SUM(CASE WHEN i.equipped = 1 THEN 1 ELSE 0 END) AS equipped_count,
                   SUM(CASE WHEN i.locked = 1 THEN 1 ELSE 0 END) AS locked_count
            FROM inventory_snapshot AS s
            LEFT JOIN inventory_item AS i USING (snapshot_id)
            WHERE s.snapshot_id = ?
            GROUP BY s.snapshot_id
            """,
            (raw_snapshot_id,),
        )
        if row is not None:
            for field in ("module_count", "core_count", "equipped_count", "locked_count"):
                row[field] = int(row[field] or 0)
        return row

    def export_inventory_snapshot(
        self, snapshot_id: int
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """在一个稳定只读事务中导出快照摘要和全部候选物品。

        计算上下文需要同时固定摘要中的物品数量和实际候选集。使用同一
        SQLite 读事务可避免后台同步完成后清理历史快照时读到半份数据。
        """

        raw_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        connection = self._db()
        try:
            connection.execute("BEGIN")
            summary = self.inventory_snapshot_summary(raw_snapshot_id)
            if summary is None:
                raise UserDataValidationError(f"背包快照不存在：{raw_snapshot_id}")
            items = self.list_inventory_items(raw_snapshot_id)
            if int(summary["stored_item_count"]) != len(items):
                raise UserDataError(
                    f"背包快照 {raw_snapshot_id} 的物品数量不一致："
                    f"摘要={summary['stored_item_count']}，条目={len(items)}"
                )
            connection.rollback()
            return summary, items
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError(f"无法原子读取背包快照：{raw_snapshot_id}") from exc
        except BaseException:
            connection.rollback()
            raise

    def list_current_inventory_items(
        self,
        *,
        kind: str | None = None,
        equipped: bool | None = None,
        character_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        snapshot_id = self.current_inventory_snapshot_id()
        if snapshot_id is None:
            return []
        return self.list_inventory_items(
            snapshot_id,
            kind=kind,
            equipped=equipped,
            character_id=character_id,
            limit=limit,
        )

    def list_inventory_items(
        self,
        snapshot_id: int,
        *,
        kind: str | None = None,
        equipped: bool | None = None,
        character_id: int | None = None,
        limit: int | None = None,
        uids: Iterable[tuple[int, int]] | None = None,
    ) -> list[dict[str, Any]]:
        """读取指定快照，而不是在长时间计算中跟随 ``is_current`` 漂移。"""

        raw_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        if kind not in (None, "module", "core"):
            raise UserDataValidationError("kind 必须是 module、core 或 None")
        conditions: list[str] = ["snapshot_id = ?"]
        parameters: list[Any] = [raw_snapshot_id]
        if kind is not None:
            conditions.append("kind = ?")
            parameters.append(kind)
        if equipped is not None:
            conditions.append("equipped = ?")
            parameters.append(int(equipped))
        if character_id is not None:
            conditions.append("equipped_character_id = ?")
            parameters.append(_integer(character_id, "character_id", minimum=1))
        normalized_uids: tuple[tuple[int, int], ...] | None = None
        if uids is not None:
            normalized_uids = tuple(sorted({
                (
                    _integer(uid_serial, "uid_serial", minimum=1),
                    _integer(uid_slot, "uid_slot", minimum=1),
                )
                for uid_serial, uid_slot in uids
            }))
            if not normalized_uids:
                return []
            conditions.append("(" + " OR ".join(
                "(uid_serial = ? AND uid_slot = ?)"
                for _uid in normalized_uids
            ) + ")")
            for uid_serial, uid_slot in normalized_uids:
                parameters.extend((uid_serial, uid_slot))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_sql = ""
        if limit is not None:
            limit_value = _integer(limit, "limit", minimum=1)
            limit_sql = " LIMIT ?"
            parameters.append(limit_value)
        rows = self._rows(
            f"""
            SELECT snapshot_id, uid_serial, uid_slot, kind, item_id, suit_id,
                   geometry, grid_count, quality, level, max_level, locked,
                   equipped, equipped_character_uid_json, equipped_character_id,
                   names_json, suit_names_json, raw_item_json
            FROM inventory_item
            {where}
            ORDER BY kind, uid_slot, uid_serial{limit_sql}
            """,
            parameters,
        )
        if not rows:
            return rows
        stat_conditions = ["snapshot_id = ?"]
        stat_parameters: list[Any] = [raw_snapshot_id]
        selected_uids = tuple(sorted({
            (int(row["uid_serial"]), int(row["uid_slot"])) for row in rows
        }))
        stat_conditions.append("(" + " OR ".join(
            "(uid_serial = ? AND uid_slot = ?)" for _uid in selected_uids
        ) + ")")
        for uid_serial, uid_slot in selected_uids:
            stat_parameters.extend((uid_serial, uid_slot))
        stats = self._rows(
            f"""
            SELECT uid_serial, uid_slot, stat_group, ordinal, property_id,
                   value, is_percent, names_json
            FROM inventory_item_stat
            WHERE {' AND '.join(stat_conditions)}
            ORDER BY uid_slot, uid_serial, stat_group, ordinal
            """,
            stat_parameters,
        )
        stats_by_uid: dict[tuple[int, int], dict[str, list[dict[str, Any]]]] = {}
        selected_uid_set = set(selected_uids)
        for stat in stats:
            uid = (stat.pop("uid_serial"), stat.pop("uid_slot"))
            if uid not in selected_uid_set:
                continue
            group = stat.pop("stat_group")
            stat["percent"] = bool(stat.pop("is_percent"))
            stat["names"] = _decoded(stat.pop("names_json"), {})
            stats_by_uid.setdefault(uid, {"main": [], "sub": []})[group].append(stat)
        for row in rows:
            row["locked"] = bool(row["locked"])
            row["equipped"] = bool(row["equipped"])
            row["equipped_character_uid"] = _decoded(
                row.pop("equipped_character_uid_json"), None
            )
            row["names"] = _decoded(row.pop("names_json"), {})
            row["suit_names"] = _decoded(row.pop("suit_names_json"), {})
            raw_item = _decoded(row.pop("raw_item_json"), {})
            row["discarded"] = bool(raw_item.get("discarded", False))
            row["is_duplicate_drive"] = bool(raw_item.get("is_duplicate_drive", False))
            row["duplicate_group_id"] = raw_item.get("duplicate_group_id")
            row["duplicate_index"] = raw_item.get("duplicate_index")
            row["duplicate_count"] = raw_item.get("duplicate_count")
            placement = raw_item.get("equipped_placement")
            row["equipped_placement"] = (
                dict(placement) if isinstance(placement, Mapping) else None
            )
            item_stats = stats_by_uid.get(
                (row["uid_serial"], row["uid_slot"]), {"main": [], "sub": []}
            )
            row["main_stats"] = item_stats["main"]
            row["sub_stats"] = item_stats["sub"]
        return rows

    def inventory_snapshot_diff(self, before_snapshot_id: int, after_snapshot_id: int) -> dict[str, Any]:
        """按原始 UID 比较两个稳定快照，并区分新增、移除和内容变化。"""

        before_id = _integer(before_snapshot_id, "before_snapshot_id", minimum=1)
        after_id = _integer(after_snapshot_id, "after_snapshot_id", minimum=1)
        before = {
            (row["uid_slot"], row["uid_serial"]): row
            for row in self._rows(
                """
                SELECT uid_slot, uid_serial, raw_item_json
                FROM inventory_item WHERE snapshot_id = ?
                """,
                (before_id,),
            )
        }
        after = {
            (row["uid_slot"], row["uid_serial"]): row
            for row in self._rows(
                """
                SELECT uid_slot, uid_serial, raw_item_json
                FROM inventory_item WHERE snapshot_id = ?
                """,
                (after_id,),
            )
        }
        added = sorted(after.keys() - before.keys())
        removed = sorted(before.keys() - after.keys())
        changed = sorted(
            uid
            for uid in before.keys() & after.keys()
            if before[uid]["raw_item_json"] != after[uid]["raw_item_json"]
        )

        def rows(values: list[tuple[int, int]]) -> list[dict[str, int]]:
            return [
                {"uid_slot": slot, "uid_serial": serial}
                for slot, serial in values
            ]

        return {
            "before_snapshot_id": before_id,
            "after_snapshot_id": after_id,
            "added": rows(added),
            "removed": rows(removed),
            "changed": rows(changed),
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
        }

    def raw_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        row = self._one(
            "SELECT raw_snapshot_json FROM inventory_snapshot WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        return _decoded(row["raw_snapshot_json"], {}) if row is not None else None

    def save_loadout_plan(
        self,
        *,
        name: str,
        character_id: int,
        assignments: Sequence[Mapping[str, Any]],
        source_snapshot_id: int | None = None,
        status: str = "draft",
        score: float | None = None,
        payload: Mapping[str, Any] | None = None,
        is_active: bool = False,
    ) -> int:
        plan_name = str(name).strip()
        if not plan_name:
            raise UserDataValidationError("装配方案名称不能为空")
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        raw_status = str(status).strip()
        if not raw_status:
            raise UserDataValidationError("装配方案状态不能为空")
        normalized: list[tuple[int, int, str, dict[str, Any]]] = []
        seen: set[tuple[int, int]] = set()
        for ordinal, raw_assignment in enumerate(assignments):
            assignment = _plain_object(raw_assignment, f"assignments[{ordinal}]")
            serial = _integer(assignment.get("uid_serial"), "assignment uid_serial", minimum=0)
            slot = _integer(assignment.get("uid_slot"), "assignment uid_slot", minimum=0)
            kind = assignment.get("kind")
            if kind not in ("module", "core"):
                raise UserDataValidationError("装配项 kind 必须是 module 或 core")
            if (serial, slot) in seen:
                raise UserDataValidationError("同一装配方案不能重复使用相同 UID")
            seen.add((serial, slot))
            for coordinate in ("target_row", "target_column"):
                value = assignment.get(coordinate)
                if value is not None and _integer(value, coordinate) not in range(1, 6):
                    raise UserDataValidationError(f"{coordinate} 必须在 1 到 5 之间")
            normalized.append((serial, slot, kind, assignment))
        if is_active:
            if source_snapshot_id is None:
                raise UserDataValidationError("激活装配方案必须记录来源背包快照")
            return self.replace_active_loadout_plans([{
                "name": plan_name,
                "character_id": raw_character_id,
                "source_snapshot_id": source_snapshot_id,
                "status": raw_status,
                "score": score,
                "payload": dict(payload or {}),
                "assignments": [
                    assignment for _serial, _slot, _kind, assignment in normalized
                ],
            }])[0]
        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if is_active:
                connection.execute(
                    "UPDATE loadout_plan SET is_active = 0, updated_at_utc = ? WHERE character_id = ?",
                    (now, raw_character_id),
                )
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_name, raw_character_id, source_snapshot_id, raw_status,
                    float(score) if score is not None else None,
                    _json(dict(payload or {})), int(is_active), now, now,
                ),
            )
            plan_id = int(cursor.lastrowid)
            for ordinal, (serial, slot, kind, assignment) in enumerate(normalized):
                connection.execute(
                    """
                    INSERT INTO loadout_plan_item(
                        plan_id, ordinal, uid_serial, uid_slot, kind,
                        target_row, target_column, rotation, raw_assignment_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id, ordinal, serial, slot, kind,
                        assignment.get("target_row"), assignment.get("target_column"),
                        assignment.get("rotation"), _json(assignment),
                    ),
                )
            connection.commit()
            return plan_id
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存装配方案") from exc

    def _repair_active_loadout_plan_conflicts_in_transaction(
        self,
        *,
        now: str,
        preferred_plan_ids: set[int] | None = None,
    ) -> int:
        """Keep one owner per native UID while preserving non-conflicting items."""

        connection = self._db()
        preferred = preferred_plan_ids or set()
        active_plans = [
            plan for plan in self.list_loadout_plans() if plan["is_active"]
        ]
        active_plans.sort(
            key=lambda plan: (
                int(plan["plan_id"]) in preferred,
                str(plan.get("updated_at_utc") or ""),
                int(plan["plan_id"]),
            ),
            reverse=True,
        )
        uid_owner: dict[tuple[int, int], int] = {}
        removed_by_plan: dict[int, set[tuple[int, int]]] = {}
        plans_by_id = {
            int(plan["plan_id"]): plan for plan in active_plans
        }
        for plan in active_plans:
            plan_id = int(plan["plan_id"])
            for item in plan["assignments"]:
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                if uid[0] == 0:
                    continue
                owner = uid_owner.setdefault(uid, plan_id)
                if owner != plan_id:
                    removed_by_plan.setdefault(plan_id, set()).add(uid)

        for plan_id, removed_uids in removed_by_plan.items():
            plan = plans_by_id[plan_id]
            connection.execute(
                """
                UPDATE loadout_plan
                SET is_active = 0, updated_at_utc = ?
                WHERE plan_id = ?
                """,
                (now, plan_id),
            )
            residual_assignments: list[dict[str, Any]] = []
            for item in plan["assignments"]:
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                if uid in removed_uids:
                    continue
                assignment = dict(item["raw_assignment"])
                assignment.update({
                    "uid_slot": uid[0],
                    "uid_serial": uid[1],
                    "kind": item["kind"],
                    "target_row": item.get("target_row"),
                    "target_column": item.get("target_column"),
                    "rotation": item.get("rotation"),
                })
                residual_assignments.append(assignment)
            if not residual_assignments:
                continue
            residual_payload = dict(plan.get("payload") or {})
            previous_source = residual_payload.get("source")
            residual_payload["source"] = "active_plan_conflict_repair"
            residual_payload["active_plan_conflict_repair"] = {
                "previous_plan_id": plan_id,
                "previous_source": previous_source,
                "removed_uids": [
                    {"uid_slot": slot, "uid_serial": serial}
                    for slot, serial in sorted(removed_uids)
                ],
            }
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, ?, 1, ?, ?)
                """,
                (
                    plan["name"],
                    int(plan["character_id"]),
                    plan.get("source_snapshot_id"),
                    (
                        "ready"
                        if any(
                            item.get("kind") == "module"
                            for item in residual_assignments
                        )
                        else "incomplete"
                    ),
                    _json(residual_payload),
                    now,
                    now,
                ),
            )
            residual_plan_id = int(cursor.lastrowid)
            for ordinal, assignment in enumerate(residual_assignments):
                connection.execute(
                    """
                    INSERT INTO loadout_plan_item(
                        plan_id, ordinal, uid_serial, uid_slot, kind,
                        target_row, target_column, rotation, raw_assignment_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        residual_plan_id,
                        ordinal,
                        assignment["uid_serial"],
                        assignment["uid_slot"],
                        assignment["kind"],
                        assignment.get("target_row"),
                        assignment.get("target_column"),
                        assignment.get("rotation"),
                        _json(assignment),
                    ),
                )
        return len(removed_by_plan)

    def repair_active_loadout_plan_conflicts(self) -> int:
        """Atomically repair historical active plans that share native UIDs."""

        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            repaired = self._repair_active_loadout_plan_conflicts_in_transaction(
                now=_utc_now()
            )
            duplicate = connection.execute(
                """
                SELECT 1
                FROM loadout_plan_item AS item
                JOIN loadout_plan AS plan USING(plan_id)
                WHERE plan.is_active = 1
                  AND item.uid_slot > 0
                GROUP BY item.uid_slot, item.uid_serial
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            ).fetchone()
            if duplicate is not None:
                raise UserDataValidationError(
                    "修复后仍存在被多个激活方案占用的装备"
                )
            connection.commit()
            return repaired
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法修复激活装配方案的装备占用冲突") from exc
        except BaseException:
            connection.rollback()
            raise

    def replace_active_loadout_plans(
        self,
        plans: Sequence[Mapping[str, Any]],
    ) -> tuple[int, ...]:
        """原子覆盖多个角色方案，并为被借装备的原槽位补入虚拟占位。"""

        normalized_plans: list[dict[str, Any]] = []
        target_characters: set[int] = set()
        claimed_uids: dict[tuple[int, int], int] = {}
        for plan_index, raw_plan in enumerate(plans):
            plan = _plain_object(raw_plan, f"plans[{plan_index}]")
            name = str(plan.get("name") or "").strip()
            if not name:
                raise UserDataValidationError("装配方案名称不能为空")
            character_id = _integer(
                plan.get("character_id"), "character_id", minimum=1
            )
            if character_id in target_characters:
                raise UserDataValidationError("批量保存中不能重复覆盖同一角色")
            target_characters.add(character_id)
            snapshot_id = _integer(
                plan.get("source_snapshot_id"), "source_snapshot_id", minimum=1
            )
            status = str(plan.get("status") or "ready").strip()
            if not status:
                raise UserDataValidationError("装配方案状态不能为空")
            normalized_assignments: list[dict[str, Any]] = []
            role_uids: set[tuple[int, int]] = set()
            for ordinal, raw_assignment in enumerate(plan.get("assignments") or ()):
                assignment = _plain_object(
                    raw_assignment, f"plans[{plan_index}].assignments[{ordinal}]"
                )
                serial = _integer(
                    assignment.get("uid_serial"), "assignment uid_serial", minimum=0
                )
                slot = _integer(
                    assignment.get("uid_slot"), "assignment uid_slot", minimum=0
                )
                kind = assignment.get("kind")
                if kind not in ("module", "core"):
                    raise UserDataValidationError(
                        "装配项 kind 必须是 module 或 core"
                    )
                uid = (slot, serial)
                virtual = is_virtual_equipment_assignment(assignment)
                if virtual:
                    if slot != 0 or serial <= 0:
                        raise UserDataValidationError(
                            "虚拟占位装备必须使用 slot=0 的正整数虚拟 UID"
                        )
                elif slot <= 0 or serial <= 0:
                    raise UserDataValidationError(
                        "真实装配项必须使用正整数原生 UID"
                    )
                if uid in role_uids:
                    raise UserDataValidationError(
                        "同一装配方案不能重复使用相同 UID"
                    )
                if not virtual and uid in claimed_uids:
                    raise UserDataValidationError(
                        f"批量保存中的装备 UID {uid} 同时分配给多个角色"
                    )
                role_uids.add(uid)
                if not virtual:
                    claimed_uids[uid] = character_id
                for coordinate in ("target_row", "target_column"):
                    value = assignment.get(coordinate)
                    if value is not None and _integer(value, coordinate) not in range(1, 6):
                        raise UserDataValidationError(
                            f"{coordinate} 必须在 1 到 5 之间"
                        )
                normalized_assignments.append(assignment)
            if not normalized_assignments:
                raise UserDataValidationError("每个激活方案至少需要一件装备")
            normalized_plans.append({
                "name": name,
                "character_id": character_id,
                "source_snapshot_id": snapshot_id,
                "status": status,
                "score": (
                    float(plan["score"]) if plan.get("score") is not None else None
                ),
                "payload": dict(plan.get("payload") or {}),
                "assignments": normalized_assignments,
            })
        if not normalized_plans:
            raise UserDataValidationError("没有可保存的装配方案")

        connection = self._db()
        now = _utc_now()

        def insert_plan(plan: Mapping[str, Any], *, is_active: bool = True) -> int:
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["name"], plan["character_id"], plan["source_snapshot_id"],
                    plan["status"], plan.get("score"),
                    _json(dict(plan.get("payload") or {})), int(is_active), now, now,
                ),
            )
            plan_id = int(cursor.lastrowid)
            for ordinal, assignment in enumerate(plan.get("assignments") or ()):
                connection.execute(
                    """
                    INSERT INTO loadout_plan_item(
                        plan_id, ordinal, uid_serial, uid_slot, kind,
                        target_row, target_column, rotation, raw_assignment_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id, ordinal, assignment["uid_serial"],
                        assignment["uid_slot"], assignment["kind"],
                        assignment.get("target_row"),
                        assignment.get("target_column"),
                        assignment.get("rotation"), _json(dict(assignment)),
                    ),
                )
            return plan_id

        try:
            connection.execute("BEGIN IMMEDIATE")
            def inventory_assignment_item(row: sqlite3.Row) -> dict[str, Any]:
                item = dict(row)
                if "names_json" in item:
                    item["names"] = _decoded(item.pop("names_json"), {})
                if "suit_names_json" in item:
                    item["suit_names"] = _decoded(
                        item.pop("suit_names_json"), {}
                    )
                return item

            inventory_by_snapshot: dict[
                int, dict[tuple[int, int], dict[str, Any]]
            ] = {}
            for snapshot_id in {
                int(plan["source_snapshot_id"]) for plan in normalized_plans
            }:
                snapshot = connection.execute(
                    """
                    SELECT complete, declared_item_count, stored_item_count
                    FROM inventory_snapshot WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchone()
                if (
                    snapshot is None
                    or not bool(snapshot["complete"])
                    or int(snapshot["declared_item_count"])
                    != int(snapshot["stored_item_count"])
                ):
                    raise UserDataValidationError(
                        f"背包快照不可用于保存：{snapshot_id}"
                    )
                inventory_by_snapshot[snapshot_id] = {
                    (
                        int(row["uid_slot"]),
                        int(row["uid_serial"]),
                    ): inventory_assignment_item(row)
                    for row in connection.execute(
                        """
                        SELECT uid_slot, uid_serial, kind, item_id, suit_id,
                               geometry, grid_count, quality,
                               names_json, suit_names_json
                        FROM inventory_item WHERE snapshot_id = ?
                        """,
                        (snapshot_id,),
                    )
                }
            for plan in normalized_plans:
                inventory = inventory_by_snapshot[int(plan["source_snapshot_id"])]
                for assignment in plan["assignments"]:
                    uid = (
                        int(assignment["uid_slot"]),
                        int(assignment["uid_serial"]),
                    )
                    if is_virtual_equipment_assignment(assignment):
                        continue
                    if (inventory.get(uid) or {}).get("kind") != assignment["kind"]:
                        raise UserDataValidationError(
                            f"装备 UID {uid} 不在方案固定的背包快照中"
                        )
            affected_plans = [
                plan
                for plan in self.list_loadout_plans()
                if plan["is_active"] and (
                    int(plan["character_id"]) in target_characters
                    or any(
                        (int(item["uid_slot"]), int(item["uid_serial"]))
                        in claimed_uids
                        for item in plan["assignments"]
                    )
                )
            ]
            for plan in affected_plans:
                connection.execute(
                    """
                    UPDATE loadout_plan
                    SET is_active = 0, updated_at_utc = ?
                    WHERE plan_id = ?
                    """,
                    (now, int(plan["plan_id"])),
                )
                if int(plan["character_id"]) in target_characters:
                    continue
                removed_uids = [
                    (int(item["uid_slot"]), int(item["uid_serial"]))
                    for item in plan["assignments"]
                    if (
                        int(item["uid_slot"]), int(item["uid_serial"])
                    ) in claimed_uids
                ]
                source_snapshot_id = int(plan.get("source_snapshot_id") or 0)
                source_inventory = inventory_by_snapshot.get(
                    source_snapshot_id, {}
                )
                if source_snapshot_id > 0 and not source_inventory:
                    source_inventory = {
                        (
                            int(row["uid_slot"]),
                            int(row["uid_serial"]),
                        ): inventory_assignment_item(row)
                        for row in connection.execute(
                            """
                            SELECT uid_slot, uid_serial, kind, item_id, suit_id,
                                   geometry, grid_count, quality,
                                   names_json, suit_names_json
                            FROM inventory_item WHERE snapshot_id = ?
                            """,
                            (source_snapshot_id,),
                        )
                    }
                residual_assignments = []
                virtual_changes: list[tuple[tuple[int, int], dict[str, Any]]] = []
                for ordinal, item in enumerate(plan["assignments"]):
                    uid = (
                        int(item["uid_slot"]),
                        int(item["uid_serial"]),
                    )
                    assignment = dict(item["raw_assignment"])
                    assignment.update({
                        "uid_slot": uid[0],
                        "uid_serial": uid[1],
                        "kind": item["kind"],
                        "target_row": item.get("target_row"),
                        "target_column": item.get("target_column"),
                        "rotation": item.get("rotation"),
                    })
                    if uid in claimed_uids:
                        assignment = make_virtual_equipment_assignment(
                            assignment,
                            inventory_item=source_inventory.get(uid),
                            character_id=int(plan["character_id"]),
                            ordinal=ordinal,
                        )
                        virtual_changes.append((uid, assignment))
                    residual_assignments.append(assignment)
                residual_payload = dict(plan.get("payload") or {})
                previous_source = residual_payload.get("source")
                residual_payload["source"] = "active_plan_overlay"
                residual_payload["active_plan_overlay"] = {
                    "previous_plan_id": int(plan["plan_id"]),
                    "previous_source": previous_source,
                    "removed_uids": [
                        {"uid_slot": slot, "uid_serial": serial}
                        for slot, serial in removed_uids
                    ],
                    "replaced_by_character_ids": sorted(target_characters),
                }
                # The previous owner must see both the empty placeholder and a
                # normal saved-plan change record.  This keeps its card eligible
                # for the same replacement optimizer as any other slot.
                virtual_display_uids = [
                    f"nte-{assignment['kind']}-0-{assignment['uid_serial']}"
                    for _removed_uid, assignment in virtual_changes
                ]
                removed_display_uids = [
                    f"nte-{next(item['kind'] for item in plan['assignments'] if (int(item['uid_slot']), int(item['uid_serial'])) == uid)}-{uid[0]}-{uid[1]}"
                    for uid, _assignment in virtual_changes
                ]
                residual_payload["changed_uids"] = virtual_display_uids
                residual_payload["last_diff"] = {
                    "changed": bool(virtual_display_uids),
                    "added_uids": virtual_display_uids,
                    "added": [
                        {"uid": display_uid, "is_changed": True}
                        for display_uid in virtual_display_uids
                    ],
                    "removed": [
                        {"uid": display_uid}
                        for display_uid in removed_display_uids
                    ],
                }
                insert_plan({
                    "name": plan["name"],
                    "character_id": int(plan["character_id"]),
                    "source_snapshot_id": plan.get("source_snapshot_id"),
                    "status": "incomplete",
                    "score": None,
                    "payload": residual_payload,
                    "assignments": residual_assignments,
                })

            saved_plan_ids = tuple(insert_plan(plan) for plan in normalized_plans)
            self._repair_active_loadout_plan_conflicts_in_transaction(
                now=now,
                preferred_plan_ids=set(saved_plan_ids),
            )
            duplicate = connection.execute(
                """
                SELECT item.uid_slot, item.uid_serial, COUNT(*) AS use_count
                FROM loadout_plan_item AS item
                JOIN loadout_plan AS plan USING(plan_id)
                WHERE plan.is_active = 1
                  AND item.uid_slot > 0
                GROUP BY item.uid_slot, item.uid_serial
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            ).fetchone()
            if duplicate is not None:
                raise UserDataValidationError(
                    "保存后仍存在被多个激活方案占用的装备 UID："
                    f"({duplicate['uid_slot']}, {duplicate['uid_serial']})"
                )
            connection.commit()
            return saved_plan_ids
        except (sqlite3.Error, UserDataValidationError) as exc:
            connection.rollback()
            if isinstance(exc, UserDataValidationError):
                raise
            raise UserDataError("无法原子覆盖激活装配方案") from exc
        except BaseException:
            connection.rollback()
            raise

    def list_loadout_plans(self, character_id: int | None = None) -> list[dict[str, Any]]:
        where = "" if character_id is None else "WHERE character_id = ?"
        parameters = () if character_id is None else (_integer(character_id, "character_id", minimum=1),)
        rows = self._rows(
            f"""
            SELECT plan_id, name, character_id, source_snapshot_id, status,
                   score, payload_json, is_active, created_at_utc, updated_at_utc
            FROM loadout_plan {where}
            ORDER BY updated_at_utc DESC, plan_id DESC
            """,
            parameters,
        )
        for row in rows:
            row["is_active"] = bool(row["is_active"])
            row["payload"] = _decoded(row.pop("payload_json"), {})
            row["assignments"] = self._rows(
                """
                SELECT ordinal, uid_serial, uid_slot, kind, target_row,
                       target_column, rotation, raw_assignment_json
                FROM loadout_plan_item WHERE plan_id = ? ORDER BY ordinal
                """,
                (row["plan_id"],),
            )
            for assignment in row["assignments"]:
                assignment["raw_assignment"] = _decoded(
                    assignment.pop("raw_assignment_json"), {}
                )
        return rows

    def get_loadout_plan(self, plan_id: int) -> dict[str, Any] | None:
        """按方案 ID 返回完整方案；读取格式与 ``list_loadout_plans`` 一致。"""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        return next(
            (
                plan
                for plan in self.list_loadout_plans()
                if plan["plan_id"] == raw_plan_id
            ),
            None,
        )

    def get_active_loadout_plan_for_role(self, role_name: str) -> dict[str, Any] | None:
        """返回指定显示角色名当前可执行的 SQLite 方案。"""

        raw_role_name = str(role_name).strip()
        if not raw_role_name:
            raise UserDataValidationError("角色名称不能为空")
        return next(
            (
                plan
                for plan in self.list_loadout_plans()
                if plan["is_active"]
                and isinstance(plan.get("payload"), Mapping)
                and plan["payload"].get("schema") == "allocation-official-snapshot-v1"
                and plan["payload"].get("source_role_name") == raw_role_name
            ),
            None,
        )

    def list_active_loadout_plans_by_role(self) -> dict[str, dict[str, Any]]:
        """返回当前所有带显示角色名的可执行 SQLite 方案。"""

        plans: dict[str, dict[str, Any]] = {}
        for plan in self.list_loadout_plans():
            payload = plan.get("payload")
            role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
            if (
                plan["is_active"]
                and isinstance(payload, Mapping)
                and payload.get("schema") == "allocation-official-snapshot-v1"
                and isinstance(role_name, str)
                and role_name.strip()
            ):
                plans.setdefault(role_name, plan)
        return plans

    def list_active_loadout_equipment_owners(self) -> list[dict[str, Any]]:
        """Return real native UIDs and their owners from active saved plans."""

        return self._rows(
            """
            SELECT item.uid_slot, item.uid_serial, item.kind,
                   plan.plan_id, plan.character_id
            FROM loadout_plan_item AS item
            JOIN loadout_plan AS plan USING(plan_id)
            WHERE plan.is_active = 1
              AND item.uid_slot > 0
            ORDER BY plan.updated_at_utc DESC, plan.plan_id DESC, item.ordinal
            """
        )

    def deactivate_loadout_plan(self, plan_id: int) -> bool:
        """从当前 UI 和新装配入口移除方案，但保留历史记录和任务审计。"""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        cursor = self._db().execute(
            "UPDATE loadout_plan SET is_active = 0, updated_at_utc = ? WHERE plan_id = ? AND is_active = 1",
            (_utc_now(), raw_plan_id),
        )
        self._db().commit()
        return cursor.rowcount > 0

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(self.database_path),
            "profile": self.profile(),
            "sync_settings": self.get_sync_settings(),
            "inventory": self.current_inventory_summary(),
            "snapshot_count": self._one(
                "SELECT COUNT(*) AS count FROM inventory_snapshot"
            )["count"],
            "loadout_plan_count": self._one(
                "SELECT COUNT(*) AS count FROM loadout_plan"
            )["count"],
        }

    def integrity_check(self) -> dict[str, Any]:
        quick_check = [row["quick_check"] for row in self._rows("PRAGMA quick_check")]
        foreign_key_errors = self._rows("PRAGMA foreign_key_check")
        return {
            "ok": quick_check == ["ok"] and not foreign_key_errors,
            "quick_check": quick_check,
            "foreign_key_errors": foreign_key_errors,
        }
