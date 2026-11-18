# 主窗口真正退出时按现有顺序撤权、等待 owner 并释放组件；隐藏窗口不进入此路径。
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.features.inventory.equipment.management.persistence_controller import wait_for_equipment_persistence
from src.observability import log_event
from src.utils.logger import disable_session_log, logger


class MainWindowLifecycleMixin:
    def closeEvent(self, e):
        tray = getattr(self, "tray_controller", None)
        if tray is not None and tray.handle_close_event(
            e, saving_session=QApplication.instance().isSavingSession(),
        ):
            return
        from src.ui.controllers.configuration_controller import defer_page_transition
        if defer_page_transition(self, self.close):
            e.ignore()
            return
        if wait_for_equipment_persistence(self, ("close",), self.close):
            e.ignore()
            return
        self.equipment_persistence_controller.close()
        wheel = getattr(self, 'quick_wheel_controller', None)
        if wheel is not None:
            wheel.begin_close()
        # 草稿确认通过后、物理等待之前撤权；取消退出不永久关闭视觉 owner。
        self.scanning_controller.begin_close()
        self.team_controller.deactivate()
        self.workshop_refresh_controller.close()
        self.native_plugin_update_controller.begin_close()
        self.calc_update_controller.begin_close()
        from src.app.page_tasks import close_page_tasks
        self._ui_latency_timer.stop()
        self.dashboard_controller.close()
        self.basic_weight_page.close_tasks()
        self.toolbox_page.shutdown()
        self.identification_controller.close()
        self.role_suit_matching.close()
        warehouse_reads = getattr(self, "warehouse_read_controller", None)
        if warehouse_reads is not None:
            warehouse_reads.close()
        for owner in (getattr(self, "_official_role_controller", None),
                      getattr(self, "allocation_catalog_controller", None)):
            if owner is not None:
                owner.close()
        if (self.team_controller.is_running() or (wheel is not None and wheel.is_running()) or close_page_tasks(self) or self.workshop_refresh_controller.is_running()
                or self.scanning_controller.is_running()
                or self.native_plugin_update_controller.is_running()
                or self.calc_update_controller.is_running()):
            e.ignore()
            QTimer.singleShot(25, self, self.close)
            return
        if hasattr(self.scanning_controller, "role_selector"):
            try:
                self.scanning_controller.role_selector.save_temporary_priority_config()
            except Exception as exc:
                logger.warning(f"保存临时优先级失败: {exc}")
        self.performance_controller.close()
        self.character_profile_sync_controller.close()
        self.auto_sync_controller.close()
        self.work_mode_controller.close()
        try:
            self.battle_report_controller.close()
        except Exception as exc:
            logger.warning(f"停止战报采集失败: {exc}")
        try:
            self.scanning_controller.close()
        except Exception as exc:
            logger.warning(f"停止视觉扫描失败: {exc}")
        try:
            self._stop_inventory_sync()
        except Exception as exc:
            logger.warning(f"停止背包同步失败: {exc}")
        try:
            self._mod_plugin_loading_service.close()
        except Exception as exc:
            logger.warning(f"停止 Mod Loader 失败: {exc}")
        try:
            self.static_catalog_page.close()
        except Exception as exc:
            logger.warning(f"关闭游戏资料库失败: {exc}")
        self.global_hotkey_manager.close()
        self._unregister_inventory_sync_lifecycle()
        self._unregister_character_profile_sync()
        self._account_context_unsubscribe()
        self._unregister_page_tasks()
        self._unregister_toolbox_lifecycle()
        self._unregister_calc_updates()
        self._unregister_warehouse_reads()
        self._unregister_equipment_persistence()
        log_event(
            "INFO",
            "application.stopping",
            "应用正在停止后台服务并退出",
            self._application_log_context,
        )
        if self._log_enabled:
            disable_session_log(
                reason="application_exit",
                context=self._current_log_context(),
            )
            self._log_enabled = False
        if self._qt_log_sink_id is not None:
            try:
                from loguru import logger as lu

                lu.remove(self._qt_log_sink_id)
            except Exception:
                pass
            self._qt_log_sink_id = None
        if tray is not None:
            tray.close()
        if wheel is not None:
            wheel.close()
        super().closeEvent(e)
