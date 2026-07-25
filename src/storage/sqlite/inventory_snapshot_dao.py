# 管理背包快照及其装备明细的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .user_data_support import (
    ALLOCATION_STRATEGIES,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SNAPSHOT_RETENTION_COUNT,
    SNAPSHOT_SOURCES,
    SUIT_REQUIREMENT_MODES,
    SYNC_METHODS,
    USER_MIGRATIONS,
    SCHEMA_VERSION,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _mark_duplicate_modules,
    _plain_object,
    _utc_now,
)

# SQLite is frequently built with the default 999 host-parameter limit.  Each
# equipment UID consumes two parameters, while the snapshot id consumes one.
# Keep comfortably below that portable limit when reading a full inventory.
_SQLITE_QUERY_PARAMETER_BUDGET = 900
_UID_PAIR_QUERY_BATCH_SIZE = (_SQLITE_QUERY_PARAMETER_BUDGET - 1) // 2


class InventorySnapshotDaoMixin:
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

        # nte-core v0.3.5+ provides independent character instances in the
        # snapshot, including characters with no equipped module/core.  These
        # rows are the authoritative fast-assembly identity source; do not
        # infer them from equipment ownership.
        raw_characters = payload.get("characters", [])
        if not isinstance(raw_characters, list):
            raise UserDataValidationError("快照 characters 必须是数组")
        declared_character_count = payload.get("character_count")
        if declared_character_count is not None:
            if _integer(declared_character_count, "snapshot character_count", minimum=0) != len(raw_characters):
                raise UserDataValidationError(
                    f"快照 character_count 为 {declared_character_count}，但 characters 实际有 {len(raw_characters)} 条"
                )
        normalized_characters: list[tuple[int, int, int]] = []
        seen_character_ids: set[int] = set()
        seen_character_uids: set[tuple[int, int]] = set()
        for index, raw_character in enumerate(raw_characters):
            character = _plain_object(raw_character, f"characters[{index}]")
            character_id = _integer(character.get("character_id"), f"characters[{index}].character_id", minimum=1)
            uid = _plain_object(character.get("uid"), f"characters[{index}].uid")
            character_slot = _integer(uid.get("slot"), f"characters[{index}].uid.slot", minimum=1)
            character_serial = _integer(uid.get("serial"), f"characters[{index}].uid.serial", minimum=1)
            if character_id in seen_character_ids or (character_slot, character_serial) in seen_character_uids:
                raise UserDataValidationError("快照包含重复角色实例")
            seen_character_ids.add(character_id)
            seen_character_uids.add((character_slot, character_serial))
            normalized_characters.append((character_id, character_slot, character_serial))

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
            for character_id, character_slot, character_serial in normalized_characters:
                connection.execute(
                    """
                    INSERT INTO character_instance_mapping(
                        character_id, uid_slot, uid_serial, source,
                        first_seen_snapshot_id, last_seen_snapshot_id,
                        created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, 'snapshot', ?, ?, ?, ?)
                    ON CONFLICT(character_id, uid_slot, uid_serial) DO UPDATE SET
                        source = 'snapshot',
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

    def current_inventory_summary(self) -> dict[str, Any] | None:
        snapshot_id = self.current_inventory_snapshot_id()
        return self.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None

    def current_inventory_snapshot_id(self) -> int | None:
        """返回当前完整库存。

        ``import_inventory_snapshot`` atomically moves ``is_current`` to the
        newly imported complete snapshot.  This intentionally lets a completed
        full visual scan replace an older nte-core capture: visual snapshots
        have no game-side ``observed_at_unix_ms``, so ordering that field first
        would otherwise keep an arbitrarily old capture active forever.
        """

        row = self._one(
            """
            SELECT snapshot_id
            FROM inventory_snapshot
            WHERE is_current = 1
              AND complete = 1
              AND source IN ('nte_core', 'gamepad')
            ORDER BY snapshot_id DESC
            LIMIT 1
            """
        )
        if row is not None:
            return int(row["snapshot_id"])

        # Compatibility for databases created before the current pointer was
        # introduced or manually repaired databases with no marked snapshot.
        fallback = self._one(
            """
            SELECT snapshot_id
            FROM inventory_snapshot
            WHERE complete = 1 AND source IN ('nte_core', 'gamepad')
            ORDER BY captured_at_utc DESC, snapshot_id DESC
            LIMIT 1
            """
        )
        return int(fallback["snapshot_id"]) if fallback is not None else None

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
        selected_uids = tuple(sorted({
            (int(row["uid_serial"]), int(row["uid_slot"])) for row in rows
        }))
        stats: list[dict[str, Any]] = []
        # A 2,000-item inventory needs 4,001 bound parameters if queried in
        # one statement.  That raises "too many SQL variables" on the normal
        # SQLite build, preventing the virtualized warehouse from loading at
        # all.  The card model remains virtualized; only this SQLite fetch is
        # split into stable, order-preserving UID batches.
        for start in range(0, len(selected_uids), _UID_PAIR_QUERY_BATCH_SIZE):
            uid_batch = selected_uids[start:start + _UID_PAIR_QUERY_BATCH_SIZE]
            stat_parameters: list[Any] = [raw_snapshot_id]
            for uid_serial, uid_slot in uid_batch:
                stat_parameters.extend((uid_serial, uid_slot))
            uid_sql = " OR ".join(
                "(uid_serial = ? AND uid_slot = ?)" for _uid in uid_batch
            )
            stats.extend(self._rows(
                f"""
                SELECT uid_serial, uid_slot, stat_group, ordinal, property_id,
                       value, is_percent, names_json
                FROM inventory_item_stat
                WHERE snapshot_id = ? AND ({uid_sql})
                ORDER BY uid_slot, uid_serial, stat_group, ordinal
                """,
                stat_parameters,
            ))
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

    def snapshot_has_independent_character_instances(self, snapshot_id: int) -> bool:
        """Whether this immutable snapshot was emitted with ``characters``.

        Old nte-core snapshots can still have mappings inferred from equipped
        equipment.  Those mappings are useful for display, but they cannot
        prove that every owned character UID was captured.  Keep this check on
        the original event payload so the sync UI can distinguish that legacy
        condition from a v0.3.5+ independent-character snapshot.
        """

        raw_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        raw = self.raw_snapshot(raw_snapshot_id)
        if not isinstance(raw, Mapping):
            return False
        payload = raw.get("params") if raw.get("method") == "event.inventory.snapshot" else raw
        return isinstance(payload, Mapping) and isinstance(payload.get("characters"), list)
