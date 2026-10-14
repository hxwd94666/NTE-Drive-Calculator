# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from typing import Any
from copy import deepcopy
from dataclasses import replace

from PySide6.QtWidgets import (
    QInputDialog,
    QMessageBox,
)

from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_UID,
)
from src.features.inventory.equipment.display_context import equipment_presentation
from src.features.inventory.equipment.loadout_scoring import score_equipment_display_state
from src.features.inventory.equipment.master_detail_view import update_equipment_role_status
from src.services.equipment.game_loadout_projection_service import (
    GameLoadoutImportRequest,
)
from src.services.equipment.loadout_management import GameLoadoutMutation, freeze_game_loadout_request
from src.utils.logger import logger
from src.domain.equipment.loadout_management import LoadoutMutation


from src.features.inventory.equipment.display_view import (
    _clear_equip_content,
    _equipment_compare_signature,
    _same_equipment_by_ocr,
    _page_equipment,
    _request_equipment_graduation_rate,
    _set_equipment_mode,
    _refresh_equip,
)


from src.features.inventory.equipment.plan_optimizer import _optimize_saved_equipment


def _saved_plan_diff_text(self, role_name, diff):
    removed = diff.get(DIFF_REMOVED, []) or []
    added = diff.get(DIFF_ADDED, []) or []
    if not removed and not added:
        return "本次保存与上一套方案没有装备变动。"
    lines = [f"{role_name} 配装变动："]
    if removed:
        lines.append("\n卸下：")
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n换上：")
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)


def _show_saved_plan_diff_dialog(self, role_name, diff):
    presentation = equipment_presentation(self)
    build_dialog = getattr(presentation, "plan_diff_dialog", None)
    if callable(build_dialog):
        build_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self, "配装变动", self._saved_plan_diff_text(role_name, diff))


def _persistence_failure(window, title, error):
    QMessageBox.warning(window, title, error)


def _persistence_projection_failure(window, error):
    window.invalidate_saved_equipment_cache()
    QMessageBox.warning(window, "配装已保存，页面刷新未完成", "请刷新页面，不要重复提交。\n" + error)


def _clear_all_equipment(self):
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        if not any(slot.plan_id is not None for slot in snapshot.slots):
            QMessageBox.information(self, "清空配装", "当前没有已保存的配装。")
            return
        answer = QMessageBox.question(self, "清空配装",
            "确定要从当前配装页移除所有已保存方案吗？\n方案历史和任务记录会保留，但这些方案不再参与装配。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def committed(result):
            self.refresh_saved_equipment_after_mutation()
            if result.skipped:
                QMessageBox.information(self, "清空配装",
                    "已清空未锁定方案。以下方案因计算锁定而保留：" + "、".join(result.skipped))
            logger.success("已清空所有未锁定角色配装")

        controller.commit(operation, LoadoutMutation("clear", snapshot), committed,
            lambda error: _persistence_failure(self, "清空配装", error),
            lambda error: _persistence_projection_failure(self, error))

    controller.prepare(prepared, lambda error: _persistence_failure(self, "清空配装", error))


def invalidate_saved_equipment_cache(self: Any) -> None:
    """Public cross-feature hook after a persisted loadout mutation."""

    self._saved_equipment_cache_valid = False
    # 写操作前启动的读取不得发布过期缓存；变更代次在交付时再次复核。
    self._equip_load_token = object()
    lane = getattr(self, "_equip_read_lane", None)
    if lane is not None:
        # 只撤销交付和排队请求；原 IO 仍由页面通道持有至物理收尾，不阻塞账号切换。
        lane.cancel()


def reset_equipment_account_state(self: Any) -> None:
    """Discard account-owned projections before the shell refreshes a page."""

    invalidate_saved_equipment_cache(self)
    self._equipment_graduation_tokens = {}
    self._saved_equipment_states = {}
    self._game_loadout_states = {}
    self._equip_selected_role_by_mode = {}
    self._equip_role_states = {}
    self._equip_render_token = object()
    self._equip_render_queue = []
    self._equip_lazy_entries = []
    _clear_equip_content(self)
    for name in ("equip_role_strip", "equip_content"):
        widget = getattr(self, name, None)
        if widget is not None:
            widget.setEnabled(False)



def refresh_saved_equipment_after_mutation(
    self: Any,
    *,
    restore_role_name: str | None = None,
) -> None:
    """Refresh mutated loadouts while retaining the selected role."""

    if restore_role_name is None:
        mode = getattr(self, "_equipment_mode", "saved")
        selected_by_mode = getattr(self, "_equip_selected_role_by_mode", {})
        if isinstance(selected_by_mode, dict):
            selected = selected_by_mode.get(mode)
            restore_role_name = str(selected) if selected else None
    invalidate_saved_equipment_cache(self)
    self._refresh_equip(restore_role_name=restore_role_name)


def _delete_role_equipment(self, role_name, *, plan_id=None):
    # 中文名仅用于提示，生产按钮始终传入方案正式身份，不再按名字反查方案。
    if plan_id is None:
        _persistence_failure(self, "删除角色配装", "当前方案缺少正式身份，请刷新页面。")
        return
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        slot = snapshot.plan(int(plan_id))
        answer = QMessageBox.question(self, "删除角色配装",
            f"确定要从当前配装页移除 [{role_name}] 的已保存方案吗？\n方案历史会保留，但该方案不再参与装配。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        controller.commit(operation, LoadoutMutation("delete", snapshot, slot.slot_id),
            lambda _result: self.refresh_saved_equipment_after_mutation(restore_role_name=role_name),
            lambda error: _persistence_failure(self, "删除角色配装", error),
            lambda error: _persistence_projection_failure(self, error))

    controller.prepare(prepared, lambda error: _persistence_failure(self, "删除角色配装", error), plan_id=int(plan_id))


def _manage_loadout_slot(self, slot_id, *, role_name=None):
    from src.features.inventory.equipment.slot_manager_dialog import LoadoutSlotDialog
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        dialog = LoadoutSlotDialog(self, controller, snapshot, operation, int(slot_id), role_name)
        dialog.open()

    controller.prepare(prepared, lambda error: _persistence_failure(self, "管理配装槽位", error), slot_id=int(slot_id))


def _toggle_role_allocation_lock(self, role_name, *, plan_id=None, state_key=None, completed=None):
    if plan_id is None:
        _persistence_failure(self, "配装锁定", "当前方案缺少正式身份，请刷新页面。")
        return
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        slot = snapshot.plan(int(plan_id))

        def committed(result):
            invalidate_saved_equipment_cache(self)
            key = state_key or role_name
            current = (getattr(self, "_equip_role_states", {}) or {}).get(key)
            # 同账号也可能先收到另一方案投影；旧写入回执不改新方案的锁徽标。
            if isinstance(current, dict) and current.get("_sqlite_plan_id") != plan_id:
                self.refresh_saved_equipment_after_mutation(restore_role_name=role_name)
                return
            update_equipment_role_status(self, key, _allocation_locked=result.locked)
            game_state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
            if isinstance(game_state, dict) and game_state.get("_game_existing_plan_id") == plan_id:
                game_state["_game_existing_plan_locked"] = result.locked
            if completed is not None:
                completed(result.locked)

        controller.commit(operation, LoadoutMutation("lock", snapshot, slot.slot_id), committed,
            lambda error: _persistence_failure(self, "配装锁定", error),
            lambda error: _persistence_projection_failure(self, error))

    controller.prepare(prepared, lambda error: _persistence_failure(self, "配装锁定", error), plan_id=int(plan_id))


def _game_loadout_scores(
    self: Any,
    role_name: str,
    state: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    presentation = equipment_presentation(self)
    return score_equipment_display_state(
        presentation,
        role_name,
        state,
        getattr(self, "roles_db", {}) or {},
    )


def _freeze_game_import(self, role_name, state):
    # 评分适配器仅运行纯评分；副本防止展示模型或随后权重编辑重解释这次保存。
    frozen = deepcopy(state)
    total_score, assignment_scores = _game_loadout_scores(self, role_name, frozen)
    return freeze_game_loadout_request(GameLoadoutImportRequest(
        projection=frozen["_game_projection"], score=total_score,
        assignment_scores=assignment_scores))


def _commit_game_import(self, operation, snapshot, requests, *, role_name=None, locked_count=0):
    title = "导入游戏内方案" if role_name is not None else "一键导入"

    def committed(plan_ids):
        self.invalidate_saved_equipment_cache()
        self._refresh_equip(restore_role_name=role_name)
        message = (f"[{role_name}] 已导入为计算器配装方案。" if role_name is not None
                   else f"已导入 {len(plan_ids)} 名角色的游戏内方案。")
        if locked_count:
            message += f"\n{locked_count} 名角色因方案已锁定而跳过。"
        QMessageBox.information(self, title, message)
        logger.info(f"已导入游戏内配装 count={len(plan_ids)}")

    self.equipment_persistence_controller.commit(operation,
        GameLoadoutMutation(snapshot, tuple(requests)), committed,
        lambda error: _persistence_failure(self, title, error),
        lambda error: _persistence_projection_failure(self, error))


def _import_game_loadout(self: Any, role_name: str) -> None:
    state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
    title = "导入游戏内方案"
    if not isinstance(state, dict):
        _persistence_failure(self, title, "当前展示已失效，请刷新后重试。")
        return
    if state.get("_game_projection") is None or not bool(state.get("_game_importable")):
        _persistence_failure(self, title, str(state.get("_game_reason") or "当前游戏内装备不能形成完整方案。"))
        return
    try:
        request = _freeze_game_import(self, role_name, state)
    except Exception as exc:
        _persistence_failure(self, title, str(exc))
        return

    def prepared(snapshot, operation):
        slots = tuple(slot for slot in snapshot.slots if slot.character_id == request.projection.character_id)
        target = slots[0] if slots else None
        if len(slots) > 1:
            labels = [f"{role_name if slot.slot_name == '主力' else slot.slot_name}（槽位 #{slot.slot_id}）"
                      + ("（已锁定）" if slot.locked else "") for slot in slots]
            label, accepted = QInputDialog.getItem(self, "导入目标槽位",
                f"将 [{role_name}] 的游戏内配装导入到：", labels, 0, False)
            if not accepted:
                return
            target = slots[labels.index(label)]
        if target is not None and target.locked:
            _persistence_failure(self, title, "目标配装槽位已锁定，请先解除锁定。")
            return
        if target is not None and target.plan_id is not None:
            answer = QMessageBox.question(self, title,
                f"导入后将替换 [{role_name}] 当前的计算器配装方案，是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        selected = replace(request, slot_id=target.slot_id if target else None,
                           create_slot_name=None if target else "主力")
        _commit_game_import(self, operation, snapshot, (selected,), role_name=role_name)

    self.equipment_persistence_controller.prepare(prepared,
        lambda error: _persistence_failure(self, title, error))


def _import_all_game_loadouts(self: Any) -> None:
    states = getattr(self, "_game_loadout_states", {}) or {}
    eligible = [(role_name, state) for role_name, state in states.items()
                if isinstance(state, dict) and state.get("_game_importable")
                and not state.get("_game_imported") and not state.get("_game_existing_plan_locked")]
    locked_count = sum(isinstance(state, dict) and bool(state.get("_game_importable"))
                       and bool(state.get("_game_existing_plan_locked")) for state in states.values())
    if not eligible:
        QMessageBox.information(self, "一键导入", "当前没有待导入的完整游戏内方案。")
        return
    try:
        requests = tuple(_freeze_game_import(self, role_name, state) for role_name, state in eligible)
    except Exception as exc:
        _persistence_failure(self, "一键导入", str(exc))
        return

    def prepared(snapshot, operation):
        selected, skipped = [], locked_count
        for request in requests:
            slots = tuple(slot for slot in snapshot.slots if slot.character_id == request.projection.character_id)
            target = next((slot for slot in slots if slot.slot_key == "primary"), next(iter(slots), None))
            if target is not None and target.locked:
                skipped += 1
                continue
            selected.append(replace(request, slot_id=target.slot_id if target else None,
                create_slot_name=None if target else request.projection.role_name))
        if not selected:
            QMessageBox.information(self, "一键导入", "当前没有待导入的完整游戏内方案。")
            return
        prompt = f"将导入 {len(selected)} 名角色的游戏内方案。对应角色的现有未锁定方案会被替换，是否继续？"
        if skipped:
            prompt += f"\n\n另有 {skipped} 名角色因现有方案已锁定而跳过。"
        answer = QMessageBox.question(self, "一键导入", prompt,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            _commit_game_import(self, operation, snapshot, selected, locked_count=skipped)

    self.equipment_persistence_controller.prepare(prepared,
        lambda error: _persistence_failure(self, "一键导入", error))


class EquipmentDisplayControllerMixin:
    """Explicit MainWindow surface for saved equipment-plan display."""

    _equipment_compare_signature = _equipment_compare_signature
    _same_equipment_by_ocr = _same_equipment_by_ocr
    _page_equipment = _page_equipment
    _set_equipment_mode = _set_equipment_mode
    _refresh_equip = _refresh_equip
    _request_equipment_graduation_rate = _request_equipment_graduation_rate
    invalidate_saved_equipment_cache = invalidate_saved_equipment_cache
    reset_equipment_account_state = reset_equipment_account_state
    refresh_saved_equipment_after_mutation = (
        refresh_saved_equipment_after_mutation
    )
    _saved_plan_diff_text = _saved_plan_diff_text
    _show_saved_plan_diff_dialog = _show_saved_plan_diff_dialog
    _clear_all_equipment = _clear_all_equipment
    _delete_role_equipment = _delete_role_equipment
    _toggle_role_allocation_lock = _toggle_role_allocation_lock
    _manage_loadout_slot = _manage_loadout_slot
    _import_game_loadout = _import_game_loadout
    _import_all_game_loadouts = _import_all_game_loadouts
    _optimize_saved_equipment = _optimize_saved_equipment
