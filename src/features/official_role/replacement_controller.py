# 编排角色页与配装页共享的官方方案单件替换弹窗。
"""Shared Qt controller for official-role replacement previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget
from shiboken6 import isValid

from src.domain.allocation_rating import allocation_grade
from src.domain.equipment.loadout_plan_scores import assignment_score_key
from src.features.inventory.warehouse.cards import warehouse_item_view
from src.features.official_role.controller import OfficialRoleController
from src.services.official_role.base_equipment_scoring import score_official_role_equipment
from src.services.official_role.replacement import prepare_official_role_replacement
from src.features.official_role.replacement_tasks import ReplacementTaskOwner
from src.services.official_role.static_source import frozen_static_source
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)


OFFICIAL_ROLE_REPLACEMENT_SUMMARY = (
    "评分按该角色的直伤权重计算，候选由高到低排列；"
    "卡片第三项百分比表示该装备带来的直伤收益。"
)


def show_official_role_replacement(
    window: QWidget,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    static_database_path, static_schema_version, static_release,
    context_key: str = "saved",
    on_saved: Callable[[], None] | None = None,
    prepared_candidates: list[dict[str, Any]] | None = None,
) -> bool:
    """Show, persist and report one official-role replacement operation."""

    # 已准备候选只展示冻结结果；此校验不重开静态库或重新投影。
    static_database_path, static_schema_version = frozen_static_source(
        detail, static_database_path=static_database_path, static_schema_version=static_schema_version,
        static_release=static_release,
    )
    detail, target = deepcopy(dict(detail)), deepcopy(dict(target))
    page = getattr(window, "_official_role_page", None)
    role = int((detail.get("character") or {})["character_id"])
    role_page_active = page is not None and page.isVisible()
    def current_view():
        return (not role_page_active or (isValid(page) and page.isVisible()
                and getattr(window, "_current_official_role_id", None) == role))
    owner = ReplacementTaskOwner(window, app_context=window.app_context, is_current=current_view)
    dependencies = owner.dependencies
    try:
        if prepared_candidates is None:
            prepared = owner.prepare(lambda: prepare_official_role_replacement(
                dependencies.user_database_path, dependencies.asset_root, detail, context_key, target,
                static_database_path=static_database_path, static_schema_version=static_schema_version,
                static_release=static_release,
            ))
            if prepared is None:
                return False
            detail, candidates = prepared
        else:
            candidates = deepcopy(prepared_candidates)
        return _show_prepared_replacement(
            window, detail, target, candidates, context_key=context_key, on_saved=on_saved, owner=owner,
        )
    finally:
        owner.close()


def _show_prepared_replacement(window, detail, target, candidates, *, context_key, on_saved, owner):
    if not candidates:
        QMessageBox.information(
            window,
            "替换优化",
            "没有同套装、同形状且未被当前方案使用的可替换装备。",
        )
        return False

    scoring_engine = getattr(window, "scoring_engine", None)
    shape_areas = dict(getattr(window, "_shape_areas", {}))

    current_item = dict(candidates[0]["current_item"])
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
        display_item = {**item, "item_icon_path": (detail.get("item_icon_paths") or {}).get(
            str(item.get("item_id") or ""))}
        view = warehouse_item_view(display_item, frozen_projection=True)
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
    def candidate_card(row):
        return card_data(
            dict(row["item"]),
            direct_damage_score=row.get("direct_damage_score"),
            hidden_score=float(row.get("score") or 0.0),
            payload=row,
        )

    role_controller = OfficialRoleController(
        owner.dependencies, parent=owner
    )

    committed_receipt = {}

    def submit_choice(choice, committed, failed, application_failed):
        # 控件选择、来源和所有数值只在GUI冻结一次，worker不再查最新权重补齐。
        row = deepcopy(choice.payload)
        # 基础保存分只为显式选中的候选计算，仍消费打开时的 detail/形状投影，不读最新配置。
        row["base_score"] = score_official_role_equipment(
            scoring_engine, detail=detail, item=dict(row["item"]), shape_areas=shape_areas,
        )
        frozen_detail, frozen_target = deepcopy(detail), deepcopy(target)
        frozen_scores = dict(current_assignment_scores)
        def accept_committed(receipt):
            # 真commit回执属于本次operation，不因随后刷新异常丢失或重贴当前身份。
            committed_receipt.update(receipt)
            committed(receipt)
        return owner.submit(lambda: role_controller.save_replacement(
            frozen_detail, frozen_target, row["item"], context_key=context_key,
            replacement_score=float(row["base_score"]), current_assignment_scores=frozen_scores,
            checkpoint=owner.checkpoint, commit_context=owner.commit_context,
        ), accept_committed, failed, application_failed)

    accepted = show_equipment_replacement_dialog(
        window,
        title="替换优化",
        role_name=str((detail.get("character") or {}).get("name_zh") or ""),
        summary=OFFICIAL_ROLE_REPLACEMENT_SUMMARY,
        current=current,
        candidates=candidates, candidate_adapter=candidate_card,
        submit_confirm=submit_choice, is_current=owner.current, cancel_pending=owner.cancel,
    )
    role_controller.close()
    if not accepted:
        return False
    if not owner.current():
        return True  # 已接受的commit事实仍成立；旧账号/离页不刷新或投递到最新页面。
    try:
        if on_saved is not None:
            on_saved()
        if owner.current():
            QMessageBox.information(window, "替换优化", "已保存为新的配装方案。")
    except Exception as exc:
        # 刷新/提示属于已提交之后的UI应用，不传播为读取/保存失败或重复提交。
        if owner.current():
            try:
                QMessageBox.information(window, "方案已保存",
                    f"配装方案 {committed_receipt.get('plan_id')} 已保存；页面刷新未完成，请稍后刷新核对。\n{exc}")
            except Exception:
                pass  # 展示层也失效时仍保留真commit回执，不伪造失败或再次提交。
    return True
