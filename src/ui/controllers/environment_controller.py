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
from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.ui.controllers.native_plugin_deployment_ui import refresh_native_plugin_status, deploy_native_plugin_from_settings
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    PluginDeploymentPendingCleanup,
    deploy_plugin,
    find_game_executables,
    game_process_running,
    npcap_installation_present,
    packaged_mod_workspace,
    packaged_plugin_dll,
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



def _environment_result_acceptor(self, operation: OperationContext, capability: str):
    revision = self.work_mode_service.operation_revision
    delivered = False

    def accept() -> bool:
        nonlocal delivered
        if delivered:
            return False
        delivered = True
        context = self.app_context
        return (
            context.account.active_account_id == operation.account_id
            and context.generation == operation.context_generation
            and self.work_mode_service.operation_revision == revision
            and self.work_mode_service.allowed(capability)
        )

    return accept


def _refresh_equipment_plugin_status(self):
    for name, worker_name in (
        ("_equipment_plugin_primary_button", None),
        ("_nte_core_diagnostic_button", "_nte_core_diagnostic_worker"),
        ("_dwmapi_diagnostic_button", "_dwmapi_diagnostic_worker"),
    ):
        button = getattr(self, name, None)
        worker = getattr(self, worker_name, None) if worker_name else None
        if button is not None:
            button.setEnabled(worker is None or not worker.isRunning())
    stop_button = getattr(self, "_equipment_plugin_stop_button", None)
    if stop_button is not None:
        stop_button.setEnabled(True)
        stop_button.setText("清理游戏目录")
    label = getattr(self, "_npcap_status_label", None)
    if label is not None:
        label.setText(
            "Npcap：已检测到" if npcap_installation_present()
            else "Npcap：未检测到（请选择官方安装程序安装）"
        )
    if refresh_native_plugin_status(self):
        return
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
        stop.setText("清理游戏目录")
    if loader_snapshot is not None and loader_snapshot.phase == "running":
        plugin_label.setText(
            "Mod Loader 正在等待游戏；请重新检测管道、握手与业务快照，监控运行不代表组件已就绪。"
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
        self.work_mode_service.set_game_executable(selected)
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
        self.work_mode_service.set_game_executable(selected)
        self._refresh_equipment_plugin_status()

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
    if not _require_environment_diagnostics(self, packet=True, label="下载 Npcap"):
        return
    self._open_url("https://npcap.com/dist/npcap-1.88.exe")

def _show_npcap_status(self):
    if not _require_environment_diagnostics(self, packet=True, label="检测 Npcap"):
        return
    if npcap_installation_present():
        QMessageBox.information(
            self, "Npcap 状态", "已检测到 Npcap，背包同步环境已满足该项依赖。"
        )
        return
    self.operation_unavailable(
        "检测 Npcap", "未检测到 Npcap，抓包同步缺少此项依赖；DLL 同步独立检测。"
        "请在设置中下载官方 Npcap 安装程序，安装后再检测。", target="detection",
    )


def _require_environment_diagnostics(self, *, packet: bool, label: str) -> bool:
    capability = "diagnostics" if not packet or self.work_mode_service.allowed("diagnostics") else "packet_capture"
    if not self.operation_entry(capability, label):
        return False
    try:
        self.work_mode_service.require(capability)
    except PermissionError as error:
        QMessageBox.warning(self, "环境检测", str(error))
        return False
    return True


def _diagnose_nte_core(self):
    if not _require_environment_diagnostics(self, packet=True, label="诊断 nte-core"):
        return
    current_worker = getattr(self, "_nte_core_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "nte-core 诊断", "诊断正在进行，请稍候。")
        return
    button = getattr(self, "_nte_core_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("诊断中…")
    capability = "diagnostics" if self.work_mode_service.allowed("diagnostics") else "packet_capture"

    def collect():
        self.work_mode_service.require(capability)
        return collect_nte_core_diagnostics(cwd=self.app_context.paths.app_dir)

    worker = WorkerThread(target=collect, parent=self)
    self._nte_core_diagnostic_worker = worker
    operation = _new_environment_operation(self, "nte_core_diagnostics")
    accept_result = _environment_result_acceptor(self, operation, capability)
    log_event(
        "INFO",
        "environment.nte_core_diagnostics_started",
        "开始诊断 nte-core",
        operation,
    )

    def finish(result):
        if self._nte_core_diagnostic_worker is not worker:
            return
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 nte-core")
        if not accept_result():
            return
        if result.get("error"):
            self.operation_unavailable("诊断 nte-core", str(result["error"]), target="detection")
            return
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
        if not (self.work_mode_service.allowed("packet_capture") or self.work_mode_service.allowed("diagnostics")):
            return
        self._show_nte_core_diagnostic_report(
            format_nte_core_diagnostics(result),
            devices,
            allow_manual_device_selection=bool(
                devices
                and isinstance(detected, dict)
                and detected.get("recommended_device") is None
            ),
        )

    def failed(error):
        if self._nte_core_diagnostic_worker is not worker:
            return
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 nte-core")
        if not accept_result():
            return
        log_event(
            "ERROR",
            "environment.nte_core_diagnostics_failed",
            "nte-core 诊断失败",
            operation,
            error=error,
        )
        self.operation_unavailable("诊断 nte-core", str(error), target="detection")

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_nte_core_diagnostic_report(
    self: Any,
    report: str,
    devices: list[str] | None = None,
    *,
    allow_manual_device_selection: bool = False,
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
    if devices and allow_manual_device_selection:
        select_device_button = cast(
            QAbstractButton,
            actions.addButton("高级排障…", QDialogButtonBox.ActionRole),
        )

        def select_capture_device() -> None:
            if not _require_environment_diagnostics(self, packet=True, label="手动指定抓取网卡"):
                return
            proceed = QMessageBox.question(
                dialog,
                "高级排障",
                "手动指定网卡会覆盖自动选择，并可能导致同步失败。"
                "仅在自动选择反复失败时继续。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                return
            selected, accepted = QInputDialog.getItem(
                dialog,
                "手动指定抓取网卡",
                "选择诊断确认的网卡：",
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
                    "高级排障",
                    "未找到“抓取网卡”设置，请重新打开设置页面后重试。",
                )
                return
            capture_device_edit.setText(selected)
            QMessageBox.information(
                self,
                "高级排障",
                "已填入抓取网卡。请点击“保存同步设置”后重新启动同步。",
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
    if not _require_environment_diagnostics(self, packet=False, label="诊断 dwmapi"):
        return
    current_worker = getattr(self, "_dwmapi_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "dwmapi 诊断", "诊断正在进行，请稍候。")
        return
    executable = self.work_mode_service.settings.game_executable
    button = getattr(self, "_dwmapi_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("诊断中…")
    record = self.work_mode_service.deployment_record
    try:
        runtime_snapshot = asdict(self._mod_plugin_loading_service.snapshot())
        runtime_snapshot["loader_path"] = str(runtime_snapshot["loader_path"])
        runtime_snapshot["payload_path"] = str(runtime_snapshot["payload_path"])
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        runtime_snapshot = {
            "phase": "probe_error",
            "detail": str(exc),
        }
    def collect():
        self.work_mode_service.require("diagnostics")
        return collect_dwmapi_diagnostics(
            game_executable_path=executable,
            application_root=self.app_context.paths.root,
            recorded_deployed_sha256=str(
                record.get("deployed_sha256") or ""
            ),
            recorded_workspace_path=str(
                record.get("workspace_path") or ""
            ),
            loading_method=selected_plugin_loading_method(self),
            loader_snapshot=runtime_snapshot,
        )

    worker = WorkerThread(target=collect, parent=self)
    self._dwmapi_diagnostic_worker = worker
    operation = _new_environment_operation(self, "dwmapi_diagnostics")
    accept_result = _environment_result_acceptor(self, operation, "diagnostics")
    log_event(
        "INFO",
        "environment.dwmapi_diagnostics_started",
        "开始诊断装备插件",
        operation,
        game_executable_configured=bool(executable),
    )

    def finish(result):
        if self._dwmapi_diagnostic_worker is not worker:
            return
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 dwmapi")
        if not accept_result():
            return
        if result.get("error"):
            self.operation_unavailable("诊断 dwmapi", str(result["error"]), target="detection")
            return
        log_event(
            "INFO",
            "environment.dwmapi_diagnostics_succeeded",
            "装备插件诊断完成",
            operation,
            diagnostic_section_count=len(result),
        )
        pipe = result.get("pipe") or {}
        loader = result.get("loader") or {}
        if pipe.get("state") in {"access_denied", "probe_error", "error"}:
            self.operation_unavailable("诊断 dwmapi", str(pipe.get("message") or "命名管道检测失败。"), target="detection")
            return
        if pipe.get("state") == "missing" and loader.get("phase") != "running":
            self.operation_unavailable("诊断 dwmapi", str(pipe.get("message") or "尚未发现游戏组件管道。"), target="detection")
            return
        if self.work_mode_service.allowed("diagnostics"):
            self._show_dwmapi_diagnostic_report(format_dwmapi_diagnostics(result))

    def failed(error):
        if self._dwmapi_diagnostic_worker is not worker:
            return
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 dwmapi")
        if not accept_result():
            return
        log_event(
            "ERROR",
            "environment.dwmapi_diagnostics_failed",
            "装备插件诊断失败",
            operation,
            error=error,
        )
        self.operation_unavailable("诊断 dwmapi", str(error), target="detection")

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
    if not self.operation_entry("native_load", "部署游戏内组件"):
        return
    try:
        self.work_mode_service.require("native_load")
    except PermissionError as error:
        QMessageBox.warning(self, "部署游戏内组件", str(error))
        return
    executable = self.work_mode_service.settings.game_executable
    if not executable.strip():
        self.operation_unavailable("部署游戏内组件", "尚未选择游戏主程序 HTGame.exe。", target="deployment")
        return
    try:
        running = game_process_running()
    except EquipmentPluginDeploymentError as error:
        QMessageBox.warning(self, "部署装备插件", str(error))
        return
    if running:
        QMessageBox.warning(
            self,
            "部署装备插件",
            "检测到游戏正在运行。\n请完全退出游戏后再部署插件。",
        )
        return
    if getattr(inspect_game_component_bundle(self.app_context.paths.root), "layout", "") == "native-capture-v1":
        deploy_native_plugin_from_settings(self)
        return
    try:
        self._mod_plugin_loading_service.ensure_proxy_deployment_allowed()
        source = packaged_plugin_dll(self.app_context.paths.root)
    except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as exc:
        if self.work_mode_service.allowed("native_load"):
            self.operation_unavailable("部署游戏内组件", str(exc), target="deployment")
        return
    if QMessageBox.question(
        self,
        "确认部署装备插件",
        "将部署装备插件到所选游戏目录。\n"
        "已有同名文件会自动备份。\n\n"
        "是否继续？",
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
            operation_guard=self.work_mode_service.require,
            game_executable_path=executable,
            plugin_dll_path=source,
            application_root=self.app_context.paths.root,
            writable_workspace_path=(
                self.app_context.paths.config_dir / "mods-plugin"
            ),
            backup_directory=(
                self.app_context.paths.config_dir / "component-backups"
            ),
        )
        self.work_mode_runtime.save_deployment(deployed)
        record = self.work_mode_service.deployment_record
        record["loading_method"] = "proxy"
        self.work_mode_service.update_deployment(record)
        log_event(
            "INFO",
            "environment.plugin_deploy_succeeded",
            "装备插件部署完成",
            operation,
            backup_created=bool(deployed.backup_path),
            registry_value_existed=deployed.workspace_registry_value_existed,
        )
        self._refresh_equipment_plugin_status()
        QMessageBox.information(
            self,
            "部署装备插件",
            f"已部署 dwmapi.dll，并注册 Mod 工作区：\n{deployed.workspace_path}",
        )
    except PluginDeploymentPendingCleanup as exc:
        self.work_mode_runtime.save_pending_deployment(exc)
        QMessageBox.warning(self, "组件部署待清理", str(exc))
    except (EquipmentPluginDeploymentError, PermissionError) as exc:
        log_event(
            "ERROR",
            "environment.plugin_deploy_failed",
            "装备插件部署失败",
            operation,
            error=exc,
        )
        if self.work_mode_service.allowed("native_load"):
            self.operation_unavailable("部署游戏内组件", str(exc), target="deployment")


def _cleanup_equipment_plugin(self):
    self.work_mode_controller.cleanup()


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
    _cleanup_equipment_plugin = _cleanup_equipment_plugin
    _focus_environment_configuration = _focus_environment_configuration
