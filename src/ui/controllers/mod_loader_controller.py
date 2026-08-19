# 处理设置页中的 Mods Plugin 加载方式与 Loader 会话交互。
"""UI intent handlers for the optional NTE Mod Loader."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox

from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from src.services.mod_plugin_loading_service import ModPluginLoadingError


def selected_plugin_loading_method(window: Any) -> str:
    combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    selected = combo.currentData() if combo is not None else None
    return "loader" if selected == "loader" else "proxy"


def _save_plugin_preference(window: Any, key: str, value: Any) -> None:
    preferences = getattr(window, "_ui_preferences", None)
    if not isinstance(preferences, dict):
        return
    preferences[key] = value
    window._save_ui_preferences()


def _new_loader_operation(window: Any) -> OperationContext:
    app_context = getattr(window, "app_context", None)
    return OperationContext.create(
        "mod_loader",
        account_id=(
            app_context.account.active_account_id
            if app_context is not None
            else None
        ),
        context_generation=(
            app_context.generation if app_context is not None else None
        ),
    )


def equipment_plugin_loading_method_changed(window: Any, _index: int) -> None:
    method = selected_plugin_loading_method(window)
    try:
        snapshot = window._mod_plugin_loading_service.snapshot()
    except (EquipmentPluginDeploymentError, ModPluginLoadingError):
        snapshot = None
    if snapshot is not None and snapshot.phase == "running" and method != "loader":
        combo = window._equipment_plugin_loading_method_combo
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, combo.findData("loader")))
        combo.blockSignals(False)
        QMessageBox.warning(
            window,
            "切换加载方式",
            "Mod Loader 正在运行。请先点击“停止 Mod Loader”，再切换到代理 DLL。",
        )
        return
    if method != "loader":
        try:
            window._mod_plugin_loading_service.stop_loader()
        except ModPluginLoadingError as exc:
            combo = window._equipment_plugin_loading_method_combo
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData("loader")))
            combo.blockSignals(False)
            QMessageBox.warning(
                window,
                "切换加载方式",
                "无法完成 Loader 会话清理，已保留 Loader 方式：" + str(exc),
            )
            return
    _save_plugin_preference(window, "equipment_plugin_loading_method", method)
    window._refresh_equipment_plugin_status()


def equipment_plugin_risk_acknowledgement_changed(
    window: Any,
    checked: bool,
) -> None:
    _save_plugin_preference(
        window,
        "equipment_plugin_risk_acknowledged",
        bool(checked),
    )


def activate_equipment_plugin_loading_method(window: Any) -> None:
    if selected_plugin_loading_method(window) == "loader":
        window._start_equipment_mod_loader()
    else:
        window._deploy_equipment_plugin()


def deactivate_equipment_plugin_loading_method(window: Any) -> None:
    if selected_plugin_loading_method(window) == "loader":
        window._stop_equipment_mod_loader()
    else:
        window._restore_equipment_plugin()


def start_equipment_mod_loader(window: Any) -> None:
    consent = getattr(window, "_equipment_plugin_consent", None)
    if consent is None or not consent.isChecked():
        QMessageBox.warning(
            window,
            "启动 Mod Loader",
            "请先阅读风险提示，并勾选确认自愿使用装备插件、承担相应风险。",
        )
        return
    executable = window._equipment_plugin_game_executable_edit.text().strip()
    if QMessageBox.question(
        window,
        "确认启动备用 Mod Loader",
        "备用 Loader 会请求管理员权限，监控官方启动器，并在 HTGame.exe 创建时加载"
        "打包的 dwmapi.dll。它不会把 DLL 写入游戏目录。\n\n"
        "请先通过代理方式的“还原游戏目录”移除本程序部署的 dwmapi.dll；"
        "两个加载方式不能同时启用。停止 Loader 时，本次会话中已注入的官方启动器"
        "可能被同步结束。\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_loader_operation(window)
    log_event(
        "INFO",
        "environment.mod_loader_start_started",
        "开始启动备用 Mod Loader",
        operation,
        game_executable_configured=bool(executable),
    )
    try:
        result = window._mod_plugin_loading_service.start_loader(
            game_executable_path=executable,
            writable_workspace_path=(
                window.app_context.paths.config_dir / "mods-plugin"
            ),
        )
        window._ui_preferences.update({
            "equipment_plugin_game_executable": executable,
            "equipment_plugin_loading_method": "loader",
            "equipment_plugin_risk_acknowledged": True,
            "equipment_plugin_workspace": str(result.workspace_path),
        })
        window._save_ui_preferences()
        log_event(
            "INFO",
            "environment.mod_loader_start_succeeded",
            "备用 Mod Loader 已启动",
            operation,
            loader_process_started=bool(result.runtime.process_id),
        )
        window._refresh_equipment_plugin_status()
        QMessageBox.information(
            window,
            "Mod Loader 已启动",
            "Loader 正在等待官方启动器。请正常启动游戏，然后使用“诊断 dwmapi”"
            "确认 nte-mods-plugin-v7 管道已经出现。",
        )
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        log_event(
            "ERROR",
            "environment.mod_loader_start_failed",
            "备用 Mod Loader 启动失败",
            operation,
            error=exc,
        )
        QMessageBox.warning(window, "启动 Mod Loader", str(exc))


def stop_equipment_mod_loader(window: Any) -> None:
    if QMessageBox.question(
        window,
        "停止 Mod Loader",
        "停止 Loader 会结束本次 Loader 会话；上游 Loader 还可能同步结束本次会话中"
        "已注入的官方启动器。请先退出游戏。\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_loader_operation(window)
    log_event(
        "INFO",
        "environment.mod_loader_stop_started",
        "开始停止备用 Mod Loader",
        operation,
    )
    try:
        stopped = window._mod_plugin_loading_service.stop_loader()
        log_event(
            "INFO",
            "environment.mod_loader_stop_succeeded",
            "备用 Mod Loader 已停止",
            operation,
            loader_was_running=bool(stopped),
        )
        window._refresh_equipment_plugin_status()
        QMessageBox.information(
            window,
            "停止 Mod Loader",
            "Mod Loader 已停止。" if stopped else "本次应用会话没有正在运行的 Mod Loader。",
        )
    except ModPluginLoadingError as exc:
        log_event(
            "ERROR",
            "environment.mod_loader_stop_failed",
            "备用 Mod Loader 停止失败",
            operation,
            error=exc,
        )
        QMessageBox.warning(window, "停止 Mod Loader", str(exc))
