# 提供逐击重放 Service 共用的结构化公式来源项构造器。
"""Small constructors shared by battle hit replay services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from math import ceil

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
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
    formula_observed = float(result.observed_damage)
    source = result.observed_damage_source
    basis = result.observed_damage_basis
    reported_damage = result.reported_damage
    if hit.damage_correction_kind in {
        "nte_core_overkill_v3",
        "calc_hp_pool_alias_reconciliation_v1",
    } and hit.raw_damage > hit.damage:
        formula_observed = float(hit.raw_damage)
        source = "reported_hit_before_overkill"
        basis = (
            "nte-core 原始上报伤害；Calc 的共享生命池重叠扣减只影响战报有效"
            "伤害，不改变本击公式结算值"
            if hit.damage_correction_kind
            == "calc_hp_pool_alias_reconciliation_v1"
            else (
                "nte-core 原始上报伤害；overkill 只从战报有效总伤害扣除，"
                "不改变本击公式结算值"
            )
        )
        reported_damage = float(hit.damage)
    selected_error = (
        None
        if result.selected_damage is None
        else replay_error_percent(formula_observed, result.selected_damage)
    )
    signed_error = (
        None
        if result.selected_damage is None
        else replay_signed_error_percent(formula_observed, result.selected_damage)
    )
    corrected_expected = (
        result.expected_damage * formula_observed / result.selected_damage
        if result.expected_damage is not None
        and result.selected_damage is not None
        and result.selected_damage > 0.0
        else result.corrected_expected_damage
    )
    return replace(
        result,
        observed_damage=formula_observed,
        selected_error_percent=selected_error,
        signed_error_percent=signed_error,
        corrected_expected_damage=corrected_expected,
        reported_damage=reported_damage,
        observed_damage_source=source,
        observed_damage_basis=basis,
        missing_evidence=tuple(dict.fromkeys((
            *result.missing_evidence,
            (
                f"原始上报伤害 {hit.raw_damage:g}；"
                f"本击有效伤害修正为 {hit.damage:g}。"
                f"{hit.damage_correction_basis}"
            ),
        ))),
    )


def reanchor_direct_replay_result(
    template: BattleHitReplayResult,
    hit: BattleAnalysisHit,
) -> BattleHitReplayResult:
    """Reuse identical formula values while recomputing observed-hit branches."""

    non_critical = template.non_critical_damage
    if non_critical is None:
        return replace(
            template,
            event_id=hit.event_id,
            observed_damage=float(hit.damage),
        )
    critical = template.critical_damage
    noncrit_error = replay_error_percent(hit.damage, non_critical)
    crit_error = (
        None if critical is None else replay_error_percent(hit.damage, critical)
    )
    best_is_crit = bool(crit_error is not None and crit_error < noncrit_error)
    selected = critical if best_is_crit and critical is not None else non_critical
    error = noncrit_error if crit_error is None else min(noncrit_error, crit_error)
    signed_error = replay_signed_error_percent(hit.damage, selected)
    separation = 0.0 if crit_error is None else abs(noncrit_error - crit_error)
    if template.critical_policy == "disabled":
        state = "not_applicable"
        confidence = "高" if error <= 2.0 else "中" if error <= 5.0 else "低"
    elif error <= 2.0 and separation >= 2.0:
        state = "critical" if best_is_crit else "non_critical"
        confidence = "高"
    elif error <= 5.0 and separation >= 1.0:
        state = "critical" if best_is_crit else "non_critical"
        confidence = "中"
    else:
        state = "ambiguous"
        confidence = "低"
    corrected_expected = (
        template.expected_damage * hit.damage / selected
        if template.expected_damage is not None and selected > 0.0
        else None
    )
    return replace(
        template,
        event_id=hit.event_id,
        observed_damage=float(hit.damage),
        selected_damage=selected,
        selected_error_percent=error,
        critical_state=state,
        confidence=confidence,
        corrected_expected_damage=corrected_expected,
        signed_error_percent=signed_error,
        reported_damage=None,
        observed_damage_source="reported_hit",
        observed_damage_basis="",
    )


def replay_error_percent(observed: float, predicted: float) -> float:
    if observed <= 0:
        return 0.0 if predicted == 0 else 100.0
    return abs(predicted - observed) / observed * 100.0


def replay_signed_error_percent(observed: float, predicted: float) -> float | None:
    if observed <= 0:
        return None
    return (predicted - observed) / observed * 100.0


def replay_target_profile_basis(
    analysis: BattleAnalysisSnapshot,
    inferred_target: bool,
) -> tuple[bool, str]:
    """Describe whether an inferred formula profile also has formal identity."""

    resolved_target = any(
        str(row.resolved_monster_id or "").strip()
        for row in getattr(analysis, "target_instance_resolutions", ())
    )
    if not inferred_target:
        return resolved_target, "用户确认的目标属性包"
    if resolved_target:
        return resolved_target, "完整目标数量、初始最大生命与正式身份唯一命中的静态环境目标参数"
    return resolved_target, "完整目标数量与初始最大生命多重集唯一命中的静态环境目标参数"


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
