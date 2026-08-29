# 将版本化自动目标推断与用户确认条件分开持久化。
"""Persist versioned automatic target inference separately from user confirmation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    UserDataValidationError,
    _decoded,
    _integer,
    _json_object,
    _utc_now,
)


class BattleInferredTargetDaoMixin(UserDataDaoMixinHost):
    """Own the account-private derived target snapshot for one battle."""

    def load_battle_inferred_target_snapshot(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        row = self._one(
            """
            SELECT * FROM battle_inferred_target_snapshot
            WHERE battle_record_id = ?
            """,
            (record_id,),
        )
        if row is None:
            return None
        row["inferred_payload"] = _decoded(
            row.pop("inferred_payload_json"),
            {},
        )
        return row

    def save_battle_inferred_target_snapshot(
        self,
        *,
        battle_record_id: int,
        payload_schema_version: int,
        algorithm_version: str,
        static_dataset_id: str | None,
        static_schema_version: int | None,
        inference_status: str,
        environment_kind: str,
        environment_ref: str,
        environment_name: str,
        source_kind: str,
        confidence: str,
        inferred_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        payload_version = _integer(
            payload_schema_version,
            "payload_schema_version",
            minimum=1,
        )
        algorithm = str(algorithm_version or "").strip()
        if not algorithm:
            raise UserDataValidationError("目标推断算法版本不能为空")
        status = str(inference_status or "").strip()
        if status not in {"resolved", "unresolved"}:
            raise UserDataValidationError("目标推断状态无效")
        dataset_id = str(static_dataset_id or "").strip() or None
        schema_version = (
            None
            if static_schema_version is None
            else _integer(
                static_schema_version,
                "static_schema_version",
                minimum=1,
            )
        )
        payload_json = _json_object(inferred_payload, "目标推断快照")
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM battle_record WHERE battle_record_id = ?",
                (record_id,),
            ).fetchone() is None:
                raise UserDataValidationError("战报记录不存在")
            connection.execute(
                """
                INSERT INTO battle_inferred_target_snapshot (
                    battle_record_id,
                    payload_schema_version,
                    algorithm_version,
                    static_dataset_id,
                    static_schema_version,
                    inference_status,
                    environment_kind,
                    environment_ref,
                    environment_name,
                    source_kind,
                    confidence,
                    inferred_payload_json,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(battle_record_id) DO UPDATE SET
                    payload_schema_version = excluded.payload_schema_version,
                    algorithm_version = excluded.algorithm_version,
                    static_dataset_id = excluded.static_dataset_id,
                    static_schema_version = excluded.static_schema_version,
                    inference_status = excluded.inference_status,
                    environment_kind = excluded.environment_kind,
                    environment_ref = excluded.environment_ref,
                    environment_name = excluded.environment_name,
                    source_kind = excluded.source_kind,
                    confidence = excluded.confidence,
                    inferred_payload_json = excluded.inferred_payload_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    record_id,
                    payload_version,
                    algorithm,
                    dataset_id,
                    schema_version,
                    status,
                    str(environment_kind or "").strip() or None,
                    str(environment_ref or "").strip() or None,
                    str(environment_name or "").strip() or None,
                    str(source_kind or "").strip() or None,
                    str(confidence or "").strip() or None,
                    payload_json,
                    _utc_now(),
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        saved = self.load_battle_inferred_target_snapshot(record_id)
        if saved is None:
            raise RuntimeError("目标推断快照保存后丢失")
        return saved
