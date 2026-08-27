# 将战报存储行投影为 Qt 无关的历史展示领域对象。
"""Pure stored-row projections shared by battle-report history workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from src.domain.battle_report import (
    BattleReportHistoryEntry,
    BattleRetentionMutation,
    StoredBattleSummary,
)
from src.integrations.nte_core_battle import parse_battle_summary


def character_analysis_scopes(
    evidence: Mapping[str, Any] | None,
) -> dict[int, str | None]:
    """Map each observed role to its unique outer-realm half selector."""

    scopes: dict[int, set[str]] = {}
    for hit in (evidence or {}).get("hits") or ():
        character_id = hit.get("character_id")
        half = str(hit.get("abyss_half") or "").strip().casefold()
        if character_id is None or half not in {"upper", "lower"}:
            continue
        scopes.setdefault(int(character_id), set()).add(
            "first" if half == "upper" else "second"
        )
    return {
        character_id: next(iter(role_scopes)) if len(role_scopes) == 1 else None
        for character_id, role_scopes in scopes.items()
    }


def analysis_scope_range(
    evidence: Mapping[str, Any] | None,
    raw_summary_payload: Mapping[str, Any],
    detail_scope: str | None,
) -> tuple[int, int] | None:
    """Resolve a persisted half selector to one half-open axis range."""

    scope = str(detail_scope or "").casefold()
    if scope == "first":
        selected_half = "upper"
    elif scope == "second":
        selected_half = "lower"
    elif scope == "current":
        active = str(
            (raw_summary_payload.get("abyss") or {}).get("active_half") or ""
        ).casefold()
        if "ascending" in active or "first" in active or "upper" in active or "上" in active:
            selected_half = "upper"
        elif "descending" in active or "second" in active or "lower" in active or "下" in active:
            selected_half = "lower"
        else:
            return None
    else:
        return None

    rows = tuple((evidence or {}).get("hits") or ())
    selected_times = tuple(
        int(row.get("relative_time_us") or 0)
        for row in rows
        if str(row.get("abyss_half") or "").casefold() == selected_half
    )
    if not selected_times:
        return None
    start_us = min(selected_times)
    later_other_times = tuple(
        int(row.get("relative_time_us") or 0)
        for row in rows
        if str(row.get("abyss_half") or "").casefold() != selected_half
        and int(row.get("relative_time_us") or 0) > start_us
    )
    all_times = tuple(int(row.get("relative_time_us") or 0) for row in rows)
    end_us = min(later_other_times) if later_other_times else max(all_times) + 1
    return start_us, max(start_us + 1, end_us)


def stored_summary(record: dict) -> StoredBattleSummary:
    scope = str(record.get("restored_detail_scope") or "current")
    if scope not in {"current", "first", "second"}:
        scope = "current"
    retention_kind = str(record["retention_kind"])
    if retention_kind not in {"auto", "manual"}:
        raise RuntimeError("战报保留状态无效")
    return StoredBattleSummary(
        battle_record_id=int(record["battle_record_id"]),
        retention_kind=cast(Literal["auto", "manual"], retention_kind),
        saved_at_utc=str(record["saved_at_utc"]),
        detail_scope=cast(Literal["current", "first", "second"], scope),
        summary=parse_battle_summary(record["raw_summary_payload"]),
        nte_core_version=(
            str(record["nte_core_version"])
            if record.get("nte_core_version") is not None
            else None
        ),
        nte_core_protocol_version=(
            int(record["nte_core_protocol_version"])
            if record.get("nte_core_protocol_version") is not None
            else None
        ),
        nte_core_data_version=(
            str(record["nte_core_data_version"])
            if record.get("nte_core_data_version") is not None
            else None
        ),
        nte_core_executable_sha256=(
            str(record["nte_core_executable_sha256"])
            if record.get("nte_core_executable_sha256") is not None
            else None
        ),
        analysis_start_us=(
            None
            if record.get("restored_analysis_start_us") is None
            else int(record["restored_analysis_start_us"])
        ),
        analysis_end_us=(
            None
            if record.get("restored_analysis_end_us") is None
            else int(record["restored_analysis_end_us"])
        ),
        analysis_character_id=(
            None
            if record.get("restored_analysis_character_id") is None
            else int(record["restored_analysis_character_id"])
        ),
    )


def history_entry(record: dict) -> BattleReportHistoryEntry:
    retention_kind = str(record["retention_kind"])
    if retention_kind not in {"auto", "manual"}:
        raise RuntimeError("战报保留状态无效")
    context_kind = str(record["combat_context_kind"])
    if context_kind not in {"abyss", "non_abyss"}:
        raise RuntimeError("战报上下文状态无效")
    floor = record["abyss_floor"]
    return BattleReportHistoryEntry(
        battle_record_id=int(record["battle_record_id"]),
        retention_kind=cast(Literal["auto", "manual"], retention_kind),
        saved_at_utc=str(record["saved_at_utc"]),
        combat_context_kind=cast(Literal["abyss", "non_abyss"], context_kind),
        abyss_floor=None if floor is None else int(floor),
        has_first_half=bool(record["has_first_half"]),
        has_second_half=bool(record["has_second_half"]),
        character_ids=tuple(int(item) for item in record["character_ids"]),
        total_damage=float(record["total_damage"]),
        total_dps=float(record["total_dps"]),
        duration_seconds=float(record["duration_seconds"]),
        total_hits=int(record["total_hits"]),
        capability_level=str(record["capability_level"]),
        source_kind=str(record["source_kind"]),
    )


def retention_mutation(result: dict) -> BattleRetentionMutation:
    record = result["record"]
    retention_kind = str(record["retention_kind"])
    if retention_kind not in {"auto", "manual"}:
        raise RuntimeError("战报保留状态无效")
    return BattleRetentionMutation(
        battle_record_id=int(record["battle_record_id"]),
        retention_kind=cast(Literal["auto", "manual"], retention_kind),
        changed=bool(result["changed"]),
        pruned_battle_record_ids=tuple(
            int(item) for item in result.get("pruned_battle_record_ids", ())
        ),
    )
