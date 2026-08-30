# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from src.app.workers import WorkerThread
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    deploy_plugin,
    find_game_executables,
    npcap_installation_present,
    packaged_mod_workspace,
    packaged_plugin_dll,
    restore_plugin,
)
from src.services.dwmapi_diagnostics import (
    collect_dwmapi_diagnostics,
    format_dwmapi_diagnostics,
)
from src.services.nte_core_diagnostics import (
    capture_device_names,
    collect_nte_core_diagnostics,
    format_nte_core_diagnostics,
)
from src.services.mod_plugin_loading_service import ModPluginLoadingError
from src.ui.controllers.mod_loader_controller import (
    activate_equipment_plugin_loading_method,
    deactivate_equipment_plugin_loading_method,
    equipment_plugin_loading_method_changed,
    equipment_plugin_risk_acknowledgement_changed,
    selected_plugin_loading_method,
    start_equipment_mod_loader,
    stop_equipment_mod_loader,
)


def _new_environment_operation(
    self: Any,
    feature: str,
) -> OperationContext:
    app_context = getattr(self, "app_context", None)
    return OperationContext.create(
        feature,
        account_id=(
            app_context.account.active_account_id
            if app_context is not None
            else None
        ),
        context_generation=(
            app_context.generation if app_context is not None else None
        ),
    )


def _refresh_equipment_plugin_status(self):
    label = getattr(self, "_npcap_status_label", None)
    if label is not None:
        label.setText(
            "Npcap：已检测到" if npcap_installation_present()
            else "Npcap：未检测到（请选择官方安装程序安装）"
        )
    plugin_label = getattr(self, "_equipment_plugin_status_label", None)
    if plugin_label is None:
        return
    executable = getattr(self, "_equipment_plugin_game_executable_edit", None)
    bundle_label = getattr(self, "_equipment_plugin_bundle_label", None)
    if executable is None:
        return
    bundled_plugin = None
    loader_snapshot = None
    try:
        bundled_plugin = packaged_plugin_dll(self.app_context.paths.root)
        packaged_mod_workspace(self.app_context.paths.root)
    except EquipmentPluginDeploymentError:
        pass
    try:
        loader_snapshot = self._mod_plugin_loading_service.snapshot()
    except ModPluginLoadingError:
        pass
    if bundle_label is not None:
        if bundled_plugin is None:
            bundle_label.setText("打包插件缺失：请重新安装完整应用包")
        else:
            loader_text = "Loader 状态未知"
            if loader_snapshot is not None:
                loader_text = (
                    "Loader 已打包"
                    if loader_snapshot.phase
                    not in {"missing_loader", "unsupported"}
                    else "Loader 不可用"
                )
            bundle_label.setText(
                f"打包插件与 Mod 脚本：{bundled_plugin}；{loader_text}"
            )
    method = selected_plugin_loading_method(self)
    if loader_snapshot is not None and loader_snapshot.phase == "running":
        method = "loader"
        method_combo = getattr(
            self, "_equipment_plugin_loading_method_combo", None
        )
        if method_combo is not None and method_combo.currentData() != "loader":
            method_combo.blockSignals(True)
            method_combo.setCurrentIndex(
                max(0, method_combo.findData("loader"))
            )
            method_combo.blockSignals(False)
    primary = getattr(self, "_equipment_plugin_primary_button", None)
    stop = getattr(self, "_equipment_plugin_stop_button", None)
    if primary is not None:
        primary.setText(
            "启动 Mod Loader" if method == "loader" else "部署代理 DLL"
        )
    if stop is not None:
        stop.setText(
            "停止 Mod Loader" if method == "loader" else "还原游戏目录"
        )
    if loader_snapshot is not None and loader_snapshot.phase == "running":
        plugin_label.setText(
            "Mod Loader 监控进程正在运行；只有诊断确认装备 IPC 管道存在，"
            "才表示游戏插件已经加载。"
        )
    elif not executable.text().strip():
        plugin_label.setText("尚未选择 HTGame.exe")
    elif bundled_plugin is None:
        plugin_label.setText("应用根目录缺少打包的 dwmapi.dll，无法部署")
    elif method == "loader" and (
        loader_snapshot is None
        or loader_snapshot.phase in {"missing_loader", "unsupported"}
    ):
        loader_detail = (
            loader_snapshot.detail
            if loader_snapshot is not None
            else "无法读取 Loader 状态"
        )
        plugin_label.setText(
            "Mod Loader 当前不可用：" + loader_detail
        )
    else:
        plugin_label.setText(
            "已选择游戏目录；"
            + (
                "启动 Loader 前请先确认游戏目录没有代理 dwmapi.dll"
                if method == "loader"
                else "部署代理 DLL 前仍需确认"
            )
        )


def _select_equipment_plugin_game_executable(self):
    selected, _ = QFileDialog.getOpenFileName(
        self, "选择游戏主程序", "", "HTGame.exe (HTGame.exe)"
    )
    if selected:
        self._equipment_plugin_game_executable_edit.setText(selected)
        self._ui_preferences["equipment_plugin_game_executable"] = selected
        self._save_ui_preferences()
        self._refresh_equipment_plugin_status()


def _detect_equipment_plugin_game_executable(self):
    current_worker = getattr(self, "_equipment_plugin_detection_worker", None)
    if current_worker is not None and current_worker.isRunning():
        return
    button = getattr(self, "_equipment_plugin_detect_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("正在检测…")
    worker = WorkerThread(target=find_game_executables, parent=self)
    self._equipment_plugin_detection_worker = worker
    operation = _new_environment_operation(self, "game_detection")
    frozen_account_id = self.app_context.account.active_account_id
    frozen_generation = self.app_context.generation

    def context_is_current() -> bool:
        return (
            self.app_context.account.active_account_id == frozen_account_id
            and self.app_context.generation == frozen_generation
        )

    log_event(
        "INFO",
        "environment.game_detection_started",
        "开始自动检测游戏位置",
        operation,
    )

    def finish(candidates):
        if button is not None:
            button.setEnabled(True)
            button.setText("自动检测")
        if not context_is_current():
            log_event(
                "INFO",
                "environment.game_detection_discarded",
                "账号上下文已变化，丢弃自动检测结果",
                operation,
            )
            return
        choices = [str(path) for path in candidates]
        log_event(
            "INFO",
            "environment.game_detection_succeeded",
            "自动检测游戏位置完成",
            operation,
            candidate_count=len(choices),
        )
        if not choices:
            QMessageBox.information(
                self,
                "检测游戏位置",
                "已检查异环安装注册表和常见游戏库目录，但未找到 HTGame.exe。"
                "你可以手动填写或选择文件，定位步骤如下：\n\n"
                "1. 右键点击桌面游戏图标，选择“打开文件所在位置”。\n"
                "2. 进入 Client\\WindowsNoEditor\\HT\\Binaries\\Win64，找到 HTGame.exe。\n"
                "3. 右键点击 HTGame.exe，选择“复制文件地址”，再粘贴到游戏主程序方框。",
            )
            return
        selected = choices[0]
        if len(choices) > 1:
            selected, accepted = QInputDialog.getItem(
                self, "选择游戏位置", "检测到多个 HTGame.exe，请选择正在使用的游戏：",
                choices, 0, False,
            )
            if not accepted:
                return
        self._equipment_plugin_game_executable_edit.setText(selected)
        self._ui_preferences["equipment_plugin_game_executable"] = selected
        self._save_ui_preferences()
        self._refresh_equipment_plugin_status()
        self._equipment_plugin_status_label.setText(
            f"已自动找到并保存游戏主程序：{selected}"
        )

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("自动检测")
        if not context_is_current():
            log_event(
                "INFO",
                "environment.game_detection_discarded",
                "账号上下文已变化，丢弃自动检测错误",
                operation,
            )
            return
        log_event(
            "ERROR",
            "environment.game_detection_failed",
            "自动检测游戏位置失败",
            operation,
            error=error,
        )
        QMessageBox.warning(
            self,
            "检测游戏位置",
            f"自动检测失败：{error}\n\n"
            "你可以手动填写或选择文件：\n"
            "1. 右键点击桌面游戏图标，选择“打开文件所在位置”。\n"
            "2. 进入 Client\\WindowsNoEditor\\HT\\Binaries\\Win64，找到 HTGame.exe。\n"
            "3. 右键点击 HTGame.exe，选择“复制文件地址”，再粘贴到游戏主程序方框。",
        )

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()

def _open_npcap_download(self):
    self._open_url("https://npcap.com/dist/npcap-1.88.exe")

def _show_npcap_status(self):
    if npcap_installation_present():
        QMessageBox.information(
            self, "Npcap 状态", "已检测到 Npcap，背包同步环境已满足该项依赖。"
        )
        return
    QMessageBox.warning(
        self,
        "Npcap 状态",
        "未检测到 Npcap。背包同步无法通过本地核心组件读取游戏数据；"
        "请点击“下载 Npcap 1.88”完成安装后再检测。",
    )


def _diagnose_nte_core(self):
    current_worker = getattr(self, "_nte_core_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "nte-core 诊断", "诊断正在进行，请稍候。")
        return
    button = getattr(self, "_nte_core_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("诊断中…")
    worker = WorkerThread(
        target=lambda: collect_nte_core_diagnostics(
            cwd=self.app_context.paths.app_dir
        ),
        parent=self,
    )
    self._nte_core_diagnostic_worker = worker
    operation = _new_environment_operation(self, "nte_core_diagnostics")
    log_event(
        "INFO",
        "environment.nte_core_diagnostics_started",
        "开始诊断 nte-core",
        operation,
    )

    def finish(result):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 nte-core")
        detected = result.get("capture_detect")
        devices = capture_device_names(detected) if isinstance(detected, dict) else []
        log_event(
            "INFO",
            "environment.nte_core_diagnostics_succeeded",
            "nte-core 诊断完成",
            operation,
            capture_device_count=len(devices),
            diagnostic_section_count=len(result),
        )
        self._show_nte_core_diagnostic_report(
            format_nte_core_diagnostics(result), devices
        )

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 nte-core")
        log_event(
            "ERROR",
            "environment.nte_core_diagnostics_failed",
            "nte-core 诊断失败",
            operation,
            error=error,
        )
        QMessageBox.warning(self, "nte-core 诊断", f"诊断程序执行失败：{error}")

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_nte_core_diagnostic_report(
    self: Any,
    report: str,
    devices: list[str] | None = None,
) -> None:
    dialog = QDialog(self)
    dialog.setWindowTitle("nte-core 诊断结果")
    dialog.resize(720, 510)
    layout = QVBoxLayout(dialog)
    hint = QLabel(
        "报告仅保留抓包所需的核心、Npcap 驱动、网卡和 DLL 线索；"
        "不会启动抓包、保存原始数据或显示 IP/MAC 地址。"
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)
    content = QPlainTextEdit(dialog)
    content.setReadOnly(True)
    content.setPlainText(report)
    layout.addWidget(content, 1)
    actions = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    if devices:
        select_device_button = cast(
            QAbstractButton,
            actions.addButton("选择可用网卡", QDialogButtonBox.ActionRole),
        )

        def select_capture_device() -> None:
            selected, accepted = QInputDialog.getItem(
                dialog,
                "选择抓取网卡",
                "请选择要手动启用的网卡：",
                devices,
                0,
                False,
            )
            if not accepted:
                return
            capture_device_edit = getattr(self, "_sync_capture_device_edit", None)
            if capture_device_edit is None:
                QMessageBox.warning(
                    self,
                    "抓取网卡",
                    "未找到“抓取网卡”设置，请重新打开设置页面后重试。",
                )
                return
            capture_device_edit.setText(selected)
            QMessageBox.information(
                self,
                "抓取网卡",
                "已将所选网卡填入“抓取网卡”。请点击“保存同步设置”后重新启动同步。",
            )

        select_device_button.clicked.connect(select_capture_device)
    copy_button = cast(
        QAbstractButton,
        actions.addButton("复制诊断", QDialogButtonBox.ActionRole),
    )
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    dialog.exec()


def _diagnose_dwmapi(self):
    current_worker = getattr(self, "_dwmapi_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "dwmapi 诊断", "诊断正在进行，请稍候。")
        return
    executable_edit = getattr(self, "_equipment_plugin_game_executable_edit", None)
    executable = executable_edit.text().strip() if executable_edit is not None else ""
    button = getattr(self, "_dwmapi_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("诊断中…")
    preferences = getattr(self, "_ui_preferences", {}) or {}
    try:
        runtime_snapshot = asdict(self._mod_plugin_loading_service.snapshot())
        runtime_snapshot["loader_path"] = str(runtime_snapshot["loader_path"])
        runtime_snapshot["payload_path"] = str(runtime_snapshot["payload_path"])
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        runtime_snapshot = {
            "phase": "probe_error",
            "detail": str(exc),
        }
    worker = WorkerThread(
        target=lambda: collect_dwmapi_diagnostics(
            game_executable_path=executable,
            application_root=self.app_context.paths.root,
            recorded_deployed_sha256=str(
                preferences.get("equipment_plugin_deployed_sha256") or ""
            ),
            recorded_workspace_path=str(
                preferences.get("equipment_plugin_workspace") or ""
            ),
            loading_method=selected_plugin_loading_method(self),
            loader_snapshot=runtime_snapshot,
        ),
        parent=self,
    )
    self._dwmapi_diagnostic_worker = worker
    operation = _new_environment_operation(self, "dwmapi_diagnostics")
    log_event(
        "INFO",
        "environment.dwmapi_diagnostics_started",
        "开始诊断装备插件",
        operation,
        game_executable_configured=bool(executable),
    )

    def finish(result):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 dwmapi")
        log_event(
            "INFO",
            "environment.dwmapi_diagnostics_succeeded",
            "装备插件诊断完成",
            operation,
            diagnostic_section_count=len(result),
        )
        self._show_dwmapi_diagnostic_report(format_dwmapi_diagnostics(result))

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 dwmapi")
        log_event(
            "ERROR",
            "environment.dwmapi_diagnostics_failed",
            "装备插件诊断失败",
            operation,
            error=error,
        )
        QMessageBox.warning(self, "dwmapi 诊断", f"诊断程序执行失败：{error}")

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_dwmapi_diagnostic_report(self: Any, report: str) -> None:
    dialog = QDialog(self)
    dialog.setWindowTitle("Mods 插件加载诊断结果")
    dialog.resize(760, 540)
    layout = QVBoxLayout(dialog)
    hint = QLabel(
        "以下信息可直接复制后发送用于排查；本操作不会执行装备、启动 Loader、复制或修改 DLL。"
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)
    content = QPlainTextEdit(dialog)
    content.setReadOnly(True)
    content.setPlainText(report)
    layout.addWidget(content, 1)
    actions = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    copy_button = cast(
        QAbstractButton,
        actions.addButton("复制诊断", QDialogButtonBox.ActionRole),
    )
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    dialog.exec()

def _deploy_equipment_plugin(self):
    consent = getattr(self, "_equipment_plugin_consent", None)
    if consent is None or not consent.isChecked():
        QMessageBox.warning(
            self,
            "部署装备插件",
            "请先阅读风险提示，并勾选确认自愿使用装备插件、承担相应风险。",
        )
        return
    executable = self._equipment_plugin_game_executable_edit.text().strip()
    try:
        self._mod_plugin_loading_service.ensure_proxy_deployment_allowed()
        source = packaged_plugin_dll(self.app_context.paths.root)
        workspace_source = packaged_mod_workspace(self.app_context.paths.root)
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        QMessageBox.warning(self, "部署装备插件", str(exc))
        return
    if QMessageBox.question(
        self,
        "确认部署装备插件",
        "将把应用打包的 nte-mods-plugin dwmapi.dll 复制到所选 HTGame.exe 同目录，"
        "并准备与最新版 nte-core 配套的装备 Mod 脚本。\n"
        "若目录已有同名文件，会先备份到当前账号数据目录。请先关闭游戏。\n\n"
        "该功能会介入游戏进程，但不会直接篡改游戏数据；"
        "仍可能触发游戏保护，产生兼容问题或账号风险。\n\n"
        f"游戏：{executable}\n打包插件：{source}\n脚本模板：{workspace_source}",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_environment_operation(self, "equipment_plugin")
    log_event(
        "INFO",
        "environment.plugin_deploy_started",
        "开始部署装备插件",
        operation,
        game_executable_configured=bool(executable),
    )
    try:
        deployed = deploy_plugin(
            game_executable_path=executable,
            plugin_dll_path=source,
            application_root=self.app_context.paths.root,
            writable_workspace_path=(
                self.app_context.paths.config_dir / "mods-plugin"
            ),
            backup_directory=(
                self.app_context.account.account_data_root
                / "equipment_plugin_backups"
            ),
        )
        prior_workspace = str(
            self._ui_preferences.get("equipment_plugin_workspace") or ""
        )
        prior_hash = str(
            self._ui_preferences.get("equipment_plugin_deployed_sha256") or ""
        )
        registry_value_before = deployed.workspace_registry_value_before
        registry_value_existed = deployed.workspace_registry_value_existed
        if prior_hash and prior_workspace == str(deployed.workspace_path):
            registry_value_before = (
                self._ui_preferences.get(
                    "equipment_plugin_workspace_registry_value_before"
                )
                or None
            )
            registry_value_existed = bool(
                self._ui_preferences.get(
                    "equipment_plugin_workspace_registry_value_existed"
                )
            )
        self._ui_preferences.update({
            "equipment_plugin_game_executable": str(deployed.game_executable),
            "equipment_plugin_dll_source": str(source),
            "equipment_plugin_backup_path": str(deployed.backup_path or ""),
            "equipment_plugin_deployed_sha256": deployed.deployed_sha256,
            "equipment_plugin_workspace": str(deployed.workspace_path),
            "equipment_plugin_workspace_registry_value_before": registry_value_before or "",
            "equipment_plugin_workspace_registry_value_existed": registry_value_existed,
        })
        self._save_ui_preferences()
        log_event(
            "INFO",
            "environment.plugin_deploy_succeeded",
            "装备插件部署完成",
            operation,
            backup_created=bool(deployed.backup_path),
            registry_value_existed=bool(registry_value_existed),
        )
        self._equipment_plugin_status_label.setText("最新版 Mod 插件与装备脚本已部署；退出游戏前可在此还原。")
        QMessageBox.information(
            self,
            "部署装备插件",
            f"已部署 dwmapi.dll，并注册 Mod 工作区：\n{deployed.workspace_path}",
        )
    except EquipmentPluginDeploymentError as exc:
        log_event(
            "ERROR",
            "environment.plugin_deploy_failed",
            "装备插件部署失败",
            operation,
            error=exc,
        )
        QMessageBox.warning(self, "部署装备插件", str(exc))


def _restore_equipment_plugin(self):
    preferences = self._ui_preferences or {}
    executable = self._equipment_plugin_game_executable_edit.text().strip()
    deployed_sha256 = str(preferences.get("equipment_plugin_deployed_sha256") or "")
    if not executable or not deployed_sha256:
        QMessageBox.information(self, "还原装备插件", "当前账号没有可还原的部署记录。")
        return
    if QMessageBox.question(
        self, "还原装备插件",
        "将还原部署前备份的 dwmapi.dll；若没有备份，则只删除本程序部署的文件。\n"
        "若 Mod 工作区仍由本程序持有，也会恢复部署前的注册表值。",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_environment_operation(self, "equipment_plugin")
    log_event(
        "INFO",
        "environment.plugin_restore_started",
        "开始还原装备插件",
        operation,
        backup_configured=bool(preferences.get("equipment_plugin_backup_path")),
    )
    try:
        workspace_restored = restore_plugin(
            game_executable_path=executable,
            deployed_sha256=deployed_sha256,
            backup_path=preferences.get("equipment_plugin_backup_path"),
            mod_workspace_path=preferences.get("equipment_plugin_workspace") or None,
            workspace_registry_value_before=preferences.get(
                "equipment_plugin_workspace_registry_value_before"
            ) or None,
            workspace_registry_value_existed=bool(
                preferences.get("equipment_plugin_workspace_registry_value_existed")
            ),
        )
        self._ui_preferences.update({
            "equipment_plugin_backup_path": "",
            "equipment_plugin_deployed_sha256": "",
            "equipment_plugin_workspace": "",
            "equipment_plugin_workspace_registry_value_before": "",
            "equipment_plugin_workspace_registry_value_existed": False,
        })
        self._save_ui_preferences()
        log_event(
            "INFO",
            "environment.plugin_restore_succeeded",
            "装备插件还原完成",
            operation,
            workspace_restored=bool(workspace_restored),
        )
        self._equipment_plugin_status_label.setText("已还原游戏目录中的 dwmapi.dll。")
        QMessageBox.information(
            self,
            "还原装备插件",
            "已完成还原。"
            + (
                "并已恢复此前的 Mod 工作区。"
                if workspace_restored
                else "Mod 工作区已被其他程序接管或不存在，未修改其注册表值。"
            ),
        )
    except EquipmentPluginDeploymentError as exc:
        log_event(
            "ERROR",
            "environment.plugin_restore_failed",
            "装备插件还原失败",
            operation,
            error=exc,
        )
        QMessageBox.warning(self, "还原装备插件", str(exc))


def _focus_environment_configuration(self):
    self._go("settings")
    scroll = getattr(self, "_settings_scroll", None)
    card = getattr(self, "_environment_configuration_card", None)
    if scroll is not None and card is not None:
        QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(card.y()))


class EnvironmentControllerMixin:
    _refresh_equipment_plugin_status = _refresh_equipment_plugin_status
    _equipment_plugin_loading_method_changed = (
        equipment_plugin_loading_method_changed
    )
    _equipment_plugin_risk_acknowledgement_changed = (
        equipment_plugin_risk_acknowledgement_changed
    )
    _activate_equipment_plugin_loading_method = (
        activate_equipment_plugin_loading_method
    )
    _deactivate_equipment_plugin_loading_method = (
        deactivate_equipment_plugin_loading_method
    )
    _start_equipment_mod_loader = start_equipment_mod_loader
    _stop_equipment_mod_loader = stop_equipment_mod_loader
    _select_equipment_plugin_game_executable = _select_equipment_plugin_game_executable
    _detect_equipment_plugin_game_executable = _detect_equipment_plugin_game_executable
    _open_npcap_download = _open_npcap_download
    _show_npcap_status = _show_npcap_status
    _diagnose_nte_core = _diagnose_nte_core
    _show_nte_core_diagnostic_report = _show_nte_core_diagnostic_report
    _diagnose_dwmapi = _diagnose_dwmapi
    _show_dwmapi_diagnostic_report = _show_dwmapi_diagnostic_report
    _deploy_equipment_plugin = _deploy_equipment_plugin
    _restore_equipment_plugin = _restore_equipment_plugin
    _focus_environment_configuration = _focus_environment_configuration
