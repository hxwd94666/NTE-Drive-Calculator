# 在战报边际页选择装备替换，但不触发任何保存动作。
"""In-memory equipment replacement picker for battle marginal candidates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget

from src.domain.allocation_rating import allocation_grade
from src.features.inventory.warehouse import warehouse_item_view
from src.services.official_role_replacement_service import (
    replacement_candidates_for_frozen_context,
)
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)


def _area(item: Mapping[str, Any]) -> int:
    return 15 if str(item.get("kind") or "") == "core" else max(
        1, int(item.get("grid_count") or 0)
    )


def _card(
    item: Mapping[str, Any],
    *,
    score: float,
    gain_percent: float,
    payload: object,
) -> EquipmentReplacementCard:
    return EquipmentReplacementCard(
        key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
        item_view=warehouse_item_view(dict(item)),
        score=score,
        grade=allocation_grade(score, _area(item)),
        direct_damage_score=gain_percent,
        payload=payload,
    )


def show_marginal_equipment_replacement(
    parent: QWidget,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    context_key: str,
    on_replaced: Callable[[Mapping[str, Any]], None],
    title: str = "边际候选临时替换",
    summary: str = (
        "这里只修改当前边际页的内存候选；关闭页面后丢弃。"
        "卡片收益用于候选筛选，确认后点击重算取得固定轴实际收益。"
    ),
) -> bool:
    """Select one candidate and mutate only the caller-owned memory draft."""

    ranked = replacement_candidates_for_frozen_context(
        detail,
        context_key,
        target,
    )[:20]
    if not ranked:
        QMessageBox.information(
            parent,
            "临时替换",
            "没有同套装、同形状且未被当前候选使用的可替换装备。",
        )
        return False
    first = ranked[0]
    current_item = dict(first["current_item"])
    current = _card(
        current_item,
        score=float(first.get("current_score") or 0.0),
        gain_percent=0.0,
        payload=None,
    )
    choices = [
        _card(
            dict(row["item"]),
            score=float(row.get("score") or 0.0),
            gain_percent=float(row.get("gain_percent") or 0.0),
            payload=row,
        )
        for row in ranked
    ]

    def apply_choice(choice: EquipmentReplacementCard) -> None:
        row = choice.payload
        if not isinstance(row, Mapping):
            raise ValueError("临时替换候选格式无效")
        replacement = row.get("item")
        if not isinstance(replacement, Mapping):
            raise ValueError("临时替换装备格式无效")
        on_replaced(replacement)

    return show_equipment_replacement_dialog(
        parent,
        title=title,
        role_name=str((detail.get("character") or {}).get("name_zh") or ""),
        summary=summary,
        current=current,
        candidates=choices,
        on_confirm=apply_choice,
    )


__all__ = ["show_marginal_equipment_replacement"]
