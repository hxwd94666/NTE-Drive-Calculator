# 把结构化逐击重放结果投影为可复制的完整算式与证据解释。
"""Qt-free presentation of one source-addressable battle-hit replay."""

from __future__ import annotations

from collections.abc import Sequence
from functools import reduce
from operator import mul

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleInferredBuffInterval,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


_FORMULA_FACTOR_IDS = (
    "skill",
    "state_coefficient",
    "scaling",
    "damage_up",
    "defense",
    "resistance",
    "vulnerability",
    "independent",
    "dot_final",
)
_REQUIRED_FORMULA_FACTOR_IDS = frozenset({
    "skill",
    "scaling",
    "damage_up",
    "defense",
    "resistance",
    "vulnerability",
    "independent",
})
_REACTION_FORMULA_FACTOR_IDS = (
    "skill",
    "state_coefficient",
    "scaling",
    "defense",
    "resistance",
    "vulnerability",
    "dot_final",
)
_REQUIRED_REACTION_FACTOR_IDS = frozenset({
    "skill",
    "scaling",
    "defense",
    "resistance",
    "vulnerability",
})
_CRIT_STATES = {
    "critical": "是",
    "non_critical": "否",
    "not_applicable": "不适用",
    "ambiguous": "无法确定",
    "unreplayable": "无法推断",
}

_COUNTERFACTUAL_METHOD_LABELS = {
    "structured_expected": "原始/候选结构化期望公式比值",
    "structured_selected": "原始/候选结构化同一实测分支公式比值",
    "skill_peer_estimate": "同技能已重放逐击中位比值",
    "type_peer_estimate": "同角色同伤害类型中位比值",
    "panel_formula_estimate": "角色面板与目标乘区比值",
    "role_peer_estimate": "该角色可重放逐击中位比值",
    "candidate_derived_daffodill_effect5": "候选五觉按洞察层数新增结算",
    "component_ratio": "变化乘区完整比值",
    "component_ratio_partial": "变化乘区已量化分量比值",
    "component_ratio_unavailable": "变化乘区缺少必要输入",
    "component_ratio_not_applicable": "已证明本击不受变化影响",
}


def _damage(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _percent(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def _factor_value(factor: BattleHitReplayFactor) -> str:
    if factor.factor_id.startswith("topple_character:"):
        return f"{factor.value:,.2f}"
    if factor.factor_id == "skill" and "倍率" in factor.label:
        return f"{factor.value * 100:.3f}%"
    if factor.factor_id == "scaling":
        return f"{factor.value:,.3f}"
    return f"{factor.value:.6f}"


def _term_value(term: BattleHitReplayTerm) -> str:
    if term.is_percent:
        return f"{term.value * 100:g}%"
    return f"{term.value:,.3f}"


def _sum_terms(terms: Sequence[BattleHitReplayTerm]) -> tuple[str, str]:
    if not terms:
        return "0", "0"
    return (
        " + ".join(f"{term.source_name}:{term.label}" for term in terms),
        " + ".join(_term_value(term) for term in terms),
    )


def _terms_for_property(
    factor: BattleHitReplayFactor, property_id: str,
) -> tuple[BattleHitReplayTerm, ...]:
    return tuple(
        term for term in factor.terms if term.property_id == property_id
    )


def _term_total(
    factor: BattleHitReplayFactor, property_id: str,
) -> float:
    return sum(term.value for term in _terms_for_property(factor, property_id))


def _source_names(terms: Sequence[BattleHitReplayTerm]) -> str:
    return "、".join(
        dict.fromkeys(f"{term.source_name}:{term.label}" for term in terms)
    ) or "无"


def _signed_percent_expression(initial: float, changes: Sequence[float]) -> str:
    expression = f"{initial * 100:g}%"
    for value in changes:
        operator = "+" if value >= 0 else "-"
        expression += f" {operator} {abs(value) * 100:g}%"
    return expression


def _group_buff_intervals(
    intervals: Sequence[BattleInferredBuffInterval],
    decision_by_id: dict,
) -> tuple[
    tuple[tuple[BattleInferredBuffInterval, ...], object, int, int], ...
]:
    """Collapse semantically identical interval evidence without changing projection."""

    grouped: dict[tuple, list[BattleInferredBuffInterval]] = {}
    for interval in intervals:
        decision = decision_by_id[interval.interval_id]
        modifier_key = tuple(
            (
                row.property_id,
                row.modifier_operation,
                row.magnitude_kind,
                row.calculation_asset_path,
                row.modifier_group_ordinal,
                row.application_requirement_asset_path,
                row.source_require_tags,
                row.source_ignore_tags,
                row.target_require_tags,
                row.target_ignore_tags,
            )
            for row in interval.modifiers
        )
        key = (
            interval.buff_name,
            interval.source_effect_definition_id,
            interval.buff_asset_path,
            interval.target_scope,
            modifier_key,
            tuple(decision.applied_property_ids),
            tuple(decision.reasons),
        )
        grouped.setdefault(key, []).append(interval)
    return tuple(
        (
            tuple(rows),
            decision_by_id[rows[0].interval_id],
            len(rows),
            sum(max(1, row.stacks) for row in rows),
        )
        for rows in grouped.values()
    )


def _confidence_summary(values: Sequence[str]) -> str:
    return "/".join(dict.fromkeys(value for value in values if value)) or "未知"


def _buff_modifier_text(
    intervals: Sequence[BattleInferredBuffInterval],
    decision: object,
    total_stacks: int,
) -> str:
    applied_property_ids = tuple(getattr(decision, "applied_property_ids", ()))
    property_ids = tuple(dict.fromkeys(
        row.property_id
        for interval in intervals
        for row in interval.modifiers
        if row.magnitude_value is not None
        and (
            not applied_property_ids
            or row.property_id in applied_property_ids
        )
    ))
    rendered: list[str] = []
    for property_id in property_ids:
        contributions: list[tuple[float, str, tuple[str, ...]]] = []
        for interval in intervals:
            for modifier in interval.modifiers:
                if (
                    modifier.property_id != property_id
                    or modifier.magnitude_value is None
                ):
                    continue
                for _ in range(max(1, interval.stacks)):
                    contributions.append((
                        modifier.magnitude_value,
                        modifier.value_confidence or interval.value_confidence,
                        interval.evidence_event_ids,
                    ))
        if not contributions:
            continue
        values = tuple(row[0] for row in contributions)
        if len(values) == 1:
            rendered.append(f"{property_id}={values[0]:g}")
            continue
        if all(value == values[0] for value in values[1:]):
            rendered.append(
                f"{property_id}={values[0]:g}×{total_stacks}="
                f"{values[0] * total_stacks:g}"
            )
            continue
        total = sum(values)
        layer_text = "；".join(
            f"{value:,.6f}".rstrip("0").rstrip(".")
            + f"[{confidence}"
            + (f"，{','.join(event_ids)}]" if event_ids else "]")
            for value, confidence, event_ids in contributions
        )
        rendered.append(
            f"{property_id}合计={total:,.2f}（逐层：{layer_text}）"
        )
    return "、".join(rendered) or "已采用结构化规则"


def _scaling_formula_lines(factor: BattleHitReplayFactor) -> list[str]:
    prefix = factor.label.split(" ", 1)[0]
    component_ids = {
        "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
        "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
        "Def": ("DefBase", "DefUp", "DefAdd"),
    }.get(prefix)
    if component_ids is None:
        return [
            f"{factor.label} = {factor.formula or '冻结面板值'}",
            f"  = {_factor_value(factor)}",
        ]
    base = [term for term in factor.terms if term.property_id == component_ids[0]]
    percent = [term for term in factor.terms if term.property_id == component_ids[1]]
    flat = [term for term in factor.terms if term.property_id == component_ids[2]]
    base_names, base_values = _sum_terms(base)
    percent_names, percent_values = _sum_terms(percent)
    flat_names, flat_values = _sum_terms(flat)
    return [
        f"{factor.label} = 基础值 × (1 + 百分比提升) + 额外固定值",
        f"  = ({base_names}) × (1 + {percent_names}) + {flat_names}",
        f"  = ({base_values}) × (1 + {percent_values}) + {flat_values}",
        f"  = {_factor_value(factor)}",
    ]


def _defense_formula_lines(factor: BattleHitReplayFactor) -> list[str]:
    if "DefBase/6" not in factor.evidence_basis:
        return []
    level = _term_total(factor, "CharacterLevel")
    defense_base = _term_total(factor, "DefBase")
    defense_up = _term_total(factor, "DefUp")
    defense_add = _term_total(factor, "DefAdd")
    penetration = _term_total(factor, "DefIgnore")
    reduction = _term_total(factor, "DefReduction")
    level_factor = level + 100.0
    panel_defense = defense_base * (1.0 + defense_up) + defense_add
    effective_defense = (
        panel_defense / 6.0 * (1.0 - penetration) * (1.0 - reduction)
    )
    return [
        "防御区 = L / (敌方有效防御 + L)，L = 角色等级 + 100",
        f"  L = {level:g} + 100 = {level_factor:g}",
        "  敌方面板防御 = DefBase × (1 + DefUp) + DefAdd",
        f"    = {defense_base:g} × (1 + {defense_up * 100:g}%) + "
        f"{defense_add:g} = {panel_defense:g}",
        "  敌方有效防御 = 敌方面板防御 / 6 × (1 - 防御穿透) × "
        "(1 - 防御降低)",
        f"    = {panel_defense:g} / 6 × (1 - {penetration * 100:g}%) × "
        f"(1 - {reduction * 100:g}%) = {effective_defense:g}",
        f"  防御区 = {level_factor:g} / ({effective_defense:g} + "
        f"{level_factor:g}) = {_factor_value(factor)}",
    ]


def _resistance_formula_lines(factor: BattleHitReplayFactor) -> list[str]:
    base_terms = tuple(
        term
        for term in factor.terms
        if term.property_id.startswith("Resistance:")
    )
    penetration_terms = tuple(
        term
        for term in factor.terms
        if term.property_id.startswith("DamagePenetrate")
    )
    resistance_modifiers = tuple(
        term
        for term in factor.terms
        if term.property_id.startswith("DamageResist")
    )
    if not base_terms:
        return []
    base = sum(term.value for term in base_terms)
    modifier_values = tuple(term.value for term in resistance_modifiers)
    target_resistance = base + sum(modifier_values)
    penetration = sum(term.value for term in penetration_terms)
    effective = target_resistance - penetration
    target_expression = _signed_percent_expression(base, modifier_values)
    comparison = ">= 0" if effective >= 0 else "< 0"
    branch_formula = (
        "1 - 有效抗性"
        if effective >= 0
        else "1 - 有效抗性 / 1.10"
    )
    branch_substitution = (
        f"1 - {effective * 100:g}%"
        if effective >= 0
        else f"1 - ({effective * 100:g}% / 1.10)"
    )
    return [
        "抗性区 = 抗性分段函数(有效抗性)",
        f"  目标抗性来源：{_source_names((*base_terms, *resistance_modifiers))}",
        f"  目标抗性 = {target_expression} = {target_resistance * 100:g}%",
        f"  属性穿透来源：{_source_names(penetration_terms)}",
        f"  属性穿透合计 = {penetration * 100:g}%",
        "  有效抗性 = 目标抗性 - 属性穿透",
        f"    = {target_resistance * 100:g}% - {penetration * 100:g}% "
        f"= {effective * 100:g}%",
        f"  因有效抗性 {effective * 100:g}% {comparison}，采用："
        f"{branch_formula}",
        f"  抗性区 = {branch_substitution} = {_factor_value(factor)}",
    ]


def _factor_lines(factor: BattleHitReplayFactor) -> list[str]:
    if factor.factor_id.startswith("topple_character:"):
        lines = [
            f"{factor.label} = 等级基础值 × 倾陷强度区 × "
            "敌方倾陷上限区 × 防御区 × 抗性区",
            f"  = {factor.formula}",
            f"  = {_factor_value(factor)}",
        ]
        if factor.terms:
            lines.append("  本格来源：")
            lines.extend(
                f"    - {term.source_name}:{term.label} = {_term_value(term)}"
                f"（{term.evidence_basis}）"
                for term in factor.terms
            )
        lines.append(f"  证据：{factor.evidence_basis}")
        return lines
    if factor.factor_id == "scaling":
        lines = _scaling_formula_lines(factor)
    elif factor.factor_id == "defense" and (
        detailed := _defense_formula_lines(factor)
    ):
        lines = detailed
    elif factor.factor_id == "resistance" and (
        detailed := _resistance_formula_lines(factor)
    ):
        lines = detailed
    else:
        lines = [f"{factor.label} = {factor.formula or '结构化计算值'}"]
        if factor.terms:
            names, values = _sum_terms(factor.terms)
            lines.extend((f"  来源项：{names}", f"  代入项：{values}"))
        lines.append(f"  = {_factor_value(factor)}")
    lines.append(f"  证据：{factor.evidence_basis}")
    return lines


class BattleHitReplayExplanationService:
    """Build deterministic dialog text without reading UI or storage state."""

    @classmethod
    def build(
        cls,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
    ) -> str:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        formula_type = (
            replay.formula_type
            if replay is not None and replay.formula_type != "未分类"
            else classify_battle_hit_channel(hit)[1]
        )
        lines = [
            f"{hit.character_name} · {damage_name}",
            f"逐击 ID：{hit.event_id}",
            f"对应公式类型：{formula_type}",
            f"目标：{hit.target_name}",
            "",
        ]
        if counterfactual is not None:
            quantification = counterfactual.quantification
            projection = (
                counterfactual.candidate_damage
                if counterfactual.candidate_damage is not None
                else counterfactual.known_projection_damage
            )
            delta = (
                None
                if projection is None
                else projection - counterfactual.baseline_damage
            )
            gain_percent = (
                delta / counterfactual.baseline_damage * 100.0
                if delta is not None and counterfactual.baseline_damage
                else None
            )
            direction = (
                "未量化"
                if delta is None
                else "提升" if delta > 0 else "下降" if delta < 0 else "持平"
            )
            method = _COUNTERFACTUAL_METHOD_LABELS.get(
                quantification.method,
                quantification.method,
            )
            projection_label = (
                "完整候选"
                if counterfactual.candidate_damage is not None
                else "已量化变化"
                if counterfactual.known_projection_damage is not None
                else "候选未量化"
            )
            lines.extend((
                "【调整后边际】",
                (
                    f"原始逐击：{counterfactual.baseline_damage:,.2f}    "
                    f"{projection_label}：{_damage(projection)}"
                ),
                (
                    f"{direction}：{_damage(delta)}（"
                    f"{'—' if gain_percent is None else f'{gain_percent:+.2f}%'}）    "
                    f"量化状态：{quantification.status}    "
                    f"证据置信度：{quantification.confidence}"
                ),
                (
                    f"原始公式值：{_damage(counterfactual.baseline_formula_damage)}    "
                    f"候选公式值：{_damage(counterfactual.candidate_formula_damage)}"
                ),
                f"量化方法：{method}",
                f"说明：{quantification.explanation}",
                *(
                    (f"启发式参考：{counterfactual.heuristic_projection_damage:,.2f}（不进入量化收益）",)
                    if counterfactual.heuristic_projection_damage is not None
                    else ()
                ),
                *(
                    ("未决依赖：" + "；".join(
                        gap.explanation for gap in quantification.gaps
                    ),)
                    if quantification.gaps
                    else ()
                ),
                *(
                    (
                        f"原轴触发逐击：{counterfactual.source_event_id}",
                        "口径：这是候选配置在既有触发时点新增的派生结算；"
                        "基线为 0，不改写原始触发逐击，也不是新的实测逐击。",
                    )
                    if counterfactual.source_event_id
                    else (
                        "口径：固定原战报动作与命中，只改变该击候选伤害；"
                        "这不是新的实测逐击。",
                    )
                ),
                "",
            ))
        if replay is None:
            lines.extend((
                (
                    "候选配置公式：—"
                    if counterfactual is not None
                    else f"实际伤害：{hit.damage:,.2f}    预计伤害：—    预计误差：—"
                ),
                "推断暴击：无法推断",
                "",
                (
                    "当前逐击没有候选配置公式；上方数值来自分级估计。"
                    if counterfactual is not None
                    else "当前逐击没有重放结果；历史分析需要按最新模型重新加载。"
                ),
            ))
            return "\n".join(lines)

        signed_error = replay.signed_error_percent
        if (
            signed_error is None
            and replay.selected_damage is not None
            and replay.observed_damage > 0
        ):
            signed_error = (
                (replay.selected_damage - replay.observed_damage)
                / replay.observed_damage
                * 100.0
            )
        if counterfactual is None:
            lines.extend((
                (
                    f"实际伤害：{_damage(replay.observed_damage)}    "
                    f"预计伤害：{_damage(replay.selected_damage)}    "
                    f"预计误差：{_percent(signed_error, signed=True)}"
                ),
                "误差定义：(预计伤害 - 实际伤害) / 实际伤害；正值为高估，负值为低估。",
                (
                    f"预计伤害期望：{_damage(replay.expected_damage)}    "
                    f"实际伤害期望：{_damage(replay.corrected_expected_damage)}"
                ),
                (
                    "期望口径：向上取整后的未暴击/暴击候选按暴击率加权；"
                    "实际期望按本击有符号误差同比补正。"
                ),
                (
                    f"推断暴击：{_CRIT_STATES.get(replay.critical_state, replay.critical_state)}"
                    f"（置信度{replay.confidence}）"
                ),
                "",
            ))
            if replay.observed_damage_source != "reported_hit":
                reported = (
                    hit.damage
                    if replay.reported_damage is None
                    else replay.reported_damage
                )
                lines.extend((
                    f"公式比较观测来源：{replay.observed_damage_basis}",
                    (
                        (
                            f"计入战报有效伤害：{_damage(reported)}；"
                            if replay.observed_damage_source
                            == "reported_hit_before_overkill"
                            else f"原始逐击上报：{_damage(reported)}；"
                        )
                        + f"公式可比观测：{_damage(replay.observed_damage)}。"
                        "两者保持独立。"
                    ),
                    "",
                ))
        else:
            lines.extend((
                (
                    "候选配置推断暴击："
                    f"{_CRIT_STATES.get(replay.critical_state, replay.critical_state)}"
                    f"（置信度{replay.confidence}）"
                ),
                "",
            ))

        factors = {factor.factor_id: factor for factor in replay.factors}
        formula_factors = [
            factors[factor_id]
            for factor_id in _FORMULA_FACTOR_IDS
            if factor_id in factors
        ]
        reaction_formula_factors = [
            factors[factor_id]
            for factor_id in _REACTION_FORMULA_FACTOR_IDS
            if factor_id in factors
        ]
        lines.append(
            "【候选配置伤害公式】"
            if counterfactual is not None
            else "【伤害公式】"
        )
        topple_cells = tuple(
            factor
            for factor in replay.factors
            if factor.factor_id.startswith("topple_character:")
        )
        if topple_cells:
            expression = " + ".join(factor.label for factor in topple_cells)
            substituted = " + ".join(
                _factor_value(factor) for factor in topple_cells
            )
            raw_damage = sum(factor.value for factor in topple_cells)
            lines.extend((
                f"团队倾陷伤害 = {expression}",
                f"  = {substituted}",
                f"  = ceil({raw_damage:,.6f}) = {_damage(replay.non_critical_damage)}",
            ))
        elif _REQUIRED_FORMULA_FACTOR_IDS.issubset(factors):
            expression = " × ".join(factor.label for factor in formula_factors)
            substituted = " × ".join(_factor_value(factor) for factor in formula_factors)
            noncrit = reduce(mul, (factor.value for factor in formula_factors), 1.0)
            lines.extend((
                f"伤害（未暴击） = {expression}",
                f"  = {substituted}",
                f"  = ceil({noncrit:,.6f}) = {_damage(replay.non_critical_damage)}",
            ))
            critical = factors.get("critical")
            if critical is not None:
                raw_critical = noncrit * critical.value
                lines.extend((
                    f"伤害（暴击） = 未取整伤害 × {critical.label}",
                    f"  = {noncrit:,.2f} × {_factor_value(critical)}",
                    f"  = ceil({raw_critical:,.6f}) = {_damage(replay.critical_damage)}",
                ))
        elif _REQUIRED_REACTION_FACTOR_IDS.issubset(factors):
            expression = " × ".join(
                factor.label for factor in reaction_formula_factors
            )
            substituted = " × ".join(
                _factor_value(factor) for factor in reaction_formula_factors
            )
            noncrit = reduce(
                mul,
                (factor.value for factor in reaction_formula_factors),
                1.0,
            )
            lines.extend((
                f"伤害（未暴击） = {expression}",
                f"  = {substituted}",
                f"  = ceil({noncrit:,.6f}) = {_damage(replay.non_critical_damage)}",
            ))
            critical = factors.get("critical")
            if critical is not None:
                raw_critical = noncrit * critical.value
                lines.extend((
                    f"伤害（暴击） = 未取整伤害 × {critical.label}",
                    f"  = {noncrit:,.2f} × {_factor_value(critical)}",
                    f"  = ceil({raw_critical:,.6f}) = {_damage(replay.critical_damage)}",
                ))
        else:
            missing_target = any(
                "单目标防御与抗性" in row
                for row in replay.missing_evidence
            )
            if replay.formula_type.startswith("直伤") and missing_target:
                lines.append(
                    "已匹配直伤适配器，但缺少本击可用的敌方防御与抗性，"
                    "暂时不能完成数值等式。"
                )
            else:
                lines.append(
                    "当前类型尚无完整反事实适配器，不能安全拼出数值等式。"
                )

        if replay.factors:
            lines.extend(("", "【乘区公式】"))
            for factor in replay.factors:
                lines.extend(_factor_lines(factor))
                lines.append("")

        projection = BattleBuffAttributeProjectionService.project_hit(
            hit,
            active_buffs,
        )
        decision_by_id = {
            row.interval_id: row for row in projection.decisions
        }
        lines.append(
            "【本击 Buff：已投影（是否被公式消费见乘区）】"
            if not any(
                "单目标防御与抗性" in row
                for row in replay.missing_evidence
            )
            else "【本击 Buff：可投影（公式输入不完整）】"
        )
        if active_buffs:
            applied = tuple(
                interval
                for interval in active_buffs
                if decision_by_id.get(interval.interval_id) is not None
                and decision_by_id[interval.interval_id].status == "applied"
            )
            for intervals, decision, interval_count, total_stacks in (
                _group_buff_intervals(applied, decision_by_id)
            ):
                interval = intervals[0]
                modifier_text = _buff_modifier_text(
                    intervals,
                    decision,
                    total_stacks,
                )
                merge_text = (
                    f"，合并 {interval_count} 条同类区间"
                    if interval_count > 1
                    else ""
                )
                lines.append(
                    f"- {interval.buff_name} ×{total_stacks}层"
                    f"（{interval.target_scope}，状态"
                    f"{_confidence_summary(tuple(row.state_confidence for row in intervals))}，"
                    f"数值{_confidence_summary(tuple(row.value_confidence for row in intervals))}"
                    f"{merge_text}）："
                    f"{modifier_text}\n"
                    f"  ID：{interval.source_effect_definition_id}\n"
                    f"  资产：{interval.buff_asset_path}"
                )
            if not applied:
                lines.append("- 没有 Buff 数值进入本击公式。")
        else:
            lines.append("- 没有匹配到可投影的命中时 Buff 区间。")

        for status, title in (
            ("not_applied", "【本击 Buff：未采用】"),
            ("unresolved", "【本击 Buff：待确认/结构化】"),
        ):
            matching = tuple(
                interval
                for interval in active_buffs
                if decision_by_id.get(interval.interval_id) is not None
                and decision_by_id[interval.interval_id].status == status
            )
            if not matching:
                continue
            lines.extend(("", title))
            for intervals, decision, interval_count, total_stacks in (
                _group_buff_intervals(matching, decision_by_id)
            ):
                interval = intervals[0]
                reason = "；".join(decision.reasons) or "未记录排除原因"
                modifier_text = _buff_modifier_text(
                    intervals,
                    decision,
                    total_stacks,
                )
                if modifier_text != "已采用结构化规则":
                    reason += f"；结构化值：{modifier_text}"
                count_text = (
                    f" ×{interval_count} 条同类区间"
                    if interval_count > 1
                    else ""
                )
                stack_text = (
                    f"，合计 {total_stacks} 层"
                    if total_stacks != interval_count
                    else ""
                )
                lines.append(
                    f"- {interval.buff_name}{count_text}（{reason}{stack_text}）\n"
                    f"  ID：{interval.source_effect_definition_id}\n"
                    f"  资产：{interval.buff_asset_path}"
                )

        lines.extend(("", "【证据边界】"))
        if replay.missing_evidence:
            lines.extend(f"- {row}" for row in replay.missing_evidence)
        else:
            lines.append("- 当前公式没有记录额外缺口；逐击观测值仍是最终强证据。")
        if counterfactual is not None:
            lines.append(
                "- 调整后数值属于固定轴反事实；原始实测逐击与数据库保持不变。"
            )
        return "\n".join(lines)
