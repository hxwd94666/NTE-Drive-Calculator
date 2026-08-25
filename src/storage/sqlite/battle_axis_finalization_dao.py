"""Atomic finalized-axis replacement for battle capture staging."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _integer,
    _utc_now,
)


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise UserDataValidationError(f"{label} 不能为空")
    return normalized


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=0)


class BattleAxisFinalizationDaoMixin(UserDataDaoMixinHost):
    """Replace live pages with one generation-consistent finalized axis."""

    def replace_staged_battle_axis(
        self,
        *,
        capture_operation_id: str,
        pages: Sequence[Mapping[str, Any]],
        source_generation: str,
        incomplete_reason: str | None = None,
    ) -> dict[str, Any]:
        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        generation = _required_text(source_generation, "source_generation")
        reason = _optional_text(incomplete_reason)
        if not pages and reason is None:
            raise UserDataValidationError("完整最终轴至少需要一页")
        normalized_pages = [dict(page) for page in pages]
        if any(
            _optional_text(page.get("generation")) != generation
            for page in normalized_pages
        ):
            raise UserDataValidationError("最终逐击页包含不同 generation")
        source_ids = {
            _required_text(page.get("battle_record_id"), "page.battle_record_id")
            for page in normalized_pages
        }
        if len(source_ids) > 1:
            raise UserDataValidationError("最终逐击页包含不同上游战斗记录")

        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            capture = connection.execute(
                """
                SELECT capture_id, capture_state, source_battle_record_id
                FROM battle_axis_capture WHERE capture_operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if capture is None or str(capture["capture_state"]) != "capturing":
                raise UserDataValidationError("逐击目标采集不存在或已经结束")
            capture_id = int(capture["capture_id"])
            if normalized_pages:
                connection.execute(
                    "DELETE FROM battle_hit_evidence WHERE capture_id = ?",
                    (capture_id,),
                )
                for page in normalized_pages:
                    rows = page.get("rows")
                    if not isinstance(rows, Sequence) or isinstance(
                        rows, (str, bytes, bytearray)
                    ):
                        raise UserDataValidationError("最终逐击页 rows 必须是数组")
                    for hit in rows:
                        if not isinstance(hit, Mapping):
                            raise UserDataValidationError("最终逐击页包含无效逐击")
                        self._insert_hit(connection, capture_id, hit)
            stored_hits = int(
                connection.execute(
                    "SELECT COUNT(*) FROM battle_hit_evidence WHERE capture_id = ?",
                    (capture_id,),
                ).fetchone()[0]
            )
            last_page = normalized_pages[-1] if normalized_pages else {}
            source_record_id = (
                next(iter(source_ids))
                if source_ids
                else _optional_text(capture["source_battle_record_id"])
            )
            if normalized_pages:
                connection.execute(
                    """
                    UPDATE battle_axis_capture
                    SET source_battle_record_id = ?, contract_version = ?,
                        source_generation = ?, axis_complete = ?,
                        first_available_cursor = ?, next_cursor = ?,
                        first_sequence = NULL, total_hits = ?, retained_hits = ?,
                        stored_hits = ?, finalization_incomplete_reason = ?,
                        updated_at_utc = ?
                    WHERE capture_id = ?
                    """,
                    (
                        source_record_id,
                        _optional_integer(
                            last_page.get("contract_version"), "contract_version"
                        ),
                        generation,
                        int(bool(last_page.get("complete", False)) and reason is None),
                        _optional_text(last_page.get("first_available_cursor")),
                        _optional_text(last_page.get("next_cursor")),
                        _optional_text(last_page.get("total_hits")),
                        _optional_integer(
                            last_page.get("retained_hits"), "retained_hits"
                        ),
                        stored_hits,
                        reason,
                        now,
                        capture_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE battle_axis_capture
                    SET source_generation = ?, axis_complete = 0,
                        stored_hits = ?, finalization_incomplete_reason = ?,
                        updated_at_utc = ?
                    WHERE capture_id = ?
                    """,
                    (generation, stored_hits, reason, now, capture_id),
                )
            connection.commit()
            return {
                "stored_hits": stored_hits,
                "generation": generation,
                "complete": (
                    bool(last_page.get("complete", False)) and reason is None
                ),
                "incomplete_reason": reason,
            }
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise

    def discard_battle_axis_capture(self, capture_operation_id: str) -> bool:
        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        connection = self._db()
        try:
            cursor = connection.execute(
                """
                DELETE FROM battle_axis_capture
                WHERE capture_operation_id = ? AND capture_state = 'capturing'
                """,
                (operation_id,),
            )
            connection.commit()
            return bool(cursor.rowcount)
        except sqlite3.Error as error:
            connection.rollback()
            raise UserDataError("无法清理未完成的战斗逐击记录") from error
