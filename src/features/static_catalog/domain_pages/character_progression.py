# 把公共养成服务结果投影为角色页可读文本，不执行任何计算。
"""Presentation-only adapter for shared progression stamina results."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.progression_stamina import (
    MaterialRequirement,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)
from src.features.static_catalog.domain_pages.character_terminology import (
    project_character_term,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.services.static_catalog_character_models import CharacterSkill


@dataclass(frozen=True, slots=True)
class ProgressionResultProjection:
    text: str
    available: bool
    more_info: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ProgressionRequirementGap:
    reason_code: str
    level: int | None = None
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressionRequirementProjection:
    status: StaminaPlanStatus
    requirements: tuple[MaterialRequirement, ...]
    gaps: tuple[ProgressionRequirementGap, ...]


def unavailable_character_level_requirements() -> ProgressionRequirementProjection:
    """Current schema has no normalized character level/breakthrough costs."""

    return ProgressionRequirementProjection(
        status=StaminaPlanStatus.UNAVAILABLE,
        requirements=(),
        gaps=(ProgressionRequirementGap(
            reason_code="character_level_cost_unavailable",
        ),),
    )


def project_skill_level_requirements(
    skill: CharacterSkill,
    *,
    from_level: int,
    to_level: int,
    terminology: StaticCatalogTerminologyService | None,
) -> ProgressionRequirementProjection:
    """Aggregate formal per-level costs without guessing hidden quantities."""

    start = int(from_level)
    end = int(to_level)
    if start >= end:
        return ProgressionRequirementProjection(
            status=StaminaPlanStatus.UNAVAILABLE,
            requirements=(),
            gaps=(ProgressionRequirementGap(
                reason_code="skill_level_interval_invalid",
            ),),
        )
    levels = {level.level: level for level in skill.levels}
    totals: dict[str, int] = {}
    gaps: list[ProgressionRequirementGap] = []
    for level_value in range(start, end):
        target_level = level_value + 1
        level = levels.get(level_value)
        if level is None:
            gaps.append(ProgressionRequirementGap(
                reason_code="skill_level_cost_row_unavailable",
                level=target_level,
            ))
            continue
        for item in level.costs:
            canonical_id = _canonical_item_id(
                item.item_id,
                terminology=terminology,
            )
            if item.hidden_amount:
                gaps.append(ProgressionRequirementGap(
                    reason_code="skill_cost_quantity_hidden",
                    level=target_level,
                    item_id=canonical_id,
                ))
                continue
            quantity = float(item.quantity)
            if quantity <= 0 or not quantity.is_integer():
                gaps.append(ProgressionRequirementGap(
                    reason_code="skill_cost_quantity_invalid",
                    level=target_level,
                    item_id=canonical_id,
                ))
                continue
            totals[canonical_id] = totals.get(canonical_id, 0) + int(quantity)
    requirements = tuple(
        MaterialRequirement(item_id=item_id, required_quantity=quantity)
        for item_id, quantity in sorted(totals.items())
    )
    status = (
        StaminaPlanStatus.PARTIAL
        if gaps and requirements
        else StaminaPlanStatus.UNAVAILABLE
        if gaps
        else StaminaPlanStatus.COMPLETE
    )
    return ProgressionRequirementProjection(
        status=status,
        requirements=requirements,
        gaps=tuple(gaps),
    )


def _canonical_item_id(
    item_id: str,
    *,
    terminology: StaticCatalogTerminologyService | None,
) -> str:
    stable_id = str(item_id or "").strip()
    if terminology is None or not stable_id:
        return stable_id
    term = terminology.resolve(
        "item",
        stable_id,
        context="progression_cost",
    )
    return term.canonical_id or stable_id


def project_progression_result(
    result: ProgressionStaminaResult,
    *,
    terminology: StaticCatalogTerminologyService | None = None,
) -> ProgressionResultProjection:
    """Format the public immutable result; do not infer missing yields."""

    identification = result.identification
    more_info: list[tuple[str, str]] = []
    lines = [
        f"猎人等级 {identification.hunter_level} · "
        f"鉴别等级 {identification.effective_level}"
        + ("（已下调）" if identification.lowered else ""),
    ]
    deficits = tuple(item for item in result.deficits if item.deficit_quantity > 0)
    if deficits:
        projections = tuple(
            project_character_term(
                terminology,
                entity_kind="item",
                stable_id=item.item_id,
                identity_label=f"缺口项 {index}",
                context="progression_cost",
            )
            for index, item in enumerate(deficits, start=1)
        )
        lines.append("消耗缺口：" + "、".join(
            f"{projection.display_name} × {item.deficit_quantity}"
            for item, projection in zip(deficits, projections)
        ))
        more_info.extend(
            row for projection in projections for row in projection.more_info
        )
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
        unresolved = tuple(
            project_character_term(
                terminology,
                entity_kind="item",
                stable_id=item_id,
                identity_label=f"未解析项 {index}",
                context="progression_cost",
            )
            for index, item_id in enumerate(result.unresolved_item_ids, start=1)
        )
        lines.append("缺少正式产出：" + "、".join(
            projection.display_name for projection in unresolved
        ))
        more_info.extend(
            row for projection in unresolved for row in projection.more_info
        )
    if result.gaps:
        lines.append("部分材料缺少正式产出，暂不能计算完整活力。")
    if len(lines) == 1:
        lines.append("当前材料已满足，无需额外活力。")
    return ProgressionResultProjection(
        text="\n".join(lines),
        available=result.status == StaminaPlanStatus.COMPLETE,
        more_info=tuple(more_info),
    )
