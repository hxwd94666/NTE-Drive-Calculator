# 编排角色页与配装页共享的官方方案单件替换弹窗。
"""Shared Qt controller for official-role replacement previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget

from src.domain.allocation_rating import allocation_grade
from src.domain.loadout_plan_scores import assignment_score_key
from src.features.inventory.warehouse import warehouse_item_view
from src.features.official_role.controller import OfficialRoleController
from src.features.official_role.dependencies import OfficialRoleDependencies
from src.services.official_role_equipment_scoring_service import (
    score_official_role_equipment,
)
from src.services.official_role_page_service import (
    replacement_candidates_for_official_role,
)
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)


def show_official_role_replacement(
    window: QWidget,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    on_saved: Callable[[], None] | None = None,
) -> bool:
    """Show, persist and report one official-role replacement operation."""

    candidates = replacement_candidates_for_official_role(detail, "saved", target)
    if not candidates:
        QMessageBox.information(
            window,
            "替换优化",
            "没有同套装、同形状且未被当前方案使用的可替换装备。",
        )
        return False

    scoring_engine = getattr(window, "scoring_engine", None)
    shape_areas = getattr(window, "_shape_areas", {})
    current_item = dict(candidates[0]["current_item"])
    current_base_score = score_official_role_equipment(
        scoring_engine,
        detail=detail,
        item=current_item,
        shape_areas=shape_areas,
    )
    current_assignment_scores = {
        assignment_score_key(item): score_official_role_equipment(
            scoring_engine,
            detail=detail,
            item=item,
            shape_areas=shape_areas,
        )
        for item in candidates[0].get("current_items") or ()
    }

    def card_data(
        item: dict[str, Any],
        *,
        direct_damage_score: float | None,
        hidden_score: float,
        payload: Mapping[str, Any] | None,
    ) -> EquipmentReplacementCard:
        view = warehouse_item_view(item)
        icon_path = (detail.get("item_icon_paths") or {}).get(
            str(item.get("item_id") or "")
        )
        if icon_path:
            view["item_icon_path"] = icon_path
        score = float(hidden_score)
        area = 15 if str(item.get("kind") or "") == "core" else int(
            item.get("grid_count") or 0
        )
        return EquipmentReplacementCard(
            key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
            item_view=view,
            score=score,
            grade=allocation_grade(score, area),
            direct_damage_score=direct_damage_score,
            payload=payload,
            note=(
                f"将从 {view.get('equipped_character_name')} 的持久化方案借用，"
                "并在同一事务中为其原槽位补入金色占位装备。"
                if view.get("equipped_character_name")
                else ""
            ),
        )

    current = card_data(
        current_item,
        direct_damage_score=candidates[0].get("current_direct_damage_score"),
        hidden_score=float(candidates[0].get("current_score") or 0.0),
        payload=None,
    )
    choices = [
        card_data(
            dict(row["item"]),
            direct_damage_score=row.get("direct_damage_score"),
            hidden_score=float(row.get("score") or 0.0),
            payload={
                **row,
                "base_score": score_official_role_equipment(
                    scoring_engine,
                    detail=detail,
                    item=dict(row["item"]),
                    shape_areas=shape_areas,
                ),
            },
        )
        for row in candidates[:30]
    ]

    role_controller = OfficialRoleController(
        OfficialRoleDependencies.from_app_context(window.app_context)
    )

    def save_choice(choice: EquipmentReplacementCard) -> None:
        row = choice.payload
        role_controller.save_replacement(
            detail,
            target,
            row["item"],
            replacement_score=float(row["base_score"]),
            current_score=current_base_score,
            current_assignment_scores=current_assignment_scores,
        )

    accepted = show_equipment_replacement_dialog(
        window,
        title="替换优化",
        role_name=str((detail.get("character") or {}).get("name_zh") or ""),
        summary=(
            "候选已按该角色最终权重的隐藏装备评分降序排列；"
            "直伤边际收益仅用于展示比较。所有卡片均按官方满级主属性计算。"
        ),
        current=current,
        candidates=choices,
        on_confirm=save_choice,
    )
    if not accepted:
        return False
    if on_saved is not None:
        on_saved()
    QMessageBox.information(window, "替换优化", "已保存为新的配装方案。")
    return True
