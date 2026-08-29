# 为历史战报列表投影已确认或自动推断的环境名称。
"""Qt-free derived environment projection for battle history entries."""

from __future__ import annotations

import sqlite3

from src.domain.battle_report import (
    BattleReportHistoryEntry,
    BattleTargetCondition,
)
from src.services.battle_environment_condition_service import (
    resolve_battle_target_condition,
)
from src.services.battle_inferred_target_snapshot_service import (
    BattleInferredTargetSnapshotService,
)
from src.services.battle_report_history_projection import history_entry
from src.storage.sqlite.user_data_dao import UserDataDao


def _confirmed_environment_name(condition: BattleTargetCondition) -> str:
    target_name = str(condition.target_name or "").strip()
    kind = str(condition.environment_kind or "").strip()
    prefix = {
        "open_world": "大世界",
        "outer_realm": "轨外之境",
        "feast": "争锋赏宴",
    }.get(kind)
    if prefix is None:
        prefix = "大世界" if condition.scene == "open_world" else "轨外之境"
    if not target_name or target_name.startswith(prefix):
        return target_name or prefix
    return f"{prefix} · {target_name}"


def list_history_entries(
    *,
    user_dao: UserDataDao,
    static_dataset_id: str | None = None,
    static_schema_version: int | None = None,
) -> tuple[BattleReportHistoryEntry, ...]:
    """Return stored rows without synchronously inferring old reports."""

    result = []
    for record in user_dao.list_battle_records():
        record_id = int(record["battle_record_id"])
        try:
            condition = resolve_battle_target_condition(
                user_dao.load_battle_target_condition(record_id)
            )
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            condition = None
        if condition is not None:
            result.append(
                history_entry(
                    record,
                    environment_name=_confirmed_environment_name(condition),
                    environment_source="user_confirmed",
                    environment_confidence="高",
                )
            )
            continue

        snapshot = user_dao.load_battle_inferred_target_snapshot(record_id)
        if not BattleInferredTargetSnapshotService.is_current_row(
            snapshot,
            static_dataset_id=static_dataset_id,
            static_schema_version=static_schema_version,
        ):
            snapshot = None
        result.append(
            history_entry(
                record,
                environment_name=(
                    "" if snapshot is None else str(snapshot.get("environment_name") or "")
                ),
                environment_source=("" if snapshot is None else "inferred"),
                environment_confidence=(
                    "" if snapshot is None else str(snapshot.get("confidence") or "")
                ),
            )
        )
    return tuple(result)


__all__ = ["list_history_entries"]
