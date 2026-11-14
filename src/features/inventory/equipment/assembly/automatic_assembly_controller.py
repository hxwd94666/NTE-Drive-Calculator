# 编排游戏界面自动装配的确认、账号投影、后台执行和结果恢复。
"""Controller helpers for step-by-step game UI equipment assembly."""

from __future__ import annotations

from src.features.input_operation_entry import (
    request_input_entry,
)

import threading
from dataclasses import asdict
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from src.app.workers import WorkerThread
from src.ui.common.text_catalog import resolve_text_catalog
from src.features.inventory.equipment.assembly.automatic_assembly_dialog import BACK_TO_SELECTION, confirm_automatic_execution
from src.features.inventory.equipment.assembly.assembly_surface import AssemblyProgressChannel, assembly_is_running, deliver_after_finished
from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend, AssemblyExecutionFailed, AssemblyExecutionStopped
from src.domain.equipment.assembly.assembly_progress import AssemblyTarget
from src.domain.equipment.loadouts.loadout_plan_scores import persisted_loadout_total_score
from src.services.equipment.assembly.assembly_progress import apply_progress_event
from src.integrations.runtime.operation_guard import bind_execution_guard
from src.features.drive_assembly.ui_bridge import (
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
)
from src.features.inventory.equipment.assembly.assembly_dialogs import assembly_report_dialog as _assembly_report_dialog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.equipment.loadouts.loadout_slot_selection_service import LoadoutSlotSelectionService

from src.features.inventory.equipment.display.plan_display_state import _sqlite_plan_display_state
from src.features.inventory.equipment.assembly.slot_selection_dialog import assembly_account_identity, select_assembly_slot_ids


def _return_to_equipment_after_assembly(window: Any) -> None:
    """Restore the calculator window and return to the equipment page."""

    show_normal = getattr(window, "showNormal", None)
    if callable(show_normal):
        show_normal()
    go_to_page = getattr(window, "_go", None)
    if callable(go_to_page):
        go_to_page("equipment")
    raise_window = getattr(window, "raise_", None)
    if callable(raise_window):
        raise_window()
    activate_window = getattr(window, "activateWindow", None)
    if callable(activate_window):
        activate_window()


def _account_database_path(window: Any) -> Path:
    app_context = getattr(window, "app_context", None)
    if app_context is None:
        explicit = getattr(window, "user_database_path", None)
        if explicit is None:
            raise RuntimeError("装配控制器缺少当前账号数据库依赖")
        return Path(explicit)
    return Path(app_context.account.user_database_path)


def _assembly_runtime_paths(window: Any) -> tuple[Path, Path]:
    """Return role-template and run-record roots for the active account."""

    app_context = getattr(window, "app_context", None)
    if app_context is None:
        template_dir = getattr(window, "role_template_dir", None)
        screenshot_dir = getattr(window, "screenshot_dir", None)
        if template_dir is None or screenshot_dir is None:
            raise RuntimeError("自动装配缺少角色模板或当前账号截图目录依赖")
        return Path(template_dir), Path(screenshot_dir) / "record"
    return (
        Path(app_context.paths.template_dir) / "roles",
        Path(app_context.account.screenshot_dir) / "record",
    )


def _sqlite_automatic_assembly_state(
    database_path: str | Path,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
    text_catalog=None,
) -> dict[str, dict[str, Any]]:
    """从 SQLite 已保存方案构建自动装配动作所需的只读投影。"""

    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        states: dict[str, dict[str, Any]] = {}
        service = LoadoutSlotSelectionService(user_dao)
        selections = service.resolve(slot_ids) if slot_ids else service.resolve_default_roles(role_names)
        for selection in selections:
            states[selection.role_name] = _sqlite_plan_display_state(
                dict(selection.plan),
                user_dao,
                static_dao,
                text_catalog=text_catalog,
            )
            states[selection.role_name]["_assembly_slot_id"] = selection.slot_id
            states[selection.role_name]["_assembly_target"] = asdict(AssemblyTarget(
                selection.slot_id, selection.character_id, selection.role_name, selection.slot_name,
                persisted_loadout_total_score(selection.plan), selection.plan_id, selection.source_snapshot_id))
    return states


def _assert_automatic_state_current(database_path, state):
    references = {int(row["_assembly_slot_id"]): (
        int(row["_sqlite_plan_id"]), int(row["_sqlite_source_snapshot_id"]),
    ) for row in state.values()}
    with UserDataDao(database_path) as dao:
        LoadoutSlotSelectionService(dao).assert_current_plans(references)


def _start_automatic_equipment_assembly(
    window: Any,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
) -> None:
    """输入与资源释放完成前，确认窗保持隐藏；结果回到同一窗口，不另弹成功框。"""
    text = resolve_text_catalog(getattr(window, "text_catalog", None)).render
    if not request_input_entry(window, "interface_input", text("assembly.auto.title")):
        return
    current_worker = getattr(window, "_automatic_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(window, text("assembly.auto.title"), text("assembly.busy"))
        return
    identity = assembly_account_identity(window)
    database_path = _account_database_path(window)
    try:
        state = _sqlite_automatic_assembly_state(database_path, role_names, slot_ids=slot_ids, text_catalog=getattr(window, 'text_catalog', None))
        template_dir, record_root = _assembly_runtime_paths(window)
    except Exception as exc:
        QMessageBox.warning(window, text("assembly.auto.title"), text("assembly.read_failed", reason=str(exc)))
        return
    execution_role_names = tuple(state)
    if not execution_role_names:
        QMessageBox.information(window, text("assembly.auto.title"), text("assembly.no_plans"))
        return
    hotkey_manager = getattr(window, "global_hotkey_manager", None)
    hotkey_owner = "automatic_equipment_apply"
    if getattr(hotkey_manager, "active_owner", None) not in (None, hotkey_owner):
        QMessageBox.information(window, text("assembly.auto.title"), text("assembly.hotkey_busy"))
        return
    stop_hotkey = str(getattr(getattr(hotkey_manager, "configuration", None), "stop", "F12"))
    confirmed = confirm_automatic_execution(window, execution_role_names, stop_hotkey)
    if confirmed is BACK_TO_SELECTION:
        return BACK_TO_SELECTION
    if confirmed is None:
        return
    surface, aliases = confirmed
    targets = tuple(AssemblyTarget(**row["_assembly_target"]) for row in state.values() if "_assembly_target" in row)
    surface.configure_targets(targets, automatic=True)
    # 确认只消费本次冻结输入；迟到的账号切换或撤权不使旧选择作用于新账号。
    if identity != assembly_account_identity(window):
        surface.hide()
        return
    if not request_input_entry(window, "interface_input", text("assembly.auto.title")):
        return
    try:
        _assert_automatic_state_current(database_path, state)
    except Exception as exc:
        surface.show_result(text("assembly.auto.not_started"), text("assembly.no_dispatch", reason=str(exc)), warning=True)
        return
    if getattr(hotkey_manager, "active_owner", None) not in (None, hotkey_owner):
        QMessageBox.information(window, text("assembly.auto.title"), text("assembly.hotkey_busy"))
        return
    stop_requested = threading.Event()
    worker = None

    def is_current():
        return identity == assembly_account_identity(window) and (
            worker is None or getattr(window, "_automatic_equipment_apply_worker", None) is worker)

    def should_stop():
        return stop_requested.is_set() or not is_current()

    execution_guard = bind_execution_guard(getattr(window, "operation_guard", None),
        should_stop=should_stop, generation=getattr(window, "operation_generation", None))
    surface.stop_requested.connect(stop_requested.set)
    surface.begin_running(text("assembly.auto.title"), text("assembly.auto.running"), visible=False)
    channel = AssemblyProgressChannel(surface, is_current, is_stopping=stop_requested.is_set)
    # legacy 执行器的名字只在本次冻结映射内转为正式槽位；UI 不用名字关联数据库。
    target_slots = {target.role_name: target.slot_id for target in targets}

    def progress(event):
        slot_id = target_slots.get(event.get("role_name"))
        if slot_id is not None:
            channel.publish({**event, "slot_id": slot_id})
    surface.hide()
    show_minimized = getattr(window, "showMinimized", None)
    if callable(show_minimized):
        show_minimized()

    def run():
        # 模板、记录根、角色、装备和别名均已冻结；worker 不读取 Qt 控件或最新账号。
        if should_stop():
            raise CancelledError("装配请求已取消或账号代次失效")
        _assert_automatic_state_current(database_path, state)
        arguments = dict(backend=PyAutoGuiMouseBackend(operation_guard=execution_guard),
            template_dir=str(template_dir), record_root=record_root,
            role_name_aliases=dict(aliases), should_stop=should_stop, on_role_progress=progress)
        try:
            if len(execution_role_names) == 1:
                return execute_selected_role_from_current_game_page(state, execution_role_names[0], **arguments)
            return execute_all_roles_from_current_game_page(state, **arguments)
        except (AssemblyExecutionStopped, AssemblyExecutionFailed) as exc:
            if exc.report is None:
                raise
            return exc.report

    worker = WorkerThread(target=run, parent=window)
    window._automatic_equipment_apply_worker = worker

    def finish(title, message, warning):
        channel.close()
        surface.progress_model.finish_pending(stopped=True)
        surface.stop_requested.disconnect(stop_requested.set)
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        surface.end_running()
        if not is_current():
            surface.hide()
            return
        _return_to_equipment_after_assembly(window)
        surface.show_result(title, message, warning=warning)
        refresh = getattr(window, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_result(report):
        channel.drain()
        verified = set(getattr(report, "verified_roles", ()))
        failures = {str(item.get("role_name")): item for item in getattr(report, "verification_failures", ())}
        for row in getattr(report, "role_reports", ()):
            role_name = getattr(row, "role_name", "")
            ok = role_name in verified and role_name not in failures
            partial = bool(getattr(row, "skipped_actions", ()))
            detail = text("assembly.status.skipped_actions", count=len(row.skipped_actions)) if partial else ""
            if role_name in failures:
                detail = text("assembly.status.visual_failed")
            apply_progress_event(surface.progress_model, {"slot_id": target_slots.get(role_name), "role_name": role_name,
                "state": "partial" if partial else "confirmed" if ok else "unknown", "dispatched": True,
                "verified": ok, "scope": "subset" if partial else "full" if ok else "none", "detail": detail})
        for role_name in getattr(report, "missing_roles", ()):
            apply_progress_event(surface.progress_model, {"slot_id": target_slots.get(role_name), "role_name": role_name, "state": "skipped"})
        current = getattr(report, "current_role", None)
        if current and getattr(report, "partial_actions", 0):
            # 当前角色没有完整报告时，也保留已知发生过输入这一事实；不宣称装备已确认。
            if not any(row.target.role_name == current and row.verified for row in surface.progress_model.rows):
                apply_progress_event(surface.progress_model, {"slot_id": target_slots.get(current), "role_name": current,
                    "state": "unknown", "dispatched": True})
        title, message, completed = _assembly_report_dialog(text("assembly.auto.title"), report, len(execution_role_names), surface.text_catalog)
        if stop_requested.is_set():
            title = text("assembly.auto.stopped")
            message = text("assembly.stopped_detail") + "\n\n" + message
            completed = False
        finish(text("assembly.auto.completed" if completed else "assembly.auto.incomplete") if not stop_requested.is_set() else title, message, not completed)

    def on_error(message):
        if stop_requested.is_set():
            finish(text("assembly.auto.stopped"), text("assembly.stopped_detail"), True)
        else:
            finish(text("assembly.auto.incomplete"), str(message), True)

    deliver_after_finished(worker, on_result, on_error, no_result=text("assembly.no_result"))
    try:
        if hotkey_manager is not None:
            hotkey_manager.start(owner=hotkey_owner, on_stop=stop_requested.set)
        # 名字在用户确认并通过派发门禁后才记忆；取消统一确认页不改配置。
        if aliases:
            name = next(iter(aliases.values()))
            window._drive_assembly_protagonist_name = name
            if surface.remember_name is not None and surface.remember_name.isChecked():
                preferences = getattr(window, "_ui_preferences", None)
                if isinstance(preferences, dict):
                    preferences["protagonist_game_name"] = name
                    saver = getattr(window, "_save_ui_preferences", None)
                    if callable(saver):
                        saver()
        worker.start()
    except Exception as exc:
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        if worker.isRunning():
            stop_requested.set()
        else:
            finish(text("assembly.auto.not_started"), str(exc), True)


def _preview_automatic_assemble_role(window: Any, role_name: str, *, slot_id=None, confirmed=False):
    # 单角色的装配方式选择只选择方式；真正执行授权统一由下一页的开始按钮完成。
    result = _start_automatic_equipment_assembly(window, [role_name] if slot_id is None else [],
        slot_ids=[int(slot_id)] if slot_id is not None else None)
    if result is BACK_TO_SELECTION:
        _preview_automatic_assemble_all_roles(window, role_names=[role_name],
            initial_slot_ids=[int(slot_id)] if slot_id is not None else None)


def _preview_automatic_assemble_all_roles(window: Any, role_names=None, *, slot_ids=None, initial_slot_ids=None):
    text = resolve_text_catalog(getattr(window, "text_catalog", None)).render
    if not request_input_entry(window, "interface_input", text("assembly.auto.title")):
        return
    if assembly_is_running(window):
        QMessageBox.information(window, text("assembly.auto.title"), text("assembly.busy"))
        return
    identity = assembly_account_identity(window)
    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    pending = tuple(initial_slot_ids) if initial_slot_ids is not None else None
    while identity == assembly_account_identity(window):
        try:
            with UserDataDao(_account_database_path(window)) as user_dao:
                selection_service = LoadoutSlotSelectionService(user_dao)
                current_slots = selection_service.list_current()
                if requested_roles:
                    missing = set(requested_roles) - {selection.role_name for selection in current_slots}
                    if missing:
                        QMessageBox.information(window, text("assembly.auto.title"), text("assembly.missing_roles", roles="、".join(sorted(missing))))
                        return
                    current_slots = tuple(row for row in current_slots if row.role_name in requested_roles)
                if slot_ids is not None:
                    selected = list(slot_ids)
                elif pending is not None:
                    selected = select_assembly_slot_ids(window, current_slots, selected_slot_ids=pending)
                else:
                    selected = select_assembly_slot_ids(window, current_slots)
                if not selected:
                    return
                selection_service.resolve(selected)
        except Exception as exc:
            QMessageBox.warning(window, text("assembly.auto.title"), text("assembly.read_failed", reason=str(exc)))
            return
        if identity != assembly_account_identity(window):
            return
        result = _start_automatic_equipment_assembly(window, [], slot_ids=selected)
        if result is not BACK_TO_SELECTION:
            return
        # 返回只保留选择草稿，不启动任务；重新读取正式槽位，失效方案不会被复活。
        pending, slot_ids = tuple(selected), None
