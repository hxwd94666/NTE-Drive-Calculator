# 精确导出战报行图并事务式导入战报包。

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    BATTLE_REPORT_MAX_MANUAL_RECORDS,
    BATTLE_REPORT_MAX_RECORDS,
    UserDataError,
    UserDataValidationError,
)


_RECORD_TABLES = (
    "battle_record",
    "battle_record_retention",
    "battle_axis_capture",
    "battle_hit_evidence",
    "battle_time_stop_interval",
    "battle_build_snapshot",
    "battle_character_build_snapshot",
    "battle_character_skill_snapshot",
    "battle_equipment_snapshot",
    "battle_equipment_stat_snapshot",
    "battle_character_stat_snapshot",
    "battle_build_edit",
    "battle_character_build_edit",
    "battle_character_skill_edit",
    "battle_character_awaken_edit",
    "battle_target_condition",
)

_INSERT_ORDER = _RECORD_TABLES


class BattleReportTransferDaoMixin(UserDataDaoMixinHost):
    """Own the SQLite-specific portable row graph for one battle record."""

    def load_battle_report_transfer_rows(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        if isinstance(battle_record_id, bool) or not isinstance(battle_record_id, int):
            raise UserDataValidationError("battle_record_id 必须是整数")
        if battle_record_id < 1:
            raise UserDataValidationError("battle_record_id 不能小于 1")
        connection = self._db()
        record = connection.execute(
            "SELECT * FROM battle_record WHERE battle_record_id = ?",
            (battle_record_id,),
        ).fetchone()
        if record is None:
            return None
        capture = connection.execute(
            "SELECT capture_id FROM battle_axis_capture WHERE battle_record_id = ?",
            (battle_record_id,),
        ).fetchone()
        capture_id = None if capture is None else int(capture["capture_id"])
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in _RECORD_TABLES:
            if table in {"battle_hit_evidence", "battle_time_stop_interval"}:
                rows = [] if capture_id is None else connection.execute(
                    f"SELECT * FROM {table} WHERE capture_id = ? "
                    + self._portable_order_by(table),
                    (capture_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE battle_record_id = ? "
                    + self._portable_order_by(table),
                    (battle_record_id,),
                ).fetchall()
            tables[table] = [dict(row) for row in rows]
        state = connection.execute(
            """
            SELECT last_detail_scope, analysis_start_us, analysis_end_us,
                   analysis_character_id, updated_at_utc
            FROM battle_report_page_state
            WHERE singleton_id = 1 AND last_battle_record_id = ?
            """,
            (battle_record_id,),
        ).fetchone()
        return {
            "source_battle_record_id": battle_record_id,
            "tables": tables,
            "saved_page_state": None if state is None else dict(state),
        }

    def battle_report_transfer_statuses(self) -> dict[int, dict[str, Any]]:
        rows = self._db().execute(
            """
            SELECT battle_record_id, axis_complete, first_available_cursor,
                   next_cursor, first_sequence, total_hits, retained_hits,
                   stored_hits
            FROM battle_axis_capture
            WHERE capture_state = 'finalized' AND battle_record_id IS NOT NULL
            ORDER BY battle_record_id
            """
        ).fetchall()
        return {int(row["battle_record_id"]): dict(row) for row in rows}

    def import_battle_report_transfer_rows(
        self,
        reports: Sequence[Mapping[str, Any]],
        *,
        before_commit=None,
    ) -> dict[str, Any]:
        if not reports:
            raise UserDataValidationError("战报包中没有可导入记录")
        normalized = [self._normalize_transfer_report(item) for item in reports]
        operation_ids = [item["capture_operation_id"] for item in normalized]
        if len(set(operation_ids)) != len(operation_ids):
            raise UserDataValidationError("战报包包含重复 capture_operation_id")

        connection = self._db()
        imported_ids: list[int] = []
        skipped = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending: list[dict[str, Any]] = []
            for item in normalized:
                existing = connection.execute(
                    """
                    SELECT battle_record_id, raw_summary_sha256
                    FROM battle_record WHERE capture_operation_id = ?
                    """,
                    (item["capture_operation_id"],),
                ).fetchone()
                if existing is None:
                    pending.append(item)
                    continue
                if str(existing["raw_summary_sha256"]) != item["raw_summary_sha256"]:
                    raise UserDataValidationError(
                        "本地已有同 capture_operation_id 的不同战报，已拒绝覆盖"
                    )
                skipped += 1
            self._validate_retention_capacity(connection, pending)
            for item in pending:
                imported_ids.append(self._insert_transfer_report(connection, item))
            if before_commit is not None:
                before_commit()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {
            "imported_battle_record_ids": tuple(imported_ids),
            "skipped_existing_count": skipped,
        }

    @staticmethod
    def _portable_order_by(table: str) -> str:
        return {
            "battle_hit_evidence": "ORDER BY sequence_order",
            "battle_time_stop_interval": "ORDER BY ordinal",
            "battle_character_build_snapshot": "ORDER BY ordinal, character_id",
            "battle_character_skill_snapshot": "ORDER BY character_id, skill_id",
            "battle_equipment_snapshot": "ORDER BY character_id, kind, uid_slot, uid_serial",
            "battle_equipment_stat_snapshot": "ORDER BY uid_slot, uid_serial, stat_group, ordinal",
            "battle_character_stat_snapshot": "ORDER BY character_id, source_group, ordinal, property_id",
            "battle_character_build_edit": "ORDER BY ordinal, character_id",
            "battle_character_skill_edit": "ORDER BY character_id, skill_id",
            "battle_character_awaken_edit": "ORDER BY character_id, ordinal, effect_id",
        }.get(table, "")

    @classmethod
    def _normalize_transfer_report(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise UserDataValidationError("战报包记录必须是对象")
        raw_tables = value.get("tables")
        if not isinstance(raw_tables, Mapping):
            raise UserDataValidationError("战报包记录缺少数据库行")
        unknown = set(raw_tables) - set(_RECORD_TABLES)
        if unknown:
            raise UserDataValidationError("战报包包含不支持的数据表")
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in _RECORD_TABLES:
            raw_rows = raw_tables.get(table, [])
            if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
                raise UserDataValidationError(f"{table} 必须是数组")
            rows = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, Mapping):
                    raise UserDataValidationError(f"{table} 包含无效数据行")
                rows.append(dict(raw_row))
            tables[table] = rows
        if len(tables["battle_record"]) != 1:
            raise UserDataValidationError("每条导出战报必须包含一个 battle_record")
        if len(tables["battle_record_retention"]) != 1:
            raise UserDataValidationError("每条导出战报必须包含一个保留状态")
        record = tables["battle_record"][0]
        operation_id = str(record.get("capture_operation_id") or "").strip()
        raw_summary = record.get("raw_summary_json")
        raw_sha256 = str(record.get("raw_summary_sha256") or "").strip().lower()
        if not operation_id or not isinstance(raw_summary, str):
            raise UserDataValidationError("战报包缺少原始摘要身份")
        try:
            summary_payload = json.loads(raw_summary)
        except json.JSONDecodeError as error:
            raise UserDataValidationError("战报包原始摘要 JSON 无效") from error
        if not isinstance(summary_payload, dict):
            raise UserDataValidationError("战报包原始摘要必须是对象")
        expected = hashlib.sha256(raw_summary.encode("utf-8")).hexdigest()
        if raw_sha256 != expected:
            raise UserDataValidationError("战报包原始摘要 SHA-256 不匹配")
        cls._validate_record_graph(tables)
        return {
            "capture_operation_id": operation_id,
            "raw_summary_sha256": raw_sha256,
            "tables": tables,
        }

    @staticmethod
    def _validate_record_graph(tables: Mapping[str, list[dict[str, Any]]]) -> None:
        record_id = tables["battle_record"][0].get("battle_record_id")
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise UserDataValidationError("来源 battle_record_id 无效")
        for table, rows in tables.items():
            if table in {"battle_hit_evidence", "battle_time_stop_interval"}:
                continue
            for row in rows:
                if row.get("battle_record_id") != record_id:
                    raise UserDataValidationError(f"{table} 的战报外键不一致")
        captures = tables["battle_axis_capture"]
        if len(captures) > 1:
            raise UserDataValidationError("单场战报不能包含多个 axis capture")
        if not captures:
            if tables["battle_hit_evidence"] or tables["battle_time_stop_interval"]:
                raise UserDataValidationError("逐击或时停缺少 axis capture")
            return
        capture_id = captures[0].get("capture_id")
        if isinstance(capture_id, bool) or not isinstance(capture_id, int):
            raise UserDataValidationError("来源 capture_id 无效")
        if (
            str(captures[0].get("capture_operation_id") or "")
            != str(tables["battle_record"][0].get("capture_operation_id") or "")
            or captures[0].get("capture_state") != "finalized"
        ):
            raise UserDataValidationError("逐击轴与战报身份或完成状态不一致")
        for table in ("battle_hit_evidence", "battle_time_stop_interval"):
            if any(row.get("capture_id") != capture_id for row in tables[table]):
                raise UserDataValidationError(f"{table} 的 capture 外键不一致")
        raw_record = captures[0].get("raw_record_json")
        raw_record_sha256 = captures[0].get("raw_record_sha256")
        if raw_record not in (None, ""):
            if not isinstance(raw_record, str):
                raise UserDataValidationError("nte-core record 原始 JSON 无效")
            try:
                decoded_record = json.loads(raw_record)
            except json.JSONDecodeError as error:
                raise UserDataValidationError("nte-core record 原始 JSON 无效") from error
            if not isinstance(decoded_record, dict):
                raise UserDataValidationError("nte-core record 原始 JSON 必须是对象")
            expected_record_sha = hashlib.sha256(raw_record.encode("utf-8")).hexdigest()
            if str(raw_record_sha256 or "").lower() != expected_record_sha:
                raise UserDataValidationError("nte-core record SHA-256 不匹配")
        for row in tables["battle_hit_evidence"]:
            cls_payload = row.get("raw_hit_json")
            try:
                decoded_hit = json.loads(str(cls_payload))
            except json.JSONDecodeError as error:
                raise UserDataValidationError("逐击原始 JSON 无效") from error
            if not isinstance(decoded_hit, dict):
                raise UserDataValidationError("逐击原始 JSON 必须是对象")
        for row in tables["battle_time_stop_interval"]:
            try:
                decoded_interval = json.loads(str(row.get("raw_interval_json")))
            except json.JSONDecodeError as error:
                raise UserDataValidationError("时停原始 JSON 无效") from error
            if not isinstance(decoded_interval, dict):
                raise UserDataValidationError("时停原始 JSON 必须是对象")

    @staticmethod
    def _validate_retention_capacity(
        connection: sqlite3.Connection,
        pending: Sequence[Mapping[str, Any]],
    ) -> None:
        current_total = int(connection.execute(
            "SELECT COUNT(*) FROM battle_record"
        ).fetchone()[0])
        current_manual = int(connection.execute(
            "SELECT COUNT(*) FROM battle_record_retention WHERE retention_kind = 'manual'"
        ).fetchone()[0])
        pending_manual = sum(
            str(item["tables"]["battle_record_retention"][0].get("retention_kind"))
            == "manual"
            for item in pending
        )
        if current_total + len(pending) > BATTLE_REPORT_MAX_RECORDS:
            raise UserDataValidationError(
                f"导入后战报将超过 {BATTLE_REPORT_MAX_RECORDS} 条，请先删除不需要的记录"
            )
        if current_manual + pending_manual > BATTLE_REPORT_MAX_MANUAL_RECORDS:
            raise UserDataValidationError(
                f"导入后手动战报将超过 {BATTLE_REPORT_MAX_MANUAL_RECORDS} 条，请先取消部分保存"
            )

    def _insert_transfer_report(
        self,
        connection: sqlite3.Connection,
        item: Mapping[str, Any],
    ) -> int:
        tables = item["tables"]
        source_record_id = int(tables["battle_record"][0]["battle_record_id"])
        record_row = dict(tables["battle_record"][0])
        record_row.pop("battle_record_id", None)
        record_id = self._insert_row(connection, "battle_record", record_row)
        if record_id is None:
            raise UserDataError("导入战报后未返回 battle_record_id")

        capture_id_map: dict[int, int] = {}
        for table in _INSERT_ORDER[1:]:
            for source in tables[table]:
                row = dict(source)
                if "battle_record_id" in row:
                    if int(row["battle_record_id"]) != source_record_id:
                        raise UserDataValidationError(f"{table} 的战报外键不一致")
                    row["battle_record_id"] = int(record_id)
                if table == "battle_axis_capture":
                    source_capture_id = int(row.pop("capture_id"))
                    row["source_inventory_snapshot_id"] = None
                    new_capture_id = self._insert_row(connection, table, row)
                    if new_capture_id is None:
                        raise UserDataError("导入逐击轴后未返回 capture_id")
                    capture_id_map[source_capture_id] = int(new_capture_id)
                    continue
                if table in {"battle_hit_evidence", "battle_time_stop_interval"}:
                    source_capture_id = int(row["capture_id"])
                    if source_capture_id not in capture_id_map:
                        raise UserDataValidationError(f"{table} 缺少来源 capture")
                    row["capture_id"] = capture_id_map[source_capture_id]
                if table == "battle_build_snapshot":
                    row["source_inventory_snapshot_id"] = None
                self._insert_row(connection, table, row)
        return int(record_id)

    @staticmethod
    def _insert_row(
        connection: sqlite3.Connection,
        table: str,
        row: Mapping[str, Any],
    ) -> int | None:
        if table not in _RECORD_TABLES:
            raise UserDataValidationError("不支持的战报数据表")
        local_columns = {
            str(column[1])
            for column in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        unknown = set(row) - local_columns
        if unknown:
            raise UserDataValidationError(f"{table} 包含当前版本不支持的字段")
        if not row:
            raise UserDataValidationError(f"{table} 数据行不能为空")
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        names = ",".join(f'"{name}"' for name in columns)
        cursor = connection.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            tuple(row[name] for name in columns),
        )
        return None if cursor.lastrowid is None else int(cursor.lastrowid)
