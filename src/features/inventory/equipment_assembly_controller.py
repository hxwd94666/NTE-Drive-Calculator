# 编排极速装配及其与游戏界面自动装配之间的入口路由。
"""Fast equipment apply controller and assembly-mode routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from src.app.workers import WorkerThread
from src.observability.context import OperationContext
from src.integrations.nte_core import is_mods_plugin_unavailable_error
from src.services.dwmapi_diagnostics import probe_equipment_pipe
from src.features.inventory.equipment_assembly_dialogs import (
    assembly_report_dialog as _assembly_report_dialog,
)
from src.services.equipment_apply_service import EquipmentApplyService
from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService
from src.storage.sqlite.user_data_dao import UserDataDao
from .equipment_automatic_assembly_controller import (
    _account_database_path,
    _preview_automatic_assemble_all_roles,
    _preview_automatic_assemble_role,
    _return_to_equipment_after_assembly,
)


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
    error: object,
    *,
    pipe_probe: dict[str, Any] | None = None,
) -> str:
    """Render one concrete failure category without conflating pipe states."""

    message = str(error or "未知错误")
    if failure_kind == "plugin_unavailable":
        probe = pipe_probe if pipe_probe is not None else probe_equipment_pipe()
        state = str(probe.get("state") or "error")
        if state == "missing":
            return (
                "当前探测确认装备插件命名管道不存在。"
                "通常表示 DLL/脚本未完成加载、Viewport Tick 未运行，或 IPC 版本不匹配。"
            )
        if state == "busy":
            return "当前探测确认命名管道存在，但连接实例仍被占用。"
        if state == "available":
            return (
                "当前探测确认命名管道存在；此前请求更可能是管道短暂不可用或等待响应超时，"
                "不是持续性的管道缺失。"
            )
        if state == "access_denied":
            return "当前探测确认命名管道访问被拒绝，请检查程序与游戏的权限级别。"
        return f"装备插件通道不可用，当前管道探测结果：{probe.get('message') or message}"
    if failure_kind == "plugin_busy":
        return "装备插件队列在 6 次串行退避后仍繁忙，本次请求未进入执行队列。"
    if failure_kind == "core_request_timeout":
        return "nte-core 的请求响应等待超时；这不是命名管道缺失的检测结果。"
    if failure_kind == "request_rejected":
        return f"装备插件已收到请求但拒绝执行：{message}"
    if failure_kind == "snapshot_timeout":
        return "装配请求已经下发，但没有在等待时间内取得新的稳定背包快照。"
    if failure_kind == "snapshot_error":
        return f"装配请求已经下发，但背包同步复核失败：{message}"
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
    self: Any,
    role_names: list[str],
    *,
    identity_overrides: dict[str, dict[str, Any]] | None = None,
    job_id: int | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None:
        raise RuntimeError("背包同步服务尚未启动，请先在首页启动后台同步")
    app_context = getattr(self, "app_context", None)
    database_path = (
        app_context.account.user_database_path if app_context is not None else getattr(self, "user_database_path", None)
    )
    if database_path is None:
        raise RuntimeError("极速装配缺少当前账号数据库依赖")
    return BulkEquipmentApplyService(
        database_path,
        sync_service,
        dao_factory=UserDataDao,
        apply_service_factory=EquipmentApplyService,
        operation_context=OperationContext.create(
            "equipment_apply",
            account_id=(
                app_context.account.active_account_id
                if app_context is not None
                else None
            ),
            context_generation=(app_context.generation if app_context is not None else None),
            job_id=job_id,
        ),
    ).run(
        role_names,
        identity_overrides=identity_overrides,
        job_id=job_id,
        progress_callback=progress_callback,
    )


def _is_missing_character_instance_request(request: dict) -> bool:
    reason = str(request.get("reason") or "")
    return "角色实例缓存均未包含" in reason or "当前稳定背包快照未包含" in reason


def _show_fast_apply_identity_gaps(
    self: Any,
    requests: list[dict[str, Any]],
    applied: list[dict[str, Any]],
) -> None:
    """Report unresolved UIDs only after every independently runnable role ran."""
    missing_instances = [request for request in requests if _is_missing_character_instance_request(request)]
    ambiguous_instances = [request for request in requests if request not in missing_instances]
    lines = []
    if applied:
        lines.append(f"已先完成 {len(applied)} 个可获取角色实例的极速装配。")
    if missing_instances:
        lines.append("以下角色尚未获取到可用的角色实例 UID，未执行极速装配：")
        lines.extend(f"• {request['role_name']}" for request in missing_instances)
    if ambiguous_instances:
        lines.append("以下角色存在无法安全确定的角色实例 UID，未执行极速装配：")
        lines.extend(f"• {request['role_name']}" for request in ambiguous_instances)
    lines.extend(
        (
            "",
            "请保持游戏在线后重新启动背包同步，等待新的稳定快照，再重试未完成角色。",
            "若多次同步仍无法获取这些角色的实例 UID，建议改用自动装配；也可以在游戏内手动完成这些角色的配装。",
        )
    )
    QMessageBox.warning(self, "部分角色未极速装配", "\n".join(lines))


def _start_nte_core_equipment_apply(
    self: Any,
    role_names: list[str],
    *,
    identity_overrides: dict[str, dict[str, Any]] | None = None,
    job_id: int | None = None,
) -> None:
    current_worker = getattr(self, "_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "正在装配", "已有装配任务正在执行，请等待指令下发完成。")
        return

    progress_state: dict[str, Any] = {
        "current": 0,
        "total": max(1, len(role_names)),
        "message": "正在准备极速装配…",
    }
    progress_dialog = QProgressDialog(
        progress_state["message"],
        "",
        0,
        progress_state["total"],
        self,
    )
    progress_dialog.setWindowTitle("极速装配进度")
    progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
    progress_dialog.setCancelButton(None)
    progress_dialog.setAutoClose(False)
    progress_dialog.setAutoReset(False)
    progress_dialog.setMinimumDuration(0)
    progress_dialog.setValue(0)
    progress_dialog.show()

    progress_timer = QTimer(progress_dialog)

    def update_progress_dialog() -> None:
        total = max(1, int(progress_state.get("total", 1)))
        progress_dialog.setMaximum(total)
        progress_dialog.setValue(min(total, max(0, int(progress_state.get("current", 0)))))
        progress_dialog.setLabelText(str(progress_state.get("message") or "正在极速装配…"))

    progress_timer.timeout.connect(update_progress_dialog)
    progress_timer.start(80)

    def update_progress(payload: dict) -> None:
        progress_state.update(payload)

    def close_progress_dialog() -> None:
        progress_timer.stop()
        progress_dialog.close()
        progress_dialog.deleteLater()

    worker = WorkerThread(
        target=lambda: _run_nte_core_equipment_apply(
            self,
            role_names,
            identity_overrides=identity_overrides,
            job_id=job_id,
            progress_callback=update_progress,
        ),
        parent=self,
    )
    self._equipment_apply_worker = worker

    def on_result(report: dict) -> None:
        close_progress_dialog()
        preflight_errors = report.get("preflight_errors") or []
        if preflight_errors:
            details = "\n".join(
                f"• [{row.get('role_name', '未知角色')}]：{row.get('error', '方案不可用')}" for row in preflight_errors
            )
            QMessageBox.warning(
                self,
                "极速装配未开始",
                "没有向游戏发送任何装配指令。以下已保存方案不能用于极速装配：\n\n"
                f"{details}\n\n请重新计算并保存完整方案后再试。",
            )
            return
        applied = report.get("applied") or []
        requests = report.get("identity_requests") or []
        details = "\n".join(
            f"• {row['role_name']}"
            + (
                f"：{row['module_count']} 个驱动" + (" + 1 个核心" if row.get("core_count") else "")
                if row.get("module_count") is not None
                else "：已下发"
            )
            + ("（原本已装好）" if row.get("already_applied") else "")
            + (
                f"（第 {row.get('attempt_count', 2)} 次装配后复查通过）"
                if row.get("repair_verified")
                else (
                    f"（已执行 {row.get('attempt_count', 2)} 次装配）"
                    if row.get("repaired") else ""
                )
            )
            for row in applied
        )
        changed_count = sum(not row.get("already_applied") for row in applied)
        unchanged_count = len(applied) - changed_count
        unverified_count = sum(not row.get("verified", False) and not row.get("already_applied") for row in applied)
        summary = f"已下发 {len(applied)} 个角色的配装" if unverified_count else f"已确认 {len(applied)} 个角色的配装"
        repaired_count = sum(bool(row.get("repaired")) for row in applied)
        if repaired_count:
            summary += f"（复核后重试 {repaired_count} 个）"
        if unchanged_count:
            summary += f"（实际装配 {changed_count} 个，原本已装好 {unchanged_count} 个）"
        if report.get("failed_role"):
            error_message = str(report.get("error") or "未知错误")
            failure_kind = str(report.get("failure_kind") or "apply_error")
            if (
                failure_kind == "plugin_unavailable"
                or _is_equipment_plugin_unavailable_error(error_message)
            ):
                reason = _equipment_failure_details(
                    "plugin_unavailable",
                    error_message,
                )
                QMessageBox.warning(
                    self,
                    "装备插件不可用",
                    f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n"
                    f"{reason}\n\n"
                    "请先确认：\n"
                    "1. 已在“设置 → 环境配置”重新部署与当前 nte-core 匹配的 "
                    "nte-mods-plugin 和 equipment.nte；\n"
                    "2. 游戏保持登录，随后从首页重新启动背包同步并等待“后台监听”；\n"
                    "3. 完成上述检查后，再点击右上角“极速装配”重新执行。\n\n"
                    f"此前已确认 {len(applied)} 个角色；任务日志已保存。此次不会立即重试。",
                )
                return
            reason = _equipment_failure_details(failure_kind, error_message)
            retry = QMessageBox.question(
                self,
                "装配暂停",
                f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n{reason}\n\n"
                f"此前已确认 {len(applied)} 个角色；任务日志已保存。是否重试失败角色并继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if retry == QMessageBox.Yes:
                _start_nte_core_equipment_apply(self, [], job_id=report["job_id"])
            return
        if requests:
            _show_fast_apply_identity_gaps(self, requests, applied)
            refresh = getattr(self, "_refresh_equip", None)
            if callable(refresh):
                refresh()
            return
        repair_errors = report.get("repair_errors") or []
        if repair_errors:
            error_details = "\n".join(
                f"• [{row.get('role_name', '未知角色')}]："
                + _equipment_failure_details(
                    str(row.get("kind") or "apply_error"),
                    row.get("error", "装配失败"),
                )
                for row in repair_errors
            )
            QMessageBox.warning(
                self,
                "装配后复核未通过",
                f"{summary}，但以下角色在最多 3 次装配后仍未通过复核："
                f"\n\n{error_details}\n\n请保持游戏在线后单独重试这些角色。",
            )
            return
        snapshot_failure = report.get("snapshot_wait_failure")
        if isinstance(snapshot_failure, dict):
            attempt = int(snapshot_failure.get("attempt") or 1)
            reason = _equipment_failure_details(
                str(snapshot_failure.get("kind") or "snapshot_error"),
                snapshot_failure.get("error"),
            )
            QMessageBox.warning(
                self,
                "装配复核未完成",
                f"{summary}。\n\n第 {attempt} 次装配后的复核未完成：{reason}\n\n"
                "由于没有取得可靠的新快照，本次没有继续发送后续装配请求。",
            )
            return
        if report.get("postrepair_snapshot_id"):
            verification_note = (
                "\n\n已在每次装配后等待稳定背包快照，并完成最终复核；单个角色最多发送 3 次装配请求。"
            )
        elif report.get("postrepair_check_timed_out"):
            verification_note = (
                "\n\n末次快照发现的遗漏已自动补装，但本次未等到第二份稳定快照，因此无法确认补装后的最终状态。"
            )
        elif report.get("postcheck_snapshot_id"):
            verification_note = "\n\n已使用装配后稳定背包快照检查全部角色，未发现遗漏。"
        else:
            verification_note = (
                "\n\n本次未等到新的稳定背包快照，已完成装配前校验并下发指令。"
                "请在下次登录后完成背包同步，以更新仓库显示。"
                if unverified_count
                else ""
            )
        QMessageBox.information(
            self, "装配完成", f"{summary}。\n任务 #{report.get('job_id')} 已保存日志。\n\n{details}{verification_note}"
        )
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        close_progress_dialog()
        QMessageBox.critical(
            self,
            "装配失败",
            f"本地组件未能完成装配：\n{message}\n\n请确认游戏已登录、插件已加载，且首页背包同步处于“后台监听”。",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _confirm_automatic_assembly_fallback(
    self: Any,
    detail: str,
) -> bool:
    """Ask before falling back from native-UID fast assembly to UI automation."""

    result = QMessageBox.question(
        self,
        "切换自动装配",
        f"{detail}\n\n是否改用逐步自动装配？",
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    return result == QMessageBox.Yes


def _preview_nte_core_assemble_role(
    self: Any,
    role_name: str,
    *,
    confirmed: bool = False,
) -> None:
    """确认后通过装备插件极速装配一个已保存角色方案。"""

    try:
        with UserDataDao(_account_database_path(self)) as user_dao:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
            source_snapshot_id = plan.get("source_snapshot_id") if plan else None
            source_summary = (
                user_dao.inventory_snapshot_summary(int(source_snapshot_id)) if source_snapshot_id is not None else None
            )
            source = source_summary.get("source") if source_summary else None
    except Exception as exc:
        QMessageBox.warning(self, "极速装配", f"无法读取已保存方案：{exc}")
        return
    if source == "gamepad":
        if _confirm_automatic_assembly_fallback(
            self,
            "当前已保存方案来自视觉扫描快照，装备 UID 是视觉扫描生成的临时标识；"
            "极速装配只能写入抓包同步（nte_core）提供的游戏原生 UID。\n\n"
            "为避免写入错误装备，可以改用逐步自动装配。若要使用极速装配，请完成一次背包同步，"
            "再重新计算并保存该角色的方案。",
        ):
            _preview_automatic_assemble_role(self, role_name, confirmed=confirmed)
        return

    if confirmed:
        _start_nte_core_equipment_apply(self, [role_name])
        return
    ret = QMessageBox.question(
        self,
        "极速装配",
        f"将通过游戏内装备插件把 [{role_name}] 的已保存方案直接装入游戏。\n\n"
        "若当前已经是目标配装会立即完成，否则发送指令并等待稳定背包快照确认；"
        "不需要切换到游戏配装页面。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, [role_name])


def _preview_nte_core_assemble_all_roles(
    self: Any,
    *,
    confirmed: bool = False,
    role_names: list[str] | None = None,
) -> None:
    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(_account_database_path(self)) as user_dao:
            plans_by_role = user_dao.list_active_loadout_plans_by_role()
            if requested_roles:
                missing = [name for name in requested_roles if name not in plans_by_role]
                if missing:
                    QMessageBox.information(
                        self,
                        "极速装配",
                        f"以下角色尚未保存当前方案：{'、'.join(missing)}",
                    )
                    return
                plans_by_role = {name: plans_by_role[name] for name in requested_roles}
            nte_roles = []
            visual_roles = []
            for role_name, plan in plans_by_role.items():
                snapshot_id = plan.get("source_snapshot_id")
                summary = user_dao.inventory_snapshot_summary(int(snapshot_id)) if snapshot_id is not None else None
                if summary and summary.get("source") == "nte_core":
                    nte_roles.append(role_name)
                elif summary and summary.get("source") == "gamepad":
                    visual_roles.append(role_name)
    except Exception as exc:
        QMessageBox.warning(self, "极速装配", f"无法读取官方 SQLite 方案：{exc}")
        return
    if nte_roles:
        role_names = list(nte_roles) if requested_roles else sorted(nte_roles)
    elif visual_roles:
        if _confirm_automatic_assembly_fallback(
            self,
            "当前已保存方案来自视觉扫描快照，装备 UID 是视觉扫描生成的临时标识；"
            "极速装配只能写入抓包同步（nte_core）提供的游戏原生 UID。\n\n"
            "为避免写入错误装备，可以改用逐步自动装配。若要使用极速装配，请完成一次背包同步，"
            "再重新计算并保存方案。",
        ):
            _preview_automatic_assemble_all_roles(
                self,
                role_names=list(requested_roles) if requested_roles else None,
            )
        return
    else:
        QMessageBox.information(self, "极速装配", "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。")
        return
    if confirmed:
        _start_nte_core_equipment_apply(self, role_names)
        return
    ret = QMessageBox.question(
        self,
        "极速装配",
        f"将依次向本地组件发送 {len(role_names)} 个角色的装配指令，"
        "已经正确装配的角色会直接跳过，其余角色在稳定背包快照确认后再处理下一个。"
        "\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, role_names)


def _preview_fast_assemble_all_roles(
    self: Any,
    role_names: list[str] | None = None,
) -> None:
    """从配装页右上角启动全部角色的极速装配。"""

    _preview_nte_core_assemble_all_roles(self, role_names=role_names)


def _select_single_role_assembly_mode(
    self: Any,
    role_name: str,
) -> str | None:
    """让用户为一个角色显式选择极速或自动装配。"""

    dialog = QMessageBox(self)
    dialog.setWindowTitle("选择装配方式")
    dialog.setIcon(QMessageBox.Question)
    # QMessageBox 会根据标签内容重新收缩；同时设置标签最小宽度和初始尺寸，
    # 确保两种装配方式的说明不会挤在窄弹窗里。
    dialog.setMinimumSize(720, 400)
    dialog.setStyleSheet("QLabel#qt_msgbox_label,QLabel#qt_msgbox_informativelabel{min-width:620px;}")
    dialog.setText(f"为 [{role_name}] 选择装配方式")
    dialog.setInformativeText(
        "极速装配：通过游戏内装备插件直接写入方案，速度快，无需打开配装页。\n\n"
        "自动装配：模拟游戏内操作逐步完成，无需装备插件，但需停在角色详情页且耗时更长。"
    )
    fast_button = dialog.addButton("极速装配", QMessageBox.ActionRole)
    automatic_button = dialog.addButton("自动装配", QMessageBox.ActionRole)
    dialog.addButton(QMessageBox.Cancel)
    dialog.resize(720, 400)
    dialog.exec()
    if dialog.clickedButton() is fast_button:
        return "fast"
    if dialog.clickedButton() is automatic_button:
        return "automatic"
    return None


def _preview_assemble_role(self: Any, role_name: str) -> None:
    """为单个角色显示装配方式选择。"""

    mode = _select_single_role_assembly_mode(self, role_name)
    if mode == "fast":
        _preview_nte_core_assemble_role(self, role_name, confirmed=True)
    elif mode == "automatic":
        _preview_automatic_assemble_role(self, role_name, confirmed=True)


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
