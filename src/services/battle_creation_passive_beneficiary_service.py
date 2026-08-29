# 将已量化创生逐击按真实伤害角色投影为受益角色结果。
"""Beneficiary projection for creation-passive fixed-axis gains."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_buff_counterfactual import (
    BattleBuffBeneficiaryResult,
    BattleDamageCoverage,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)
from src.domain.battle_report import BattleAnalysisHit


class BattleCreationPassiveBeneficiaryService:
    """Attribute a quantified direct-hit gain without guessing missing owners."""

    @staticmethod
    def calculate(
        all_hits: Sequence[BattleAnalysisHit],
        direct_hits: Sequence[BattleAnalysisHit],
        unavailable_hits: Sequence[BattleAnalysisHit],
        *,
        team_damage: float,
        quantification: BattleDamageQuantification,
    ) -> tuple[
        tuple[BattleBuffBeneficiaryResult, ...],
        float | None,
        float | None,
    ]:
        gain = quantification.quantified_increment
        if gain is None:
            return (), None, None
        direct_damage = sum(max(0.0, float(hit.damage)) for hit in direct_hits)
        if direct_damage <= 0.0:
            complete_unattributed = (
                gain
                if quantification.status in {"complete", "not_applicable"}
                else None
            )
            return (), gain, complete_unattributed
        gain_ratio = gain / direct_damage
        character_totals: dict[int, float] = {}
        character_names: dict[int, str] = {}
        for hit in all_hits:
            character_id = hit.character_id
            if (
                hit.direction != "outgoing"
                or hit.damage <= 0.0
                or character_id is None
                or character_id <= 0
            ):
                continue
            character_totals[character_id] = (
                character_totals.get(character_id, 0.0) + float(hit.damage)
            )
            if hit.character_name.strip():
                character_names.setdefault(character_id, hit.character_name)
        grouped_hits: dict[int, list[BattleAnalysisHit]] = {}
        for hit in direct_hits:
            character_id = hit.character_id
            if character_id is None or character_id <= 0:
                continue
            grouped_hits.setdefault(character_id, []).append(hit)
            if hit.character_name.strip():
                character_names.setdefault(character_id, hit.character_name)
        unavailable_by_character: dict[int, float] = {}
        for hit in unavailable_hits:
            character_id = hit.character_id
            if character_id is None or character_id <= 0:
                continue
            unavailable_by_character[character_id] = (
                unavailable_by_character.get(character_id, 0.0)
                + max(0.0, float(hit.damage))
            )
        team_without = max(0.0, team_damage - gain)
        beneficiaries = tuple(
            BattleCreationPassiveBeneficiaryService._one(
                character_id=character_id,
                character_name=character_names.get(character_id, str(character_id)),
                direct_hits=tuple(grouped_hits[character_id]),
                character_total=character_totals.get(character_id, 0.0),
                unavailable_damage=unavailable_by_character.get(character_id, 0.0),
                gain_ratio=gain_ratio,
                team_without=team_without,
                parent_quantification=quantification,
            )
            for character_id in sorted(grouped_hits)
        )
        attributed_gain = sum(
            row.quantified_damage_gain or 0.0 for row in beneficiaries
        )
        unattributed_gain = max(0.0, gain - attributed_gain)
        complete_unattributed = (
            unattributed_gain
            if quantification.status in {"complete", "not_applicable"}
            else None
        )
        return beneficiaries, unattributed_gain, complete_unattributed

    @staticmethod
    def _one(
        *,
        character_id: int,
        character_name: str,
        direct_hits: tuple[BattleAnalysisHit, ...],
        character_total: float,
        unavailable_damage: float,
        gain_ratio: float,
        team_without: float,
        parent_quantification: BattleDamageQuantification,
    ) -> BattleBuffBeneficiaryResult:
        direct_damage = sum(max(0.0, float(hit.damage)) for hit in direct_hits)
        direct_gain = direct_damage * gain_ratio
        baseline = max(character_total, direct_damage)
        without = max(0.0, baseline - direct_gain)
        status = parent_quantification.status
        if status == "partial":
            proven_unchanged = max(
                0.0,
                baseline - direct_damage - unavailable_damage,
            )
            quantification = BattleDamageQuantification.from_buckets(
                status="partial",
                partially_quantified_damage=direct_damage,
                unavailable_damage=unavailable_damage,
                proven_unchanged_damage=proven_unchanged,
                quantified_increment=direct_gain,
                gaps=parent_quantification.gaps,
            )
        else:
            quantification = BattleDamageQuantification.from_buckets(
                status=status,
                fully_quantified_damage=direct_damage,
                proven_unchanged_damage=max(0.0, baseline - direct_damage),
                quantified_increment=direct_gain,
            )
        complete = status in {"complete", "not_applicable"}
        recipient_percent = direct_gain / without * 100.0 if without > 0.0 else 0.0
        team_percent = (
            direct_gain / team_without * 100.0 if team_without > 0.0 else 0.0
        )
        return BattleBuffBeneficiaryResult(
            character_id=character_id,
            character_name=character_name,
            affected_hits=len(direct_hits),
            quantified_hits=len(direct_hits),
            baseline_damage=baseline,
            without_quantified_effect_damage=without,
            quantified_damage_gain=direct_gain,
            quantified_recipient_gain_percent=recipient_percent,
            quantified_team_contribution_percent=team_percent,
            without_buff_damage=without if complete else None,
            damage_gain=direct_gain if complete else None,
            recipient_gain_percent=recipient_percent if complete else None,
            team_contribution_percent=team_percent if complete else None,
            quantification=quantification,
            damage_coverage=BattleDamageCoverage(
                basis_damage=baseline,
                covered_damage=min(baseline, direct_damage),
                unresolved_damage=min(
                    max(0.0, baseline - direct_damage),
                    unavailable_damage,
                ),
            ),
        )


__all__ = ["BattleCreationPassiveBeneficiaryService"]
