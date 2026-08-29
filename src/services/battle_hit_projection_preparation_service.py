# 在一次战报分析请求内准备可复用的逐击 Buff 投影，不写入战报快照。
"""Ephemeral Buff projections shared by replay and marginal analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_interval_index import BattleBuffIntervalIndex
from src.services.battle_hit_buff_projection_cache import (
    BattleHitBuffProjectionCache,
)


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
    ) -> PreparedBattleHitProjections:
        interval_index = BattleBuffIntervalIndex(analysis.buff_intervals)
        evidence_by_event = {row.event_id: row for row in skill_evidence}
        projection_cache = BattleHitBuffProjectionCache(interval_index)
        formula_by_event: dict[str, BattleHitBuffProjection] = {}
        beneficiary_by_event: dict[str, BattleHitBuffProjection] = {}
        for hit in analysis.hits:
            if hit.direction != "outgoing":
                continue
            evidence = evidence_by_event.get(hit.event_id)
            formula_character_id = (
                evidence.source_character_id
                if evidence is not None
                and evidence.source_character_id is not None
                else hit.character_id
            )
            formula_hit = (
                hit
                if formula_character_id == hit.character_id
                else replace(hit, character_id=formula_character_id)
            )
            projection = projection_cache.project(formula_hit)
            formula_by_event[hit.event_id] = projection
            if formula_hit is hit:
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
