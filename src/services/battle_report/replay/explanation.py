# 把结构化逐击重放结果投影为可复制的完整算式与证据解释。

from __future__ import annotations

from collections.abc import Sequence
from functools import reduce
from operator import mul

from src.domain.battle_report.counterfactual.results import BattleBuildHitCounterfactual
from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.replay.models import BattleHitReplayResult
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.domain.localization.catalog import TextCatalog, localized_text
from src.services.battle_report.damage.composition import (
    classify_battle_hit_channel,
)
from src.services.battle_report.buffs.attribute_projection import (
    BattleBuffAttributeProjectionService,
)
from src.services.official_role.skill_names import preferred_battle_damage_name
from src.services.battle_report.replay.critical_explanation import critical_explanation


from src.services.battle_report.replay.explanation_sections import (
    _COUNTERFACTUAL_METHOD_LABELS,
    _CRIT_STATES,
    _FORMULA_FACTOR_IDS,
    _REACTION_FORMULA_FACTOR_IDS,
    _REQUIRED_FORMULA_FACTOR_IDS,
    _REQUIRED_REACTION_FACTOR_IDS,
    _buff_modifier_text,
    _confidence_summary,
    _damage,
    _direction_label,
    _factor_lines,
    _factor_value,
    _group_buff_intervals,
    _label_table,
    _percent,
)
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
        projection=None, allow_projection_fallback: bool = True,
        text_catalog: TextCatalog | None = None,
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
            localized_text(
                text_catalog,
                "battle_report.explanation.header.hit_id",
                "逐击 ID：{event_id}",
                event_id=hit.event_id,
            ),
            localized_text(
                text_catalog,
                "battle_report.explanation.header.formula_type",
                "对应公式类型：{formula_type}",
                formula_type=formula_type,
            ),
            localized_text(
                text_catalog,
                "battle_report.explanation.header.target",
                "目标：{target}",
                target=hit.target_name,
            ),
            "",
        ]
        if replay is not None and replay.formula_context_kind:
            unknown_label = localized_text(
                text_catalog,
                "battle_report.explanation.common.unknown",
                "未知",
            )
            lines.extend((
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.title",
                    "【派生公式归属】",
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.characters",
                    "执行角色：{action}；倍率定义角色：{definition_owner}；"
                    "面板角色：{panel}",
                    action=replay.formula_action_character_id or unknown_label,
                    definition_owner=(
                        replay.formula_definition_owner_character_id
                        or unknown_label
                    ),
                    panel=replay.formula_panel_character_id or unknown_label,
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.skill_level",
                    "技能等级角色：{level_owner}；等级技能：{ability}",
                    level_owner=(
                        replay.formula_skill_level_character_id or unknown_label
                    ),
                    ability=replay.formula_skill_level_ability_id or unknown_label,
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.attribute",
                    "公式属性：{attribute}；属性来源：{attribute_source}",
                    attribute=replay.formula_damage_attribute or "unknown",
                    attribute_source=(
                        replay.formula_damage_attribute_source or "unknown"
                    ),
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.inference",
                    "推论类型：{kind}；置信度：{confidence}",
                    kind=replay.formula_context_kind,
                    confidence=replay.formula_context_confidence or unknown_label,
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.basis",
                    "依据：{basis}",
                    basis=replay.formula_context_basis,
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula_context.scope",
                    "口径：这里只覆盖公式消费者，不改写 Core 原始逐击角色、"
                    "动作泳道或技能构成。",
                ),
                "",
            ))
        if counterfactual is not None:
            quantification = counterfactual.quantification
            projected_damage = (
                counterfactual.candidate_damage
                if counterfactual.candidate_damage is not None
                else counterfactual.known_projection_damage
            )
            delta = (
                None
                if projected_damage is None
                else projected_damage - counterfactual.baseline_damage
            )
            gain_percent = (
                delta / counterfactual.baseline_damage * 100.0
                if delta is not None and counterfactual.baseline_damage
                else None
            )
            direction = _direction_label(delta, text_catalog=text_catalog)
            method = _label_table(
                _COUNTERFACTUAL_METHOD_LABELS, text_catalog=text_catalog,
            ).get(quantification.method, quantification.method)
            projection_label = (
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.projection.full",
                    "完整候选",
                )
                if counterfactual.candidate_damage is not None
                else localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.projection.quantified",
                    "已量化变化",
                )
                if counterfactual.known_projection_damage is not None
                else localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.projection.unquantified",
                    "候选未量化",
                )
            )
            gain_text = (
                "—" if gain_percent is None else f"{gain_percent:+.2f}%"
            )
            lines.extend((
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.title",
                    "【调整后边际】",
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.baseline_line",
                    "原始逐击：{baseline}    {projection_label}：{projected}",
                    baseline=f"{counterfactual.baseline_damage:,.2f}",
                    projection_label=projection_label,
                    projected=_damage(projected_damage),
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.delta_line",
                    "{direction}：{delta}（{gain}）    量化状态：{status}    "
                    "证据置信度：{confidence}",
                    direction=direction,
                    delta=_damage(delta),
                    gain=gain_text,
                    status=quantification.status,
                    confidence=quantification.confidence,
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.formula_values",
                    "原始公式值：{baseline}    候选公式值：{candidate}",
                    baseline=_damage(counterfactual.baseline_formula_damage),
                    candidate=_damage(counterfactual.candidate_formula_damage),
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.method",
                    "量化方法：{method}",
                    method=method,
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.counterfactual.explanation",
                    "说明：{explanation}",
                    explanation=quantification.explanation,
                ),
                *(
                    (
                        localized_text(
                            text_catalog,
                            "battle_report.explanation.counterfactual.heuristic_reference",
                            "启发式参考：{value}（不进入量化收益）",
                            value=(
                                f"{counterfactual.heuristic_projection_damage:,.2f}"
                            ),
                        ),
                    )
                    if counterfactual.heuristic_projection_damage is not None
                    else ()
                ),
                *(
                    (
                        localized_text(
                            text_catalog,
                            "battle_report.explanation.counterfactual.gaps",
                            "未决依赖：{gaps}",
                            gaps="；".join(
                                gap.explanation for gap in quantification.gaps
                            ),
                        ),
                    )
                    if quantification.gaps
                    else ()
                ),
                *(
                    (
                        localized_text(
                            text_catalog,
                            "battle_report.explanation.counterfactual.source_event",
                            "原轴触发逐击：{event_id}",
                            event_id=counterfactual.source_event_id,
                        ),
                        localized_text(
                            text_catalog,
                            "battle_report.explanation.counterfactual.scope.event",
                            "口径：这是候选配置在既有触发时点新增的派生结算；"
                            "基线为 0，不改写原始触发逐击，也不是新的实测逐击。",
                        ),
                    )
                    if counterfactual.source_event_id
                    else (
                        localized_text(
                            text_catalog,
                            "battle_report.explanation.counterfactual.scope.hit",
                            "口径：固定原战报动作与命中，只改变该击候选伤害；"
                            "这不是新的实测逐击。",
                        ),
                    )
                ),
                "",
            ))
        if replay is None:
            lines.extend((
                (
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.no_replay.candidate_formula",
                        "候选配置公式：—",
                    )
                    if counterfactual is not None
                    else localized_text(
                        text_catalog,
                        "battle_report.explanation.no_replay.damage_line",
                        "实际伤害：{actual}    预计伤害：—    预计误差：—",
                        actual=f"{hit.damage:,.2f}",
                    )
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.critical.inferred_unavailable",
                    "推断暴击：无法推断",
                ),
                "",
                (
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.no_replay.candidate_formula_note",
                        "当前逐击没有候选配置公式；上方数值来自分级估计。",
                    )
                    if counterfactual is not None
                    else localized_text(
                        text_catalog,
                        "battle_report.explanation.no_replay.missing_replay_note",
                        "当前逐击没有重放结果；历史分析需要按最新模型重新加载。",
                    )
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
                localized_text(
                    text_catalog,
                    "battle_report.explanation.damage.comparison_line",
                    "实际伤害：{actual}    预计伤害：{selected}    "
                    "预计误差：{error}",
                    actual=_damage(replay.observed_damage),
                    selected=_damage(replay.selected_damage),
                    error=_percent(signed_error, signed=True),
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.damage.error_definition",
                    "误差定义：(预计伤害 - 实际伤害) / 实际伤害；"
                    "正值为高估，负值为低估。",
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.damage.expectation_line",
                    "预计伤害期望：{expected}    实际伤害期望：{corrected}",
                    expected=_damage(replay.expected_damage),
                    corrected=_damage(replay.corrected_expected_damage),
                ),
                localized_text(
                    text_catalog,
                    "battle_report.explanation.damage.expectation_scope",
                    "期望口径：向下取整后的未暴击/暴击候选按暴击率加权；"
                    "实际期望按本击有符号误差同比补正。",
                ),
                critical_explanation(hit, replay),
                "",
            ))
            if replay.observed_damage_source != "reported_hit":
                reported = (
                    hit.damage
                    if replay.reported_damage is None
                    else replay.reported_damage
                )
                lines.extend((
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.damage.observation_source",
                        "公式比较观测来源：{basis}",
                        basis=replay.observed_damage_basis,
                    ),
                    (
                        (
                            localized_text(
                                text_catalog,
                                "battle_report.explanation.damage.reported_effective",
                                "计入战报有效伤害：{value}；",
                                value=_damage(reported),
                            )
                            if replay.observed_damage_source
                            == "reported_hit_before_overkill"
                            else localized_text(
                                text_catalog,
                                "battle_report.explanation.damage.reported_raw",
                                "原始逐击上报：{value}；",
                                value=_damage(reported),
                            )
                        )
                        + localized_text(
                            text_catalog,
                            "battle_report.explanation.damage.comparable",
                            "公式可比观测：{value}。",
                            value=_damage(replay.observed_damage),
                        )
                        + localized_text(
                            text_catalog,
                            "battle_report.explanation.damage.independence",
                            "两者保持独立。",
                        )
                    ),
                    "",
                ))
        else:
            lines.extend((
                localized_text(
                    text_catalog,
                    "battle_report.explanation.critical.candidate_inferred",
                    "候选配置推断暴击：{state}（置信度{confidence}）",
                    state=_label_table(
                        _CRIT_STATES, text_catalog=text_catalog,
                    ).get(replay.critical_state, replay.critical_state),
                    confidence=replay.confidence,
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
            localized_text(
                text_catalog,
                "battle_report.explanation.formula.title.candidate",
                "【候选配置伤害公式】",
            )
            if counterfactual is not None
            else localized_text(
                text_catalog,
                "battle_report.explanation.formula.title.actual",
                "【伤害公式】",
            )
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
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula.topple_total",
                    "团队倾陷伤害 = {expression}",
                    expression=expression,
                ),
                f"  = {substituted}",
                f"  = floor({raw_damage:,.6f}) = "
                f"{_damage(replay.non_critical_damage)}",
            ))
        elif _REQUIRED_FORMULA_FACTOR_IDS.issubset(factors):
            expression = " × ".join(factor.label for factor in formula_factors)
            substituted = " × ".join(_factor_value(factor) for factor in formula_factors)
            noncrit = reduce(mul, (factor.value for factor in formula_factors), 1.0)
            lines.extend((
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula.non_critical",
                    "伤害（未暴击） = {expression}",
                    expression=expression,
                ),
                f"  = {substituted}",
                f"  = floor({noncrit:,.6f}) = "
                f"{_damage(replay.non_critical_damage)}",
            ))
            critical = factors.get("critical")
            if critical is not None:
                raw_critical = noncrit * critical.value
                lines.extend((
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.formula.critical",
                        "伤害（暴击） = 未取整伤害 × {label}",
                        label=critical.label,
                    ),
                    f"  = {noncrit:,.2f} × {_factor_value(critical)}",
                    f"  = floor({raw_critical:,.6f}) = "
                    f"{_damage(replay.critical_damage)}",
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
                localized_text(
                    text_catalog,
                    "battle_report.explanation.formula.non_critical",
                    "伤害（未暴击） = {expression}",
                    expression=expression,
                ),
                f"  = {substituted}",
                f"  = floor({noncrit:,.6f}) = "
                f"{_damage(replay.non_critical_damage)}",
            ))
            critical = factors.get("critical")
            if critical is not None:
                raw_critical = noncrit * critical.value
                lines.extend((
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.formula.critical",
                        "伤害（暴击） = 未取整伤害 × {label}",
                        label=critical.label,
                    ),
                    f"  = {noncrit:,.2f} × {_factor_value(critical)}",
                    f"  = floor({raw_critical:,.6f}) = "
                    f"{_damage(replay.critical_damage)}",
                ))
        else:
            missing_target = any(
                "单目标防御与抗性" in row
                for row in replay.missing_evidence
            )
            if replay.formula_type.startswith("直伤") and missing_target:
                lines.append(localized_text(
                    text_catalog,
                    "battle_report.explanation.formula.missing_target_evidence",
                    "已匹配直伤适配器，但缺少本击可用的敌方防御与抗性，"
                    "暂时不能完成数值等式。",
                ))
            else:
                lines.append(localized_text(
                    text_catalog,
                    "battle_report.explanation.formula.missing_adapter",
                    "当前类型尚无完整反事实适配器，不能安全拼出数值等式。",
                ))

        if replay.factors:
            lines.extend((
                "",
                localized_text(
                    text_catalog,
                    "battle_report.explanation.section.factors",
                    "【乘区公式】",
                ),
            ))
            for factor in replay.factors:
                lines.extend(_factor_lines(factor, text_catalog=text_catalog))
                lines.append("")

        if projection is None and allow_projection_fallback:
            projection = BattleBuffAttributeProjectionService.project_hit(hit, active_buffs)
        if projection is None:
            return "\n".join((
                *lines,
                localized_text(
                    text_catalog,
                    "battle_report.explanation.projection.unavailable",
                    "本击未生成原生 Buff 投影详情；公式结果保持已计算值。",
                ),
            ))
        decision_by_id = {
            row.interval_id: row for row in projection.decisions
        }
        if not allow_projection_fallback:
            lines.append(localized_text(
                text_catalog,
                "battle_report.explanation.projection.formula_scope",
                "Buff投影口径：正式公式命中视图；倾陷各角色属性以贡献乘区为准。",
            ))
        lines.append(
            localized_text(
                text_catalog,
                "battle_report.explanation.buff.title.applied",
                "【本击 Buff：已投影（是否被公式消费见乘区）】",
            )
            if not any(
                "单目标防御与抗性" in row
                for row in replay.missing_evidence
            )
            else localized_text(
                text_catalog,
                "battle_report.explanation.buff.title.projectable",
                "【本击 Buff：可投影（公式输入不完整）】",
            )
        )
        if active_buffs:
            applied = tuple(
                interval
                for interval in active_buffs
                if decision_by_id.get(interval.interval_id) is not None
                and decision_by_id[interval.interval_id].status == "applied"
            )
            structured_rule = localized_text(
                text_catalog,
                "battle_report.explanation.buff.structured_rule",
                "已采用结构化规则",
            )
            for intervals, decision, interval_count, total_stacks in (
                _group_buff_intervals(applied, decision_by_id)
            ):
                interval = intervals[0]
                modifier_text = _buff_modifier_text(
                    intervals,
                    decision,
                    total_stacks,
                    text_catalog=text_catalog,
                )
                merge_text = (
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.buff.merge_note",
                        "，合并 {count} 条同类区间",
                        count=interval_count,
                    )
                    if interval_count > 1
                    else ""
                )
                lines.append(localized_text(
                    text_catalog,
                    "battle_report.explanation.buff.entry_line",
                    "- {buff_name} ×{stacks}层（{scope}，状态{state_confidence}，"
                    "数值{value_confidence}{merge}）：{modifier}\n"
                    "  ID：{effect_id}\n"
                    "  资产：{asset_path}",
                    buff_name=interval.buff_name,
                    stacks=total_stacks,
                    scope=interval.target_scope,
                    state_confidence=_confidence_summary(
                        tuple(row.state_confidence for row in intervals),
                        text_catalog=text_catalog,
                    ),
                    value_confidence=_confidence_summary(
                        tuple(row.value_confidence for row in intervals),
                        text_catalog=text_catalog,
                    ),
                    merge=merge_text,
                    modifier=modifier_text or structured_rule,
                    effect_id=interval.source_effect_definition_id,
                    asset_path=interval.buff_asset_path,
                ))
            if not applied:
                lines.append(localized_text(
                    text_catalog,
                    "battle_report.explanation.buff.discarded_none",
                    "- 没有 Buff 数值进入本击公式。",
                ))
        else:
            lines.append(localized_text(
                text_catalog,
                "battle_report.explanation.buff.intervals_none",
                "- 没有匹配到可投影的命中时 Buff 区间。",
            ))

        status_titles = {
            "not_applied": localized_text(
                text_catalog,
                "battle_report.explanation.buff.title.not_applied",
                "【本击 Buff：未采用】",
            ),
            "unresolved": localized_text(
                text_catalog,
                "battle_report.explanation.buff.title.unresolved",
                "【本击 Buff：待确认/结构化】",
            ),
        }
        for status, title in status_titles.items():
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
                reason = "；".join(decision.reasons) or localized_text(
                    text_catalog,
                    "battle_report.explanation.buff.reason_missing",
                    "未记录排除原因",
                )
                modifier_text = _buff_modifier_text(
                    intervals,
                    decision,
                    total_stacks,
                    text_catalog=text_catalog,
                )
                if modifier_text:
                    reason += localized_text(
                        text_catalog,
                        "battle_report.explanation.buff.structured_value_suffix",
                        "；结构化值：{modifier}",
                        modifier=modifier_text,
                    )
                count_text = (
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.buff.count_note",
                        " ×{count} 条同类区间",
                        count=interval_count,
                    )
                    if interval_count > 1
                    else ""
                )
                stack_text = (
                    localized_text(
                        text_catalog,
                        "battle_report.explanation.buff.stack_note",
                        "，合计 {total} 层",
                        total=total_stacks,
                    )
                    if total_stacks != interval_count
                    else ""
                )
                lines.append(localized_text(
                    text_catalog,
                    "battle_report.explanation.buff.skipped_line",
                    "- {buff_name}{count}（{reason}{stacks}）\n"
                    "  ID：{effect_id}\n"
                    "  资产：{asset_path}",
                    buff_name=interval.buff_name,
                    count=count_text,
                    reason=reason,
                    stacks=stack_text,
                    effect_id=interval.source_effect_definition_id,
                    asset_path=interval.buff_asset_path,
                ))

        lines.extend((
            "",
            localized_text(
                text_catalog,
                "battle_report.explanation.section.evidence",
                "【证据边界】",
            ),
        ))
        if replay.missing_evidence:
            lines.extend(f"- {row}" for row in replay.missing_evidence)
        else:
            lines.append(localized_text(
                text_catalog,
                "battle_report.explanation.evidence.no_gaps",
                "- 当前公式没有记录额外缺口；逐击观测值仍是最终强证据。",
            ))
        if counterfactual is not None:
            lines.append(localized_text(
                text_catalog,
                "battle_report.explanation.evidence.counterfactual_scope",
                "- 调整后数值属于固定轴反事实；原始实测逐击与数据库保持不变。",
            ))
        return "\n".join(lines)
