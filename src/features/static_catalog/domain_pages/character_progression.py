# 把公共养成服务结果投影为角色页可读文本，不执行任何计算。
"""Presentation-only adapter for shared progression stamina results."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.progression_stamina import ProgressionStaminaResult, StaminaPlanStatus


@dataclass(frozen=True, slots=True)
class ProgressionResultProjection:
    text: str
    available: bool


def project_progression_result(
    result: ProgressionStaminaResult,
) -> ProgressionResultProjection:
    """Format the public immutable result; do not infer missing yields."""

    identification = result.identification
    lines = [
        f"猎人等级 {identification.hunter_level} · "
        f"鉴别等级 {identification.effective_level}"
        + ("（已下调）" if identification.lowered else ""),
    ]
    deficits = tuple(item for item in result.deficits if item.deficit_quantity > 0)
    if deficits:
        lines.append("材料缺口：" + "、".join(
            f"{item.item_id} × {item.deficit_quantity}"
            for item in deficits
        ))
    if result.runs:
        lines.append("刷取计划：" + "、".join(
            f"{run.label} × {run.runs} 次（{run.total_stamina} 活力）"
            for run in result.runs
        ))
    if result.total_stamina is not None:
        lines.append(f"总活力：{result.total_stamina}")
    elif result.known_stamina:
        lines.append(f"已知活力：{result.known_stamina}；完整总活力不可用")
    if result.unresolved_item_ids:
        lines.append("缺少正式产出：" + "、".join(result.unresolved_item_ids))
    if result.gaps:
        lines.append("缺口：" + "、".join(result.gaps))
    if len(lines) == 1:
        lines.append("当前材料已满足，无需额外活力。")
    return ProgressionResultProjection(
        text="\n".join(lines),
        available=result.status == StaminaPlanStatus.COMPLETE,
    )
