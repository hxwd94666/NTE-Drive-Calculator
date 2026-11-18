# 冻结保存按钮输入，在后台批量持久化并按真实阶段更新进度。
from __future__ import annotations
from html import escape

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog, rich_label
from concurrent.futures import CancelledError
from copy import deepcopy
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
)

from src.app.constants import SUPPORT_US_URL
from src.app.theme import current_style_sheet, theme_color
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.loadouts.plan_changes import SavedPlansChanged
from src.features.allocation.execution.save_progress import AllocationSaveProgress
from src.services.allocation.results.slot_comparisons import selected_legacy_comparison_diffs
from src.features.inventory.equipment.display.display_controller import invalidate_saved_equipment_cache
from src.services.allocation.direct.direct_postprocess import optimization_payload
from src.services.allocation.execution.baseline_fallback import baseline_save_available
from src.services.allocation.results.postprocess_facts import allocation_role_state_from_plan
from src.services.allocation.inputs.locks import AllocationLockSnapshot
from src.services.allocation.results.plan_save import legacy_plan_tape_main_values, save_allocation_plans
from src.integrations.static_catalog.static_catalog_release import StaticCatalogReleaseReader
from src.services.allocation.results.plan_save import require_allocation_release
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


def show_allocation_save_success(parent, saved_count: int, *, text_catalog=None) -> None:
    """Keep optional support inside the success dialog, without interrupting calculation."""

    dialog = QDialog(parent)
    dialog.setObjectName("allocationSaveSuccessDialog")
    dialog.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.allocation.show_allocation_save_success.dialog'))
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 16, 20, 14)
    layout.setSpacing(10)
    summary = QHBoxLayout()
    summary.setSpacing(12)
    mark = plain_label("✓", dialog)
    mark.setObjectName("allocationSaveSuccessMark")
    mark.setAlignment(Qt.AlignCenter)
    mark.setFixedSize(34, 34)
    mark.setStyleSheet(
        f"background:{theme_color('#238636')};color:#fff;"
        "border-radius:17px;font-size:20px;font-weight:700;"
    )
    summary.addWidget(mark)
    text = QVBoxLayout()
    text.setSpacing(2)
    headline = plain_label(resolve_text_catalog(text_catalog).render('ui.allocation.show_allocation_save_success.headline', saved_count=f'{saved_count}'), dialog)
    headline.setObjectName("allocationSaveSuccessHeadline")
    headline.setStyleSheet(f"color:{theme_color('#f0f6fc')};font-size:16px;font-weight:700")
    text.addWidget(headline)
    detail = plain_label(resolve_text_catalog(text_catalog).render('ui.allocation.show_allocation_save_success.detail'), dialog)
    detail.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
    text.addWidget(detail)
    summary.addLayout(text, 1)
    layout.addLayout(summary)
    support = rich_label(
        f'<a href="{escape(SUPPORT_US_URL, quote=True)}" style="color:{theme_color("#58a6ff")};'
        f'text-decoration:underline;">{escape(resolve_text_catalog(text_catalog).render("ui.allocation.support_caption"))}</a>',
        dialog,
    )
    support.setObjectName("allocationSaveSupportLink")
    support.setTextFormat(Qt.RichText)
    support.setTextInteractionFlags(Qt.TextBrowserInteraction)
    support.setOpenExternalLinks(True)
    support.setFocusPolicy(Qt.StrongFocus)
    support_row = QHBoxLayout()
    support_row.addSpacing(46)
    support_row.addWidget(support)
    support_row.addStretch()
    layout.addLayout(support_row)
    layout.addSpacing(12)
    actions = QHBoxLayout()
    actions.addStretch()
    confirm = TextActionButton(resolve_text_catalog(text_catalog).render('selector.confirm'), dialog)
    confirm.setDefault(True)
    confirm.clicked.connect(dialog.accept)
    actions.addWidget(confirm)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(390, 164))
    QApplication.beep()
    dialog.exec()


def save_allocation(owner, *, show_message=True, on_saved=None, text_catalog=None):
    from src.features.allocation.execution.runner import (
        _allocation_paths,
        _select_allocation_save_slots,
        _persistable_plan_diff,
        _plan_changed_uids,
        _plan_assignment_scores,
        _allocation_context_identity,
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
        button.setText(resolve_text_catalog(text_catalog).render('matching.saving'))
        button.repaint()
    dialog = None
    saved_count = 0
    context_identity = _allocation_context_identity(owner)
    cancel_event = owner._cancel_event
    cancel_event.clear()
    started = time.perf_counter()

    def checkpoint():
        if _allocation_context_identity(owner) != context_identity or cancel_event.is_set():
            raise CancelledError("保存已取消或账号上下文已失效")

    try:
        database_path, _, _, _, static_path = _allocation_paths(owner)
        snapshot_id = owner._pending_allocation_snapshot_id
        identity = owner._pending_allocation_static_identity
        release = require_allocation_release(getattr(owner, "_pending_allocation_static_release", None), static_path)
        lock_snapshot = owner._allocation_lock_snapshot
        if snapshot_id is None or not isinstance(identity, tuple) or len(identity) != 3:
            raise RuntimeError("本次计算缺少冻结背包或静态数据集，请重新执行计算。")
        if not isinstance(lock_snapshot, AllocationLockSnapshot) or lock_snapshot.inventory_snapshot_id != snapshot_id:
            raise RuntimeError("本次计算缺少一致的配装锁定快照，请重新执行计算。")
        eligible = {role: plan for role, plan in owner.final_plan.items() if isinstance(plan, dict) and plan.get("valid")
            and (not plan.get("damage_optimization") or plan["damage_optimization"].get("status") == "completed"
                 or baseline_save_available(plan))}
        skipped = [role for role, plan in owner.final_plan.items() if isinstance(plan, dict) and plan.get("damage_optimization") and role not in eligible]
        if not eligible:
            raise RuntimeError("本次没有可保存的合法配装，请检查库存、筛选条件或冻结来源。")
        if skipped:
            choice = QMessageBox.warning(owner.dialog_parent, resolve_text_catalog(text_catalog).render('ui.allocation.save_allocation.choice'),
                plain_tooltip(resolve_text_catalog(text_catalog).render('ui.allocation.save_allocation.choice_2') + "、".join(eligible) + resolve_text_catalog(text_catalog).render('ui.allocation.save_allocation.choice_3') + "、".join(skipped)
                + resolve_text_catalog(text_catalog).render('ui.allocation.save_allocation.choice_4')),
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
            if choice != QMessageBox.Ok:
                return False
        with UserDataDao(database_path) as user_dao, StaticCatalogReleaseReader().read_unit(
                static_path, expected_release=release) as unit:
            static_dao = unit.dao
            targets = _select_allocation_save_slots(owner, user_dao, static_dao, snapshot_id, plans=eligible)
            if targets is None:
                return False
        checkpoint()
        # The plan is owned by this save operation until the modal worker finishes.
        source_plans = eligible
        strategy = owner._pending_strat
        combo_limit = owner._pending_blueprint_combo_limit

        def prepare_and_save(progress):
            checkpoint()
            plans = deepcopy(source_plans)
            diffs = selected_legacy_comparison_diffs(owner._allocation_frozen_comparisons, plans, targets)
            rows = []
            for role, plan in plans.items():
                if not isinstance(plan, dict) or not plan.get("valid"):
                    continue
                character_id, slot_id = targets[role]
                diff = diffs.get(role, {})
                rows.append(dict(
                    role_name=role, role_state=allocation_role_state_from_plan(plan), character_id=character_id,
                    snapshot_id=snapshot_id, name=localized_text(text_catalog, 'auto.features.allocation.execution.save_workflow.prepare_and_save.f653f78e9', '计算方案：{role}', role=f'{role}'), score=float(plan.get("score", 0.0) or 0.0),
                    slot_id=slot_id, axis_preview_source=plan.get("axis_preview_source"),
                    baseline_preview_source=plan.get("baseline_preview_source"),
                    team_enhancement_preview=plan.get("team_enhancement_preview"),
                    direct_group_preview_source=plan.get("direct_group_preview_source"), payload={
                        "schema": "allocation-official-snapshot-v1", "source": "allocation",
                        "source_role_name": role, "static_dataset_id": identity[1],
                        "strategy": strategy,
                        "blueprint_combo_limit": combo_limit,
                        "last_diff": _persistable_plan_diff(diff),
                        "changed_uids": sorted(_plan_changed_uids(plan, diff)),
                        "assignment_scores": _plan_assignment_scores(role, plan),
                        "tape_main_values": legacy_plan_tape_main_values(plan),
                        # 目标值、预算及模型身份独立保存，不覆盖基础逐件分数。
                        **optimization_payload(plan),
                        # 回退事实取本次冻结结果，不从保存时的新偏好重新解释。
                        **({"core_main_filter_resolution": {
                            "requested": list(plan.get("requested_core_main_filters") or ()),
                            "fallback": bool(plan.get("main_filter_fallback")),
                            "rejected": bool(plan.get("main_filter_fallback_rejected")),
                        }} if "requested_core_main_filters" in plan else {}),
                    },
                ))
            if not rows:
                raise RuntimeError("本次计算没有可保存的有效方案。")
            checkpoint()
            count = save_allocation_plans(
                database_path=database_path, static_database_path=static_path,
                static_identity=identity, static_release=release, lock_snapshot=lock_snapshot, rows=rows,
                checkpoint=checkpoint, progress=progress,
            )
            return count, plans, diffs, len(rows)

        dialog = AllocationSaveProgress(owner.dialog_parent, text_catalog=text_catalog)
        saved_count, plans, diffs, row_count = dialog.run(prepare_and_save, owner)
        if getattr(owner, "_closing", False):
            return bool(saved_count)  # 已接受提交保留事实；退出不渲染或弹出成功模态。
        if _allocation_context_identity(owner) != context_identity:
            return False
        if saved_count and on_saved is not None:
            # 正式提交且代次仍有效才通知；先失效旧投影，后续渲染异常不撤销已保存事实。
            team_ids = {entry.team.team_id for plan in plans.values()
                        if plan.get("team_enhancement_preview") is not None
                        for entry in plan["team_enhancement_preview"].commit.entries}
            on_saved(SavedPlansChanged(context_identity[0], context_identity[1], str(context_identity[2]),
                tuple(sorted({targets[role][0] for role in plans})), tuple(sorted(team_ids)),
                tuple(sorted({targets[role][1] for role in plans if targets[role][1] is not None}))))
        owner.allocation_plan_diff = diffs
        owner._allocation_dirty = False
        # 仅正式提交/同一作业确认后更新可丢弃展示标志，不进入方案 payload 或冻结评分。
        for role, plan in plans.items():
            if plan.get("team_enhancement_preview") is not None:
                plan["team_enhancement_saved"] = True
                if role in owner.final_plan:
                    owner.final_plan[role]["team_enhancement_saved"] = True
        # Role/equipment pages refresh through navigation when the user opens them.
        # Rebuilding both off-screen pages here used to freeze the save dialog.
        invalidate_saved_equipment_cache(owner.dialog_parent)
        dialog.set_stage(("正在更新计算结果…", row_count + 1, row_count + 2))
        owner._render_results({**deepcopy(owner.final_plan), **plans})
        dialog.set_stage(("保存完成", row_count + 2, row_count + 2))
        dialog.close()
        logger.info(f"allocation_save_complete roles={saved_count} elapsed_ms={(time.perf_counter() - started) * 1000:.1f}")
        if show_message:
            show_allocation_save_success(owner.dialog_parent, saved_count, text_catalog=text_catalog)
        return True
    except CancelledError:
        return False
    except Exception as error:
        if getattr(owner, "_closing", False):
            logger.warning("allocation.save_closed | saved_count={} error_type={}", saved_count, type(error).__name__)
            return bool(saved_count)
        if dialog is not None:
            dialog.close()
        if saved_count:
            QMessageBox.warning(owner.dialog_parent, resolve_text_catalog(text_catalog).render('rewind.execution.save_plan.save_plan_button'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.allocation.save_allocation.qmessagebox', error=f'{error}')))
            return True
        QMessageBox.critical(owner.dialog_parent, resolve_text_catalog(text_catalog).render('roles.page.save_profiles.qmessagebox_2'), ui_enum_text(text_catalog, str(error)))
        return False
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        owner._saving = False
        if button is not None:
            button.setText(old_text)
            button.setEnabled(old_enabled)
