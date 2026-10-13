# 在现有设置部署入口展示原生插件配套状态并提交游戏退出后的整套部署。
from PySide6.QtCore import QEventLoop, QSize, Qt, QThread, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout

from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen
from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.components.inspection import inspect_deployed_native_plugin
from src.integrations.components.environment import EquipmentPluginDeploymentError
from src.integrations.components.native_files import PluginDeploymentPendingCleanup
from src.integrations.components.native_files import deploy_native_plugin
from src.services.components.loader_service import ModPluginLoadingError, ModPluginLoadingWaiting
from src.utils.logger import logger


def _deployment_error_hint(error: Exception) -> str:
    """短提示区分权限、超时与未完成；具体定位保留在检测和日志。"""
    logger.warning(f"组件部署未完成 kind={type(error).__name__}")
    if isinstance(error, PermissionError):
        return "组件处理未完成；请核对工作模式、游戏目录权限，并确认游戏已退出。"
    if isinstance(error, TimeoutError):
        return "等待组件或同步任务结束超时；请退出游戏后重新检测再试。"
    return "组件处理未完成；请确认游戏和启动器已退出，再到环境设置查看检测详情。"


class _DeploymentWorker(QThread):
    def __init__(self, target, parent):
        super().__init__(parent)
        self._target = target
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self._target()
        except Exception as error:
            self.error = error


class _DeploymentProgress(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.running = True

    def reject(self):
        if not self.running:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            event.ignore()
        else:
            super().closeEvent(event)


def _run_deployment_worker(window, target):
    """后台执行文件核对与替换，终态后返回原类型错误；期间不取消已派发部署。"""

    dialog = _DeploymentProgress(window)
    dialog.setWindowTitle("正在部署原生组件")
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    layout = QVBoxLayout(dialog)
    label = QLabel("正在核对并写入游戏组件，请保持游戏关闭…", dialog)
    label.setWordWrap(True)
    layout.addWidget(label)
    progress = QProgressBar(dialog)
    progress.setRange(0, 0)
    layout.addWidget(progress)
    fit_dialog_to_available_screen(dialog, QSize(420, 120))

    loop = QEventLoop(dialog)
    worker = _DeploymentWorker(target, dialog)
    worker.finished.connect(loop.quit)
    try:
        dialog.show()
        QTimer.singleShot(0, worker.start)
        loop.exec()
        worker.wait()
        if worker.error is not None:
            raise worker.error
        return worker.result
    finally:
        dialog.running = False
        dialog.close()
        dialog.deleteLater()


def _refresh_work_mode_detection(window) -> None:
    controller = getattr(window, "work_mode_controller", None)
    if controller is not None:
        controller.component_state_changed()


def _set_component_status(label, *, issues=(), ready=False, pending=False):
    if pending:
        label.setText("Loader 工作区核对未完成；请查看检测详情。")
        label.setToolTip("")
    elif ready:
        label.setText("组件已准备好，启动游戏后会自动检查连接和可用功能。")
        label.setToolTip("")
    else:
        label.setText(f"组件未准备好（{len(issues)} 项）；请查看检测详情。")
        label.setToolTip("\n".join(str(issue) for issue in issues))


def refresh_native_plugin_status(window) -> None:
    bundle = inspect_game_component_bundle(window.app_context.paths.root)
    combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    if combo is not None:
        combo.blockSignals(True)
        index = combo.findData("native-capture")
        if index < 0:
            index = combo.findData("proxy")
            if index >= 0:
                combo.setItemText(index, "D3D 采集代理")
                combo.setItemData(index, "native-capture")
            else:
                combo.addItem("D3D 采集代理", "native-capture")
                index = combo.findData("native-capture")
        if combo.currentData() not in {"loader", "native-capture"}:
            combo.setCurrentIndex(index)
        combo.setEnabled(True)
        combo.blockSignals(False)
    primary = getattr(window, "_equipment_plugin_primary_button", None)
    if primary is not None:
        primary.setText("启动原生 Loader" if combo is not None and combo.currentData() == "loader" else "部署原生组件")
    label = getattr(window, "_equipment_plugin_status_label", None)
    if label is not None:
        if not bundle.ready:
            _set_component_status(label, issues=bundle.issues)
        elif combo is not None and combo.currentData() == "loader":
            try:
                service = window._mod_plugin_loading_service
                workspace = service.inspect_native_workspace()
                _set_component_status(label, ready=workspace.files_compatible, issues=workspace.issues)
            except (EquipmentPluginDeploymentError, ModPluginLoadingError):
                _set_component_status(label, pending=True)
        else:
            result = inspect_deployed_native_plugin(
                application_root=window.app_context.paths.root,
                game_executable_path=window.work_mode_service.settings.game_executable,
                recorded_files=window.work_mode_service.deployment_record.get("managed_files", {}),
                bundle_inspection=bundle,
            )
            _set_component_status(label, ready=result.files_compatible, issues=result.issues)


def _confirm_d3d_deployment(window) -> bool:
    dialog = QDialog(window)
    dialog.setObjectName("d3dDeploymentConfirmation")
    dialog.setWindowTitle("部署 D3D 原生组件")
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(14)

    warning = QLabel("部署前，请完全退出游戏", dialog)
    warning.setObjectName("d3dDeploymentExitWarning")
    warning.setStyleSheet(f"color:{theme_color('#f85149')};font-size:17px;font-weight:700")
    layout.addWidget(warning)
    guidance = QLabel("确认游戏窗口和游戏进程均已关闭，再继续部署。", dialog)
    guidance.setWordWrap(True)
    layout.addWidget(guidance)
    detail = QLabel(
        "本次将替换 d3d12.dll、NTE_Capture.dll，并清理旧 dwmapi.dll。\n"
        "已有组件直接替换，不保留备份。",
        dialog,
    )
    detail.setObjectName("d3dDeploymentChanges")
    detail.setWordWrap(True)
    detail.setStyleSheet(f"color:{theme_color('#8b949e')}")
    layout.addWidget(detail)

    actions = QHBoxLayout()
    actions.addStretch()
    cancel = QPushButton("取消", dialog)
    cancel.setDefault(True)
    cancel.setFocus()
    cancel.clicked.connect(dialog.reject)
    actions.addWidget(cancel)
    proceed = QPushButton("已退出游戏，继续部署", dialog)
    proceed.setObjectName("d3dDeploymentProceed")
    proceed.setAutoDefault(False)
    proceed.clicked.connect(dialog.accept)
    actions.addWidget(proceed)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(560, 250))
    return dialog.exec() == QDialog.Accepted


def deploy_native_plugin_from_settings(window) -> None:
    bundle = inspect_game_component_bundle(window.app_context.paths.root)
    if not bundle.ready:
        window.operation_unavailable("部署原生组件", "；".join(bundle.issues), target="deployment")
        return
    if window.native_game_session.battle_active:
        QMessageBox.information(window, "部署原生组件", "请先结束当前战报采集，再部署组件。")
        return
    try:
        if window._mod_plugin_loading_service.snapshot().phase == "running":
            QMessageBox.information(window, "部署原生组件", "请先停止 Loader，再部署 D3D 原生组件。")
            return
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as error:
        window.operation_unavailable("部署原生组件", _deployment_error_hint(error), target="deployment")
        return
    executable = window.work_mode_service.settings.game_executable
    generation = window.operation_generation()
    if not _confirm_d3d_deployment(window):
        return

    policy = window.work_mode_service
    context = window.app_context
    session = window.native_game_session
    runtime = window.work_mode_runtime
    root = context.paths.root

    def guard(capability):
        policy.require(capability)
        if ((policy.operation_revision, context.generation) != generation
                or policy.settings.game_executable != executable
                or session.battle_active):
            raise PermissionError("原生组件部署上下文已改变，已停止操作。")

    try:
        guard("native_load")
        invalidate = getattr(window, "invalidate_inventory_sync_notifications", None)
        if invalidate is not None:
            invalidate()
        sync_service = getattr(window, "_inventory_sync_service", None)
        if sync_service is not None:
            sync_service.request_stop()
        window.character_profile_sync_controller.request_stop()

        def deploy_after_stop():
            nonlocal generation
            if sync_service is not None and sync_service.is_running:
                sync_service.stop()
            session.close()
            revision = runtime.prepare_manual_native_deployment(expected_operation_revision=generation[0])
            generation = (revision, generation[1])
            guard("native_load")
            return deploy_native_plugin(
                application_root=root,
                game_executable_path=executable,
                operation_guard=guard,
                cleanup_legacy_proxy=True,
            )

        try:
            deployed = _run_deployment_worker(window, deploy_after_stop)
        finally:
            if sync_service is None or not sync_service.is_running:
                window._stop_inventory_sync()
        runtime.save_deployment(deployed)
        window._refresh_equipment_plugin_status()
        _refresh_work_mode_detection(window)
        QMessageBox.information(window, "原生组件已部署", "请启动游戏，然后重新检测连接和各项业务能力。")
    except PluginDeploymentPendingCleanup as error:
        runtime.save_pending_deployment(error)
        _refresh_work_mode_detection(window)
        QMessageBox.warning(
            window, "组件部署待清理",
            "状态：部署尚未完成。\n"
            "原因：旧组件清理未通过核对。\n"
            "下一步：在环境设置查看清理结果，处理后重新部署。",
        )
    except (EquipmentPluginDeploymentError, PermissionError, TimeoutError) as error:
        if window.work_mode_service.allowed("native_load"):
            window.operation_unavailable("部署原生组件", _deployment_error_hint(error), target="deployment")


def start_native_loader_from_settings(window) -> None:
    try:
        window._stop_inventory_sync()
        window.character_profile_sync_controller.request_stop()
        window.work_mode_runtime.start_native_loader()
        window._refresh_equipment_plugin_status()
        _refresh_work_mode_detection(window)
        QMessageBox.information(window, "原生 Loader 已启动",
                                "请正常启动游戏，随后重新检测连接和各项能力。")
    except ModPluginLoadingWaiting as error:
        window._refresh_equipment_plugin_status()
        QMessageBox.warning(
            window, "Loader 等待关闭程序",
            "状态：尚未启动 Loader\n原因：" + str(error) +
            "\n下一步：完全退出启动器和游戏，再点击“启动原生 Loader”。",
        )
    except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as error:
        window._refresh_equipment_plugin_status()
        window.operation_unavailable("启动原生 Loader", _deployment_error_hint(error), target="deployment")
