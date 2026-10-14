# 在一次战报分析请求内准备可复用的逐击 Buff 投影，不写入战报快照。

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.buffs.models import BattleHitBuffProjection
from src.domain.battle_report.replay.models import BattleSkillDamageEvidence
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.buff_cache import (
    BattleHitBuffProjectionCache,
)
from src.services.battle_report.replay.formula_projection import project_formula_hit
from src.services.battle_report.damage.weave_source import BattleWeaveSourceIndex


@dataclass(frozen=True, slots=True)
class PreparedBattleHitProjections:
    interval_index: BattleBuffIntervalIndex
    formula_by_event: Mapping[str, BattleHitBuffProjection]
    beneficiary_by_event: Mapping[str, BattleHitBuffProjection]


class BattleHitProjectionPreparationService:
    """Prepare formula projections and safe beneficiary aliases once."""

    @staticmethod
    def prepare(
        analysis: BattleAnalysisSnapshot,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        *,
        projection_memo: BattleBuffProjectionMemo | None = None,
    ) -> PreparedBattleHitProjections:
        interval_index = BattleBuffIntervalIndex(analysis.buff_intervals)
        evidence_by_event = {row.event_id: row for row in skill_evidence}
        projection_cache = BattleHitBuffProjectionCache(interval_index, memo=projection_memo)
        formula_by_event: dict[str, BattleHitBuffProjection] = {}
        beneficiary_by_event: dict[str, BattleHitBuffProjection] = {}
        outgoing = tuple(hit for hit in analysis.hits if hit.direction == "outgoing")
        sources = BattleWeaveSourceIndex(analysis.hits)
        formula_hits = tuple(project_formula_hit(
            hit, evidence_by_event.get(hit.event_id), weave_sources=sources,
        ) for hit in outgoing)
        projection_cache.prepare(formula_hits)
        for hit, formula_hit in zip(outgoing, formula_hits, strict=True):
            evidence = evidence_by_event.get(hit.event_id)
            projection = projection_cache.project(formula_hit)
            formula_by_event[hit.event_id] = projection
            if (
                formula_hit.character_id == hit.character_id
                or evidence is not None
                and evidence.formula_context_kind.startswith("linko_coattack:")
            ):
                beneficiary_by_event[hit.event_id] = projection
        return PreparedBattleHitProjections(
            interval_index=interval_index,
            formula_by_event=formula_by_event,
            beneficiary_by_event=beneficiary_by_event,
        )


__all__ = [
    "BattleHitProjectionPreparationService",
    "PreparedBattleHitProjections",
]
