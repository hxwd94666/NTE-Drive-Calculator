# 分层展示冻结优化结论、收益和搜索限制，不重算历史值或混加全队指标。
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from src.domain.allocation.damage_display import fixed_coverage_note, search_is_complete, team_gain_ratio
from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text


_TERMINATIONS = {
    "evaluation_budget": "达到伤害计算次数上限，仍可能存在更好的配装。",
    "round_budget": "达到优化轮数上限，仍可能存在更好的配装。",
    "time_budget": "达到本次优化时长上限，仍可能存在更好的配装。",
    "enumeration_budget": "达到候选遍历上限，尚未检查所有组合。",
    "model_gap": "部分候选缺少可计算的机制，尚未排除这些候选更优的可能。",
}


_REASONS = {
    "direct_projection_growth_missing": "角色等级或突破缺少精确成长数据，请检查角色养成与静态资料。",
    "direct_projection_non_equipment_crit_mismatch": "冻结的养成暴击属性与角色配置不同，请核对弧盘、好感与暴击设置。",
    "axis_blueprint_geometry_missing": "新图纸缺少正式形状坐标，请更新配套静态资料。",
    "group_member_model_missing": "同级组中有角色缺少完整养成数据或伤害模型。",
    "unsupported_role_model": "该角色的标准伤害模型尚未支持。",
    "incomplete_baseline": "角色优先结果尚未配齐装备，请先检查库存和筛选条件。",
    "baseline_equipment_reserved": "基础方案用了已锁定配装的装备，请检查配装锁后重新计算。",
    "baseline_constraints_invalid": "基础配装不满足当前约束，请检查主词条、套装及暴击限制。",
    "axis_baseline_constraints_invalid": "实际装备不满足本次约束，请检查主词条、套装及暴击限制。",
    "non_positive_baseline": "基础伤害为零，暂不计算提升比例。",
    "axis_crit_fixed_projection_mismatch": "战报养成与当前配装条件的暴击属性不同，请核对角色养成与所选战报。",
    "non_positive_or_unavailable_baseline": "基础伤害为零或缺少计算依据，暂不计算提升比例。",
    "axis_group_member_unmapped": "同级组中有角色尚未关联正式角色身份。",
    "axis_group_member_not_in_report": "同级组中有角色不在所选战报中，请调整角色组或选择其他战报。",
    "axis_baseline_not_ready": "所选战报暂时没有可用于比较装备的伤害部分，请检查战报分析缺项。",
    "outer_realm_recipient_evidence_missing": "赛季环合增益缺少受益角色或生效区间证据，本次装备变化的轴伤收益暂不能确认；已保留基础配装。",
}

@dataclass(frozen=True, slots=True)
class DamageOptimizationPresentation:
    headline: str
    note: str
    details: str
    improved: bool = False


def _number(value) -> bool:
    # 历史元数据也走此入口；坏值只影响展示，不使整个配装页面退出。
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _explain_reason(reason: str, *, text_catalog=None) -> str:
    if reason in _REASONS:
        return _text(text_catalog, f"allocation.damage.reason.{reason}", _REASONS[reason])
    if reason.startswith("direct_projection_fork_"):
        return _text(text_catalog, "allocation.damage.reason.direct_projection_fork_generic", "弧盘等级、突破或精炼常驻属性尚未核实，请检查角色养成与静态资料。")
    if reason.startswith("axis_crit_fork_"):
        return _text(text_catalog, "allocation.damage.reason.axis_crit_fork_generic", "战报中的弧盘等级、突破或常驻属性尚未核实，请检查战报养成和静态资料。")
    if reason.startswith("axis_crit_"):
        return _text(text_catalog, "allocation.damage.reason.axis_crit_generic", "战报与本次配装的暴击属性来源未核对通过，请检查养成、好感与暴击设置。")
    # 内部错误码留在冻结元数据，不把协议字段堆进面向玩家的说明。
    return reason if any("\u4e00" <= char <= "\u9fff" for char in reason) else _text(text_catalog, "allocation.damage.reason.generic", "计算资料或分析组件未就绪，请检查角色配置与组件。")


def _amount(value: float, *, signed=False) -> str:
    # 极小提升保留有效数字；极大历史值用科学计数法，不撑宽卡片。
    if value and (abs(value) < 0.01 or abs(value) >= 1e12):
        return format(value, "+.6g" if signed else ".6g")
    return format(value, "+,.2f" if signed else ",.2f")


def _percentage(ratio: float) -> str | None:
    # 数值本身有限，也可能在百分比换算时溢出；此时改展示绝对变化。
    scaled = ratio * 100
    return f"{scaled:+.2f}%" if math.isfinite(scaled) else None


def _coverage_note(value) -> str:
    return fixed_coverage_note(value)


def _coverage_terms(text_catalog) -> str:
    return localized_text(
        text_catalog, "allocation.damage.detail.coverage_terms",
        "量化包括可用现有模型重算的伤害，以及已证实不受换装影响的伤害。"
        "估算指根据战报证据和已知机制推算的伤害。",
    )


def _coverage_details(text_catalog) -> str:
    return "\n".join((_coverage_terms(text_catalog), localized_text(
        text_catalog, "allocation.damage.detail.coverage_scope",
        "覆盖率按原战报伤害量计算，不是命中次数或预测准确率；未计入部分的换装收益未知。",
    )))


def _text(catalog, key: str, fallback: str) -> str:
    if catalog is None:
        return fallback
    return catalog.render(key)


def damage_optimization_presentation(value, *, text_catalog=None) -> DamageOptimizationPresentation | None:
    failure = value.get("postprocess_failure") if isinstance(value, Mapping) else None
    if (isinstance(failure, Mapping) and failure.get('baseline_complete') is True
            and value.get('result_kind') == 'baseline' and value.get('status') == 'baseline_only'):
        from src.integrations.preferences.locale_files import bundled_text_catalog
        catalog = text_catalog if text_catalog is not None else bundled_text_catalog()
        key = 'timeout_note' if failure.get('code') == 'damage_job_time_budget' else 'model_note'
        prefix = 'allocation.damage.baseline_retained.'
        return DamageOptimizationPresentation(catalog.render(prefix+'headline'), catalog.render(prefix+key), catalog.render(prefix+'details'))
    presentation = _damage_presentation(value, text_catalog=text_catalog)
    priority = value.get("baseline_priority") if isinstance(value, Mapping) else None
    if presentation is not None and isinstance(priority, Mapping):
        from dataclasses import replace
        note = priority.get("reason") or localized_text(
            text_catalog, "allocation.damage.note.baseline_priority",
            "基础配装按队内已观测出伤名次生成，两队同名次并列；使用本次背包。")
        presentation = replace(presentation, note=presentation.note + "\n" + str(note))
    return presentation


def _damage_presentation(value, *, text_catalog=None) -> DamageOptimizationPresentation | None:
    if not isinstance(value, Mapping) or not value:
        return None
    objective = value.get("objective")
    axis = objective == "frozen_axis_expected_v1"
    if not isinstance(objective, str) or objective not in {"frozen_axis_expected_v1", "standard_direct_v1"}:
        return DamageOptimizationPresentation(_text(text_catalog, "allocation.damage.headline.pending", "优化结果待核对"),
            _text(text_catalog, "allocation.damage.note.recheck", "目标类型未知，请重新计算。"),
            localized_text(text_catalog, "allocation.damage.detail.legacy_record_kept", "旧记录保留，不按当前模型重新解释。"))
    label = _text(text_catalog, "allocation.damage.label.axis", "轴伤优先") if axis else _text(text_catalog, "allocation.damage.label.direct", "直伤优先")
    coverage_note = _coverage_note(value.get("coverage")) if axis else ""
    if axis and value.get("coverage") is not None and not coverage_note:
        return DamageOptimizationPresentation(_text(text_catalog, "allocation.damage.headline.coverage_pending", "轴伤覆盖率待核对"),
            _text(text_catalog, "allocation.damage.note.coverage_invalid", "覆盖率记录异常，请重新计算。"),
            localized_text(text_catalog, "allocation.damage.detail.coverage_not_full_team", "未将部分目标当作完整全队收益展示。"))
    coverage_brief = ""
    if coverage_note:
        c = value["coverage"]
        strict = c["quantified_damage"] / c["basis_damage"] * 100
        estimated = c["estimated_damage"] / c["basis_damage"] * 100
        coverage_brief = localized_text(
            text_catalog, "allocation.damage.coverage.brief",
            " · 计分 {scored}%（量化 {quantified}%＋估算 {estimated}%）",
            scored=f"{strict + estimated:.1f}", quantified=f"{strict:.1f}", estimated=f"{estimated:.1f}")

    if value.get("result_kind") == "baseline" and value.get("status") == "baseline_only":
        pending_reason = value.get("reason")
        reason = (_explain_reason(str(pending_reason), text_catalog=text_catalog) if pending_reason
                  else localized_text(text_catalog, "allocation.damage.reason.optimization_incomplete", "伤害优化尚未完成。"))
        details = _text(text_catalog, "allocation.damage.note.baseline_saved", "保留本次角色优先生成的合法配装；未宣称伤害提升，可直接保存，无需重新计算。")
        if coverage_note:
            details += "\n" + _coverage_details(text_catalog)
        return DamageOptimizationPresentation(_text(text_catalog, "allocation.damage.headline.baseline_saved", "基础配装｜可保存") + coverage_brief, " ".join(filter(None, (reason, coverage_note))),
            details)
    event_scope = value.get("scope") or {}
    excluded = event_scope.get("excluded_unscoped_event_count", 0) if isinstance(event_scope, Mapping) else 0
    warning = (localized_text(text_catalog, "allocation.damage.warning.unscoped_events_excluded",
                              "已排除 {count} 击半场归属不明的记录，原战报保留。", count=excluded)
               if axis and type(excluded) is int and excluded > 0 else "")
    status = str(value.get("status") or "")
    if status != "completed":
        state = {"failed": _text(text_catalog, "allocation.damage.state.failed", "优化失败"), "budget_limited": _text(text_catalog, "allocation.damage.state.search_incomplete", "搜索未完成"), "baseline_only": _text(text_catalog, "allocation.damage.state.not_participating", "未参与优化")}.get(status, _text(text_catalog, "allocation.damage.state.pending", "暂未优化"))
        pending_reason = value.get("reason")
        reason = (_explain_reason(str(pending_reason), text_catalog=text_catalog) if pending_reason
                  else localized_text(text_catalog, "allocation.damage.reason.not_ready", "计算资料或分析组件未就绪，请检查后重试。"))
        if status == "budget_limited":
            reason = localized_text(text_catalog, "allocation.damage.reason.budget_exhausted", "搜索达到上限，尚未生成可保存的优化结果。")
        elif status == "baseline_only":
            reason = localized_text(text_catalog, "allocation.damage.reason.not_participating", "该角色未参与本轮伤害优化。")
        return DamageOptimizationPresentation(
            f"{label}｜{state}", reason,
            "\n".join(filter(None, (warning, localized_text(text_catalog, "allocation.damage.detail.save_baseline_hint",
                                                              "如需保存基础配装，请切回角色优先重新计算。")))),
        )
    if value.get("team_reports"):
        return _joint_team_presentation(value, text_catalog=text_catalog)
    before, after = value.get("baseline_value"), value.get("candidate_value")
    if not _number(before) or not _number(after) or after < before:
        return DamageOptimizationPresentation(f"{label}｜{_text(text_catalog, 'allocation.damage.headline.result_pending', '结果待核对')}",
            _text(text_catalog, "allocation.damage.note.result_invalid", "收益记录异常，请重新计算。"),
            localized_text(text_catalog, "allocation.damage.detail.invalid_not_fabricated", "未用缺失值、负值或非有限数伪造提升。"))
    change = after - before
    if axis:
        metric = ui_enum_text(text_catalog, "参与计分部分轴伤" if coverage_note else "全队轴伤估算")
    else:
        metric = ui_enum_text(text_catalog, "标准直伤指标")
    if change > 0:
        ratio = change / before if before > 0 else None
        gain = _percentage(ratio) if ratio is not None and math.isfinite(ratio) else None
        gain = gain or _amount(change, signed=True)
        # 小幅真实改善不能四舍五入后伪装成没有变化。
        if ratio is not None and 0 < ratio < 0.00005:
            gain = localized_text(text_catalog, "allocation.damage.gain.below_threshold", "提升不足 0.01%")
        headline = f"{metric} {gain}"
    else:
        headline = f"{label}｜{_text(text_catalog, 'allocation.damage.headline.no_improvement', '本轮未找到提升')}"
    termination = str(value.get("termination") or "")
    blueprint_limited = (value.get("blueprint_usage") or {}).get("complete") is False
    complete = termination == "local_optimum" and value.get("neighborhood_complete") is True and not blueprint_limited
    if not complete:
        headline += " · " + _text(text_catalog, "allocation.damage.state.search_incomplete", "搜索未完成")
    if warning:
        headline += localized_text(text_catalog, "allocation.damage.headline.unscoped_events_excluded",
                                   " · 排除 {count} 击归属不明记录", count=excluded)
    headline += coverage_brief
    note = localized_text(text_catalog, "allocation.damage.note.compare_baseline",
                          "对比本次角色优先：{before} → {after}。", before=_amount(before), after=_amount(after))
    if coverage_note:
        note += " " + coverage_note
    # 页面只保留口径、停止原因与必要限制；计数、组件哈希和诊断代码仍保存在原元数据。
    scope = (_text(text_catalog, "allocation.damage.scope.axis", "口径：所选战报事件的全队估算，按本次整队配装计算；各角色显示同一值，请勿相加。") if axis else
             _text(text_catalog, "allocation.damage.scope.standard", "口径：标准攻击估算（卡带按满级），不代表实战伤害。"))
    paired = value.get("neighborhood") in ("single_and_pair", "single_and_pair_fixed_signature") or "group_index" in value
    if complete and value.get("neighborhood") == "single_pair_exchange_blueprint":
        search = _text(text_catalog, "allocation.damage.search.exchange", "已检查单件、同角色双件、双角色交换和冻结范围内换图纸，未找到进一步提升。")
    elif complete and value.get("exchange_complete") is True:
        search = _text(text_catalog, "allocation.damage.search.pair", "固定布局的单件、同角色双件及双角色交换未找到进一步提升。")
    elif complete:
        search = _text(text_catalog, "allocation.damage.search.pair", "固定布局的单件、同角色双件及双角色交换未找到进一步提升。") if paired else _text(text_catalog, "allocation.damage.search.single", "固定布局的单件替换未找到进一步提升。")
    else:
        search = (_text(text_catalog, f"allocation.damage.termination.{termination}", _TERMINATIONS[termination])
                  if termination in _TERMINATIONS else _text(text_catalog, "allocation.damage.search.incomplete", "本轮搜索尚未完成，可能还有更好的配装。"))
    if coverage_note:
        scope = localized_text(text_catalog, "allocation.damage.scope.axis_coverage",
            "口径：同一批可计分伤害的全队目标，包含明确标注的机制估算；其余伤害未参与装备比较。各角色显示同一值，请勿相加。")
    details = [scope, localized_text(text_catalog, "allocation.damage.detail.search_line", "搜索：{search}不保证全局最优。", search=search)]
    if coverage_note:
        details.append(_coverage_details(text_catalog))
    if blueprint_limited:
        details.append(localized_text(text_catalog, "allocation.damage.detail.blueprint_budget",
            "图纸枚举达到预算，仅搜索本次已生成并验证的布局；当前合法结果仍可保存。"))
    if warning:
        details.insert(0, warning)
    if axis and isinstance(event_scope, Mapping) and event_scope.get("settlement_profile_character_count", 0):
        details.append(localized_text(text_catalog, "allocation.damage.detail.character_inputs_frozen",
            "养成缺项沿用战报结算时冻结的资料，不读取当前角色档案。"))
    if value.get("overall_termination") == "not_ready":
        details.append(localized_text(text_catalog, "allocation.damage.detail.partial_groups", "部分角色组未完成，本次只保存已完成的组。"))
    return DamageOptimizationPresentation(headline, note, "\n".join(details), change > 0)


def damage_optimization_summary(value, *, text_catalog=None) -> str:
    """保留纯文本入口；首屏只展示结论与必要限制，完整口径由详情承载。"""
    presentation = damage_optimization_presentation(value, text_catalog=text_catalog)
    return "\n".join((presentation.headline, presentation.note)) if presentation else ""


def _joint_team_presentation(value, *, text_catalog=None):
    pending = localized_text(text_catalog, "allocation.damage.team.headline_pending", "双队收益待核对")
    reports = value.get("team_reports")
    before, after = value.get("baseline_value"), value.get("candidate_value")
    if (not isinstance(reports, list) or len(reports) != 2 or not _number(before) or before != 1
            or not _number(after) or after < 1):
        return DamageOptimizationPresentation(pending,
            localized_text(text_catalog, "allocation.damage.team.note_normalization_invalid", "归一化记录异常，请重新计算。"), "")
    lines = []
    ratios = []
    has_coverage = False
    for index, report in enumerate(reports, 1):
        if (not isinstance(report, Mapping) or not isinstance(report.get("baseline"), Mapping)
                or not isinstance(report.get("selected_result"), Mapping)):
            return DamageOptimizationPresentation(pending,
                localized_text(text_catalog, "allocation.damage.team.note_scoring_invalid", "战报计分记录异常，请重新计算。"), "")
        baseline = report.get("baseline", {}).get("candidate_value")
        candidate = report.get("selected_result", {}).get("candidate_value")
        if not _number(baseline) or baseline <= 0 or not _number(candidate):
            return DamageOptimizationPresentation(pending,
                localized_text(text_catalog, "allocation.damage.team.note_scoring_invalid", "战报计分记录异常，请重新计算。"), "")
        ratio = team_gain_ratio(baseline, candidate)
        if not math.isfinite(ratio):
            return DamageOptimizationPresentation(pending,
                localized_text(text_catalog, "allocation.damage.team.note_gain_out_of_range", "战报增幅超出可展示范围。"), "")
        ratios.append(ratio)
        coverage = _coverage_note(report.get("selected_result", {}).get("coverage"))
        has_coverage = has_coverage or bool(coverage)
        lines.append(localized_text(text_catalog, "allocation.damage.team.line",
            "队伍 {index} · 战报 #{record_id}：计分部分 {ratio}。",
            index=index, record_id=report.get('battle_record_id'), ratio=f"{ratio:+.2%}") + coverage)
    if not math.isclose(sum(ratios) / 2, after - 1, rel_tol=1e-9, abs_tol=1e-9):
        return DamageOptimizationPresentation(pending,
            localized_text(text_catalog, "allocation.damage.team.note_total_mismatch", "综合增幅与两队结果不一致。"), "")
    headline = localized_text(text_catalog, "allocation.damage.team.headline", "双队综合增幅 {gain}", gain=f"{after - 1:+.2%}")
    if not search_is_complete(value):
        headline += " · " + _text(text_catalog, "allocation.damage.state.search_incomplete", "搜索未完成")
    details = localized_text(text_catalog, "allocation.damage.team.details",
        "两份战报各以本次基础配装的计分伤害为 100，综合增幅为两队相对增幅的等权平均。"
        "各队独立冻结计分范围，未计入伤害的收益未知；覆盖率不是准确率。"
        "队伍之间允许收益取舍，关联成员须一起保存。各成员展示同一综合结果，请勿相加。搜索不保证全局最优。")
    if has_coverage:
        details += "\n" + _coverage_terms(text_catalog)
    return DamageOptimizationPresentation(headline, "\n".join(lines), details, after > 1)
