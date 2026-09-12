# 处理设置页中的 Mods Plugin 加载意图，记录只属于本机工作模式。
"""UI intent handlers for managed game component loading."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox

from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError, PluginDeploymentPendingCleanup,
)
from src.services.mod_plugin_loading_service import ModPluginLoadingError


def selected_plugin_loading_method(window: Any) -> str:
    combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    selected = combo.currentData() if combo is not None else None
    if selected == "native-capture":
        return selected
    if selected not in {"loader", "proxy"}:
        selected = window.work_mode_service.deployment_record.get("loading_method")
    return "loader" if selected == "loader" else "proxy"


def _new_loader_operation(_window: Any) -> OperationContext:
    return OperationContext.create("mod_loader")


def _persist_loader_registration(window: Any, *, executable: str, pending: bool) -> None:
    service = window._mod_plugin_loading_service
    workspace = service.pending_workspace_cleanup_path
    record = window.work_mode_service.deployment_record
    record.update({"loading_method": "loader", "game_executable": executable})
    if workspace is not None:
        record["workspace_path"] = str(workspace)
    if service.active_payload_sha256:
        record["loader_payload_sha256"] = service.active_payload_sha256
    window.work_mode_service.update_deployment(record)
    window.work_mode_service.set_game_executable(executable)
    if pending:
        window.work_mode_service.set_cleanup_pending(True)
    window.work_mode_runtime.invalidate()


def equipment_plugin_loading_method_changed(window: Any, _index: int) -> None:
    method = selected_plugin_loading_method(window)
    try:
        snapshot = window._mod_plugin_loading_service.snapshot()
        if snapshot.phase == "running" and method != "loader":
            combo = window._equipment_plugin_loading_method_combo
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData("loader")))
            combo.blockSignals(False)
            QMessageBox.warning(window, "切换加载方式", "Loader 正在等待加载，请先清理当前加载入口后再切换。")
            return
        record = window.work_mode_service.deployment_record
        record["loading_method"] = method
        window.work_mode_service.update_deployment(record)
        window.work_mode_runtime.invalidate()
        window._refresh_equipment_plugin_status()
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as error:
        QMessageBox.warning(window, "切换加载方式", str(error))


def activate_equipment_plugin_loading_method(window: Any) -> None:
    if selected_plugin_loading_method(window) == "loader":
        window._start_equipment_mod_loader()
    else:
        window._deploy_equipment_plugin()


def deactivate_equipment_plugin_loading_method(window: Any) -> None:
    window.work_mode_controller.cleanup()


def start_equipment_mod_loader(window: Any) -> None:
    if not window.operation_entry("native_load", "启动 Mod Loader"):
        return
    try:
        window.work_mode_service.require("native_load")
    except PermissionError as error:
        QMessageBox.warning(window, "启动 Mod Loader", str(error))
        return
    executable = window.work_mode_service.settings.game_executable
    if not executable.strip():
        window.operation_unavailable("启动 Mod Loader", "尚未选择游戏主程序 HTGame.exe。", target="deployment")
        return
    from src.integrations.game_component_bundle import inspect_game_component_bundle
    if inspect_game_component_bundle(window.app_context.paths.root).layout == "native-capture-v1":
        from src.ui.controllers.native_plugin_deployment_ui import start_native_loader_from_settings
        start_native_loader_from_settings(window)
        return
    try:
        snapshot = window._mod_plugin_loading_service.snapshot()
        if snapshot.phase not in {"stopped", "running"}:
            window.operation_unavailable("启动 Mod Loader", snapshot.detail or "Loader 当前不可用，请检测随附组件。", target="deployment")
            return
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as error:
        window.operation_unavailable("启动 Mod Loader", str(error), target="deployment")
        return
    if QMessageBox.question(
        window, "确认启动 Mod Loader",
        "Loader 将等待官方启动器创建游戏进程，并加载当前 Calc 随附的代理组件。\n\n"
        "启动前仅移除哈希匹配的已知代理 DLL；来源未知或已修改的文件会保留并报告冲突。"
        "请先退出游戏，再启动 Loader。\n\n"
        "停止后续加载不代表游戏中的 DLL 已卸载；清理需等待游戏退出，且不会恢复旧 DLL 或历史加载配置。是否继续？",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_loader_operation(window)
    log_event("INFO", "environment.mod_loader_start_started", "开始启动 Mod Loader", operation,
              game_executable_configured=bool(executable))
    try:
        record = window.work_mode_service.deployment_record
        result = window._mod_plugin_loading_service.start_loader(
            game_executable_path=executable,
            writable_workspace_path=window.app_context.paths.config_dir / "mods-plugin",
            proxy_backup_directory=window.app_context.paths.config_dir / "component-backups",
            recorded_proxy_sha256=str(record.get("deployed_sha256") or ""),
            recorded_proxy_workspace_path=record.get("workspace_path") or None,
        )
        _persist_loader_registration(window, executable=executable, pending=False)
        record = window.work_mode_service.deployment_record
        record["workspace_path"] = str(result.workspace_path)
        if result.removed_proxy is not None:
            record["deployed_sha256"] = result.removed_proxy.sha256
        window.work_mode_service.update_deployment(record)
        if not window.work_mode_service.allowed("native_load"):
            window.work_mode_service.set_cleanup_pending(True)
        log_event("INFO", "environment.mod_loader_start_succeeded", "Mod Loader 监控已启动", operation,
                  loader_process_started=bool(result.runtime.process_id), local_proxy_removed=bool(result.removed_proxy))
        window._refresh_equipment_plugin_status()
        QMessageBox.information(
            window, "Mod Loader 监控已启动",
            "Loader 已开始等待游戏。请正常启动游戏并使用工作模式“重新检测”确认管道、握手与业务快照；监控运行本身不代表组件已加载。",
        )
    except PluginDeploymentPendingCleanup as error:
        window.work_mode_runtime.save_pending_deployment(error)
        QMessageBox.warning(window, "启动 Mod Loader", str(error))
    except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as error:
        if window._mod_plugin_loading_service.pending_workspace_cleanup_path is not None:
            _persist_loader_registration(window, executable=executable, pending=True)
        log_event("ERROR", "environment.mod_loader_start_failed", "Mod Loader 启动失败", operation, error=error)
        if window.work_mode_service.allowed("native_load"):
            window.operation_unavailable("启动 Mod Loader", str(error), target="deployment")


def stop_equipment_mod_loader(window: Any) -> None:
    """All stop/cleanup requests share the mode owner's pending lifecycle."""
    window.work_mode_controller.cleanup()
