# 仅在完整逐击轴形成唯一数值闭环时合并共享生命池的目标别名。
"""Remove duplicated settlement residuals from derived battle analysis only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any


HP_POOL_RECONCILIATION_VERSION = "battle-target-hp-pool-reconciliation-v1"

_RESIDUAL_DAMAGE_NAME = "Server settlement residual"
_AMOUNT_TOLERANCE = 0.5
_TERMINAL_HP_TOLERANCE = 1.0
_MAX_SEQUENCE_LAG = 3


@dataclass(frozen=True, slots=True)
class BattleTargetHpPoolReconciliation:
    """One fail-closed projection over immutable Core axis rows."""

    rows: tuple[Mapping[str, Any], ...]
    applied: bool = False
    primary_target_id: str = ""
    alias_target_id: str = ""
    residual_sequence: int = 0
    residual_damage: float = 0.0
    alias_damage: float = 0.0
    overlap_correction: float = 0.0
    attributed_character_ids: tuple[int, ...] = ()
    evidence_basis: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _sequence(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("sequence_order") or row.get("sequence_text") or 0)
    except (TypeError, ValueError):
        return 0


def _target_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("target_id"))


def _is_outgoing(row: Mapping[str, Any]) -> bool:
    return _text(row.get("direction")).casefold() == "outgoing"


def _character_id(row: Mapping[str, Any]) -> int | None:
    try:
        value = int(row.get("character_id"))
    except (TypeError, ValueError):
        return None
    known = bool(row.get("character_known", value > 0))
    return value if known and value > 0 else None


def _effective_damage(row: Mapping[str, Any]) -> float:
    primary = max(0.0, _number(row.get("damage")) or 0.0)
    overkill = min(
        primary,
        max(0.0, _number(row.get("overkill_damage")) or 0.0),
    )
    follow_up = max(0.0, _number(row.get("follow_up_damage")) or 0.0)
    return primary - overkill + follow_up


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= _AMOUNT_TOLERANCE


def _is_residual(row: Mapping[str, Any]) -> bool:
    damage = _number(row.get("damage"))
    before = _number(row.get("target_hp_before"))
    after = _number(row.get("target_hp_after"))
    return (
        _is_outgoing(row)
        and _text(row.get("damage_name")) == _RESIDUAL_DAMAGE_NAME
        and _character_id(row) is None
        and bool(_target_id(row))
        and damage is not None
        and damage > 0.0
        and before is not None
        and after is not None
        and _close(before, damage)
        and abs(after) <= _AMOUNT_TOLERANCE
        and (_number(row.get("follow_up_damage")) or 0.0) == 0.0
        and (_number(row.get("overkill_damage")) or 0.0) == 0.0
    )


def _same_max_hp(rows: Sequence[Mapping[str, Any]], maximum: float) -> bool:
    values = tuple(_number(row.get("target_max_hp")) for row in rows)
    return bool(values) and all(
        value is not None and _close(value, maximum) for value in values
    )


def _ordered_gap_match(
    alias_rows: Sequence[Mapping[str, Any]],
    gaps: Sequence[tuple[int, float]],
) -> bool:
    alias_index = 0
    previous_gap_sequence = 0
    for gap_sequence, gap_amount in gaps:
        subtotal = 0.0
        last_alias_sequence = 0
        while alias_index < len(alias_rows) and subtotal < gap_amount - _AMOUNT_TOLERANCE:
            alias = alias_rows[alias_index]
            alias_sequence = _sequence(alias)
            if (
                alias_sequence <= max(last_alias_sequence, previous_gap_sequence)
                or alias_sequence >= gap_sequence
            ):
                return False
            subtotal += _effective_damage(alias)
            if subtotal > gap_amount + _AMOUNT_TOLERANCE:
                return False
            last_alias_sequence = alias_sequence
            alias_index += 1
        if (
            not _close(subtotal, gap_amount)
            or last_alias_sequence <= 0
            or gap_sequence - last_alias_sequence > _MAX_SEQUENCE_LAG
        ):
            return False
        previous_gap_sequence = gap_sequence
    return alias_index == len(alias_rows)


class BattleTargetHpPoolReconciliationService:
    """Reconcile one duplicated target lifecycle without changing raw evidence."""

    @classmethod
    def reconcile(
        cls,
        rows: Sequence[Mapping[str, Any]],
        *,
        axis_complete: bool,
    ) -> BattleTargetHpPoolReconciliation:
        source = tuple(rows)
        unresolved = BattleTargetHpPoolReconciliation(rows=source)
        if not axis_complete:
            return unresolved

        residuals = tuple(row for row in source if _is_residual(row))
        if len(residuals) != 1:
            return unresolved
        residual = residuals[0]
        primary_target_id = _target_id(residual)
        maximum = _number(residual.get("target_max_hp"))
        if maximum is None or maximum <= 0.0:
            return unresolved

        primary_rows = tuple(
            row
            for row in source
            if row is not residual
            and _is_outgoing(row)
            and _target_id(row) == primary_target_id
            and _effective_damage(row) > 0.0
        )
        if (
            not primary_rows
            or not _same_max_hp(primary_rows, maximum)
            or any(row.get("overkill_damage") is None for row in primary_rows)
        ):
            return unresolved

        vital_primary_rows = tuple(
            row
            for row in primary_rows
            if _number(row.get("target_hp_before")) is not None
            and _number(row.get("target_hp_after")) is not None
        )
        if not vital_primary_rows:
            return unresolved
        first_before = _number(
            min(vital_primary_rows, key=_sequence).get("target_hp_before")
        )
        if first_before is None or not _close(first_before, maximum):
            return unresolved
        if not any(
            abs(_number(row.get("target_hp_after")) or 0.0)
            <= _AMOUNT_TOLERANCE
            for row in vital_primary_rows
        ):
            return unresolved

        gaps: list[tuple[int, float]] = []
        for row in sorted(vital_primary_rows, key=_sequence):
            before = _number(row.get("target_hp_before"))
            after = _number(row.get("target_hp_after"))
            assert before is not None and after is not None
            hp_loss = before - after
            gap = hp_loss - _effective_damage(row)
            if gap > _TERMINAL_HP_TOLERANCE + _AMOUNT_TOLERANCE:
                gaps.append((_sequence(row), gap))
        if not gaps:
            return unresolved

        target_ids = {
            _target_id(row)
            for row in source
            if _is_outgoing(row)
            and _effective_damage(row) > 0.0
            and _target_id(row)
            and _target_id(row) != primary_target_id
        }
        matches: list[tuple[str, tuple[Mapping[str, Any], ...], int]] = []
        gap_total = sum(amount for _, amount in gaps)
        for alias_target_id in sorted(target_ids):
            alias_rows = tuple(
                sorted(
                    (
                        row
                        for row in source
                        if _is_outgoing(row)
                        and _target_id(row) == alias_target_id
                        and _effective_damage(row) > 0.0
                    ),
                    key=_sequence,
                )
            )
            character_ids = {
                character_id
                for row in alias_rows
                if (character_id := _character_id(row)) is not None
            }
            if (
                not alias_rows
                or len(character_ids) != 1
                or any(_character_id(row) is None for row in alias_rows)
                or any(row.get("overkill_damage") is None for row in alias_rows)
                or not _same_max_hp(alias_rows, maximum)
                or not _close(sum(map(_effective_damage, alias_rows)), gap_total)
                or not _ordered_gap_match(alias_rows, gaps)
            ):
                continue
            matches.append((alias_target_id, alias_rows, next(iter(character_ids))))
        if len(matches) != 1:
            return unresolved

        alias_target_id, alias_rows, alias_character_id = matches[0]
        alias_damage = sum(map(_effective_damage, alias_rows))
        residual_damage = _effective_damage(residual)
        logical_rows = tuple(
            row
            for row in source
            if row is not residual
            and _is_outgoing(row)
            and _target_id(row) in {primary_target_id, alias_target_id}
            and _effective_damage(row) > 0.0
        )
        if (
            not logical_rows
            or not _same_max_hp(logical_rows, maximum)
            or any(row.get("overkill_damage") is None for row in logical_rows)
        ):
            return unresolved
        overlap_correction = sum(map(_effective_damage, logical_rows)) - maximum
        if (
            overlap_correction < -_AMOUNT_TOLERANCE
            or not _close(alias_damage - residual_damage, overlap_correction)
        ):
            return unresolved
        overlap_correction = max(0.0, overlap_correction)

        projected = [dict(row) for row in source if row is not residual]
        primary_name = next(
            (
                _text(row.get("target_name"))
                for row in primary_rows
                if _text(row.get("target_name"))
            ),
            _text(residual.get("target_name")),
        )
        for row in projected:
            if _target_id(row) == alias_target_id:
                row["target_id"] = primary_target_id
                if primary_name:
                    row["target_name"] = primary_name

        remaining = overlap_correction
        for row in sorted(projected, key=_sequence, reverse=True):
            if remaining <= _AMOUNT_TOLERANCE:
                break
            if (
                not _is_outgoing(row)
                or _target_id(row) != primary_target_id
            ):
                continue
            primary = max(0.0, _number(row.get("damage")) or 0.0)
            core_overkill = min(
                primary,
                max(0.0, _number(row.get("overkill_damage")) or 0.0),
            )
            available = primary - core_overkill
            if available <= 0.0:
                continue
            correction = min(available, remaining)
            row["_calc_damage_overlap_correction"] = correction
            row["_calc_damage_correction_kind"] = (
                "calc_hp_pool_alias_reconciliation_v1"
            )
            row["_calc_damage_correction_confidence"] = "高"
            row["_calc_damage_correction_basis"] = (
                "完整逐击轴中两个目标 wire ID 共用同一生命池；别名逐击按顺序、"
                "金额和 HP 缺口唯一闭合，服务器残差是重复生命周期结算。"
            )
            remaining -= correction
        if remaining > _AMOUNT_TOLERANCE:
            return unresolved

        return BattleTargetHpPoolReconciliation(
            rows=tuple(projected),
            applied=True,
            primary_target_id=primary_target_id,
            alias_target_id=alias_target_id,
            residual_sequence=_sequence(residual),
            residual_damage=residual_damage,
            alias_damage=alias_damage,
            overlap_correction=overlap_correction,
            attributed_character_ids=(alias_character_id,),
            evidence_basis=(
                "完整轴、同最大生命、逐组顺序金额闭合且候选唯一；原始 Core 行未改写。"
            ),
        )
