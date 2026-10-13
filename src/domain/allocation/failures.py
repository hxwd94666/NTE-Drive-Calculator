# 合并冻结失败证据和一行说明；只有可证明的短缺才修正已有结果。
"""诊断与渲染职责不同：文本不隐式重诊断，未知组员不伪装为已证明失败。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

from src.models.equipment import Drive

if TYPE_CHECKING:
    from src.domain.stat_catalog import StatCatalog
    from src.optimizer.allocation_kernel import AllocationKernelRequest


_RESERVATION_REASON = "前序同分驱动无法一对一回填"
_GROUP_SHORTAGE_REASON = "同级组前序预留回填未通过，本角色独立可行性仍未判定。"


def _mandatory_shape_counts(blueprints: list[dict]) -> dict[str, int]:
    if not blueprints:
        return {}
    mandatory: Counter[str] | None = None
    for blueprint in blueprints:
        pieces = (
            *(blueprint.get("set_pieces") or ()),
            *(blueprint.get("extra_pieces") or ()),
        )
        counts = Counter(str(shape) for shape in pieces)
        if mandatory is None:
            mandatory = counts
        else:
            mandatory &= counts
    return dict(mandatory or {})


def _stat_name(catalog: StatCatalog, raw: object) -> str:
    name = str(raw or "").strip()
    return catalog.normalize_stat_name(name, is_percent="%" in name) or name


def _shortage_reason(
    role: str, request: AllocationKernelRequest, catalog: StatCatalog,
    drives: list[Drive],
) -> str | None:
    blueprints = request.blueprints_db.get(role, [])
    if not blueprints:
        return None
    config = request.stat_priority_configs.get(role) or {}
    # 零权重黑名单只影响评分，不构造硬剔除短缺证据。
    hard_blacklist = bool(config.get("blacklist")) and not config.get("blacklist_zero_weight")
    blacklist = (
        {_stat_name(catalog, stat) for stat in config["blacklist"]}
        if hard_blacklist else set()
    )
    by_shape = Counter(drive.shape_id for drive in drives)
    eligible = Counter(
        drive.shape_id for drive in drives
        if not blacklist or not any(
            _stat_name(catalog, stat) in blacklist for stat in drive.sub_stats
        )
    )

    def shortage(counts: Counter[str]):
        for shape, needed in sorted(counts.items()):
            if eligible[shape] < needed:
                return shape, needed, by_shape[shape], eligible[shape]
        return None

    # 只以全部冻结图纸证明短缺；存在无缺口图纸时不改为硬失败。
    mandatory = _mandatory_shape_counts(blueprints)
    common = shortage(Counter(mandatory))
    if common is not None:
        shape, needed, raw_count, eligible_count = common
    else:
        per_blueprint = [
            shortage(Counter(str(shape) for shape in (
                *(blueprint.get("set_pieces") or ()),
                *(blueprint.get("extra_pieces") or ()),
            )))
            for blueprint in blueprints
        ]
        if any(item is None for item in per_blueprint):
            return None
        shape, needed, raw_count, eligible_count = per_blueprint[0]
        cause = (
            "经副词条黑名单过滤后合格"
            if hard_blacklist and raw_count >= needed else "候选"
        )
        return (
            f"全部 {len(blueprints)} 张图纸各有驱动形状缺口；"
            f"例如 {shape} 需要 {needed} 件，{cause} {eligible_count} 件。"
        )
    if hard_blacklist and raw_count >= needed:
        return (
            f"必需 {shape} 驱动经副词条黑名单过滤后不足："
            f"至少需要 {needed} 件，合格 {eligible_count} 件；"
            "请调整黑名单或补充合格驱动。"
        )
    return (
        f"本次候选缺少必需 {shape} 驱动："
        f"至少需要 {needed} 件，候选 {raw_count} 件；"
        "请检查筛选或补充该形状。"
    )


def explain_allocation_failures(
    plans: dict[str, dict], request: AllocationKernelRequest, catalog: StatCatalog,
) -> dict[str, dict]:
    """Replace only misleading failed reasons backed by a frozen-input proof."""
    drives = [item for item in request.inventory if isinstance(item, Drive)]
    proven = {
        role: reason
        for role, plan in plans.items()
        if isinstance(plan, dict) and plan.get("valid") is False
        and (
            str(plan.get("reason") or "").strip() == _RESERVATION_REASON
            or plan.get("search_status") in {
                "bounded_unproven", "budget_exhausted", "candidate_truncated",
            }
        )
        if (reason := _shortage_reason(role, request, catalog, drives)) is not None
    }
    if not proven:
        return plans

    explained = dict(plans)
    for role, reason in proven.items():
        explained[role] = {
            **plans[role], "reason": reason, "search_status": "constraint_proven",
        }
    for group in request.priority_groups:
        if not any(role in proven for role in group):
            continue
        for role in group:
            plan = explained.get(role)
            if (isinstance(plan, dict) and plan.get("valid") is False
                    and str(plan.get("reason") or "").strip() == _RESERVATION_REASON):
                explained[role] = {**plan, "reason": _GROUP_SHORTAGE_REASON}
    return explained


def allocation_failure_text(plan: Mapping[str, Any] | None) -> str:
    """Render existing solver evidence without recomputing any constraint."""
    plan = plan or {}
    reason = " ".join(str(
        plan.get("reason") or "本次未生成可保存方案，原因待诊断"
    ).split())
    progress = plan.get("group_search") or {}
    if plan.get("search_status") in {
        "bounded_unproven", "budget_exhausted", "candidate_truncated",
    } and progress.get("members", 0) > 1 and progress.get("completed", 0) > 0:
        reason += f"（同级组已完成 {progress['completed']}/{progress['members']} 人）"
    return reason
