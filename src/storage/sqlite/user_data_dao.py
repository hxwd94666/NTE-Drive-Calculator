# 提供按账号存储设置、背包快照和装配方案的 SQLite 数据层。
"""按账号存储设置、背包快照和装配方案的 SQLite 数据层。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
BASE_SCHEMA_VERSION = 1
DEFAULT_SCHEMA_PATH = Path(__file__).with_name("schema") / "001_user_data.sql"
USER_MIGRATIONS = {
    2: Path(__file__).with_name("schema") / "003_user_data_v2.sql",
}
SYNC_METHODS = frozenset({"nte_core", "gamepad"})
SNAPSHOT_SOURCES = frozenset({"nte_core", "gamepad", "import"})


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
        if not existed:
            try:
                self._initialize(
                    Path(schema_path),
                    account_id=str(account_id),
                    account_name=str(account_name or account_id),
                )
            except BaseException:
                self.close()
                self.database_path.unlink(missing_ok=True)
                raise
        self._migrate_schema()
        self._validate_schema()

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
                connection.executescript(migration_path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                    (target_version, _utc_now()),
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            connection.rollback()
            raise UserDataError("无法升级用户数据库结构") from exc

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
        self._db().execute(
            """
            UPDATE sync_settings
            SET inventory_sync_method = ?, equipment_apply_method = ?,
                capture_device_id = ?, raw_capture_enabled = ?,
                inventory_settle_seconds = ?, auto_start_inventory_sync = ?,
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
                _utc_now(),
            ),
        )
        self._db().commit()
        return self.get_sync_settings()

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

    def current_inventory_summary(self) -> dict[str, Any] | None:
        snapshot_id = self.current_inventory_snapshot_id()
        return self.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None

    def current_inventory_snapshot_id(self) -> int | None:
        """返回当前稳定背包快照 ID；尚未同步时返回 ``None``。"""

        row = self._one(
            "SELECT snapshot_id FROM inventory_snapshot WHERE is_current = 1"
        )
        return int(row["snapshot_id"]) if row is not None else None

    def inventory_snapshot_summary(self, snapshot_id: int) -> dict[str, Any] | None:
        """读取指定不可变快照的摘要，供计算任务固定输入版本。"""

        raw_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        row = self._one(
            """
            SELECT s.snapshot_id, s.source, s.generation, s.sequence,
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
                   names_json, suit_names_json
            FROM inventory_item
            {where}
            ORDER BY kind, uid_slot, uid_serial{limit_sql}
            """,
            parameters,
        )
        if not rows:
            return rows
        stats = self._rows(
            """
            SELECT uid_serial, uid_slot, stat_group, ordinal, property_id,
                   value, is_percent, names_json
            FROM inventory_item_stat
            WHERE snapshot_id = ?
            ORDER BY uid_slot, uid_serial, stat_group, ordinal
            """,
            (raw_snapshot_id,),
        )
        stats_by_uid: dict[tuple[int, int], dict[str, list[dict[str, Any]]]] = {}
        selected_uids = {(row["uid_serial"], row["uid_slot"]) for row in rows}
        for stat in stats:
            uid = (stat.pop("uid_serial"), stat.pop("uid_slot"))
            if uid not in selected_uids:
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
