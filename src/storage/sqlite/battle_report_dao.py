# 提供账号战报持久化、保留和历史查询。
"""Account-owned battle report persistence and retention queries."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    BATTLE_REPORT_MAX_MANUAL_RECORDS,
    BATTLE_REPORT_MAX_RECORDS,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _utc_now,
)


_DETAIL_SCOPES = frozenset({"current", "first", "second"})
_HISTORY_SELECT = """
    SELECT
        record.battle_record_id,
        record.capture_operation_id,
        COALESCE(record.evidence_source_kind, record.source_kind) AS source_kind,
        COALESCE(
            record.evidence_capability_level,
            record.capability_level
        ) AS capability_level,
        record.combat_context_kind,
        record.abyss_floor,
        record.has_first_half,
        record.has_second_half,
        record.captured_at_utc,
        record.finalized_at_utc,
        record.dps_time_mode,
        record.duration_seconds,
        record.total_damage,
        record.total_dps,
        record.total_damage_taken,
        record.total_hits,
        record.character_count,
        record.skill_count,
        record.character_ids_json,
        record.abyss_detected,
        record.abyss_success,
        record.payload_schema_version,
        record.raw_summary_sha256,
        record.nte_core_record_id,
        record.nte_core_contract_version,
        record.axis_complete,
        record.axis_first_sequence,
        record.axis_total_hits,
        record.axis_stored_hits,
        record.created_at_utc,
        retention.retention_kind,
        retention.auto_saved_at_utc,
        retention.manual_saved_at_utc,
        retention.updated_at_utc,
        CASE retention.retention_kind
            WHEN 'manual' THEN retention.manual_saved_at_utc
            ELSE retention.auto_saved_at_utc
        END AS saved_at_utc
    FROM battle_record AS record
    JOIN battle_record_retention AS retention USING (battle_record_id)
"""


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise UserDataValidationError(f"{label} 不能为空")
    return normalized


def _non_negative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UserDataValidationError(f"{label} 必须是数字")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise UserDataValidationError(f"{label} 必须是有限非负数")
    return number


class BattleReportDaoMixin(UserDataDaoMixinHost):
    """Own SQL for immutable summaries, retention and page restore state."""

    @staticmethod
    def _normalized_history_row(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        character_ids = _decoded(str(result.pop("character_ids_json")), [])
        if not isinstance(character_ids, list) or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in character_ids
        ):
            raise UserDataError("战报角色索引损坏")
        result["character_ids"] = tuple(int(item) for item in character_ids)
        for field in (
            "has_first_half",
            "has_second_half",
            "abyss_detected",
            "abyss_success",
        ):
            result[field] = bool(result[field])
        if result.get("axis_complete") is not None:
            result["axis_complete"] = bool(result["axis_complete"])
        return result

    def _battle_record_history_row(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        row = self._one(
            _HISTORY_SELECT + " WHERE record.battle_record_id = ?",
            (battle_record_id,),
        )
        return None if row is None else self._normalized_history_row(row)

    def insert_auto_summary_snapshot(
        self,
        *,
        capture_operation_id: str,
        combat_context_kind: str,
        abyss_floor: int | None,
        has_first_half: bool,
        has_second_half: bool,
        captured_at_utc: str,
        finalized_at_utc: str,
        dps_time_mode: str,
        duration_seconds: float,
        total_damage: float,
        total_dps: float,
        total_damage_taken: float,
        total_hits: int,
        character_count: int,
        skill_count: int,
        character_ids: Sequence[int],
        abyss_detected: bool,
        abyss_success: bool,
        payload_schema_version: int,
        raw_summary_json: str,
        raw_summary_sha256: str,
    ) -> dict[str, Any]:
        """Insert one final summary and enforce account-wide automatic FIFO."""

        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        context_kind = _required_text(combat_context_kind, "combat_context_kind")
        if context_kind not in {"abyss", "non_abyss"}:
            raise UserDataValidationError(
                "combat_context_kind 必须是 abyss 或 non_abyss"
            )
        normalized_floor = (
            None
            if abyss_floor is None
            else _integer(abyss_floor, "abyss_floor", minimum=1)
        )
        if context_kind == "non_abyss" and normalized_floor is not None:
            raise UserDataValidationError("非深渊战报不能保存 abyss_floor")
        if bool(abyss_detected) != (context_kind == "abyss"):
            raise UserDataValidationError("深渊识别状态与战斗上下文不一致")

        normalized_character_ids = [
            _integer(item, f"character_ids[{index}]", minimum=1)
            for index, item in enumerate(character_ids)
        ]
        raw_json = _required_text(raw_summary_json, "raw_summary_json")
        try:
            decoded_payload = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise UserDataValidationError("raw_summary_json 不是有效 JSON") from error
        if not isinstance(decoded_payload, Mapping):
            raise UserDataValidationError("raw_summary_json 顶层必须是对象")
        expected_sha256 = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        supplied_sha256 = _required_text(raw_summary_sha256, "raw_summary_sha256")
        if supplied_sha256 != expected_sha256:
            raise UserDataValidationError("raw_summary_sha256 与原始 JSON 不一致")

        normalized_total_hits = _integer(total_hits, "total_hits", minimum=0)
        normalized_character_count = _integer(
            character_count,
            "character_count",
            minimum=0,
        )
        normalized_skill_count = _integer(skill_count, "skill_count", minimum=0)
        normalized_schema_version = _integer(
            payload_schema_version,
            "payload_schema_version",
            minimum=1,
        )
        captured_at = _required_text(captured_at_utc, "captured_at_utc")
        finalized_at = _required_text(finalized_at_utc, "finalized_at_utc")
        mode = _required_text(dps_time_mode, "dps_time_mode")
        now = _utc_now()
        connection = self._db()
        pruned_ids: list[int] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT battle_record_id, raw_summary_sha256
                FROM battle_record
                WHERE capture_operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["raw_summary_sha256"]) != supplied_sha256:
                    raise UserDataValidationError(
                        "同一 capture_operation_id 对应了不同战报 payload"
                    )
                record_id = int(existing["battle_record_id"])
                self._upsert_battle_report_page_state(
                    record_id,
                    detail_scope="current",
                    updated_at_utc=now,
                )
                connection.commit()
                record = self._battle_record_history_row(record_id)
                if record is None:
                    raise UserDataError("幂等战报记录在提交后不存在")
                return {
                    "record": record,
                    "inserted": False,
                    "pruned_battle_record_ids": (),
                }

            cursor = connection.execute(
                """
                INSERT INTO battle_record(
                    capture_operation_id, source_kind, capability_level,
                    combat_context_kind, abyss_floor, has_first_half,
                    has_second_half, captured_at_utc, finalized_at_utc,
                    dps_time_mode, duration_seconds, total_damage, total_dps,
                    total_damage_taken, total_hits, character_count, skill_count,
                    character_ids_json, abyss_detected, abyss_success,
                    payload_schema_version, raw_summary_json, raw_summary_sha256,
                    created_at_utc
                ) VALUES (
                    ?, 'nte_core_summary', 'summary_only', ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    operation_id,
                    context_kind,
                    normalized_floor,
                    int(bool(has_first_half)),
                    int(bool(has_second_half)),
                    captured_at,
                    finalized_at,
                    mode,
                    _non_negative_number(duration_seconds, "duration_seconds"),
                    _non_negative_number(total_damage, "total_damage"),
                    _non_negative_number(total_dps, "total_dps"),
                    _non_negative_number(total_damage_taken, "total_damage_taken"),
                    normalized_total_hits,
                    normalized_character_count,
                    normalized_skill_count,
                    _json(normalized_character_ids),
                    int(bool(abyss_detected)),
                    int(bool(abyss_success)),
                    normalized_schema_version,
                    raw_json,
                    supplied_sha256,
                    now,
                ),
            )
            if cursor.lastrowid is None:
                raise UserDataError("创建战报后未返回 battle_record_id")
            record_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO battle_record_retention(
                    battle_record_id, retention_kind, auto_saved_at_utc,
                    manual_saved_at_utc, updated_at_utc
                ) VALUES (?, 'auto', ?, NULL, ?)
                """,
                (record_id, now, now),
            )
            pruned_ids = self._prune_oldest_battle_records(
                retention_kind="auto",
                maximum=BATTLE_REPORT_MAX_RECORDS,
                count_all=True,
                exclude_record_id=record_id,
            )
            self._upsert_battle_report_page_state(
                record_id,
                detail_scope="current",
                updated_at_utc=now,
            )
            connection.commit()
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise

        record = self._battle_record_history_row(record_id)
        if record is None:
            raise UserDataError("战报提交后不存在")
        return {
            "record": record,
            "inserted": True,
            "pruned_battle_record_ids": tuple(pruned_ids),
        }

    def list_battle_records(
        self,
        *,
        limit: int = BATTLE_REPORT_MAX_RECORDS,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        normalized_limit = _integer(limit, "limit", minimum=1)
        if normalized_limit > BATTLE_REPORT_MAX_RECORDS:
            raise UserDataValidationError(
                f"limit 不能超过 {BATTLE_REPORT_MAX_RECORDS}"
            )
        normalized_offset = _integer(offset, "offset", minimum=0)
        rows = self._rows(
            _HISTORY_SELECT
            + """
              ORDER BY saved_at_utc DESC, record.battle_record_id DESC
              LIMIT ? OFFSET ?
              """,
            (normalized_limit, normalized_offset),
        )
        return [self._normalized_history_row(row) for row in rows]

    def load_battle_record(self, battle_record_id: int) -> dict[str, Any] | None:
        normalized_id = _integer(
            battle_record_id,
            "battle_record_id",
            minimum=1,
        )
        result = self._battle_record_history_row(normalized_id)
        if result is None:
            return None
        raw_row = self._one(
            """
            SELECT raw_summary_json
            FROM battle_record
            WHERE battle_record_id = ?
            """,
            (normalized_id,),
        )
        if raw_row is None:
            raise UserDataError("战报原始 JSON 不存在")
        raw_json = str(raw_row["raw_summary_json"])
        result["raw_summary_json"] = raw_json
        payload = _decoded(raw_json, None)
        if not isinstance(payload, dict):
            raise UserDataError("战报原始 JSON 顶层不是对象")
        result["raw_summary_payload"] = payload
        return result

    def promote_battle_record_to_manual(
        self,
        battle_record_id: int,
    ) -> dict[str, Any]:
        normalized_id = _integer(
            battle_record_id,
            "battle_record_id",
            minimum=1,
        )
        connection = self._db()
        now = _utc_now()
        pruned_ids: list[int] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            retention = connection.execute(
                """
                SELECT retention_kind
                FROM battle_record_retention
                WHERE battle_record_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if retention is None:
                raise UserDataValidationError("要保存的战报不存在")
            changed = str(retention["retention_kind"]) != "manual"
            if changed:
                connection.execute(
                    """
                    UPDATE battle_record_retention
                    SET retention_kind = 'manual', manual_saved_at_utc = ?,
                        updated_at_utc = ?
                    WHERE battle_record_id = ?
                    """,
                    (now, now, normalized_id),
                )
                pruned_ids = self._prune_oldest_battle_records(
                    retention_kind="manual",
                    maximum=BATTLE_REPORT_MAX_MANUAL_RECORDS,
                    count_all=False,
                    exclude_record_id=normalized_id,
                )
            connection.commit()
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise
        record = self._battle_record_history_row(normalized_id)
        if record is None:
            raise UserDataError("手动保存的当前战报被意外淘汰")
        return {
            "record": record,
            "changed": changed,
            "pruned_battle_record_ids": tuple(pruned_ids),
        }

    def unmark_manual_battle_record(
        self,
        battle_record_id: int,
    ) -> dict[str, Any]:
        normalized_id = _integer(
            battle_record_id,
            "battle_record_id",
            minimum=1,
        )
        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            retention = connection.execute(
                """
                SELECT retention_kind
                FROM battle_record_retention
                WHERE battle_record_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if retention is None:
                raise UserDataValidationError("要取消保存的战报不存在")
            changed = str(retention["retention_kind"]) != "auto"
            if changed:
                connection.execute(
                    """
                    UPDATE battle_record_retention
                    SET retention_kind = 'auto', manual_saved_at_utc = NULL,
                        updated_at_utc = ?
                    WHERE battle_record_id = ?
                    """,
                    (now, normalized_id),
                )
            connection.commit()
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise
        record = self._battle_record_history_row(normalized_id)
        if record is None:
            raise UserDataError("取消手动保存后的战报不存在")
        return {"record": record, "changed": changed}

    def delete_battle_record(self, battle_record_id: int) -> bool:
        normalized_id = _integer(
            battle_record_id,
            "battle_record_id",
            minimum=1,
        )
        cursor = self._db().execute(
            "DELETE FROM battle_record WHERE battle_record_id = ?",
            (normalized_id,),
        )
        self._db().commit()
        return cursor.rowcount > 0

    def battle_report_page_state(self) -> dict[str, Any]:
        row = self._one(
            """
            SELECT last_battle_record_id, last_detail_scope, updated_at_utc,
                   analysis_start_us, analysis_end_us, analysis_character_id
            FROM battle_report_page_state
            WHERE singleton_id = 1
            """
        )
        if row is None:
            return {
                "last_battle_record_id": None,
                "last_detail_scope": "current",
                "updated_at_utc": None,
                "analysis_start_us": None,
                "analysis_end_us": None,
                "analysis_character_id": None,
            }
        return row

    def update_battle_report_page_state(
        self,
        *,
        battle_record_id: int | None,
        detail_scope: str,
    ) -> dict[str, Any]:
        normalized_id = (
            None
            if battle_record_id is None
            else _integer(battle_record_id, "battle_record_id", minimum=1)
        )
        normalized_scope = _required_text(detail_scope, "detail_scope")
        if normalized_scope not in _DETAIL_SCOPES:
            raise UserDataValidationError(
                "detail_scope 必须是 current、first 或 second"
            )
        if normalized_id is not None and self._one(
            "SELECT battle_record_id FROM battle_record WHERE battle_record_id = ?",
            (normalized_id,),
        ) is None:
            raise UserDataValidationError("页面要恢复的战报不存在")
        now = _utc_now()
        self._upsert_battle_report_page_state(
            normalized_id,
            detail_scope=normalized_scope,
            updated_at_utc=now,
        )
        self._db().commit()
        return self.battle_report_page_state()

    def update_battle_report_analysis_state(
        self,
        *,
        battle_record_id: int,
        start_us: int | None,
        end_us: int | None,
        character_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        if (start_us is None) != (end_us is None):
            raise UserDataValidationError("分析时段起止必须同时为空或同时提供")
        if start_us is not None:
            normalized_start = _integer(start_us, "start_us", minimum=0)
            normalized_end = _integer(end_us, "end_us", minimum=1)
            if normalized_end <= normalized_start:
                raise UserDataValidationError("分析时段结束必须晚于开始")
        else:
            normalized_start = None
            normalized_end = None
        normalized_character_id = (
            None
            if character_id is None
            else _integer(character_id, "character_id", minimum=1)
        )
        connection = self._db()
        connection.execute(
            """
            UPDATE battle_report_page_state
            SET last_battle_record_id = ?, analysis_start_us = ?,
                analysis_end_us = ?, analysis_character_id = ?,
                updated_at_utc = ?
            WHERE singleton_id = 1
            """,
            (
                normalized_id,
                normalized_start,
                normalized_end,
                normalized_character_id,
                _utc_now(),
            ),
        )
        connection.commit()
        return self.battle_report_page_state()

    def restore_battle_report_record(self) -> dict[str, Any] | None:
        state = self.battle_report_page_state()
        last_id = state["last_battle_record_id"]
        if last_id is not None:
            record = self.load_battle_record(int(last_id))
            if record is not None:
                record["restored_detail_scope"] = state["last_detail_scope"]
                record["restored_analysis_start_us"] = state["analysis_start_us"]
                record["restored_analysis_end_us"] = state["analysis_end_us"]
                record["restored_analysis_character_id"] = state[
                    "analysis_character_id"
                ]
                return record
        latest = self.list_battle_records(limit=1)
        if not latest:
            return None
        record = self.load_battle_record(int(latest[0]["battle_record_id"]))
        if record is not None:
            record["restored_detail_scope"] = "current"
        return record

    def _upsert_battle_report_page_state(
        self,
        battle_record_id: int | None,
        *,
        detail_scope: str,
        updated_at_utc: str,
    ) -> None:
        self._db().execute(
            """
            INSERT INTO battle_report_page_state(
                singleton_id, last_battle_record_id, last_detail_scope,
                updated_at_utc
            ) VALUES (1, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                last_battle_record_id = excluded.last_battle_record_id,
                last_detail_scope = excluded.last_detail_scope,
                updated_at_utc = excluded.updated_at_utc
            """,
            (battle_record_id, detail_scope, updated_at_utc),
        )

    def _prune_oldest_battle_records(
        self,
        *,
        retention_kind: str,
        maximum: int,
        count_all: bool,
        exclude_record_id: int,
    ) -> list[int]:
        if count_all:
            count_row = self._db().execute(
                "SELECT COUNT(*) AS count FROM battle_record"
            ).fetchone()
        else:
            count_row = self._db().execute(
                """
                SELECT COUNT(*) AS count
                FROM battle_record_retention
                WHERE retention_kind = ?
                """,
                (retention_kind,),
            ).fetchone()
        count = int(count_row["count"] if count_row is not None else 0)
        excess = max(0, count - maximum)
        if excess == 0:
            return []
        order_column = (
            "auto_saved_at_utc"
            if retention_kind == "auto"
            else "manual_saved_at_utc"
        )
        rows = self._db().execute(
            f"""
            SELECT battle_record_id
            FROM battle_record_retention
            WHERE retention_kind = ? AND battle_record_id <> ?
            ORDER BY {order_column} ASC, battle_record_id ASC
            LIMIT ?
            """,
            (retention_kind, exclude_record_id, excess),
        ).fetchall()
        record_ids = [int(row["battle_record_id"]) for row in rows]
        if len(record_ids) != excess:
            raise UserDataError("战报保留上限已超出，但没有足够的可淘汰记录")
        self._db().executemany(
            "DELETE FROM battle_record WHERE battle_record_id = ?",
            [(record_id,) for record_id in record_ids],
        )
        return record_ids
