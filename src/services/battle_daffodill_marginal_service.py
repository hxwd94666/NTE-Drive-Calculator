# 将达芙蒂尔五觉候选状态投影为不改写原轴的边际派生结算。
"""Daffodill Effect5 additions for build counterfactual comparison."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from math import ceil

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
)
from src.services.battle_replay_formula_ratio_service import replay_formula_value


DAFFODILL_MARGINAL_MODEL_VERSION = "battle-daffodill-marginal-v1"
DAFFODILL_EFFECT_FIVE_METHOD = "candidate_derived_daffodill_effect5"
_BASE_TOPPLE_EFFECT = "buff_tenacity_damage"
_EXTRA_TOPPLE_EFFECT = "ge_player_daffodill_extraunbalance_damage"
_EFFECT_FIVE_DEFINITION = "character_awaken:1054:Effect5"
_SETTLEMENT_SOURCE_KIND = "candidate_derived_awakening_settlement"


class BattleDaffodillMarginalService:
    """Add only candidate settlements backed by a real topple and formula."""

    @staticmethod
    def direct_formula_intervals(
        analysis: BattleAnalysisSnapshot,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        """Exclude Effect5 settlements owned by this derived-row adapter."""

        return tuple(
            interval for interval in analysis.buff_intervals
            if not (
                interval.source_kind == _SETTLEMENT_SOURCE_KIND
                and interval.source_effect_definition_id == _EFFECT_FIVE_DEFINITION
            )
        )

    @classmethod
    def direct_formula_inputs_unchanged(
        cls,
        original: BattleAnalysisSnapshot,
        candidate: BattleAnalysisSnapshot,
    ) -> bool:
        return replace(
            original,
            buff_intervals=cls.direct_formula_intervals(original),
        ) == replace(
            candidate,
            buff_intervals=cls.direct_formula_intervals(candidate),
        )

    @staticmethod
    def _settlements(
        analysis: BattleAnalysisSnapshot,
    ) -> dict[str, BattleInferredBuffInterval]:
        hits = {hit.event_id: hit for hit in analysis.hits}
        result: dict[str, BattleInferredBuffInterval] = {}
        for interval in analysis.buff_intervals:
            if (
                interval.source_kind != _SETTLEMENT_SOURCE_KIND
                or interval.source_effect_definition_id != _EFFECT_FIVE_DEFINITION
            ):
                continue
            source_event_id = next((
                event_id
                for event_id in reversed(interval.evidence_event_ids)
                if event_id in hits
                and hits[event_id].gameplay_effect_id.casefold()
                == _BASE_TOPPLE_EFFECT
            ), "")
            if source_event_id:
                result[source_event_id] = interval
        return result

    @classmethod
    def derived_rows(
        cls,
        *,
        original: BattleAnalysisSnapshot,
        candidate: BattleAnalysisSnapshot,
    ) -> tuple[BattleBuildHitCounterfactual, ...]:
        candidate_hits = {hit.event_id: hit for hit in candidate.hits}
        candidate_replays = {row.event_id: row for row in candidate.hit_replays}
        original_settlements = cls._settlements(original)
        result: list[BattleBuildHitCounterfactual] = []
        for source_event_id, interval in cls._settlements(candidate).items():
            if source_event_id in original_settlements:
                continue
            source_hit = candidate_hits.get(source_event_id)
            if source_hit is None:
                continue
            special_hit = min(
                (
                    hit
                    for hit in candidate_hits.values()
                    if hit.target_id == source_hit.target_id
                    and hit.gameplay_effect_id.casefold() == _EXTRA_TOPPLE_EFFECT
                    and 0 <= hit.relative_time_us - source_hit.relative_time_us
                    <= 100_000
                ),
                key=lambda hit: hit.relative_time_us,
                default=None,
            )
            settlement = (
                replay_formula_value(candidate_replays.get(special_hit.event_id))[0]
                if special_hit is not None
                else None
            )
            confidence = "高" if settlement is not None else "中"
            anchor_basis = (
                "同目标已观测额外倾陷公式"
                if settlement is not None
                else "团队倾陷中的达芙蒂尔格子"
            )
            if settlement is None:
                factor = next((
                    factor
                    for factor in (
                        candidate_replays.get(source_event_id).factors
                        if source_event_id in candidate_replays
                        else ()
                    )
                    if factor.factor_id == "topple_character:1054"
                ), None)
                settlement = None if factor is None else float(ceil(factor.value))
            if settlement is None or settlement <= 0.0:
                continue
            for ordinal in range(1, interval.stacks + 1):
                result.append(BattleBuildHitCounterfactual(
                    event_id=(
                        f"derived:daffodill-effect5:{source_event_id}:{ordinal}"
                    ),
                    character_id=1054,
                    character_name="达芙蒂尔",
                    skill_name="完美真相",
                    damage_name=f"五觉额外倾陷·洞察第{ordinal}层",
                    baseline_damage=0.0,
                    known_projection_damage=float(settlement),
                    candidate_damage=float(settlement),
                    heuristic_projection_damage=None,
                    quantification=BattleCounterfactualRatio.complete(
                        1.0,
                        method=DAFFODILL_EFFECT_FIVE_METHOD,
                        confidence=confidence,
                        dependency_scope="mechanic_specific",
                        included_dimension_ids=("candidate_derived_settlement",),
                        explanation=(
                            f"候选五觉按原轴同目标洞察第 {ordinal} 层，在既有倾陷"
                            f"时点追加一次达芙蒂尔个人倾陷结算；零觉基础的一次结算"
                            f"仍由原始逐击保留，因此总次数为 1 + 洞察层数；公式锚点为"
                            f"{anchor_basis}"
                        ),
                    ),
                    baseline_formula_damage=0.0,
                    candidate_formula_damage=float(settlement),
                    source_event_id=source_event_id,
                ))
        return tuple(result)

    @staticmethod
    def composition_hit(
        row: BattleBuildHitCounterfactual,
        candidate_hits: Mapping[str, BattleAnalysisHit],
    ) -> BattleAnalysisHit:
        source = candidate_hits[row.source_event_id]
        return replace(
            source,
            event_id=row.event_id,
            relative_time_us=source.relative_time_us + 1,
            character_id=1054,
            character_name="达芙蒂尔",
            skill_name=row.skill_name,
            damage_name=row.damage_name,
            damage_component="candidate_derived",
            attack_type="Awakening Damage",
            damage_attribute="chaos",
            damage=BattleDaffodillMarginalService.candidate_damage(row),
            classification="direct",
            gameplay_effect_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
            raw_damage=None,
            overkill_damage=None,
            damage_correction_kind="",
            damage_correction_confidence="",
            damage_correction_basis="",
        )

    @staticmethod
    def composition_replay(
        row: BattleBuildHitCounterfactual,
        candidate_hits: Mapping[str, BattleAnalysisHit],
        candidate_replays: Mapping[str, BattleHitReplayResult],
    ) -> BattleHitReplayResult:
        source = candidate_hits.get(row.source_event_id)
        special_hit = min(
            (
                hit
                for hit in candidate_hits.values()
                if source is not None
                and hit.target_id == source.target_id
                and hit.gameplay_effect_id.casefold() == _EXTRA_TOPPLE_EFFECT
                and 0 <= hit.relative_time_us - source.relative_time_us <= 100_000
            ),
            key=lambda hit: hit.relative_time_us,
            default=None,
        )
        anchor = candidate_replays.get(
            special_hit.event_id if special_hit is not None else row.source_event_id
        )
        factors = tuple(
            factor
            for factor in (() if anchor is None else anchor.factors)
            if factor.factor_id in {"topple_target", "topple_character:1054"}
        )
        if not any(factor.factor_id == "topple_character:1054" for factor in factors):
            factors = (BattleHitReplayFactor(
                factor_id="topple_character:1054",
                label="达芙蒂尔倾陷贡献",
                value=BattleDaffodillMarginalService.candidate_damage(row),
                evidence_basis=row.quantification.explanation,
            ),)
        return BattleHitReplayResult(
            event_id=row.event_id,
            observed_damage=BattleDaffodillMarginalService.candidate_damage(row),
            non_critical_damage=BattleDaffodillMarginalService.candidate_damage(row),
            critical_damage=None,
            selected_damage=BattleDaffodillMarginalService.candidate_damage(row),
            selected_error_percent=0.0,
            critical_state="not_applicable",
            confidence=row.quantification.confidence,
            factors=factors,
            missing_evidence=(
                "候选五觉按洞察层数追加达芙蒂尔个人倾陷结算；"
                f"原轴触发逐击 {row.source_event_id} 保持不变。",
            ),
            formula_type="候选五觉·额外倾陷伤害",
            critical_rate=0.0,
            expected_damage=BattleDaffodillMarginalService.candidate_damage(row),
            critical_policy="disabled",
        )

    @staticmethod
    def candidate_damage(row: BattleBuildHitCounterfactual) -> float:
        if row.candidate_damage is None:
            raise ValueError("达芙蒂尔候选派生结算必须具有完整候选伤害")
        return row.candidate_damage


__all__ = [
    "DAFFODILL_EFFECT_FIVE_METHOD",
    "DAFFODILL_MARGINAL_MODEL_VERSION",
    "BattleDaffodillMarginalService",
]
