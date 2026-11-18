# 编排极速装配及其与游戏界面自动装配之间的入口路由。
"""Fast equipment apply controller and assembly-mode routing."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from src.features.input_operation_entry import (
    request_input_entry,
    show_input_unavailable,
    show_sync_required,
)

from collections.abc import Callable
from concurrent.futures import CancelledError
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from src.app.workers import WorkerThread
from src.features.inventory.equipment.assembly.assembly_surface import AssemblyProgressChannel, assembly_is_running, assembly_surface, deliver_after_finished, release_assembly_surface
from src.observability.context import OperationContext
from src.integrations.native_core.nte_core import is_mods_plugin_unavailable_error
from src.features.inventory.equipment.assembly.assembly_dialogs import assembly_report_dialog as _assembly_report_dialog
from src.features.inventory.equipment.assembly.fast_apply_completion_summary import build_fast_apply_completion_summary
from src.services.equipment.assembly.assembly_progress import freeze_assembly_targets, reconcile_fast_result
from src.services.equipment.assembly.apply import EquipmentApplyService
from src.services.equipment.assembly.bulk_apply import BulkEquipmentApplyService
from src.domain.inventory.source_capabilities import is_visual_inventory_source
from src.services.equipment.loadouts.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.features.inventory.equipment.assembly.automatic_assembly_controller import (
    _account_database_path,
    _preview_automatic_assemble_all_roles,
    _preview_automatic_assemble_role,
    _return_to_equipment_after_assembly,
)
from src.features.inventory.equipment.assembly.slot_selection_dialog import assembly_account_identity, select_assembly_slot_ids


__all__ = [
    "_preview_assemble_role",
    "_preview_fast_assemble_all_roles",
    "_preview_automatic_assemble_all_roles",
    "_assembly_report_dialog",
    "_return_to_equipment_after_assembly",
]


def _is_equipment_plugin_unavailable_error(error: object) -> bool:
    """识别核心已启动但游戏内装备插件桥接不可用的不可重试错误。"""

    return is_mods_plugin_unavailable_error(error)


def _equipment_failure_details(
    failure_kind: str,
    error: object, *, text_catalog=None,
) -> str:
    """Render one concrete failure category without conflating pipe states."""

    message = str(error or "未知错误")
    if failure_kind == "recovery_exhausted":
        return "组件状态更新期间的自动补救已停止；已下发步骤保持原状，请等待同步并核对后再继续。"
    if failure_kind == "cancelled":
        return "已停止后续装配，已经下发的操作不会回滚；请核对游戏内实际装备。"
    if failure_kind == "plugin_unavailable":
        return localized_text(text_catalog, 'auto.features.inventory.equipment.assembly.assembly_controller.equipment_failure_details.fa38f95ed', '原生装备通道不可用：{message}。请在工作模式检测详情中核对当前原生组件连接与装备能力。', message=f'{message}')
    if failure_kind == "plugin_busy":
        return "装备执行仍繁忙或正在等待同步就绪，本次请求尚未派发；已完成的步骤不会回滚。"
    if failure_kind == "outcome_unknown":
        return "本次装备操作结果未知，已停止后续装配且未自动重发。请等待背包同步并核对游戏装备后再重试。"
    if failure_kind == "core_request_timeout":
        return "nte-core 的请求响应等待超时；这不是命名管道缺失的检测结果。"
    if failure_kind == "request_rejected":
        return localized_text(text_catalog, 'auto.features.inventory.equipment.assembly.assembly_controller.equipment_failure_details.fdd6ddb2d', '装备插件已收到请求但拒绝执行：{message}', message=f'{message}')
    if failure_kind == "snapshot_timeout":
        return "装配请求已经下发，但没有在等待时间内取得新的稳定背包快照。"
    if failure_kind == "snapshot_error":
        return localized_text(text_catalog, 'auto.features.inventory.equipment.assembly.assembly_controller.equipment_failure_details.f937e5efb', '装配请求已经下发，但背包同步复核失败：{message}', message=f'{message}')
    if failure_kind == "loadout_mismatch":
        return message
    return message


def _equipment_assembly_is_running(window: Any) -> bool:
    """Report whether an account-bound assembly worker is still active."""

    for attribute in (
        "_equipment_apply_worker",
        "_automatic_equipment_apply_worker",
    ):
        worker = getattr(window, attribute, None)
        if worker is not None and worker.isRunning():
            return True
    return False


def _run_nte_core_equipment_apply(
    service: BulkEquipmentApplyService,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
    identity_overrides: dict[str, dict[str, Any]] | None = None,
    job_id: int | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return service.run(
        role_names,
        slot_ids=slot_ids,
        identity_overrides=identity_overrides,
        job_id=job_id,
        progress_callback=progress_callback,
    )


def _start_nte_core_equipment_apply(
    self: Any,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
    identity_overrides: dict[str, dict[str, Any]] | None = None,
    job_id: int | None = None,
) -> None:
    text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
    if not request_input_entry(self, "native_equipment", text("assembly.fast.title")):
        return
    sync = getattr(self, "_inventory_sync_service", None)
    if sync is None or not sync.is_running:
        show_sync_required(self, text("assembly.fast.title"))
        return
    current_worker = getattr(self, "_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, text("assembly.fast.title"), text("assembly.busy"))
        return
    hotkey_manager = getattr(self, "global_hotkey_manager", None)
    hotkey_owner = "fast_equipment_apply"
    if getattr(hotkey_manager, "active_owner", None) not in (None, hotkey_owner):
        QMessageBox.information(self, text("assembly.fast.title"), text("assembly.hotkey_busy"))
        return
    configuration = getattr(hotkey_manager, "configuration", None)
    stop_hotkey = str(getattr(configuration, "stop", "F12"))
    stop_hint = text("assembly.fast.stop_hint", hotkey=stop_hotkey)

    # 任务启动前在 Controller 线程冻结账号依赖；worker 不读取会变化的窗口字段。
    app_context = getattr(self, "app_context", None)
    generation = app_context.generation if app_context is not None else None
    account_id = app_context.account.active_account_id if app_context is not None else None
    database_path = app_context.account.user_database_path if app_context is not None else getattr(self, "user_database_path", None)
    if database_path is None:
        show_input_unavailable(self, text("assembly.fast.title"), text("assembly.database_not_ready"))
        return
    cancel_event = Event()
    worker = None

    def is_current():
        if worker is not None and getattr(self, "_equipment_apply_worker", None) is not worker:
            return False
        if app_context is None:
            return getattr(self, "app_context", None) is None and getattr(self, "user_database_path", None) == database_path
        return (
            getattr(self, "app_context", None) is app_context
            and app_context.generation == generation
            and app_context.account.active_account_id == account_id
            and app_context.account.user_database_path == database_path
        )

    def check_current():
        if not is_current():
            raise CancelledError("账号上下文已变化")

    progress_dialog = assembly_surface(self)
    try:
        if job_id is None:
            targets = freeze_assembly_targets(database_path, role_names, slot_ids)
        else:
            targets = tuple(row.target for row in progress_dialog.progress_model.rows)
        progress_dialog.configure_targets(targets)
    except Exception as exc:
        progress_dialog.configure_targets(())
        progress_dialog.show_result(text("assembly.fast.preflight_blocked"), text("assembly.no_dispatch", reason=str(exc)), warning=True)
        return
    expected_plans = {row.slot_id: (row.plan_id, row.source_snapshot_id) for row in targets}

    service = BulkEquipmentApplyService(
        database_path, sync, dao_factory=UserDataDao, apply_service_factory=EquipmentApplyService,
        operation_guard=getattr(self, "operation_guard", None), cancel_event=cancel_event,
        check_current=check_current, expected_plans=expected_plans,
        operation_context=OperationContext.create("equipment_apply", account_id=account_id,
                                                  context_generation=generation, job_id=job_id),
    )

    progress_dialog.begin_running(text("assembly.fast.title"), text("assembly.fast.preparing"))
    progress_dialog.status_view.notice.setText(stop_hint + " " + text("assembly.status.stop_notice"))
    channel = AssemblyProgressChannel(progress_dialog, is_current, is_stopping=cancel_event.is_set)

    def request_cancel():
        cancel_event.set()

    progress_dialog.stop_requested.connect(request_cancel)

    def close_progress_dialog() -> None:
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        progress_dialog.stop_requested.disconnect(request_cancel)
        channel.close()
        progress_dialog.end_running()
        progress_dialog.hide()

    worker = WorkerThread(
        target=lambda: _run_nte_core_equipment_apply(
            service,
            role_names,
            slot_ids=slot_ids,
            identity_overrides=identity_overrides,
            job_id=job_id,
            progress_callback=channel.publish,
        ),
        parent=self,
    )
    self._equipment_apply_worker = worker

    def on_result(report: dict) -> None:
        close_progress_dialog()
        if not is_current():
            return
        reconcile_fast_result(progress_dialog.progress_model, report)
        applied = report.get("applied") or []
        skipped = report.get("skipped_roles") or []
        summary, role_details = build_fast_apply_completion_summary(applied, skipped_roles=skipped, catalog=progress_dialog.text_catalog)
        problems = []
        for key in ("preflight_errors", "identity_requests", "repair_errors"):
            for row in report.get(key) or ():
                problems.append(text("assembly.status.problem", role=row.get("role_name") or text("selector.unknown_role"),
                                     reason=str(row.get("error") or row.get("reason") or text("assembly.status.skipped"))))
        if report.get("failed_role"):
            problems.append(_equipment_failure_details(str(report.get("failure_kind") or "apply_error"), report.get("error")))
        failure = report.get("snapshot_wait_failure")
        if isinstance(failure, dict):
            problems.append(_equipment_failure_details(str(failure.get("kind") or "snapshot_error"), failure.get("error")))
        if report.get("preflight_errors"):
            title = text("assembly.fast.preflight_blocked")
        elif report.get("failure_kind") == "cancelled" or cancel_event.is_set():
            title = text("assembly.fast.stopped")
        elif any(row.state == "unknown" for row in progress_dialog.progress_model.rows) or failure:
            title = text("assembly.fast.result_unknown")
        elif any(row.state == "failed" for row in progress_dialog.progress_model.rows):
            title = text("assembly.fast.incomplete")
        elif not applied and (skipped or problems):
            title = text("assembly.fast.not_executed")
        elif skipped or problems or any(row.state in {"partial", "skipped"} for row in progress_dialog.progress_model.rows):
            title = text("assembly.fast.partial")
        else:
            title = text("assembly.fast.completed")
        message = "\n\n".join(part for part in (summary, role_details, "\n".join(problems), text("assembly.fast.check_equipment")) if part)
        if report.get("job_id") is not None:
            message += "\n" + text("assembly.fast.log_saved", job=report["job_id"])
        progress_dialog.show_result(title, message, warning=bool(problems) or any(
            row.state in {"unknown", "failed"} for row in progress_dialog.progress_model.rows))
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        close_progress_dialog()
        if not is_current():
            return
        started = any(row.dispatched or row.state in {"operating", "waiting", "unknown"}
                      for row in progress_dialog.progress_model.rows)
        progress_dialog.progress_model.finish_pending(stopped=True)
        title = text("assembly.fast.stopped" if cancel_event.is_set() else "assembly.fast.result_unknown" if started else "assembly.fast.preflight_blocked")
        detail = text("assembly.stopped_detail") + "\n\n" + str(message) if started else text("assembly.no_dispatch", reason=str(message))
        progress_dialog.show_result(title, detail, warning=True)

    deliver_after_finished(worker, on_result, on_error, no_result=text("assembly.no_result"))
    try:
        if hotkey_manager is not None:
            hotkey_manager.start(owner=hotkey_owner, on_stop=request_cancel)
        progress_dialog.show()
        worker.start()
    except Exception:
        close_progress_dialog()
        raise


def _confirm_automatic_assembly_fallback(
    self: Any,
    detail: str,
) -> bool:
    """Ask before falling back from native-UID fast assembly to UI automation."""

    result = QMessageBox.question(
        self,
        resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.confirm_automatic_assembly_fallback.result'),
        plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.confirm_automatic_assembly_fallback.result_2', detail=f'{detail}')),
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    return result == QMessageBox.Yes


def _preview_nte_core_assemble_role(
    self: Any,
    role_name: str,
    *,
    slot_id: int | None = None,
    confirmed: bool = False,
) -> None:
    """确认后通过装备插件极速装配一个已保存角色方案。"""
    text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
    if not request_input_entry(self, "native_equipment", text("assembly.fast.title")):
        return

    try:
        with UserDataDao(_account_database_path(self)) as user_dao:
            slot = user_dao.get_loadout_slot(int(slot_id)) if slot_id is not None else None
            plan = slot.get("current_plan") if slot is not None else user_dao.get_active_loadout_plan_for_role(role_name)
            if slot is not None and plan is not None:
                role_name = str((plan.get("payload") or {}).get("source_role_name") or role_name)
            source_snapshot_id = plan.get("source_snapshot_id") if plan else None
            source_summary = (
                user_dao.inventory_snapshot_summary(int(source_snapshot_id)) if source_snapshot_id is not None else None
            )
            source = source_summary.get("source") if source_summary else None
    except Exception as exc:
        QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('assembly.fast.title'), plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.preview_nte_core_assemble_role.qmessagebox', exc=f'{exc}')))
        return
    if is_visual_inventory_source(source):
        if _confirm_automatic_assembly_fallback(
            self,
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.preview_nte_core_assemble_role.caption'),
        ):
            _preview_automatic_assemble_role(
                self,
                role_name,
                slot_id=slot_id,
                confirmed=confirmed,
            )
        return

    if confirmed:
        _start_nte_core_equipment_apply(
            self,
            [role_name] if slot_id is None else [],
            slot_ids=[int(slot_id)] if slot_id is not None else None,
        )
        return
    ret = QMessageBox.question(
        self,
        resolve_text_catalog(getattr(self, 'text_catalog', None)).render('assembly.fast.title'),
        plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.preview_nte_core_assemble_role.ret', role_name=f'{role_name}')),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(
            self,
            [role_name] if slot_id is None else [],
            slot_ids=[int(slot_id)] if slot_id is not None else None,
        )


def _preview_nte_core_assemble_all_roles(
    self: Any,
    *,
    role_names: list[str] | None = None,
) -> None:
    text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
    if not request_input_entry(self, "native_equipment", text("assembly.fast.title")):
        return
    if assembly_is_running(self):
        QMessageBox.information(self, text("assembly.fast.title"), text("assembly.busy"))
        return
    identity = assembly_account_identity(self)
    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(_account_database_path(self)) as user_dao:
            selection_service = LoadoutSlotSelectionService(user_dao)
            current_slots = selection_service.list_current()
            if requested_roles:
                available_roles = {selection.role_name for selection in current_slots}
                missing = [role_name for role_name in requested_roles if role_name not in available_roles]
                if missing:
                    QMessageBox.information(
                        self,
                        text("assembly.fast.title"),
                        text("assembly.missing_roles", roles="、".join(missing)),
                    )
                    return
                current_slots = tuple(
                    selection
                    for selection in current_slots
                    if selection.role_name in requested_roles
                )
            selected_slot_ids = select_assembly_slot_ids(self, current_slots, native=True)
            if not selected_slot_ids:
                return
            selections = selection_service.resolve(
                selected_slot_ids,
                defer_equipment_conflicts=True,
            )
            nte_slot_ids = []
            visual_roles = []
            for selection in selections:
                role_name = selection.role_name
                plan = selection.plan
                snapshot_id = plan.get("source_snapshot_id")
                summary = user_dao.inventory_snapshot_summary(int(snapshot_id)) if snapshot_id is not None else None
                if summary and summary.get("source") == "nte_core":
                    nte_slot_ids.append(selection.slot_id)
                elif summary and is_visual_inventory_source(summary.get("source")):
                    visual_roles.append(role_name)
    except Exception as exc:
        QMessageBox.warning(self, text("assembly.fast.title"), text("assembly.read_failed", reason=str(exc)))
        return
    if assembly_account_identity(self) != identity:
        return
    incompatible = [selection.role_name for selection in selections if selection.slot_id not in nte_slot_ids]
    if nte_slot_ids and incompatible:
        surface = assembly_surface(self)
        surface.show_result(text("assembly.fast.preflight_blocked"),
                            text("assembly.fast.mixed_sources", roles="、".join(incompatible)), warning=True)
        return
    if nte_slot_ids:
        selected_slot_ids = list(nte_slot_ids)
    elif visual_roles:
        release_assembly_surface(self)
        if _confirm_automatic_assembly_fallback(
            self,
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.preview_nte_core_assemble_all_roles.caption'),
        ):
            _preview_automatic_assemble_all_roles(
                self,
                role_names=visual_roles,
                slot_ids=[selection.slot_id for selection in selections if selection.role_name in visual_roles],
            )
        return
    else:
        QMessageBox.information(self, text("assembly.fast.title"), text("assembly.fast.no_native_plans"))
        return
    # 角色选择框的“开始极速装配”已是本次执行确认，不再叠加普通确认弹窗。
    _start_nte_core_equipment_apply(self, [], slot_ids=selected_slot_ids)


def _preview_fast_assemble_all_roles(
    self: Any,
    role_names: list[str] | None = None,
) -> None:
    """从配装页右上角选择角色并启动极速装配。"""

    _preview_nte_core_assemble_all_roles(self, role_names=role_names)


def _select_single_role_assembly_mode(
    self: Any,
    role_name: str,
) -> str | None:
    """让用户为一个角色显式选择极速或自动装配。"""

    text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
    dialog = QMessageBox(self)
    dialog.setTextFormat(Qt.PlainText)
    dialog.setTextFormat(Qt.PlainText)
    dialog.setWindowTitle(text("assembly.method.title"))
    dialog.setIcon(QMessageBox.Question)
    # QMessageBox 会根据标签内容重新收缩；同时设置标签最小宽度和初始尺寸，
    # 确保两种装配方式的说明不会挤在窄弹窗里。
    dialog.setMinimumSize(720, 400)
    dialog.setStyleSheet("QLabel#qt_msgbox_label,QLabel#qt_msgbox_informativelabel{min-width:620px;}")
    dialog.setText(text("assembly.method.summary", role=role_name))
    dialog.setInformativeText(text("assembly.method.description"))
    fast_button = dialog.addButton(text("assembly.fast.title"), QMessageBox.ActionRole)
    automatic_button = dialog.addButton(text("assembly.auto.title"), QMessageBox.ActionRole)
    dialog.addButton(text("common.cancel"), QMessageBox.RejectRole)
    dialog.resize(720, 400)
    dialog.exec()
    if dialog.clickedButton() is fast_button:
        return "fast"
    if dialog.clickedButton() is automatic_button:
        return "automatic"
    return None


def _preview_assemble_role(
    self: Any,
    role_name: str,
    *,
    slot_id: int | None = None,
) -> None:
    """为单个角色或指定配装槽位显示装配方式选择。"""

    mode = _select_single_role_assembly_mode(self, role_name)
    if mode == "fast":
        if slot_id is None:
            _preview_nte_core_assemble_role(self, role_name, confirmed=True)
        else:
            _preview_nte_core_assemble_role(
                self,
                role_name,
                slot_id=slot_id,
                confirmed=True,
            )
    elif mode == "automatic":
        if slot_id is None:
            _preview_automatic_assemble_role(self, role_name, confirmed=True)
        else:
            _preview_automatic_assemble_role(
                self,
                role_name,
                slot_id=slot_id,
                confirmed=True,
            )


def request_equipment_assembly(
    self: Any,
    *,
    role_names: list[str],
    method: str,
) -> None:
    """Public feature boundary used by calculated-result actions."""

    names = list(dict.fromkeys(str(role_name) for role_name in role_names if str(role_name).strip()))
    if not names:
        return
    if method not in {"nte_core", "gamepad"}:
        raise ValueError(f"unsupported equipment assembly method: {method}")
    if len(names) == 1:
        if method == "nte_core":
            _preview_nte_core_assemble_role(self, names[0])
        else:
            _preview_automatic_assemble_role(self, names[0])
        return
    if method == "nte_core":
        _preview_fast_assemble_all_roles(self, role_names=names)
    else:
        _preview_automatic_assemble_all_roles(
            self,
            role_names=names,
        )


class EquipmentAssemblyControllerMixin:
    """Explicit MainWindow surface for fast and UI-driven equipment assembly."""

    _equipment_assembly_is_running = _equipment_assembly_is_running
    _preview_assemble_role = _preview_assemble_role
    _preview_fast_assemble_all_roles = _preview_fast_assemble_all_roles
    _preview_automatic_assemble_all_roles = _preview_automatic_assemble_all_roles
    request_equipment_assembly = request_equipment_assembly
