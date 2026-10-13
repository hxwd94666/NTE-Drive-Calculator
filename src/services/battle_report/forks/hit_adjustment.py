# 在通用 Buff 投影后应用必须逐目标读取本击状态的弧盘修正。
"""Per-hit fork adjustments kept outside interval generation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitBuffProjection,
    BattleInferredBuffInterval,
)


FORK_HIT_ADJUSTMENT_MODEL_VERSION = "battle-fork-hit-adjustment-v1"
BOXING_CANDY_REQUIREMENT_PREFIX = "battle-fork|boxing-candy-low-hp="
_BOXING_CANDY_MARKER = "upgradestar_pack_fork_boxingcandy"


class BattleForkHitAdjustmentService:
    """Apply target-specific fork values without leaking across same-time hits."""

    @staticmethod
    def adjust_projection(
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
        projection: BattleHitBuffProjection,
    ) -> BattleHitBuffProjection:
        active = tuple(
            row for row in intervals
            if _BOXING_CANDY_MARKER
            in row.source_effect_definition_id.casefold()
            and row.source_character_id == hit.character_id
            and row.start_us <= hit.relative_time_us < row.end_us
        )
        if not active:
            return projection
        hp = hit.target_hp_before
        maximum = hit.target_max_hp
        if hp is None or maximum is None or maximum <= 0.0:
            return replace(
                projection,
                exclusion_reasons=tuple(dict.fromkeys((
                    *projection.exclusion_reasons,
                    "不屈之绵缺少本击目标结算前生命，保留基础档且不推断强化档",
                ))),
            )
        if hp / maximum >= 0.5:
            return projection
        interval_ids = {row.interval_id for row in active}
        delta_by_interval: dict[str, float] = {}
        for interval in active:
            for modifier in interval.modifiers:
                requirement = modifier.application_requirement_asset_path
                if not requirement.startswith(BOXING_CANDY_REQUIREMENT_PREFIX):
                    continue
                enhanced = float(
                    requirement[len(BOXING_CANDY_REQUIREMENT_PREFIX):]
                )
                base = float(modifier.magnitude_value or 0.0)
                delta_by_interval[interval.interval_id] = enhanced - base
        if not delta_by_interval:
            return projection
        adjusted = []
        for modifier in projection.modifiers:
            matching = interval_ids.intersection(modifier.interval_ids)
            delta = sum(
                delta_by_interval.get(interval_id, 0.0)
                for interval_id in matching
            )
            adjusted.append(replace(
                modifier,
                additive_value=modifier.additive_value + delta,
            ))
        return replace(projection, modifiers=tuple(adjusted))
