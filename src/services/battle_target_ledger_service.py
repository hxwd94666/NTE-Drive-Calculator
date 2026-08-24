# 按半场与正式目标实例生成严格闭合、但不篡改逐击事实的生命账本。
"""Pure target-ledger projection for battle analysis."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleMaxHpReductionEvent,
    BattleTargetSummary,
)


_TargetKey = tuple[str, str]


def _half(value: object) -> str:
    return str(value or "").strip().casefold()


def _event_key(
    event: BattleMaxHpReductionEvent,
    hit_keys_by_target: dict[str, set[_TargetKey]],
) -> _TargetKey:
    scope_half = _half(event.scope_half)
    if scope_half:
        return scope_half, event.target_id
    candidates = hit_keys_by_target.get(event.target_id, set())
    if len(candidates) == 1:
        return next(iter(candidates))
    return "", event.target_id


class BattleTargetLedgerService:
    """Summarize each half-scoped target and expose one signed residual."""

    @staticmethod
    def summarize(
        hits: Sequence[BattleAnalysisHit],
        max_hp_events: Sequence[BattleMaxHpReductionEvent],
        estimated_max_hp_events: Sequence[BattleMaxHpReductionEvent],
    ) -> tuple[BattleTargetSummary, ...]:
        grouped: dict[_TargetKey, list[BattleAnalysisHit]] = defaultdict(list)
        hit_keys_by_target: dict[str, set[_TargetKey]] = defaultdict(set)
        for hit in hits:
            if hit.direction != "outgoing":
                continue
            key = (_half(hit.scope_half), hit.target_id)
            grouped[key].append(hit)
            hit_keys_by_target[hit.target_id].add(key)

        event_groups: dict[_TargetKey, list[BattleMaxHpReductionEvent]] = defaultdict(list)
        for event in max_hp_events:
            event_groups[_event_key(event, hit_keys_by_target)].append(event)
        estimate_groups: dict[_TargetKey, list[BattleMaxHpReductionEvent]] = defaultdict(list)
        for event in estimated_max_hp_events:
            estimate_groups[_event_key(event, hit_keys_by_target)].append(event)

        result = []
        for key in grouped.keys() | event_groups.keys() | estimate_groups.keys():
            scope_half, target_id = key
            rows = sorted(
                grouped.get(key, ()),
                key=lambda row: (row.relative_time_us, row.sequence),
            )
            events = event_groups.get(key, ())
            estimates = estimate_groups.get(key, ())
            hp_before = [row.target_hp_before for row in rows if row.target_hp_before is not None]
            hp_after = [row.target_hp_after for row in rows if row.target_hp_after is not None]
            max_values = [row.target_max_hp for row in rows if row.target_max_hp is not None]
            initial_hp = hp_before[0] if hp_before else None
            terminal_hp = min(hp_after) if hp_after else None
            observed_hp_loss = (
                max(0.0, initial_hp - terminal_hp)
                if initial_hp is not None and terminal_hp is not None
                else 0.0
            )
            direct_damage = sum(row.damage for row in rows)
            settlement_damage = sum(row.effective_hp_loss for row in events)
            effective_damage = direct_damage + settlement_damage
            result.append(BattleTargetSummary(
                target_id=target_id,
                target_name=next(
                    (row.target_name for row in rows if row.target_name != "未知目标"),
                    next(
                        (event.target_name for event in events),
                        next(
                            (event.target_name for event in estimates),
                            "未知目标",
                        ),
                    ),
                ),
                hits=len(rows),
                damage=direct_damage,
                first_hp=initial_hp,
                last_hp=hp_after[-1] if hp_after else None,
                max_hp=max(max_values) if max_values else None,
                max_hp_reduction=sum(row.max_hp_reduction for row in events),
                max_hp_reduction_damage=settlement_damage,
                effective_damage=effective_damage,
                estimated_max_hp_reduction_damage=sum(
                    row.effective_hp_loss for row in estimates
                ),
                scope_half=scope_half,
                initial_hp=initial_hp,
                terminal_hp=terminal_hp,
                observed_hp_loss=observed_hp_loss,
                unexplained_hp_delta=observed_hp_loss - effective_damage,
            ))
        half_order = {"upper": 0, "lower": 1}
        return tuple(sorted(
            result,
            key=lambda row: (
                half_order.get(row.scope_half, 2),
                -row.effective_damage,
                row.target_id,
            ),
        ))
