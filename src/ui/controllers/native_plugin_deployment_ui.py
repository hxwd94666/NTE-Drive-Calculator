# 在现有设置部署入口展示原生插件配套状态并提交游戏退出后的整套部署。
from PySide6.QtWidgets import QMessageBox

from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from src.services.native_plugin_deployment import PluginDeploymentPendingCleanup
from src.services.native_plugin_deployment import deploy_native_plugin
from src.services.mod_plugin_loading_service import ModPluginLoadingError


def _refresh_work_mode_detection(window) -> None:
    controller = getattr(window, "work_mode_controller", None)
    if controller is not None:
        controller.component_state_changed()


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
    bundle_label = getattr(window, "_equipment_plugin_bundle_label", None)
    if bundle_label is not None:
        bundle_label.setText("原生组件整包已核对" if bundle.ready else "；".join(bundle.issues))
    label = getattr(window, "_equipment_plugin_status_label", None)
    if label is not None:
        if not bundle.ready:
            label.setText("原生配套文件尚未齐备，暂不能部署；修复组件包后重新检测。")
        elif combo is not None and combo.currentData() == "loader":
            try:
                service = window._mod_plugin_loading_service
                workspace = service.inspect_native_workspace()
                label.setText("Loader 运行目录文件已核对；启动游戏后检测连接和各项能力。"
                              if workspace.files_compatible else "；".join(workspace.issues))
            except (EquipmentPluginDeploymentError, ModPluginLoadingError) as error:
                label.setText(str(error))
        else:
            result = inspect_deployed_native_plugin(
                application_root=window.app_context.paths.root,
                game_executable_path=window.work_mode_service.settings.game_executable,
                recorded_files=window.work_mode_service.deployment_record.get("managed_files", {}),
                bundle_inspection=bundle,
            )
            label.setText("原生组件文件已核对；启动游戏后检测连接和各项能力。"
                          if result.files_compatible else "；".join(result.issues))


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
        window.operation_unavailable("部署原生组件", str(error), target="deployment")
        return
    executable = window.work_mode_service.settings.game_executable
    generation = window.operation_generation()
    if QMessageBox.question(
        window, "确认部署原生组件",
        "将更新游戏内组件，并清理本程序管理的旧版组件。已有组件会直接替换，不保留备份。\n"
        "请先完全退出游戏。\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    ) != QMessageBox.Yes:
        return

    def guard(capability):
        window.work_mode_service.require(capability)
        if (window.operation_generation() != generation
                or window.work_mode_service.settings.game_executable != executable
                or window.native_game_session.battle_active):
            raise PermissionError("原生组件部署上下文已改变，已停止操作。")

    try:
        guard("native_load")
        window._stop_inventory_sync()
        window.character_profile_sync_controller.request_stop()
        window.native_game_session.close()
        revision = window.work_mode_runtime.prepare_manual_native_deployment(expected_operation_revision=generation[0])
        generation = (revision, generation[1])
        guard("native_load")
        deployed = deploy_native_plugin(
            application_root=window.app_context.paths.root,
            game_executable_path=executable,
            operation_guard=guard,
        )
        window.work_mode_runtime.save_deployment(deployed)
        window._refresh_equipment_plugin_status()
        _refresh_work_mode_detection(window)
        QMessageBox.information(window, "原生组件已部署", "请启动游戏，然后重新检测连接和各项业务能力。")
    except PluginDeploymentPendingCleanup as error:
        window.work_mode_runtime.save_pending_deployment(error)
        _refresh_work_mode_detection(window)
        QMessageBox.warning(window, "组件部署待清理", str(error))
    except (EquipmentPluginDeploymentError, PermissionError) as error:
        if window.work_mode_service.allowed("native_load"):
            window.operation_unavailable("部署原生组件", str(error), target="deployment")


def start_native_loader_from_settings(window) -> None:
    try:
        window._stop_inventory_sync()
        window.character_profile_sync_controller.request_stop()
        window.work_mode_runtime.start_native_loader()
        window._refresh_equipment_plugin_status()
        _refresh_work_mode_detection(window)
        QMessageBox.information(window, "原生 Loader 已启动",
                                "请正常启动游戏，随后重新检测连接和各项能力。")
    except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as error:
        window._refresh_equipment_plugin_status()
        window.operation_unavailable("启动原生 Loader", str(error), target="deployment")
