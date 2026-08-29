# 一次建立 Buff 反事实组与逐击覆盖关系，避免逐组扫描整条逐击轴。
"""Request-scoped work planning for Buff counterfactual calculations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_interval_index import BattleBuffIntervalIndex


def battle_buff_counterfactual_key(
    interval: BattleInferredBuffInterval,
) -> str:
    """Return the stable source identity used by Service and presentation."""

    source_identity = (
        getattr(interval, "source_effect_definition_id", "")
        or getattr(interval, "buff_asset_path", "")
        or getattr(interval, "buff_name", "")
    )
    return "\x1f".join((
        str(getattr(interval, "source_character_id", 0)),
        source_identity,
        getattr(interval, "buff_asset_path", ""),
        getattr(interval, "target_scope", "unknown"),
    ))


@dataclass(frozen=True, slots=True)
class BattleBuffCounterfactualGroupPlan:
    group_key: str
    intervals: tuple[BattleInferredBuffInterval, ...]
    active_hits: tuple[BattleAnalysisHit, ...]


class BattleBuffCounterfactualPlanService:
    """Invert hit-to-interval coverage into stable Buff-group work units."""

    @staticmethod
    def prepare(
        outgoing_hits: Sequence[BattleAnalysisHit],
        intervals: Sequence[BattleInferredBuffInterval],
        interval_index: BattleBuffIntervalIndex,
    ) -> tuple[BattleBuffCounterfactualGroupPlan, ...]:
        intervals_by_group: dict[str, list[BattleInferredBuffInterval]] = {}
        group_by_interval_id: dict[str, str] = {}
        for interval in intervals:
            if interval.source_kind == "candidate_derived_awakening_settlement":
                continue
            group_key = battle_buff_counterfactual_key(interval)
            intervals_by_group.setdefault(group_key, []).append(interval)
            group_by_interval_id[interval.interval_id] = group_key

        active_hits_by_group: dict[str, list[BattleAnalysisHit]] = {
            group_key: [] for group_key in intervals_by_group
        }
        for hit in outgoing_hits:
            active_group_keys = tuple(dict.fromkeys(
                group_key
                for interval in interval_index.active_for_hit(hit)
                if (
                    group_key := group_by_interval_id.get(interval.interval_id)
                ) is not None
            ))
            for group_key in active_group_keys:
                active_hits_by_group[group_key].append(hit)

        return tuple(
            BattleBuffCounterfactualGroupPlan(
                group_key=group_key,
                intervals=tuple(group_intervals),
                active_hits=tuple(active_hits_by_group[group_key]),
            )
            for group_key, group_intervals in sorted(
                intervals_by_group.items(),
                key=lambda item: (
                    item[1][0].source_character_name,
                    item[1][0].buff_name,
                    item[0],
                ),
            )
        )


__all__ = [
    "BattleBuffCounterfactualGroupPlan",
    "BattleBuffCounterfactualPlanService",
    "battle_buff_counterfactual_key",
]
