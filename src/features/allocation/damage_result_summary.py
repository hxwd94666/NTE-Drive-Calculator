# 分层展示冻结优化结论、收益和搜索限制，不重算历史值或混加全队指标。
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math


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
    "axis_baseline_not_ready": "所选战报的基础配装尚未得到完整伤害估算，请先检查战报分析缺项。",
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


def _explain_reason(reason: str) -> str:
    if reason in _REASONS:
        return _REASONS[reason]
    if reason.startswith("direct_projection_fork_"):
        return "弧盘等级、突破或精炼常驻属性尚未核实，请检查角色养成与静态资料。"
    if reason.startswith("axis_crit_fork_"):
        return "战报中的弧盘等级、突破或常驻属性尚未核实，请检查战报养成和静态资料。"
    if reason.startswith("axis_crit_"):
        return "战报与本次配装的暴击属性来源未核对通过，请检查养成、好感与暴击设置。"
    # 内部错误码留在冻结元数据，不把协议字段堆进面向玩家的说明。
    return reason if any("\u4e00" <= char <= "\u9fff" for char in reason) else "计算资料或分析组件未就绪，请检查角色配置与组件。"


def _amount(value: float, *, signed=False) -> str:
    # 极小提升保留有效数字；极大历史值用科学计数法，不撑宽卡片。
    if value and (abs(value) < 0.01 or abs(value) >= 1e12):
        return format(value, "+.6g" if signed else ".6g")
    return format(value, "+,.2f" if signed else ",.2f")


def _percentage(ratio: float) -> str | None:
    # 数值本身有限，也可能在百分比换算时溢出；此时改展示绝对变化。
    scaled = ratio * 100
    return f"{scaled:+.2f}%" if math.isfinite(scaled) else None


def damage_optimization_presentation(value) -> DamageOptimizationPresentation | None:
    if not isinstance(value, Mapping) or not value:
        return None
    objective = value.get("objective")
    axis = objective == "frozen_axis_expected_v1"
    if not isinstance(objective, str) or objective not in {"frozen_axis_expected_v1", "standard_direct_v1"}:
        return DamageOptimizationPresentation("优化结果待核对", "目标类型未知，请重新计算。", "旧记录保留，不按当前模型重新解释。")
    label = "轴伤优先" if axis else "直伤优先"
    event_scope = value.get("scope") or {}
    excluded = event_scope.get("excluded_unscoped_event_count", 0) if isinstance(event_scope, Mapping) else 0
    warning = f"已排除 {excluded} 击半场归属不明的记录，原战报保留。" if axis and type(excluded) is int and excluded > 0 else ""
    status = str(value.get("status") or "")
    if status != "completed":
        state = {"failed": "优化失败", "budget_limited": "搜索未完成", "baseline_only": "未参与优化"}.get(status, "暂未优化")
        reason = _explain_reason(str(value.get("reason") or "计算资料或分析组件未就绪，请检查后重试。"))
        if status == "budget_limited":
            reason = "搜索达到上限，尚未生成可保存的优化结果。"
        elif status == "baseline_only":
            reason = "该角色未参与本轮伤害优化。"
        return DamageOptimizationPresentation(
            f"{label}｜{state}", reason,
            "\n".join(filter(None, (warning, "如需保存基础配装，请切回角色优先重新计算。"))),
        )
    before, after = value.get("baseline_value"), value.get("candidate_value")
    if not _number(before) or not _number(after) or after < before:
        return DamageOptimizationPresentation(f"{label}｜结果待核对", "收益记录异常，请重新计算。", "未用缺失值、负值或非有限数伪造提升。")
    change = after - before
    metric = "全队轴伤估算" if axis else "标准直伤指标"
    if change > 0:
        ratio = change / before if before > 0 else None
        gain = _percentage(ratio) if ratio is not None and math.isfinite(ratio) else None
        gain = gain or _amount(change, signed=True)
        # 小幅真实改善不能四舍五入后伪装成没有变化。
        if ratio is not None and 0 < ratio < 0.00005:
            gain = "提升不足 0.01%"
        headline = f"{metric} {gain}"
    else:
        headline = f"{label}｜本轮未找到提升"
    termination = str(value.get("termination") or "")
    complete = termination == "local_optimum" and value.get("neighborhood_complete") is True
    if not complete:
        headline += " · 搜索未完成"
    if warning:
        headline += f" · 排除 {excluded} 击归属不明记录"
    note = f"对比本次角色优先：{_amount(before)} → {_amount(after)}。"
    # 页面只保留口径、停止原因与必要限制；计数、组件哈希和诊断代码仍保存在原元数据。
    scope = ("口径：所选战报事件的全队估算，按本次整队配装计算；各角色显示同一值，请勿相加。" if axis else
             "口径：标准攻击估算（卡带按满级），不代表实战伤害。")
    paired = value.get("neighborhood") in ("single_and_pair", "single_and_pair_fixed_signature") or "group_index" in value
    if complete and value.get("neighborhood") == "single_pair_exchange_blueprint":
        search = "已检查单件、同角色双件、双角色交换和冻结范围内换图纸，未找到进一步提升。"
    elif complete and value.get("exchange_complete") is True:
        search = "固定布局的单件、同角色双件及双角色交换未找到进一步提升。"
    elif complete:
        search = "固定布局的单件及同角色双件替换未找到进一步提升。" if paired else "固定布局的单件替换未找到进一步提升。"
    else:
        search = _TERMINATIONS.get(termination, "本轮搜索尚未完成，可能还有更好的配装。")
    details = [scope, "搜索：" + search + "不保证全局最优。"]
    if warning:
        details.insert(0, warning)
    if axis and isinstance(event_scope, Mapping) and event_scope.get("settlement_profile_character_count", 0):
        details.append("养成缺项沿用战报结算时冻结的资料，不读取当前角色档案。")
    if value.get("overall_termination") == "not_ready":
        details.append("部分角色组未完成，本次只保存已完成的组。")
    return DamageOptimizationPresentation(headline, note, "\n".join(details), change > 0)


def damage_optimization_summary(value) -> str:
    """保留纯文本入口；首屏只展示结论与必要限制，完整口径由详情承载。"""
    presentation = damage_optimization_presentation(value)
    return "\n".join((presentation.headline, presentation.note)) if presentation else ""
