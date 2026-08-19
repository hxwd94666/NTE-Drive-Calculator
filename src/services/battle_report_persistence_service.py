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
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError


@dataclass(frozen=True, slots=True)
class BattleReportPersistenceDependencies:
    account_id: str
    user_database_path: Path
    generation: int
    static_database_path: Path | None = None


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
            static_database_path=(
                None
                if dependencies.static_database_path is None
                else Path(dependencies.static_database_path).resolve()
            ),
        )
        self._context_is_current = context_is_current
        self._operation_context = operation_context

    def begin_capture(
        self,
        *,
        capture_operation_id: str,
        captured_at_utc: str,
    ) -> None:
        """Create durable axis staging without choosing the post-battle build yet."""

        dependencies = self._dependencies
        if not self._context_is_current(dependencies):
            raise UserDataError("战报账号上下文已经变化")
        with UserDataDao(
            dependencies.user_database_path,
            account_id=dependencies.account_id,
            account_name=dependencies.account_id,
        ) as user_dao:
            database_profile = user_dao.profile()
            if str(database_profile["account_id"]) != dependencies.account_id:
                raise UserDataError("战报目标数据库与冻结账号不一致")
            if not self._context_is_current(dependencies):
                raise UserDataError("战报账号上下文已经变化")
            user_dao.begin_battle_axis_capture(
                capture_operation_id=capture_operation_id,
                captured_at_utc=captured_at_utc,
                account_generation=dependencies.generation,
            )

    @staticmethod
    def _load_effective_profiles(
        *,
        static_dao: StaticGameDataDao,
        user_dao: UserDataDao,
    ) -> dict[int, dict[str, Any]]:
        profiles: dict[int, dict[str, Any]] = {}
        for template in static_dao.list_character_graduation_templates():
            character_id = int(template["character_id"])
            profile = dict(template.get("profile") or {})
            profile["character_id"] = character_id
            profile["profile_source"] = "official_graduation"
            profiles[character_id] = profile
        for saved in user_dao.list_character_profiles(include_inactive=True):
            character_id = int(saved["character_id"])
            if character_id not in profiles:
                continue
            profile = dict(saved)
            profile["profile_source"] = "account_role_page"
            profiles[character_id] = profile
        return profiles

    def append_axis_page(
        self,
        *,
        capture_operation_id: str,
        page: Mapping[str, Any],
    ) -> None:
        dependencies = self._dependencies
        if not self._context_is_current(dependencies):
            raise UserDataError("战报账号上下文已经变化")
        with UserDataDao(
            dependencies.user_database_path,
            account_id=dependencies.account_id,
            account_name=dependencies.account_id,
        ) as user_dao:
            user_dao.append_battle_axis_page(
                capture_operation_id=capture_operation_id,
                page=page,
            )

    def discard_capture(self, *, capture_operation_id: str) -> None:
        dependencies = self._dependencies
        if not dependencies.user_database_path.is_file():
            return
        with UserDataDao(
            dependencies.user_database_path,
            account_id=dependencies.account_id,
            account_name=dependencies.account_id,
        ) as user_dao:
            user_dao.discard_battle_axis_capture(capture_operation_id)

    def finalize_summary(
        self,
        *,
        raw_summary_payload: Mapping[str, Any],
        summary: BattleSummary,
        capture_operation_id: str,
        captured_at_utc: str,
        finalized_at_utc: str,
        raw_record_payload: Mapping[str, Any] | None = None,
    ) -> BattleSummaryPersistenceOutcome:
        if summary.total_damage <= 0 and summary.total_hits <= 0:
            self.discard_capture(capture_operation_id=capture_operation_id)
            return BattleSummaryPersistenceOutcome(status="skipped_empty")
        if not self._context_is_current(self._dependencies):
            self.discard_capture(capture_operation_id=capture_operation_id)
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
            capture_state = user_dao.battle_axis_capture_state(capture_operation_id)
            post_battle_build: dict[str, Any] | None = None
            if capture_state is not None:
                if dependencies.static_database_path is None:
                    raise UserDataError("战报采集缺少静态数据库路径")
                snapshot_id = user_dao.latest_native_inventory_snapshot_id()
                if snapshot_id is None:
                    raise UserDataError("战后没有可保存的完整游戏原生背包快照")
                with StaticGameDataDao(dependencies.static_database_path) as static_dao:
                    static_summary = static_dao.summary()
                    dataset = dict(static_summary.get("dataset") or {})
                    post_battle_build = {
                        "snapshot_id": snapshot_id,
                        "dataset_id": str(dataset.get("dataset_id") or "") or None,
                        "static_schema_version": int(static_summary["schema_version"]),
                        "profiles": self._load_effective_profiles(
                            static_dao=static_dao,
                            user_dao=user_dao,
                        ),
                    }
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
            if post_battle_build is not None:
                user_dao.finalize_battle_axis_capture(
                    capture_operation_id=capture_operation_id,
                    battle_record_id=int(result["record"]["battle_record_id"]),
                    record=raw_record_payload,
                    observed_characters=self._observed_characters(summary),
                    source_inventory_snapshot_id=int(post_battle_build["snapshot_id"]),
                    static_dataset_id=cast(
                        str | None,
                        post_battle_build["dataset_id"],
                    ),
                    static_schema_version=int(
                        post_battle_build["static_schema_version"]
                    ),
                    character_profiles=cast(
                        Mapping[int, Mapping[str, Any]],
                        post_battle_build["profiles"],
                    ),
                    finalized_at_utc=finalized_at_utc,
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

    @staticmethod
    def _observed_characters(summary: BattleSummary) -> dict[int, str]:
        observed: dict[int, str] = {}
        for characters in (
            summary.characters,
            *(
                half.characters
                for half in (summary.abyss.first_half, summary.abyss.second_half)
                if half is not None
            ),
        ):
            for character in characters:
                observed.setdefault(int(character.character_id), character.name)
        return observed
