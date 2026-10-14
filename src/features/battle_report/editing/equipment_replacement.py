# 为本场配置副本与边际页共用装备选择，只修改调用方草稿，不触发保存。
"""In-memory equipment picker shared by persistent-edit and marginal drafts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget

from src.features.official_role.replacement_tasks import ReplacementTaskOwner
from src.domain.allocation_rating import allocation_grade
from src.features.inventory.warehouse.cards import warehouse_item_view
from src.services.official_role.replacement import replacement_candidates_for_frozen_context
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
    item_icon_path,
) -> EquipmentReplacementCard:
    return EquipmentReplacementCard(
        key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
        item_view=warehouse_item_view({**item, "item_icon_path": item_icon_path}, frozen_projection=True),
        score=score,
        grade=allocation_grade(score, _area(item)),
        direct_damage_score=gain_percent,
        payload=payload,
    )


def show_battle_equipment_replacement(
    parent: QWidget,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    context_key: str,
    static_database_path, static_schema_version,
    on_replaced: Callable[[Mapping[str, Any]], None],
    is_current: Callable[[], bool] = lambda: True,
    title: str = "边际候选临时替换",
    summary: str = (
        "这里只修改当前边际页的内存候选；关闭页面后丢弃。"
        "卡片收益用于候选筛选，确认后点击重算取得固定轴实际收益。"
    ),
) -> bool:
    """Select one candidate and mutate only the caller-owned memory draft."""

    frozen_detail, frozen_target = deepcopy(dict(detail)), deepcopy(dict(target))
    owner = ReplacementTaskOwner(parent, app_context=getattr(parent.window(), "app_context", None),
                                 is_current=is_current)
    try:
        ranked = owner.prepare(lambda: replacement_candidates_for_frozen_context(
            frozen_detail, context_key, frozen_target, static_database_path=static_database_path,
            static_schema_version=static_schema_version, static_release=frozen_detail["static_release"],
        ))
        if ranked is None or not owner.current():
            return False
        return _show_prepared_candidates(parent, frozen_detail, ranked[:20], on_replaced=on_replaced,
                                      title=title, summary=summary, is_current=owner.current)
    finally:
        owner.close()


def _show_prepared_candidates(parent, detail, ranked, *, on_replaced, title, summary, is_current):
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
        item_icon_path=(detail.get("item_icon_paths") or {}).get(str(current_item.get("item_id") or "")),
    )
    choices = [
        _card(
            dict(row["item"]),
            score=float(row.get("score") or 0.0),
            gain_percent=float(row.get("gain_percent") or 0.0),
            payload=row,
            item_icon_path=(detail.get("item_icon_paths") or {}).get(str(row["item"].get("item_id") or "")),
        )
        for row in ranked
    ]

    def apply_choice(choice: EquipmentReplacementCard) -> None:
        if not is_current():
            raise ValueError("边际候选已变化，请重新打开临时替换")
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
        on_confirm=apply_choice, is_current=is_current,
    )


__all__ = ["show_battle_equipment_replacement"]
