# 覆纹公式统一绑定同一正式事件中的原伤害来源。
"""Shared source resolution for recorded damage consumed by Weave."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report import BattleAnalysisHit


# 线序号只在半场、方向与目标范围内有意义；与技能证据共享同一配对键。
def weave_source_key(hit: BattleAnalysisHit) -> tuple[int, str, str, str]:
    return hit.sequence, hit.target_id, hit.scope_half, hit.direction


def _is_source(hit: BattleAnalysisHit) -> bool:
    return not hit.is_follow_up and hit.classification != "weave" and hit.damage > 0.0


class BattleWeaveSourceIndex:
    """Keep the first eligible source in one frozen, ordered hit collection."""

    def __init__(self, hits: Sequence[BattleAnalysisHit]) -> None:
        self._sources: dict[tuple[int, str, str, str], BattleAnalysisHit] = {}
        for hit in hits:
            if _is_source(hit):
                self._sources.setdefault(weave_source_key(hit), hit)

    def find(self, hit: BattleAnalysisHit) -> BattleAnalysisHit | None:
        return self._sources.get(weave_source_key(hit))


BattleWeaveSourceLookup = Sequence[BattleAnalysisHit] | BattleWeaveSourceIndex


def find_paired_weave_source_hit(
    hit: BattleAnalysisHit,
    hits: BattleWeaveSourceLookup,
) -> BattleAnalysisHit | None:
    """Return the original hit whose damage and source Weave records."""

    if isinstance(hits, BattleWeaveSourceIndex):
        return hits.find(hit)
    return next(
        (
            row
            for row in hits
            if weave_source_key(row) == weave_source_key(hit)
            and _is_source(row)
        ),
        None,
    )
