# 冻结保存按钮输入，在后台批量持久化并按真实阶段更新进度。
from __future__ import annotations

from concurrent.futures import CancelledError
from copy import deepcopy
import time

from PySide6.QtWidgets import QMessageBox

from src.features.allocation.save_progress import AllocationSaveProgress
from src.features.allocation.slot_plan_diff import selected_slot_plan_diff
from src.services.allocation_lock_service import AllocationLockSnapshot
from src.services.allocation_main_value_service import legacy_plan_tape_main_values
from src.services.allocation_plan_save import save_allocation_plans
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


def save_allocation(owner, *, show_message=True):
    from src.features.allocation.runner import (
        _allocation_paths, _select_allocation_save_slots, _role_state_from_plan,
        _persistable_plan_diff, _plan_changed_uids, _plan_assignment_scores,
    )

    if not owner.final_plan or getattr(owner, "_saving", False):
        return False
    calculation = getattr(owner, "_worker", None)
    if calculation is not None and calculation.isRunning():
        return False
    owner._saving = True
    button = getattr(owner, "btn_save", None)
    old_text = button.text() if button is not None else ""
    old_enabled = button.isEnabled() if button is not None else False
    if button is not None:
        button.setEnabled(False)
        button.setText("正在保存…")
        button.repaint()
    dialog = None
    saved_count = 0
    context = owner.app_context
    generation = context.generation
    cancel_event = owner._cancel_event
    cancel_event.clear()
    started = time.perf_counter()

    def checkpoint():
        if context.generation != generation or cancel_event.is_set():
            raise CancelledError("保存已取消或账号上下文已失效")

    try:
        database_path, _, _, _, static_path = _allocation_paths(owner)
        snapshot_id = owner._pending_allocation_snapshot_id
        identity = owner._pending_allocation_static_identity
        lock_snapshot = owner._allocation_lock_snapshot
        if snapshot_id is None or not isinstance(identity, tuple) or len(identity) != 3:
            raise RuntimeError("本次计算缺少冻结背包或静态数据集，请重新执行计算。")
        if not isinstance(lock_snapshot, AllocationLockSnapshot) or lock_snapshot.inventory_snapshot_id != snapshot_id:
            raise RuntimeError("本次计算缺少一致的配装锁定快照，请重新执行计算。")
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_path) as static_dao:
            targets = _select_allocation_save_slots(owner, user_dao, static_dao, snapshot_id)
            if targets is None:
                return False
            plans = deepcopy(owner.final_plan)
            diffs = selected_slot_plan_diff(user_dao, plans, targets)
        rows = []
        for role, plan in plans.items():
            if not isinstance(plan, dict) or not plan.get("valid"):
                continue
            character_id, slot_id = targets[role]
            diff = diffs.get(role, {})
            rows.append(dict(
                role_name=role, role_state=_role_state_from_plan(plan), character_id=character_id,
                snapshot_id=snapshot_id, name=f"计算方案：{role}", score=float(plan.get("score", 0.0) or 0.0),
                slot_id=slot_id, payload={
                    "schema": "allocation-official-snapshot-v1", "source": "allocation",
                    "source_role_name": role, "static_dataset_id": identity[1],
                    "strategy": owner._pending_strat,
                    "blueprint_combo_limit": owner._pending_blueprint_combo_limit,
                    "last_diff": _persistable_plan_diff(diff),
                    "changed_uids": sorted(_plan_changed_uids(plan, diff)),
                    "assignment_scores": _plan_assignment_scores(role, plan),
                    "tape_main_values": legacy_plan_tape_main_values(plan),
                },
            ))
        if not rows:
            raise RuntimeError("本次计算没有可保存的有效方案。")
        checkpoint()
        dialog = AllocationSaveProgress(owner.dialog_parent)
        saved_count = dialog.run(lambda progress: save_allocation_plans(
            database_path=database_path, static_database_path=static_path,
            static_identity=identity, lock_snapshot=lock_snapshot, rows=rows,
            checkpoint=checkpoint, progress=progress,
        ), owner)
        if context.generation != generation:
            return False
        owner.allocation_plan_diff = diffs
        owner._allocation_dirty = False
        phases = (
            ("正在更新计算结果…", lambda: owner._render_results(plans)),
            ("正在更新角色页面…", owner._refresh_my_role),
            ("正在更新配装页面…", owner._refresh_equip),
        )
        for index, (label, refresh) in enumerate(phases, 1):
            dialog.set_stage((label, len(rows) + index, len(rows) + 4))
            dialog.repaint()
            refresh()
        dialog.set_stage(("保存完成", len(rows) + 4, len(rows) + 4))
        dialog.close()
        logger.info(f"allocation_save_complete roles={saved_count} elapsed_ms={(time.perf_counter() - started) * 1000:.1f}")
        if show_message:
            QMessageBox.information(owner.dialog_parent, "保存成功",
                                    f"已保存 {saved_count} 个方案，并同步到角色与配装页面。")
        return True
    except CancelledError:
        return False
    except Exception as error:
        if dialog is not None:
            dialog.close()
        if saved_count:
            QMessageBox.warning(owner.dialog_parent, "方案已保存", f"页面刷新失败，请重新进入页面。\n{error}")
            return True
        QMessageBox.critical(owner.dialog_parent, "保存失败", str(error))
        return False
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        owner._saving = False
        if button is not None:
            button.setText(old_text)
            button.setEnabled(old_enabled)
