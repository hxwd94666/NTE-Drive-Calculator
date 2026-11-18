# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from src.domain.localization.catalog import localized_text
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
from src.features.inventory.equipment.management.batch_selection_dialog import BatchChoice, choose_batch_items
from src.features.inventory.equipment.display.display_context import equipment_presentation
from src.features.inventory.equipment.display.detail_cache import clear_equipment_detail_cache
from src.features.inventory.equipment.assembly.assembly_surface import release_assembly_surface
from src.features.inventory.equipment.display.loadout_scoring import score_equipment_display_state
from src.features.inventory.equipment.display.master_detail_view import update_equipment_role_status
from src.services.equipment.loadouts.game_loadout_projection_service import GameLoadoutImportRequest
from src.services.equipment.loadouts.loadout_management import GameLoadoutMutation, freeze_game_loadout_request
from src.utils.logger import logger
from src.domain.equipment.loadouts.loadout_management import LoadoutMutation


from src.features.inventory.equipment.display.display_view import (
    _clear_equip_content,
    _equipment_compare_signature,
    _same_equipment_by_ocr,
    _page_equipment,
    _request_equipment_graduation_rate,
    _set_equipment_mode,
    _refresh_equip,
)


from src.features.inventory.equipment.optimization.plan_optimizer import _optimize_saved_equipment


def _saved_plan_diff_text(self, role_name, diff):
    catalog = getattr(self, "text_catalog", None)
    removed = diff.get(DIFF_REMOVED, []) or []
    added = diff.get(DIFF_ADDED, []) or []
    if not removed and not added:
        return localized_text(
            catalog,
            "inventory.display_controller.no_changes",
            "本次保存与上一套方案没有装备变动。",
        )
    lines = [localized_text(
        catalog,
        "inventory.display_controller.role_heading",
        "{role_name} 配装变动：",
        role_name=role_name,
    )]
    if removed:
        lines.append("\n" + ui_enum_text(catalog, "卸下："))
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n" + ui_enum_text(catalog, "换上："))
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)


def _show_saved_plan_diff_dialog(self, role_name, diff):
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    presentation = equipment_presentation(self)
    build_dialog = getattr(presentation, "plan_diff_dialog", None)
    if callable(build_dialog):
        build_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self, _ui_text('loadouts.actions.show_saved_plan_diff_dialog.qmessagebox'), self._saved_plan_diff_text(role_name, diff))


def _persistence_failure(window, title, error):
    QMessageBox.warning(window, title, ui_enum_text(getattr(window, 'text_catalog', None), str(error)))


def _persistence_projection_failure(window, error):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.invalidate_saved_equipment_cache()
    QMessageBox.warning(window, _ui_text('loadouts.actions.persistence_projection_failure.qmessagebox'), plain_tooltip(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.inventory.persistence_projection_failure.qmessagebox') + error))


def _batch_delete_equipment(self):
    text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        slots = tuple(slot for slot in snapshot.slots if slot.plan_id is not None)
        if not slots:
            QMessageBox.information(self, text("batch.delete"), text("batch.delete_empty"))
            return
        selected = choose_batch_items(self, [BatchChoice(slot.slot_id,
            str(slot.role_name or slot.character_id),
            text("batch.locked") if slot.locked else "", enabled=not slot.locked,
            character_id=slot.character_id, subtitle=slot.slot_name, score=slot.score)
            for slot in slots], title=text("batch.delete_title"),
            description=text("batch.delete_description"),
            accept_text=text("batch.delete_selected"))
        if not selected:
            return
        answer = QMessageBox.question(self, text("batch.delete"),
            text("batch.delete_confirm", count=len(selected)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def committed(_result):
            self.refresh_saved_equipment_after_mutation()
            logger.success(f"已删除所选配装 count={len(selected)}")

        controller.commit(operation, LoadoutMutation("delete_batch", snapshot, slot_ids=tuple(selected)),
            committed, lambda error: _persistence_failure(self, text("batch.delete"), error),
            lambda error: _persistence_projection_failure(self, error))

    controller.prepare(prepared, lambda error: _persistence_failure(self, text("batch.delete"), error))


def invalidate_saved_equipment_cache(self: Any) -> None:
    """Public cross-feature hook after a persisted loadout mutation."""

    self._saved_equipment_cache_valid = False
    # 当前详情可保留至写入回执刷新；隐藏详情先释放，防止回到旧方案/旧锁状态。
    clear_equipment_detail_cache(self, keep_visible=True)
    # 写操作前启动的读取不得发布过期缓存；变更代次在交付时再次复核。
    self._equip_load_token = object()
    lane = getattr(self, "_equip_read_lane", None)
    if lane is not None:
        # 只撤销交付和排队请求；原 IO 仍由页面通道持有至物理收尾，不阻塞账号切换。
        lane.cancel()


def reset_equipment_account_state(self: Any) -> None:
    """Discard account-owned projections before the shell refreshes a page."""

    invalidate_saved_equipment_cache(self)
    release_assembly_surface(self)
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
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    if plan_id is None:
        _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('loadouts.actions.delete_role_equipment.answer'), localized_text(getattr(self, 'text_catalog', None), "inventory.display_controller.state_missing_identity", "当前方案缺少正式身份，请刷新页面。"))
        return
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        slot = snapshot.plan(int(plan_id))
        answer = QMessageBox.question(self, _ui_text('loadouts.actions.delete_role_equipment.answer'),
            plain_tooltip(_ui_text('loadouts.actions.delete_role_equipment.answer.state', role_name=f'{role_name}')),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        controller.commit(operation, LoadoutMutation("delete", snapshot, slot.slot_id),
            lambda _result: self.refresh_saved_equipment_after_mutation(restore_role_name=role_name),
            lambda error: _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('loadouts.actions.delete_role_equipment.answer'), error),
            lambda error: _persistence_projection_failure(self, error))

    controller.prepare(prepared, lambda error: _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('loadouts.actions.delete_role_equipment.answer'), error), plan_id=int(plan_id))


def _manage_loadout_slot(self, slot_id, *, role_name=None):
    from src.features.inventory.equipment.management.slot_manager_dialog import LoadoutSlotDialog
    controller = self.equipment_persistence_controller

    def prepared(snapshot, operation):
        dialog = LoadoutSlotDialog(self, controller, snapshot, operation, int(slot_id), role_name)
        dialog.open()

    controller.prepare(prepared, lambda error: _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.manage_loadout_slot.caption'), error), slot_id=int(slot_id))


def _toggle_role_allocation_lock(self, role_name, *, plan_id=None, state_key=None, completed=None):
    if plan_id is None:
        _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.toggle_role_allocation_lock.caption'), localized_text(getattr(self, 'text_catalog', None), "inventory.display_controller.state_missing_identity", "当前方案缺少正式身份，请刷新页面。"))
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
            lambda error: _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.toggle_role_allocation_lock.caption'), error),
            lambda error: _persistence_projection_failure(self, error))

    controller.prepare(prepared, lambda error: _persistence_failure(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.toggle_role_allocation_lock.caption'), error), plan_id=int(plan_id))


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


def _game_import_needs_read(self, snapshot, request):
    """只有静止且影响范围已闭合的游戏页可窄投影，供体或未知范围复用正式读取。"""
    if getattr(self, "_equipment_mode", "game") != "game":
        return True
    lane = getattr(self, "_equip_read_lane", None)
    if lane is not None and lane.is_running():
        return True
    if any(widget is not None and not widget.isEnabled() for widget in (
        getattr(self, "equip_role_strip", None), getattr(self, "equip_content", None),
    )):
        return True
    other_slots = tuple(slot for slot in getattr(snapshot, "slots", ())
                        if slot.plan_id is not None and slot.character_id != request.projection.character_id)
    if not other_slots:
        return False
    if not getattr(self, "_saved_equipment_cache_valid", False):
        return True
    states = {state.get("_sqlite_plan_id"): state
              for state in (getattr(self, "_saved_equipment_states", {}) or {}).values() if isinstance(state, dict)}
    claimed = {(int(item["uid_slot"]), int(item["uid_serial"])) for item in request.projection.assignments}
    for slot in other_slots:
        state = states.get(slot.plan_id)
        if (state is None or state.get("_sqlite_source_snapshot_id") != slot.source_snapshot_id
                or "_official_items" not in state):
            return True
        # 已验证投影与冻结当前方案身份一致时才检查 UID；缓存不齐不猜“没有供体”。
        if any((int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0)) in claimed
               for item in state["_official_items"] if not item.get("virtual")):
            return True
    return False


def _commit_game_import(self, operation, snapshot, requests, *, role_name=None, locked_count=0):
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    title = resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.inventory.equipment.display.display_controller.import_game_loadout.text') if role_name is not None else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('batch.import')
    imported_state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)

    def committed(plan_ids):
        # 先判断读取/模式及供体范围，再撤销旧读；否则在途读取消后会把旧详情留在禁用态。
        needs_read = role_name is None or _game_import_needs_read(self, snapshot, requests[0])
        self.invalidate_saved_equipment_cache()
        if role_name is not None:
            current = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
            projection = current.get("_game_projection") if isinstance(current, dict) else None
            frozen = requests[0].projection
            # 回执只标记同一冻结游戏投影；新同步替换的角色状态不得被旧导入标为已保存。
            if (current is imported_state and projection is not None
                    and projection.snapshot_id == frozen.snapshot_id
                    and projection.equipment_fingerprint == frozen.equipment_fingerprint):
                changes = {"_game_imported": True, "_game_existing_plan_id": int(plan_ids[0]),
                           "_game_existing_plan_locked": False}
                current.update(changes)
                if not needs_read:
                    update_equipment_role_status(self, role_name, **changes)
        if needs_read:
            # 保留当前选择/滚动，不因导入另一角色而跳转；新读取覆盖供体方案及全部对比状态。
            self._refresh_equip()
        else:
            # 计算缓存仍失效；确认无跨角色影响的静止游戏详情保留原控件与滚动位置。
            button = getattr(self, "equip_import_all_btn", None)
            if button is not None:
                ready_count = sum(isinstance(state, dict) and bool(state.get("_game_importable"))
                                  and not state.get("_game_imported") and not state.get("_game_existing_plan_locked")
                                  for state in (getattr(self, "_game_loadout_states", {}) or {}).values())
                button.setEnabled(ready_count > 0)
                button.setToolTip(plain_tooltip(_ui_text('loadouts.actions.commit_game_import.button.state', ready_count=f'{ready_count}')))
        message = (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.inventory.equipment.display.display_controller.committed.text', role_name=f'{role_name}') if role_name is not None
                   else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.inventory.equipment.display.display_controller.committed.text_2', value=f'{len(plan_ids)}'))
        if locked_count:
            message += localized_text(
                getattr(self, 'text_catalog', None),
                "inventory.display_controller.import_locked_skipped",
                "\n{count} 名角色因方案已锁定而跳过。",
                count=locked_count,
            )
        QMessageBox.information(self, title, message)
        logger.info(f"已导入游戏内配装 count={len(plan_ids)}")

    self.equipment_persistence_controller.commit(operation,
        GameLoadoutMutation(snapshot, tuple(requests)), committed,
        lambda error: _persistence_failure(self, title, error),
        lambda error: _persistence_projection_failure(self, error))


def _import_game_loadout(self: Any, role_name: str) -> None:
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
    title = resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.inventory.equipment.display.display_controller.import_game_loadout.text')
    if not isinstance(state, dict):
        _persistence_failure(
            self, title,
            localized_text(
                getattr(self, 'text_catalog', None),
                "inventory.display_controller.stale_display",
                "当前展示已失效，请刷新后重试。",
            ),
        )
        return
    if state.get("_game_projection") is None or not bool(state.get("_game_importable")):
        _persistence_failure(
            self, title,
            str(state.get("_game_reason") or localized_text(
                getattr(self, 'text_catalog', None),
                "inventory.display_controller.incomplete_game_loadout",
                "当前游戏内装备不能形成完整方案。",
            )),
        )
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
            catalog = getattr(self, 'text_catalog', None)
            labels = [
                localized_text(
                    catalog,
                    "inventory.display_controller.slot_label",
                    "{name}（槽位 #{slot_id}）",
                    name=role_name if slot.slot_name == '主力' else slot.slot_name,
                    slot_id=slot.slot_id,
                ) + (ui_enum_text(catalog, "（已锁定）") if slot.locked else "")
                for slot in slots
            ]
            label, accepted = QInputDialog.getItem(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.prepared.label_accepted'),
                plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.prepared.label_accepted_2', role_name=f'{role_name}')), labels, 0, False)
            if not accepted:
                return
            target = slots[labels.index(label)]
        if target is not None and target.locked:
            _persistence_failure(
                self, title,
                localized_text(
                    getattr(self, 'text_catalog', None),
                    "inventory.display_controller.target_slot_locked",
                    "目标配装槽位已锁定，请先解除锁定。",
                ),
            )
            return
        if target is not None and target.plan_id is not None:
            answer = QMessageBox.question(self, title,
                plain_tooltip(_ui_text('loadouts.actions.import_game_loadout.answer.state', role_name=f'{role_name}')),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        selected = replace(request, slot_id=target.slot_id if target else None,
                           create_slot_name=None if target else "主力")
        _commit_game_import(self, operation, snapshot, (selected,), role_name=role_name)

    self.equipment_persistence_controller.prepare(prepared,
        lambda error: _persistence_failure(self, title, error))


def _batch_import_game_loadouts(self: Any) -> None:
    text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
    states = getattr(self, "_game_loadout_states", {}) or {}
    eligible = [(role_name, state) for role_name, state in states.items()
                if isinstance(state, dict) and state.get("_game_importable")
                and not state.get("_game_imported") and not state.get("_game_existing_plan_locked")]
    locked_count = sum(isinstance(state, dict) and bool(state.get("_game_importable"))
                       and bool(state.get("_game_existing_plan_locked")) for state in states.values())
    if not eligible:
        QMessageBox.information(self, text("batch.import"), text("batch.import_empty"))
        return
    try:
        requests = tuple(_freeze_game_import(self, role_name, state) for role_name, state in eligible)
    except Exception as exc:
        _persistence_failure(self, text("batch.import"), str(exc))
        return

    def prepared(snapshot, operation):
        candidates, choices, skipped = {}, [], locked_count
        for request in requests:
            slots = tuple(slot for slot in snapshot.slots if slot.character_id == request.projection.character_id)
            target = next((slot for slot in slots if slot.slot_key == "primary"), next(iter(slots), None))
            locked = target is not None and target.locked
            character_id = request.projection.character_id
            choices.append(BatchChoice(character_id, request.projection.role_name,
                text("batch.locked") if locked else text("batch.import_action"),
                enabled=not locked, character_id=character_id,
                subtitle=text("batch.import_target", slot=target.slot_name if target else "主力"),
                score=request.score, score_source="game"))
            if locked:
                skipped += 1
                continue
            candidates[character_id] = replace(request, slot_id=target.slot_id if target else None,
                create_slot_name=None if target else "主力")
        if not candidates:
            QMessageBox.information(self, text("batch.import"), text("batch.import_empty"))
            return
        selected_ids = choose_batch_items(self, choices, title=text("batch.import_title"),
            description=text("batch.import_description"),
            accept_text=text("batch.import_selected"))
        if not selected_ids:
            return
        selected = tuple(candidates[identity] for identity in selected_ids)
        prompt = text("batch.import_confirm", count=len(selected))
        if skipped:
            prompt += "\n\n" + text("batch.import_skipped", count=skipped)
        answer = QMessageBox.question(self, text("batch.import"), prompt,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            _commit_game_import(self, operation, snapshot, selected, locked_count=skipped)

    self.equipment_persistence_controller.prepare(prepared,
        lambda error: _persistence_failure(self, text("batch.import"), error))


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
    _batch_delete_equipment = _batch_delete_equipment
    _delete_role_equipment = _delete_role_equipment
    _toggle_role_allocation_lock = _toggle_role_allocation_lock
    _manage_loadout_slot = _manage_loadout_slot
    _import_game_loadout = _import_game_loadout
    _batch_import_game_loadouts = _batch_import_game_loadouts
    _optimize_saved_equipment = _optimize_saved_equipment
