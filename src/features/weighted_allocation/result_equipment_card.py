# 构建加权配装结果中的装备卡片和账号权重提示。
"""Equipment-card presentation for weighted allocation results."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtWidgets import QWidget

from src.domain.allocation_rating import allocation_grade
from src.features.inventory.warehouse import warehouse_item_view
from src.features.inventory.warehouse_result_card import WarehouseResultCard


def assignment_weight_tooltip(
    owner,
    assignment,
    candidate,
    weights: Mapping[str, float],
    main_weights: Mapping[str, float],
) -> str:
    if candidate is None:
        return ""
    labels = getattr(owner, "_weighted_property_names", {})
    lines = ["账号 SQLite 词条权重"]
    if assignment.kind == "core":
        for stat in candidate.main_stats:
            property_id = str(stat.property_id)
            lines.append(
                f"主词条 {labels.get(property_id, property_id)}："
                f"{float(main_weights.get(property_id, 0.0)):g}"
            )
    for stat in candidate.sub_stats:
        property_id = str(stat.property_id)
        lines.append(
            f"副词条 {labels.get(property_id, property_id)}："
            f"{float(weights.get(property_id, 0.0)):g}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


def result_equipment_card(
    owner,
    assignment,
    candidates: dict,
    weights: dict,
    main_weights: dict,
    *,
    candidate_row_builder: Callable,
    replacement_callback=None,
    direct_damage_score: float | None = None,
) -> QWidget:
    candidate = candidates.get(assignment.uid)
    item = candidate_row_builder(owner, assignment, candidate)
    view = warehouse_item_view(item)
    icon_path = getattr(owner, "_weighted_item_icons", {}).get(
        assignment.item_id
    )
    if icon_path:
        view["item_icon_path"] = icon_path
    area = (
        15
        if assignment.kind == "core"
        else int(candidate.grid_count or 0)
        if candidate is not None
        else int(assignment.grid_count or 0)
    )
    card = WarehouseResultCard(
        view,
        score=assignment.score,
        grade=allocation_grade(assignment.score, area),
        direct_damage_score=direct_damage_score,
        replacement_callback=replacement_callback,
        parent=owner if isinstance(owner, QWidget) else None,
    )
    tooltip = assignment_weight_tooltip(
        owner,
        assignment,
        candidate,
        weights,
        main_weights,
    )
    if tooltip:
        card.setToolTip("\n".join(filter(None, (card.toolTip(), tooltip))))
    return card

