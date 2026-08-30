# 覆纹公式统一绑定同一正式事件中的原伤害来源。
"""Shared source resolution for recorded damage consumed by Weave."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report import BattleAnalysisHit


def find_paired_weave_source_hit(
    hit: BattleAnalysisHit,
    hits: Sequence[BattleAnalysisHit],
) -> BattleAnalysisHit | None:
    """Return the original hit whose damage and source Weave records."""

    return next(
        (
            row
            for row in hits
            if row.sequence == hit.sequence
            and row.target_id == hit.target_id
            and row.scope_half == hit.scope_half
            and row.direction == hit.direction
            and not row.is_follow_up
            and row.classification != "weave"
            and row.damage > 0.0
        ),
        None,
    )
