# 提供只含角色优先级、计算和统一结果的词条配装页面。
"""Minimal role-priority UI for the audited weighted-allocation facade."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QMessageBox,
    QScrollArea,
    QWidget,
)

from src.app.workers import WorkerThread
from src.domain.allocation_rating import allocation_grade
from src.observability import OperationContext, log_event
from src.features.inventory.warehouse import warehouse_item_view
from src.features.weighted_allocation.runner import (
    WeightedAllocationPreview,
    WeightedAllocationRequest,
    replace_weighted_allocation_assignment,
    run_weighted_allocation,
    save_weighted_allocation_preview,
)
from src.services.allocation_legacy_adapter import score_allocation_candidate
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.official_role_page_service import (
    calculate_official_role_final_weights,
    calculate_official_role_hidden_equipment_score,
    calculate_official_role_item_gain,
    load_official_role_detail,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)
from .weighted_preferences import _current_snapshot_and_profile
from .weighted_result_view import _allocation_candidate_row
from .dependencies import weighted_allocation_dependencies


_INTERNAL_PROFILE_NAME = "__weighted_allocation_role_priority__"
# 普通入口不展示候选；避免为不可见的 Top-K 重复执行昂贵的 DFS 与评分。
_INTERNAL_TOP_K = 1

_MAIN_PROPERTY_CHOICES = (
    ("生命值百分比", "HPMaxUp"),
    ("攻击力百分比", "AtkUp"),
    ("防御力百分比", "DefUp"),
    ("暴击率", "CritBase"),
    ("暴击伤害", "CritDamageBase"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
    ("治疗加成", "HealUp"),
    ("光属性异能伤害增强", "DamageUpCosmosBase"),
    ("灵属性异能伤害增强", "DamageUpNatureBase"),
    ("咒属性异能伤害增强", "DamageUpIncantationBase"),
    ("暗属性异能伤害增强", "DamageUpChaosBase"),
    ("魂属性异能伤害增强", "DamageUpPsycheBase"),
    ("相属性异能伤害增强", "DamageUpLakshanaBase"),
    ("心灵伤害增强", "DamageUpPsychicallyBase"),
)
_SUBSTAT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"),
    ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"),
    ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"),
    ("防御力", "DefAdd"),
    ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"),
    ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
)
_RESULT_PROPERTY_LABELS = {property_id: label for label, property_id in _SUBSTAT_PROPERTY_CHOICES}
_RESULT_PROPERTY_LABELS.update(
    {
        property_id: f"{label}%" if "伤害增强" in label or "治疗加成" in label else label
        for label, property_id in _MAIN_PROPERTY_CHOICES
        if property_id not in _RESULT_PROPERTY_LABELS
    }
)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        if item.layout() is not None:
            _clear_layout(item.layout())
            item.layout().deleteLater()


def render_weighted_allocation_result(*args, **kwargs):
    from .weighted_result_view import render_weighted_allocation_result as render

    return render(*args, **kwargs)


def start_weighted_allocation(window) -> None:
    window._weighted_restore_token = object()
    dependencies = weighted_allocation_dependencies(window)
    try:
        snapshot_id, profile_id, version = _current_snapshot_and_profile(window)
    except Exception as exc:
        QMessageBox.warning(window, "无法开始计算", str(exc))
        return
    request = WeightedAllocationRequest(
        dependencies.user_database_path,
        snapshot_id,
        profile_id,
        version,
        _INTERNAL_TOP_K,
        include_role_top_k=False,
        operation_context=OperationContext.create(
            "allocation",
            account_id=dependencies.account_id,
            context_generation=dependencies.generation,
            snapshot_id=snapshot_id,
        ),
    )
    log_event(
        "INFO",
        "allocation.requested",
        "用户开始词条配装计算",
        request.operation_context,
        snapshot_id=snapshot_id,
        profile_id=profile_id,
        profile_version=version,
    )
    window.weighted_run_button.setEnabled(False)
    window._weighted_allocation_saved_preview = None
    _set_weighted_equipment_actions_enabled(window, False)
    window.weighted_status_label.setText("正在计算…")
    worker = WorkerThread(target=lambda: run_weighted_allocation(request), parent=window)
    token = object()
    window._weighted_calculation_token = token
    window._weighted_allocation_worker = worker
    worker.result_ready.connect(lambda preview: _on_done(window, token, preview))
    worker.error.connect(lambda error: _on_error(window, token, error))
    worker.start()


def _on_done(
    window,
    token: object,
    preview: WeightedAllocationPreview,
) -> None:
    if (
        getattr(window, "_weighted_calculation_token", None) is not token
        or preview.user_database_path != weighted_allocation_dependencies(window).user_database_path
    ):
        operation = getattr(preview, "operation_context", None) or OperationContext.create(
            "allocation",
            account_id=window.app_context.account.active_account_id,
            context_generation=window.app_context.generation,
        )
        log_event(
            "WARNING",
            "allocation.result_discarded",
            "计算结果属于旧请求或旧账号，已丢弃",
            operation,
            result="discarded",
        )
        return
    window._weighted_allocation_worker = None
    window.weighted_run_button.setEnabled(True)
    window._weighted_allocation_preview = preview
    window._weighted_allocation_saved_preview = None
    window.weighted_save_button.setEnabled(bool(preview.result.unified.selected))
    captured_at = preview.context.snapshot.captured_at_utc
    window.weighted_status_label.setText(f"计算完成。背包数据截至 {captured_at}")
    render_weighted_allocation_result(
        window,
        preview,
    )
    _set_weighted_equipment_actions_enabled(window, bool(preview.result.unified.selected))


def _on_error(window, token: object, error: str) -> None:
    if getattr(window, "_weighted_calculation_token", None) is not token:
        return
    window._weighted_allocation_worker = None
    window.weighted_run_button.setEnabled(True)
    window.weighted_status_label.setText(f"计算失败：{error}")
    QMessageBox.critical(window, "计算失败", error)


def start_weighted_allocation_save(
    window,
    after_save: Callable[[], None] | None = None,
) -> None:
    preview = _validated_weighted_preview(window, action_name="保存")
    if preview is None:
        return
    dependencies = weighted_allocation_dependencies(window)
    operation = OperationContext.create(
        "allocation",
        account_id=dependencies.account_id,
        context_generation=dependencies.generation,
        snapshot_id=preview.result.snapshot_id,
    )
    worker = WorkerThread(
        target=lambda: save_weighted_allocation_preview(preview, operation),
        parent=window,
    )
    # Keep the QThread reachable for its complete lifetime.  A local variable
    # can be garbage-collected while Qt is still executing the worker.
    window._weighted_allocation_save_worker = worker
    window.weighted_save_button.setEnabled(False)
    _set_weighted_equipment_actions_enabled(window, False)
    worker.result_ready.connect(lambda _ids: _on_weighted_save_done(window, preview, after_save))
    worker.error.connect(lambda error: _on_weighted_save_error(window, preview, error))
    worker.start()


def _on_weighted_save_done(
    window,
    preview: WeightedAllocationPreview,
    after_save: Callable[[], None] | None = None,
) -> None:
    if preview.user_database_path != weighted_allocation_dependencies(window).user_database_path:
        return
    window._weighted_allocation_save_worker = None
    window._weighted_allocation_saved_preview = preview
    window.weighted_save_button.setEnabled(True)
    _set_weighted_equipment_actions_enabled(window, True)
    window.weighted_status_label.setText("方案已保存。")
    if after_save is not None:
        after_save()


def _on_weighted_save_error(
    window,
    saved_preview: WeightedAllocationPreview,
    error: str,
) -> None:
    if saved_preview.user_database_path != weighted_allocation_dependencies(window).user_database_path:
        return
    window._weighted_allocation_save_worker = None
    window.weighted_save_button.setEnabled(True)
    preview = getattr(window, "_weighted_allocation_preview", None)
    _set_weighted_equipment_actions_enabled(
        window,
        isinstance(preview, WeightedAllocationPreview) and bool(preview.result.unified.selected),
    )
    QMessageBox.critical(window, "保存失败", error)


def _set_weighted_equipment_actions_enabled(window, enabled: bool) -> None:
    window._weighted_equipment_actions_available = bool(enabled)
    for name in ("weighted_one_key_button", "weighted_automatic_button"):
        button = getattr(window, name, None)
        if button is not None:
            button.setEnabled(bool(enabled))
    for button in getattr(window, "_weighted_role_equip_buttons", ()):
        button.setEnabled(bool(enabled))


def _configured_equipment_apply_method(window) -> str:
    settings_reader = getattr(window, "_get_sync_settings", None)
    if callable(settings_reader):
        settings = settings_reader()
    else:
        settings = weighted_allocation_dependencies(window).account_settings.load("sync")
    method = str(settings.get("equipment_apply_method") or "").strip()
    if method not in {"nte_core", "gamepad"}:
        raise RuntimeError("装配执行方式无效，请先在设置中重新保存。")
    return method


def _perform_weighted_equipment_action(
    window,
    *,
    mode: str,
    role_name: str | None = None,
) -> None:
    try:
        method = "gamepad" if mode == "automatic" else _configured_equipment_apply_method(window)
    except Exception as exc:
        QMessageBox.warning(window, "无法装配", str(exc))
        return
    if role_name is None:
        preview = getattr(window, "_weighted_allocation_preview", None)
        role_names = [
            getattr(window, "_weighted_role_names", {}).get(option.character_id, str(option.character_id))
            for option in (preview.result.unified.selected if isinstance(preview, WeightedAllocationPreview) else ())
        ]
        window.request_equipment_assembly(
            role_names=role_names,
            method=method,
        )
        return
    window.request_equipment_assembly(
        role_names=[role_name],
        method=method,
    )


def _request_weighted_equipment(
    window,
    *,
    mode: str,
    role_name: str | None = None,
) -> None:
    preview = _validated_weighted_preview(window, action_name="装配")
    if preview is None:
        return
    action = lambda: _perform_weighted_equipment_action(
        window,
        mode=mode,
        role_name=role_name,
    )
    _run_after_weighted_preview_saved(window, preview, action)


def _request_weighted_replacement(window, role_name: str, assignment, role) -> None:
    preview = _validated_weighted_preview(window, action_name="替换")
    if preview is None:
        return
    role_option = next(
        (
            option
            for option in preview.result.unified.selected
            if any(item.uid == assignment.uid for item in option.assignments)
        ),
        None,
    )
    if role_option is None:
        QMessageBox.warning(window, "无法替换", "当前角色结果已变化，请重新计算。")
        return

    same_role_uids = {item.uid for item in role_option.assignments if item.uid != assignment.uid}
    temporary_owner_by_uid = {
        item.uid: option.character_id
        for option in preview.result.unified.selected
        for item in option.assignments
        if not item.virtual
    }
    role_names = getattr(window, "_weighted_role_names", {})
    asset_catalog = GameUiAssetCatalog(weighted_allocation_dependencies(window).game_ui_asset_root)

    def annotate_temporary_owner(
        item: dict[str, Any],
        uid: tuple[int, int],
    ) -> dict[str, Any]:
        owner_id = temporary_owner_by_uid.get(uid)
        if owner_id is None:
            return item
        result = dict(item)
        result["equipped"] = True
        result["equipped_character_id"] = owner_id
        result["equipped_character_name"] = str(role_names.get(owner_id, owner_id))
        icon_path = asset_catalog.character_icon(owner_id)
        if icon_path is not None:
            result["equipped_character_icon_path"] = str(icon_path)
        return result

    candidate_map = {candidate.uid: candidate for candidate in preview.context.candidates}
    compatible = []
    for candidate in preview.context.candidates:
        if candidate.uid == assignment.uid or candidate.uid in same_role_uids:
            continue
        if candidate.kind != assignment.kind:
            continue
        if not assignment.virtual and str(candidate.suit_id or "") != str(assignment.suit_id or ""):
            continue
        if (
            assignment.kind == "module"
            and str(candidate.geometry or "").casefold() != str(assignment.geometry or "").casefold()
        ):
            continue
        compatible.append(candidate)
    if not compatible:
        QMessageBox.information(
            window,
            "替换优化",
            "当前计算临时候选池中没有可替换的同套装、同形状装备。",
        )
        return

    source_rows = [
        annotate_temporary_owner(
            _allocation_candidate_row(window, item, candidate_map.get(item.uid)),
            item.uid,
        )
        for item in role_option.assignments
    ]
    with StaticGameDataDao() as static_dao:
        projected = project_equipment_items_to_max_level(
            [
                *source_rows,
                *(
                    annotate_temporary_owner(
                        _allocation_candidate_row(window, assignment, candidate),
                        candidate.uid,
                    )
                    for candidate in compatible
                ),
            ],
            static_dao,
        )
    source_count = len(source_rows)
    projected_current_items = projected[:source_count]
    projected_candidates = projected[source_count:]
    current_item = next(
        (
            item
            for item in projected_current_items
            if (
                int(item.get("uid_slot") or 0),
                int(item.get("uid_serial") or 0),
            )
            == assignment.uid
        ),
        None,
    )
    if current_item is None:
        QMessageBox.warning(window, "无法替换", "当前装备不在计算临时候选池中。")
        return

    context_key = "_weighted_replacement"
    # Do not reuse the lazy result-card cache here.  Replacement ordering needs
    # the selected role's profile, fork and public extra-shape context in full.
    detail = load_official_role_detail(
        preview.user_database_path,
        role_option.character_id,
    )
    context = {
        "title": "词条配装临时结果",
        "items": tuple(projected_current_items),
        "calculation_items": tuple(projected_current_items),
        "available": True,
    }
    full_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: context,
        },
    }
    final_weights = calculate_official_role_final_weights(full_detail, context_key)

    def item_score(item: Mapping[str, Any]) -> float:
        """Keep all visible replacement and saved-plan scores on base weights."""
        uid = (int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0))
        candidate = candidate_map.get(uid)
        if candidate is None:
            if assignment.virtual and uid == assignment.uid:
                return float(assignment.score)
            raise RuntimeError(f"替换装备 UID {uid} 不在计算固定的背包快照中。")
        return score_allocation_candidate(preview.context, role, candidate)

    def hidden_sort_score(item: Mapping[str, Any]) -> float:
        """Use final role weights only to order candidates, never to display/save."""
        return calculate_official_role_hidden_equipment_score(
            full_detail,
            item,
            property_weights=final_weights["property_weights"],
            main_property_weights=final_weights["main_property_weights"],
        )

    current_gain = calculate_official_role_item_gain(
        full_detail,
        context_key,
        current_item,
    )
    current_direct_damage_score = float(current_gain["gain_percent"]) if current_gain else None

    def direct_damage_score(
        candidate_item: Mapping[str, Any],
    ) -> float | None:
        replaced = tuple(
            candidate_item
            if (
                int(item.get("uid_slot") or 0),
                int(item.get("uid_serial") or 0),
            )
            == assignment.uid
            else item
            for item in projected_current_items
        )
        candidate_detail = {
            **full_detail,
            "equipment_contexts": {
                **full_detail["equipment_contexts"],
                context_key: {
                    **context,
                    "items": replaced,
                    "calculation_items": replaced,
                },
            },
        }
        item_gain = calculate_official_role_item_gain(
            candidate_detail,
            context_key,
            candidate_item,
        )
        return float(item_gain["gain_percent"]) if item_gain else None

    def card(
        item: Mapping[str, Any],
        *,
        score: float,
        direct_damage_score: float | None,
        payload,
    ) -> EquipmentReplacementCard:
        view = warehouse_item_view(item)
        icon_path = getattr(window, "_weighted_item_icons", {}).get(str(item.get("item_id") or ""))
        if icon_path:
            view["item_icon_path"] = icon_path
        area = 15 if str(item.get("kind") or "") == "core" else int(item.get("grid_count") or 0)
        return EquipmentReplacementCard(
            key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
            item_view=view,
            score=score,
            grade=allocation_grade(score, area),
            direct_damage_score=direct_damage_score,
            payload=payload,
            note=(
                f"将从 {view.get('equipped_character_name')} 的临时方案借用，并为其原槽位补入金色占位装备。"
                if view.get("equipped_character_name")
                else ""
            ),
        )

    current_hidden_score = hidden_sort_score(current_item)
    current_card = card(
        current_item,
        score=current_hidden_score,
        direct_damage_score=current_direct_damage_score,
        payload=None,
    )
    choices = []
    for candidate, item in zip(compatible, projected_candidates):
        base_score = item_score(item)
        hidden_score = hidden_sort_score(item)
        choices.append(
            card(
                item,
                score=hidden_score,
                direct_damage_score=direct_damage_score(item),
                payload={
                    "_uid_slot": candidate.uid_slot,
                    "_uid_serial": candidate.uid_serial,
                    # Keep base score separately for diagnostics; preview saving
                    # recalculates it from the frozen allocation context.
                    "base_score": base_score,
                    "hidden_sort_score": hidden_score,
                },
            )
        )
    choices.sort(
        key=lambda choice: float(choice.payload.get("hidden_sort_score") or 0.0),
        reverse=True,
    )

    show_equipment_replacement_dialog(
        window,
        title=f"{role_name} · 替换优化",
        role_name=role_name,
        summary=(
            "候选已先加载该角色完整面板，并按最终权重的隐藏装备评分降序排列；"
            "直伤边际收益仅用于展示比较。候选与持有者只来自当前词条配装临时结果，不读取活动配装库；"
            "借用其他角色装备后会在其原槽位生成可继续替换的金色占位装备。"
        ),
        current=current_card,
        candidates=choices[:30],
        on_confirm=lambda choice: _on_weighted_replacement_done(
            window,
            preview,
            assignment.uid,
            choice.payload,
        ),
    )


def _validated_weighted_preview(
    window,
    *,
    action_name: str,
) -> WeightedAllocationPreview | None:
    """Return the current account's complete preview for save-dependent actions."""

    preview = getattr(window, "_weighted_allocation_preview", None)
    if not isinstance(preview, WeightedAllocationPreview) or not preview.result.unified.selected:
        QMessageBox.information(window, f"无法{action_name}", "请先完成一次有效的配装计算。")
        return None
    if preview.user_database_path != weighted_allocation_dependencies(window).user_database_path:
        QMessageBox.warning(window, "账号已切换", f"请在当前账号重新计算后再{action_name}。")
        return None
    return preview


def _run_after_weighted_preview_saved(
    window,
    preview: WeightedAllocationPreview,
    action: Callable[[], None],
) -> None:
    """Run an action only after the exact in-memory preview is persisted."""

    if getattr(window, "_weighted_allocation_saved_preview", None) is preview:
        action()
        return
    start_weighted_allocation_save(window, after_save=action)


def _on_weighted_replacement_done(
    window,
    preview: WeightedAllocationPreview,
    old_uid: tuple[int, int],
    selected: dict[str, Any],
) -> None:
    if getattr(window, "_weighted_allocation_preview", None) is not preview:
        raise RuntimeError("当前计算结果已变化，请重新打开替换窗口。")
    new_uid = (int(selected["_uid_slot"]), int(selected["_uid_serial"]))
    updated_preview = replace_weighted_allocation_assignment(
        preview,
        old_uid=old_uid,
        new_uid=new_uid,
    )
    save_weighted_allocation_preview(updated_preview)
    window._weighted_allocation_preview = updated_preview
    window._weighted_allocation_saved_preview = updated_preview
    page_scroll = getattr(window, "weighted_page_scroll", None)
    scroll_value = page_scroll.verticalScrollBar().value() if isinstance(page_scroll, QScrollArea) else None
    restore_character_id = next(
        (
            option.character_id
            for option in preview.result.unified.selected
            if any(assignment.uid == old_uid for assignment in option.assignments)
        ),
        None,
    )
    restore_viewport_offset = None
    if isinstance(page_scroll, QScrollArea) and restore_character_id is not None:
        for card in window.weighted_result_widget.findChildren(QWidget):
            if card.property("weighted_character_id") == restore_character_id:
                restore_viewport_offset = page_scroll.viewport().mapFromGlobal(card.mapToGlobal(QPoint(0, 0))).y()
                break
    render_weighted_allocation_result(
        window,
        updated_preview,
        restore_scroll_value=scroll_value,
        restore_character_id=restore_character_id,
        restore_viewport_offset=restore_viewport_offset,
    )
    _set_weighted_equipment_actions_enabled(window, True)
    window.weighted_status_label.setText("替换已保存为新的 SQLite 配装方案；重新计算会重新生成推荐方案。")
