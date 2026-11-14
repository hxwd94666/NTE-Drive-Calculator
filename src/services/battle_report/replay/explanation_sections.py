# 逐击重放解释的表格与渲染助手：只做字符串投影，不读取外部状态。
# 把结构化逐击重放结果投影为可复制的完整算式与证据解释。

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report.replay.models import BattleHitReplayFactor, BattleHitReplayTerm
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.domain.localization.catalog import TextCatalog, localized_text
from src.integrations.preferences.locale_files import enum_text


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


def _label_table(
    table: dict, *, text_catalog: TextCatalog | None = None,
) -> dict:
    """整表投影代码内标签表；未注入目录时保留中文基线，服务层不读取资源文件。"""

    return table if text_catalog is None else enum_text(text_catalog, table)


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


def _source_names(
    terms: Sequence[BattleHitReplayTerm], *, text_catalog: TextCatalog | None = None,
) -> str:
    return "、".join(
        dict.fromkeys(f"{term.source_name}:{term.label}" for term in terms)
    ) or localized_text(
        text_catalog, "battle_report.explanation.common.none", "无",
    )


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


def _confidence_summary(
    values: Sequence[str], *, text_catalog: TextCatalog | None = None,
) -> str:
    return "/".join(
        dict.fromkeys(value for value in values if value)
    ) or localized_text(
        text_catalog, "battle_report.explanation.common.unknown", "未知",
    )


def _direction_label(
    delta: float | None, *, text_catalog: TextCatalog | None = None,
) -> str:
    if delta is None:
        return localized_text(
            text_catalog,
            "battle_report.explanation.counterfactual.direction.unquantified",
            "未量化",
        )
    if delta > 0:
        return localized_text(
            text_catalog, "battle_report.explanation.counterfactual.direction.up", "提升",
        )
    if delta < 0:
        return localized_text(
            text_catalog, "battle_report.explanation.counterfactual.direction.down", "下降",
        )
    return localized_text(
        text_catalog, "battle_report.explanation.counterfactual.direction.flat", "持平",
    )


def _buff_modifier_text(
    intervals: Sequence[BattleInferredBuffInterval],
    decision: object,
    total_stacks: int,
    *,
    text_catalog: TextCatalog | None = None,
) -> str:
    """逐属性合并 Buff 数值；空串表示本组只有结构化规则可用。"""

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
            localized_text(
                text_catalog,
                "battle_report.explanation.buff.property_total",
                "{property_id}合计={total}（逐层：{layers}）",
                property_id=property_id,
                total=f"{total:,.2f}",
                layers=layer_text,
            )
        )
    return "、".join(rendered)


def _scaling_formula_lines(
    factor: BattleHitReplayFactor, *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    prefix = factor.label.split(" ", 1)[0]
    component_ids = {
        "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
        "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
        "Def": ("DefBase", "DefUp", "DefAdd"),
    }.get(prefix)
    if component_ids is None:
        formula = factor.formula or localized_text(
            text_catalog,
            "battle_report.explanation.scaling.frozen_panel_value",
            "冻结面板值",
        )
        return [
            f"{factor.label} = {formula}",
            f"  = {_factor_value(factor)}",
        ]
    base = [term for term in factor.terms if term.property_id == component_ids[0]]
    percent = [term for term in factor.terms if term.property_id == component_ids[1]]
    flat = [term for term in factor.terms if term.property_id == component_ids[2]]
    base_names, base_values = _sum_terms(base)
    percent_names, percent_values = _sum_terms(percent)
    flat_names, flat_values = _sum_terms(flat)
    return [
        localized_text(
            text_catalog,
            "battle_report.explanation.scaling.panel_formula",
            "{label} = 基础值 × (1 + 百分比提升) + 额外固定值",
            label=factor.label,
        ),
        f"  = ({base_names}) × (1 + {percent_names}) + {flat_names}",
        f"  = ({base_values}) × (1 + {percent_values}) + {flat_values}",
        f"  = {_factor_value(factor)}",
    ]


def _defense_formula_lines(
    factor: BattleHitReplayFactor, *, text_catalog: TextCatalog | None = None,
) -> list[str]:
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
        localized_text(
            text_catalog,
            "battle_report.explanation.defense.zone_intro",
            "防御区 = L / (敌方有效防御 + L)，L = 角色等级 + 100",
        ),
        f"  L = {level:g} + 100 = {level_factor:g}",
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.defense.panel_formula",
            "敌方面板防御 = DefBase × (1 + DefUp) + DefAdd",
        ),
        f"    = {defense_base:g} × (1 + {defense_up * 100:g}%) + "
        f"{defense_add:g} = {panel_defense:g}",
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.defense.effective_formula",
            "敌方有效防御 = 敌方面板防御 / 6 × (1 - 防御穿透) × "
            "(1 - 防御降低)",
        ),
        f"    = {panel_defense:g} / 6 × (1 - {penetration * 100:g}%) × "
        f"(1 - {reduction * 100:g}%) = {effective_defense:g}",
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.defense.zone_line",
            "防御区 = {level_factor} / ({effective_defense} + "
            "{level_factor}) = {value}",
            level_factor=f"{level_factor:g}",
            effective_defense=f"{effective_defense:g}",
            value=_factor_value(factor),
        ),
    ]


def _resistance_formula_lines(
    factor: BattleHitReplayFactor, *, text_catalog: TextCatalog | None = None,
) -> list[str]:
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
    if effective >= 0:
        branch_formula = localized_text(
            text_catalog,
            "battle_report.explanation.resistance.branch_positive",
            "1 - 有效抗性",
        )
    else:
        branch_formula = localized_text(
            text_catalog,
            "battle_report.explanation.resistance.branch_negative",
            "1 - 有效抗性 / 1.10",
        )
    branch_substitution = (
        f"1 - {effective * 100:g}%"
        if effective >= 0
        else f"1 - ({effective * 100:g}% / 1.10)"
    )
    return [
        localized_text(
            text_catalog,
            "battle_report.explanation.resistance.zone_intro",
            "抗性区 = 抗性分段函数(有效抗性)",
        ),
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.target_sources",
            "目标抗性来源：{sources}",
            sources=_source_names(
                (*base_terms, *resistance_modifiers), text_catalog=text_catalog,
            ),
        ),
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.target_value",
            "目标抗性 = {expression} = {percent}",
            expression=target_expression,
            percent=f"{target_resistance * 100:g}%",
        ),
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.penetration_sources",
            "属性穿透来源：{sources}",
            sources=_source_names(penetration_terms, text_catalog=text_catalog),
        ),
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.penetration_total",
            "属性穿透合计 = {percent}",
            percent=f"{penetration * 100:g}%",
        ),
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.effective_intro",
            "有效抗性 = 目标抗性 - 属性穿透",
        ),
        f"    = {target_resistance * 100:g}% - {penetration * 100:g}% "
        f"= {effective * 100:g}%",
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.branch_choice",
            "因有效抗性 {effective} {comparison}，采用：{branch}",
            effective=f"{effective * 100:g}%",
            comparison=comparison,
            branch=branch_formula,
        ),
        "  " + localized_text(
            text_catalog,
            "battle_report.explanation.resistance.zone_line",
            "抗性区 = {substitution} = {value}",
            substitution=branch_substitution,
            value=_factor_value(factor),
        ),
    ]


def _factor_lines(
    factor: BattleHitReplayFactor, *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    if factor.factor_id.startswith("topple_character:"):
        lines = [
            localized_text(
                text_catalog,
                "battle_report.explanation.topple.formula",
                "{label} = 等级基础值 × 倾陷强度区 × "
                "敌方倾陷上限区 × 防御区 × 抗性区",
                label=factor.label,
            ),
            f"  = {factor.formula}",
            f"  = {_factor_value(factor)}",
        ]
        if factor.terms:
            lines.append("  " + localized_text(
                text_catalog,
                "battle_report.explanation.topple.term_sources",
                "本格来源：",
            ))
            lines.extend(
                f"    - {term.source_name}:{term.label} = {_term_value(term)}"
                f"（{term.evidence_basis}）"
                for term in factor.terms
            )
        lines.append("  " + localized_text(
            text_catalog,
            "battle_report.explanation.factor.evidence",
            "证据：{basis}",
            basis=factor.evidence_basis,
        ))
        return lines
    if factor.factor_id == "scaling":
        lines = _scaling_formula_lines(factor, text_catalog=text_catalog)
    elif factor.factor_id == "defense" and (
        detailed := _defense_formula_lines(factor, text_catalog=text_catalog)
    ):
        lines = detailed
    elif factor.factor_id == "resistance" and (
        detailed := _resistance_formula_lines(factor, text_catalog=text_catalog)
    ):
        lines = detailed
    else:
        formula = factor.formula or localized_text(
            text_catalog,
            "battle_report.explanation.factor.structured_value",
            "结构化计算值",
        )
        lines = [f"{factor.label} = {formula}"]
        if factor.terms:
            names, values = _sum_terms(factor.terms)
            lines.extend((
                "  " + localized_text(
                    text_catalog,
                    "battle_report.explanation.factor.sources",
                    "来源项：{names}",
                    names=names,
                ),
                "  " + localized_text(
                    text_catalog,
                    "battle_report.explanation.factor.substitutions",
                    "代入项：{values}",
                    values=values,
                ),
            ))
        lines.append(f"  = {_factor_value(factor)}")
    lines.append("  " + localized_text(
        text_catalog,
        "battle_report.explanation.factor.evidence",
        "证据：{basis}",
        basis=factor.evidence_basis,
    ))
    return lines


# 展示计算证据与原始观测的差别，不把固定轴候选写成实测事实。
