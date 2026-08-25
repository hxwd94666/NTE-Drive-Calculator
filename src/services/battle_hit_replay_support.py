# 提供逐击重放 Service 共用的结构化公式来源项构造器。
"""Small constructors shared by battle hit replay services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from math import ceil

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleSkillDamageEvidence,
)


_PANEL_PROPERTIES = {
    "Atk": ("PanelAtk", "Atk"),
    "HPMax": ("PanelHP", "HPMax"),
    "Def": ("PanelDef", "Def"),
}


def ceil_replay_damage(value: float) -> float:
    """Round one deterministic replay settlement upward after all factors."""

    return float(ceil(max(0.0, float(value))))


def first_replay_value(values: Mapping[str, float], property_id: str) -> float:
    components = {
        "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
        "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
        "Def": ("DefBase", "DefUp", "DefAdd"),
    }.get(property_id)
    if components is not None and components[0] in values:
        return (
            float(values[components[0]])
            * (1.0 + float(values.get(components[1], 0.0)))
            + float(values.get(components[2], 0.0))
        )
    for candidate in _PANEL_PROPERTIES.get(property_id, (property_id,)):
        if candidate in values:
            return float(values[candidate])
    return 0.0


def replay_factor(
    factor_id: str,
    label: str,
    value: float,
    basis: str,
    *,
    formula: str = "",
    terms: tuple[BattleHitReplayTerm, ...] = (),
) -> BattleHitReplayFactor:
    return BattleHitReplayFactor(
        factor_id,
        label,
        float(value),
        basis,
        formula,
        terms,
    )


def dot_final_replay_factors(
    evidence: BattleSkillDamageEvidence,
) -> tuple[BattleHitReplayFactor, ...]:
    """Explain a DOT-only final multiplier without exposing it to direct hits."""

    if not evidence.dot_final_multiplier_basis:
        return ()
    value = max(1.0, evidence.dot_final_multiplier)
    return (replay_factor(
        "dot_final",
        "DOT 专属最终乘区",
        value,
        evidence.dot_final_multiplier_basis,
        formula=(
            "1 + min(结算前 DOT 种类数 × 25%, 100%)"
            if value > 1.0 else "固定为 1"
        ),
    ),)


def replay_source_terms(
    baseline: BattleCharacterBaseline,
    projection: BattleHitBuffProjection,
    property_ids: tuple[str, ...],
) -> tuple[BattleHitReplayTerm, ...]:
    terms = [
        BattleHitReplayTerm(
            term_id=f"{row.source_group}:{row.property_id}",
            property_id=row.property_id,
            label=row.label,
            value=row.value,
            source_group=row.source_group,
            source_name=row.source_name,
            is_percent=row.is_percent,
            evidence_basis=f"{baseline.source} 角色属性来源快照",
        )
        for row in baseline.source_stats
        if row.property_id in property_ids and row.value != 0.0
    ]
    for modifier in projection.modifiers:
        if modifier.property_id not in property_ids or modifier.additive_value == 0.0:
            continue
        names = "、".join(modifier.buff_names) or modifier.property_id
        terms.append(BattleHitReplayTerm(
            term_id=f"buff:{modifier.property_id}:{':'.join(modifier.interval_ids)}",
            property_id=modifier.property_id,
            label=names,
            value=modifier.additive_value,
            source_group="buff",
            source_name=f"Buff：{names}",
            is_percent=any(
                token in modifier.property_id
                for token in ("Up", "Crit", "Damage", "Ignore", "Penetrate")
            ),
            evidence_basis=f"命中时 Buff 投影（置信度{modifier.confidence}）",
        ))
    if terms:
        return tuple(terms)
    resolved = {row.property_id: row for row in baseline.stats}
    return tuple(
        BattleHitReplayTerm(
            term_id=f"resolved:{property_id}",
            property_id=property_id,
            label=resolved[property_id].label,
            value=resolved[property_id].value,
            source_group="resolved",
            source_name="冻结合计",
            is_percent=resolved[property_id].is_percent,
            evidence_basis=f"{baseline.source} 合计值；历史来源未拆分",
        )
        for property_id in property_ids
        if property_id in resolved and resolved[property_id].value != 0.0
    )


def apply_observed_damage_correction(
    result: BattleHitReplayResult,
    hit: BattleAnalysisHit,
) -> BattleHitReplayResult:
    if hit.raw_damage is None or not hit.damage_correction_kind:
        return result
    return replace(
        result,
        missing_evidence=tuple(dict.fromkeys((
            *result.missing_evidence,
            (
                f"原始上报伤害 {hit.raw_damage:g}；"
                f"本击有效伤害修正为 {hit.damage:g}。"
                f"{hit.damage_correction_basis}"
            ),
        ))),
    )


def replay_error_percent(observed: float, predicted: float) -> float:
    if observed <= 0:
        return 0.0 if predicted == 0 else 100.0
    return abs(predicted - observed) / observed * 100.0


def replay_signed_error_percent(observed: float, predicted: float) -> float | None:
    if observed <= 0:
        return None
    return (predicted - observed) / observed * 100.0


def literal_replay_term(
    term_id: str,
    property_id: str,
    label: str,
    value: float,
    source_group: str,
    source_name: str,
    *,
    is_percent: bool,
    basis: str,
) -> BattleHitReplayTerm:
    return BattleHitReplayTerm(
        term_id=term_id,
        property_id=property_id,
        label=label,
        value=float(value),
        source_group=source_group,
        source_name=source_name,
        is_percent=is_percent,
        evidence_basis=basis,
    )
