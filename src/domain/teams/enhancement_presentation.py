# 队伍卡片和历史详情共用本队计分收益、覆盖与停止状态；缺记录不补成百分比。
from dataclasses import dataclass

from src.domain.localization.catalog import localized_text
from src.domain.allocation.damage_display import fixed_coverage_note, search_is_complete, team_gain_ratio
from src.domain.teams.enhancement import validate_team_result_payload
from src.domain.teams.report_matching import ReportRange


@dataclass(frozen=True)
class TeamEnhancementPresentation:
    headline: str
    coverage: str
    search: str
    source: str


def team_enhancement_presentation(payload):
    validate_team_result_payload(payload)
    if payload["result_kind"] == "baseline":
        headline = "基础配装已保存（未记录已证实提升）"
    else:
        ratio = team_gain_ratio(payload["baseline_value"], payload["candidate_value"])
        headline = "本轮未找到提升" if ratio == 0 else localized_text(None, 'auto.domain.teams.enhancement_presentation.team_enhancement_presentation.f316146e0', '本队计分部分 {ratio}', ratio=f'{ratio:+.2%}')
        if 0 < abs(ratio) < .00005:
            headline = "本队计分部分 " + ("提升不足 0.01%" if ratio > 0 else "下降不足 0.01%")
    coverage = fixed_coverage_note(payload["coverage"]) or "覆盖率未记录；未计入部分的收益未知，不补成全场 100%。"
    state = payload["search"]
    reason = state.get("termination") or state.get("fallback_status") or state.get("overall_termination")
    labels = {"time_budget": "达到时间上限", "evaluation_budget": "达到评价次数上限", "round_budget": "达到轮数上限",
              "enumeration_budget": "候选遍历被截断", "budget_limited": "搜索预算截断", "not_ready": "模型或组件未就绪",
              "failed": "增强尝试失败", "local_optimum": "声明邻域内未发现进一步改善"}
    if search_is_complete(state):
        search = "已完成本次声明邻域搜索，不保证全局最优。"
    else:
        search = "搜索未完整确认" + ("：" + labels.get(reason, str(reason)) if reason else "") + "。"
    if state.get("blueprint_enumeration_complete") is False:
        search += "图纸枚举已截断。"
    scope = ReportRange(**payload["scope"])
    anchor = "原始 A" if payload["anchor_mode"] == "original" else "已启用编辑 A"
    return TeamEnhancementPresentation(headline, coverage, search, localized_text(None, 'auto.domain.teams.enhancement_presentation.team_enhancement_presentation.f014aba12', '战报 #{record_id} · {label} · {anchor}', record_id=f'{scope.record_id}', label=f'{scope.label}', anchor=f'{anchor}'))
