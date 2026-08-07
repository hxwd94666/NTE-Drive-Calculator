# 持久化最终 nte-core 摘要且不依赖 Qt 页面。
"""Persist final nte-core summaries without depending on Qt or feature pages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from src.domain.battle_report import (
    BattleCharacterSummary,
    BattleSummary,
    BattleSummaryPersistenceOutcome,
)
from src.observability import OperationContext
from src.observability.operation import log_event
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError


@dataclass(frozen=True, slots=True)
class BattleReportPersistenceDependencies:
    account_id: str
    user_database_path: Path
    generation: int


BattleReportContextGuard = Callable[[BattleReportPersistenceDependencies], bool]


class BattleReportPersistenceService:
    """Validate context and atomically add one summary to account history."""

    PAYLOAD_SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        dependencies: BattleReportPersistenceDependencies,
        context_is_current: BattleReportContextGuard,
        operation_context: OperationContext,
    ) -> None:
        self._dependencies = BattleReportPersistenceDependencies(
            account_id=str(dependencies.account_id),
            user_database_path=Path(dependencies.user_database_path).resolve(),
            generation=int(dependencies.generation),
        )
        self._context_is_current = context_is_current
        self._operation_context = operation_context

    def finalize_summary(
        self,
        *,
        raw_summary_payload: Mapping[str, Any],
        summary: BattleSummary,
        capture_operation_id: str,
        captured_at_utc: str,
        finalized_at_utc: str,
    ) -> BattleSummaryPersistenceOutcome:
        if summary.total_damage <= 0 and summary.total_hits <= 0:
            return BattleSummaryPersistenceOutcome(status="skipped_empty")
        if not self._context_is_current(self._dependencies):
            return BattleSummaryPersistenceOutcome(status="discarded_stale")

        try:
            raw_json = json.dumps(
                dict(raw_summary_payload),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise UserDataError("最终战报摘要无法序列化为 JSON") from error
        raw_sha256 = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        character_ids = self._history_character_ids(summary)

        dependencies = self._dependencies
        if not self._context_is_current(dependencies):
            return BattleSummaryPersistenceOutcome(status="discarded_stale")
        if not dependencies.user_database_path.is_file():
            raise UserDataError("冻结账号的用户数据库不存在")
        with UserDataDao(
            dependencies.user_database_path,
            account_id=dependencies.account_id,
            account_name=dependencies.account_id,
        ) as user_dao:
            profile = user_dao.profile()
            if str(profile["account_id"]) != dependencies.account_id:
                raise UserDataError("战报目标数据库与冻结账号不一致")
            if not self._context_is_current(dependencies):
                return BattleSummaryPersistenceOutcome(status="discarded_stale")
            result = user_dao.insert_auto_summary_snapshot(
                capture_operation_id=capture_operation_id,
                combat_context_kind=(
                    "abyss" if summary.abyss.detected else "non_abyss"
                ),
                abyss_floor=(
                    summary.abyss.floor if summary.abyss.detected else None
                ),
                has_first_half=summary.abyss.first_half is not None,
                has_second_half=summary.abyss.second_half is not None,
                captured_at_utc=captured_at_utc,
                finalized_at_utc=finalized_at_utc,
                dps_time_mode=summary.dps_time_mode,
                duration_seconds=summary.duration_seconds,
                total_damage=summary.total_damage,
                total_dps=summary.total_dps,
                total_damage_taken=summary.total_damage_taken,
                total_hits=summary.total_hits,
                character_count=len(character_ids),
                skill_count=len(summary.skills),
                character_ids=character_ids,
                abyss_detected=summary.abyss.detected,
                abyss_success=summary.abyss.success,
                payload_schema_version=self.PAYLOAD_SCHEMA_VERSION,
                raw_summary_json=raw_json,
                raw_summary_sha256=raw_sha256,
            )

        record = result["record"]
        record_id = int(record["battle_record_id"])
        retention_kind = str(record["retention_kind"])
        if retention_kind not in {"auto", "manual"}:
            raise UserDataError("战报保留状态无效")
        pruned_ids = tuple(
            int(item) for item in result["pruned_battle_record_ids"]
        )
        log_event(
            "INFO",
            "battle_report.summary_saved",
            "最终战报摘要已保存",
            self._operation_context,
            phase="persisted",
            battle_record_id=record_id,
            inserted=bool(result["inserted"]),
            retention_kind=retention_kind,
            pruned_record_count=len(pruned_ids),
            total_hits=summary.total_hits,
            character_count=len(character_ids),
            skill_count=len(summary.skills),
        )
        return BattleSummaryPersistenceOutcome(
            status="saved",
            battle_record_id=record_id,
            pruned_battle_record_ids=pruned_ids,
            retention_kind=cast(Literal["auto", "manual"], retention_kind),
        )

    @staticmethod
    def _history_character_ids(summary: BattleSummary) -> tuple[int, ...]:
        ordered: list[int] = []
        seen: set[int] = set()

        def append(characters: tuple[BattleCharacterSummary, ...]) -> None:
            for character in characters:
                character_id = int(character.character_id)
                if character_id not in seen:
                    seen.add(character_id)
                    ordered.append(character_id)

        append(summary.characters)
        for half in (summary.abyss.first_half, summary.abyss.second_half):
            if half is not None:
                append(half.characters)
        return tuple(ordered)
