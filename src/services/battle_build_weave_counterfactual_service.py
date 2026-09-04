# 将候选原伤害比与覆纹自身倍率比合并为完整的固定轴覆纹反事实。
"""Link a recorded Weave packet to its paired source-hit counterfactual."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_weave_source_service import find_paired_weave_source_hit


def link_weave(
    rows: Sequence[BattleBuildHitCounterfactual],
    hits: Mapping[str, BattleAnalysisHit],
) -> tuple[BattleBuildHitCounterfactual, ...]:
    """Multiply Weave's own candidate ratio by its recorded source-hit ratio."""

    rows_by_event = {row.event_id: row for row in rows}
    all_hits = tuple(hits.values())
    linked: list[BattleBuildHitCounterfactual] = []
    for row in rows:
        hit = hits.get(row.event_id)
        source = (
            None
            if hit is None or hit.classification != "weave"
            else find_paired_weave_source_hit(hit, all_hits)
        )
        source_row = None if source is None else rows_by_event.get(source.event_id)
        if source_row is None:
            linked.append(row)
            continue
        source_ratio = source_row.quantification
        if (
            source_ratio.status == "not_applicable"
            or (
                source_ratio.quantified_ratio == 1.0
                and not source_ratio.gaps
            )
        ):
            linked.append(row)
            continue
        quantification = _combine_ratios(row.quantification, source_ratio)
        ratio = quantification.quantified_ratio
        linked.append(replace(
            row,
            known_projection_damage=(
                None if ratio is None else row.baseline_damage * ratio
            ),
            candidate_damage=(
                row.baseline_damage * ratio
                if ratio is not None
                and quantification.status in {"complete", "not_applicable"}
                else None
            ),
            quantification=quantification,
            candidate_formula_damage=(
                None
                if row.candidate_formula_damage is None
                or source_ratio.quantified_ratio is None
                else row.candidate_formula_damage
                * source_ratio.quantified_ratio
            ),
            source_event_id=source.event_id,
        ))
    return tuple(linked)


def _combine_ratios(
    weave: BattleCounterfactualRatio,
    source: BattleCounterfactualRatio,
) -> BattleCounterfactualRatio:
    ratios = tuple(
        ratio for ratio in (weave.quantified_ratio, source.quantified_ratio)
        if ratio is not None
    )
    gaps = _unique((
        *weave.gaps,
        *source.gaps,
        *((_missing_source_gap(),) if source.quantified_ratio is None else ()),
    ))
    included = _unique_text((
        *weave.included_dimension_ids,
        *source.included_dimension_ids,
        *(("weave_recorded_source_damage",)
          if source.quantified_ratio is not None else ()),
    ))
    cancelled = tuple(
        value for value in _unique_text((
            *weave.cancelled_dimension_ids,
            *source.cancelled_dimension_ids,
        ))
        if value not in set(included)
    )
    ratio = None if not ratios else _product(ratios)
    explanation = (
        "先按配对原伤害的候选/基线比联动覆纹记录值，"
        "再叠加覆纹自身候选倍率。"
    )
    if ratio is None:
        return BattleCounterfactualRatio.unavailable(
            method="linked_weave_source_unavailable",
            confidence="低",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=cancelled,
            gaps=gaps or (_missing_source_gap(),),
            explanation="覆纹或其配对原伤害缺少可量化的候选公式。",
        )
    if gaps and not included:
        return BattleCounterfactualRatio.unavailable(
            method="linked_weave_source_unavailable",
            confidence="低",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=cancelled,
            gaps=gaps,
            explanation="覆纹或其配对原伤害缺少可量化的候选公式。",
        )
    if gaps:
        return BattleCounterfactualRatio.partial(
            ratio,
            method="linked_weave_source_partial",
            confidence=_minimum_confidence(weave.confidence, source.confidence),
            dependency_scope="mechanic_specific",
            included_dimension_ids=included,
            cancelled_dimension_ids=cancelled,
            gaps=gaps,
            explanation=f"{explanation}仍有未解析依赖，结果按部分量化展示。",
        )
    return BattleCounterfactualRatio.complete(
        ratio,
        method=weave.method,
        confidence=_minimum_confidence(weave.confidence, source.confidence),
        dependency_scope="mechanic_specific",
        included_dimension_ids=included,
        cancelled_dimension_ids=cancelled,
        explanation=explanation,
    )


def _product(values: Sequence[float]) -> float:
    result = 1.0
    for value in values:
        result *= float(value)
    return result


def _unique(
    values: Sequence[BattleQuantificationGap],
) -> tuple[BattleQuantificationGap, ...]:
    return tuple(dict.fromkeys(values))


def _unique_text(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _minimum_confidence(*values: str) -> str:
    order = {"未解析": 0, "低": 1, "中": 2, "高": 3}
    normalized = tuple(value if value in order else "低" for value in values)
    return min(normalized, key=order.__getitem__) if normalized else "未解析"


def _missing_source_gap() -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code="linked_weave_source_unavailable",
        dimension_id="weave_recorded_source_hit",
        dependency_scope="mechanic_specific",
        property_ids=(),
        explanation="覆纹或其配对原伤害缺少可量化的候选公式。",
    )


__all__ = ["link_weave"]
