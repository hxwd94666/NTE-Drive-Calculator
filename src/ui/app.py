# PySide6 主窗口入口和功能模块挂载。
"""组合桌面窗口及功能 owner；数据准备只在显式启动时执行。"""
import sys, ctypes
from src.ui.axis_allocation_composition import build_axis_allocation_selection_service
from pathlib import Path
from src.domain.equipment.loadout_management import LoadoutAccount
from src.services.equipment.loadout_management import LoadoutManagementService
from src.features.inventory.equipment.persistence_controller import EquipmentPersistenceController, wait_for_equipment_persistence
from typing import Optional
from src.app.context import (
    AccountChangedEvent,
    AccountSwitchError,
    CallbackAccountLifecycle,
)
from src.ui.controllers.workshop_refresh_controller import WorkshopRefreshController
from src.app.constants import (
    APP_VERSION,
)
from src.ui.application_runtime import ApplicationRuntime, prepare_application_runtime
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
)
from PySide6.QtCore import QTimer, Qt, Signal, QPoint
from PySide6.QtGui import QIcon
from src.observability import OperationContext, log_event
from src.utils.logger import (
    disable_session_log,
    logger,
    set_log_dir,
)
from src.ui.qt_log_sink import QtLogSink

from src.features.accounts.manager import populate_account_combo, show_account_manager_dialog
from src.features.settings.page import refresh_account_scoped_settings
from src.features.settings.updates.controller import CalcUpdateController
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.services.components.loader_service import ModPluginLoadingService
from src.ui.work_mode_composition import (
    initialize_mode_policy, initialize_mode_runtime, migrate_legacy_component_facts, initialize_character_profile_sync,
)
from src.ui.main_window_mixins import FeatureMainWindowMixin
from src.ui.equipment_presentation import EquipmentPresentation
from src.features.configuration.page_owner import BasicWeightPage
from src.features.blueprints.page import BlueprintPage
from src.features.toolbox.page import ToolboxPage
from src.ui.toolbox_composition import build_toolbox_dependencies, register_toolbox_account_lifecycle
from src.features.static_catalog.controller import StaticCatalogController
from src.features.static_catalog.dependencies import (
    build_static_catalog_domain_pages,
    build_static_catalog_providers,
)
from src.features.static_catalog.page import StaticCatalogPage
from src.services.static_catalog.service import StaticCatalogService
from src.services.inventory.warehouse_inventory_service import WarehouseInventoryService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.features.battle_report.dependencies import build_battle_report_controller
from src.features.identification.controller import IdentificationController
from src.features.onboarding.guide import OnboardingGuide
from src.features.official_role.page import refresh_official_role_page
from src.features.scanning.controller import ScanningController

def _is_admin():
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _restart_as_admin():
    if sys.platform != "win32":
        return False
    try:
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        params = " ".join(f'"{a}"' for a in args)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return result > 32
    except Exception:
        return False


def _ensure_admin():
    if _is_admin():
        return
    if _restart_as_admin():
        sys.exit(0)
    raise RuntimeError("需要管理员权限启动，请右键程序选择“以管理员身份运行”。")


# ── Main Window
from src.ui.main_window_data_mixin import MainWindowDataMixin

from src.ui.main_window_navigation_mixin import MainWindowNavigationMixin
from src.ui.main_window_theme_mixin import MainWindowThemeMixin

class MainWindow(MainWindowThemeMixin, MainWindowNavigationMixin, MainWindowDataMixin, FeatureMainWindowMixin, QMainWindow):
    log_signal = Signal(str)
    inventory_sync_state_signal = Signal(object)
    W, H = 1260, 860

    def __init__(self, runtime: ApplicationRuntime):
        super().__init__()
        # 所有窗口消费者使用本次组合的同一上下文，导入模块时没有默认账号。
        self.app_context = runtime.context
        self._account_manager = runtime.accounts
        self._equipment_switch_waiting = False
        self.equipment_persistence_controller = EquipmentPersistenceController(
            lambda: LoadoutAccount(str(self.app_context.account.active_account_id), self.app_context.generation,
                                   Path(self.app_context.account.user_database_path).resolve()),
            LoadoutManagementService(), self,
        )
        self._unregister_equipment_persistence = self.app_context.register_account_lifecycle(
            CallbackAccountLifecycle(is_running=self.equipment_persistence_controller.is_running,
                stop=self.equipment_persistence_controller.stop,
                rebuild=lambda _account: self.equipment_persistence_controller.rebuild(), start=lambda: None),
        )
        self._language_settings = runtime.language_settings
        from src.ui.role_suit_matching.controller import RoleSuitMatchingController
        self.role_suit_matching = RoleSuitMatchingController(
            self.app_context, self._language_settings.startup().catalog, self)
        self.setWindowTitle("NTE Drive Calc")
        screen_geo = QApplication.primaryScreen().availableGeometry()
        initial_w = min(self.W, max(640, screen_geo.width() - 80))
        initial_h = min(self.H, max(480, screen_geo.height() - 80))
        min_w = min(1000, max(640, screen_geo.width() - 120))
        min_h = min(700, max(480, screen_geo.height() - 120))
        self.resize(initial_w, initial_h)
        self.setMinimumSize(min_w, min_h)
        if self.app_context.paths.app_icon_path.exists():
            self.setWindowIcon(QIcon(str(self.app_context.paths.app_icon_path)))
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self._drag_pos: Optional[QPoint] = None
        self._drag_edges: tuple[bool, bool, bool, bool] = (False,) * 4
        self._resize_margin = 8
        self.move(
            screen_geo.x() + (screen_geo.width() - initial_w) // 2,
            screen_geo.y() + (screen_geo.height() - initial_h) // 2,
        )
        self.roles_db: dict = {}
        self.sets_db: dict = {}
        self.all_set_names: list[str] = []
        self.tape_main_stats: list[str] = []
        self.stats_config: dict = {}
        self.equipped_state: dict = {}
        self._shape_areas: dict = {}
        self.scoring_engine = None
        self._inventory_sync_service = None
        initialize_mode_policy(self)
        self._application_log_context = OperationContext.create(
            "application",
        )
        self._account_context_unsubscribe = self.app_context.subscribe_account_changed(
            self._on_app_context_account_changed
        )
        from src.ui.controllers.configuration_controller import register_page_configuration_lifecycle
        self.basic_weight_page = BasicWeightPage(
            self.app_context, self._go, self.on_configuration_changed, self,
        )
        self._unregister_page_tasks = register_page_configuration_lifecycle(
            self.app_context, lambda: (self.basic_weight_page.task_owner,
                                      getattr(self, "_official_role_controller", None),
                                      getattr(self, "allocation_catalog_controller", None)),
            after_stop=self.basic_weight_page.clear_projection,
        )
        from src.features.inventory.warehouse.read_controller import reset_warehouse_account
        # 切换前撤销旧投影；只读通道跨账号复用，慢 IO 不阻塞切换。
        self._unregister_warehouse_reads = self.app_context.register_account_lifecycle(
            CallbackAccountLifecycle(
                is_running=lambda: False, stop=lambda: reset_warehouse_account(self),
                rebuild=lambda _account: None, start=lambda: None,
            )
        )
        self._inventory_sync_lifecycle = CallbackAccountLifecycle(
            is_running=lambda: bool(self._inventory_sync_service and self._inventory_sync_service.is_running),
            stop=self._stop_inventory_sync,
            rebuild=lambda _account: None,
            start=lambda: self.auto_sync_controller.refresh(),
        )
        self._unregister_inventory_sync_lifecycle = self.app_context.register_account_lifecycle(
            self._inventory_sync_lifecycle,
            nte_core=True,
        )
        self._log_enabled = False
        set_log_dir(self.app_context.account.log_dir, reopen_session=False)
        log_event(
            "INFO",
            "application.started",
            "应用主窗口开始初始化",
            self._application_log_context,
            app_version=APP_VERSION,
            administrator_required=True,
        )

        # Hotkey config
        self._hk_capture = "F9"
        self._hk_finish = "F10"
        self._hk_stop = "F12"
        self._hk_battle_rerecord = "F11"
        self._account_settings = self.app_context.account_settings
        legacy_theme = self._account_settings.legacy_theme_preference()
        self._account_settings.migrate_legacy_settings()
        self._account_settings.remove_legacy_theme_preference()
        self._global_theme_settings = runtime.theme_settings
        self._mod_plugin_loading_service = ModPluginLoadingService(
            application_root=self.app_context.paths.root, operation_guard=self.operation_guard,
            native_workspace_path=self.app_context.paths.config_dir / "native-loader",
        )
        initialize_mode_runtime(self)
        self._theme_preference = self._load_theme_preference(legacy_theme)
        self._load_hotkey_config()
        self.global_hotkey_manager = GlobalHotkeyManager(
            capture_hotkey=self._hk_capture,
            finish_hotkey=self._hk_finish,
            stop_hotkey=self._hk_stop,
            battle_rerecord_hotkey=self._hk_battle_rerecord,
        )
        initialize_character_profile_sync(self)
        self.equipment_presentation = EquipmentPresentation(
            app_context=self.app_context,
            dialog_parent=self,
        )
        self.scanning_controller = ScanningController(
            global_preferences=runtime.theme_settings.store,
            role_suit_matching=self.role_suit_matching,
            prepare_calculation=self.prepare_calculation,
            axis_selection_service_provider=lambda: build_axis_allocation_selection_service(self.app_context),
            navigate=self._go,
            work_mode_provider=lambda: self.work_mode_service.settings.mode,
            operation_entry=self.operation_entry,
            operation_unavailable=self.operation_unavailable,
            operation_guard=self.operation_guard,
            operation_generation=self.operation_generation,
            app_context=self.app_context,
            dialog_parent=self,
            minimize_window=self.showMinimized,
            restore_window=self.showNormal,
            activate_window=self.activateWindow,
            update_inventory_status=self._update_inventory_status,
            refresh_home=self._refresh_home,
            preferences_provider=lambda: self._ui_preferences,
            save_preferences=self._save_ui_preferences,
            refresh_roles=lambda: refresh_official_role_page(self),
            refresh_equipment=self.refresh_saved_equipment_after_mutation,
            card_factory=self._card,
            equipment_presentation=self.equipment_presentation,
            hotkey_manager=self.global_hotkey_manager,
        )
        self.identification_controller = IdentificationController(
            role_suit_matching=self.role_suit_matching,
            operation_entry=self.operation_entry,
            operation_unavailable=self.operation_unavailable,
            app_context=self.app_context,
            dialog_parent=self,
            card_factory=self._card,
            equipment_presentation=self.equipment_presentation,
            hotkey_manager=self.global_hotkey_manager,
            minimize_window=self.showMinimized,
            restore_window=self.showNormal,
            activate_window=self.activateWindow,
        )
        self.battle_report_controller = build_battle_report_controller(
            axis_allocation_requested=self.scanning_controller.start_axis_allocation_from_battle,
            operation_entry=self.operation_entry,
            operation_unavailable=self.operation_unavailable,
            app_context=self.app_context,
            dialog_parent=self,
            inventory_sync_is_running=lambda: bool(
                self._inventory_sync_service
                and self._inventory_sync_service.is_running
            ),
            stop_inventory_sync=self._stop_inventory_sync,
            start_inventory_sync=lambda: self._start_inventory_sync(automatic=True),
            hotkey_manager=self.global_hotkey_manager,
            work_mode_service=self.work_mode_service, native_session=self.native_game_session,
        )
        self.blueprint_page = BlueprintPage(
            app_context=self.app_context,
            navigate=self._go,
        )
        self.toolbox_page = ToolboxPage(
            dependencies=build_toolbox_dependencies(
                self.app_context,
                operation_entry=self.operation_entry,
                operation_unavailable=self.operation_unavailable,
                operation_guard=self.operation_guard,
                operation_generation=self.operation_generation,
                material_importer=self._import_cultivation_materials,
                navigate_static_catalog=lambda: self._go("static_catalog"),
            ),
            dialog_parent=self,
        )
        self._unregister_toolbox_lifecycle = register_toolbox_account_lifecycle(self.app_context, self.toolbox_page)
        static_catalog_controller = StaticCatalogController(
            StaticCatalogService(
                static_database_path=self.app_context.paths.static_database_path,
                providers=build_static_catalog_providers(
                    self.app_context.paths.static_database_path,
                    role_catalog=self.app_context.paths.role_catalog,
                ),
                domain_database_paths=(
                    {key: self.app_context.paths.role_catalog.database_path for key in self.app_context.paths.role_catalog.catalog_domains}
                    if self.app_context.paths.role_catalog else None
                ),
            )
        )
        static_catalog_domains = ()
        try:
            static_catalog_domains = build_static_catalog_domain_pages(
                self.app_context.paths.static_database_path,
                self.app_context.paths.game_ui_asset_root,
                equipment_presentation=self.equipment_presentation,
                role_catalog=self.app_context.paths.role_catalog,
                equipment_inventory_loader=self._load_static_catalog_inventory,
                open_catalog_link=lambda link: (
                    self.static_catalog_page.open_catalog_link(link)
                ),
            )
            self.static_catalog_page = StaticCatalogPage(
                controller=static_catalog_controller,
                dialog_parent=self,
                game_ui_asset_root=self.app_context.paths.game_ui_asset_root,
                domain_pages=static_catalog_domains,
            )
        except Exception:
            for spec in reversed(static_catalog_domains):
                try:
                    spec.close()
                except Exception:
                    pass
            try:
                static_catalog_controller.close()
            except Exception:
                pass
            raise
        self.onboarding_guide = OnboardingGuide(
            app_context=self.app_context,
            parent=self,
            on_help=self._show_group_chat_notice,
        )
        self.calc_update_controller = CalcUpdateController(
            identity_provider=lambda: (self.app_context.account.active_account_id, self.app_context.generation),
            load_settings=lambda: self.app_context.account_settings.load("update"),
            save_settings=lambda values: self.app_context.account_settings.save("update", values),
            style_sheet=self._current_style_sheet, show_group=self._show_group_chat_notice,
            show_netdisk=self._show_netdisk_download_dialog, request_close=self.close, parent=self,
            update_channel=runtime.distribution.update_channel,
        )
        # rebuild 早于账号变更通知；回调直接读取新上下文，不借旧账号 settings 缓存。
        self._unregister_calc_updates = self.app_context.register_account_lifecycle(CallbackAccountLifecycle(
            is_running=lambda: False, stop=self.calc_update_controller.stop_for_account,
            rebuild=lambda _account: self.calc_update_controller.rebuild_account(), start=lambda: None,
        ))
        self._ui_preferences = self._load_ui_preferences()
        migrate_legacy_component_facts(self)
        self._apply_theme_preference()

        self.log_signal.connect(self._on_log)
        self.inventory_sync_state_signal.connect(self._on_inventory_sync_state)
        self._log_sink = QtLogSink(self.log_signal)
        try:
            from loguru import logger as lu

            self._qt_log_sink_id = lu.add(
                self._log_sink,
                format="{time:HH:mm:ss} | {level: <8} | {message}",
                level="INFO",
                colorize=False,
            )
        except Exception as exc:
            self._qt_log_sink_id = None
            logger.debug(f"注册界面日志输出失败，仅写入文件日志: {exc}")
        self._build_ui()
        from src.ui.controllers.dashboard_controller import initialize_dashboard
        initialize_dashboard(self)
        from src.app.page_tasks import start_ui_latency_monitor
        self._ui_latency_timer = start_ui_latency_monitor(self, lambda: self._nav_key_for_index(self.stack.currentIndex()))
        if self._ui_preferences["log_enabled"]:
            self._toggle_log(True)
        self._load_data()
        self.workshop_refresh_controller = WorkshopRefreshController(
            self.app_context.paths.workshop_weight_template_file,
            self.app_context.paths.equipment_allocation_database_path, parent=self,
        )
        self.workshop_refresh_controller.start()
        self.auto_sync_controller.start()
        self.work_mode_controller.start()
        self._on_log("系统就绪")
        self.onboarding_guide.maybe_show()
        self.work_mode_controller.start_upgrade_guidance()
        self.calc_update_controller.startup()

    def _load_static_catalog_inventory(self):
        """Freeze the current account projection for the equipment archive."""

        account = self.app_context.account
        static_path = self.app_context.paths.static_database_path
        service = WarehouseInventoryService(
            account.user_database_path,
            static_dao_factory=lambda: StaticGameDataDao(static_path),
        )
        return (
            account.active_account_id,
            self.app_context.generation,
            service.load_current_snapshot(),
        )

    # ── Frameless
    def _on_edge(self, pos):
        w, h = self.width(), self.height()
        m = self._resize_margin
        return (pos.x() < m, pos.y() < m, pos.x() > w - m, pos.y() > h - m)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()
            self._drag_edges = self._on_edge(e.position().toPoint())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        drag_edges = getattr(self, "_drag_edges", (False,) * 4)
        left_button_held = bool(e.buttons() & Qt.LeftButton)
        if not left_button_held:
            self._drag_pos = None
            self._drag_edges = (False,) * 4
            drag_edges = self._drag_edges
        if self._drag_pos is not None and left_button_held and any(drag_edges):
            d = e.globalPosition().toPoint() - self._drag_pos
            g = self.geometry()
            L, T, R, B = drag_edges
            if L:
                g.setLeft(g.left() + d.x())
            if T:
                g.setTop(g.top() + d.y())
            if R:
                g.setRight(g.right() + d.x())
            if B:
                g.setBottom(g.bottom() + d.y())
            self.setGeometry(
                g.normalized()
                if g.width() >= self.minimumWidth() and g.height() >= self.minimumHeight()
                else self.geometry()
            )
            self._drag_pos = e.globalPosition().toPoint()
        elif not any(drag_edges):
            pos = e.position().toPoint()
            E = self._on_edge(pos)
            if E[0] and E[1]:
                self.setCursor(Qt.SizeFDiagCursor)
            elif E[2] and E[3]:
                self.setCursor(Qt.SizeFDiagCursor)
            elif E[0] and E[3]:
                self.setCursor(Qt.SizeBDiagCursor)
            elif E[1] and E[2]:
                self.setCursor(Qt.SizeBDiagCursor)
            elif E[0] or E[2]:
                self.setCursor(Qt.SizeHorCursor)
            elif E[1] or E[3]:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = None
            self._drag_edges = (False,) * 4
        super().mouseReleaseEvent(e)

    def closeEvent(self, e):
        from src.ui.controllers.configuration_controller import defer_page_transition
        if defer_page_transition(self, self.close):
            e.ignore()
            return
        if wait_for_equipment_persistence(self, ("close",), self.close):
            e.ignore()
            return
        self.equipment_persistence_controller.close()
        # 草稿确认通过后、物理等待之前撤权；取消退出不永久关闭视觉 owner。
        self.scanning_controller.begin_close()
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
        if (close_page_tasks(self) or self.workshop_refresh_controller.is_running()
                or self.scanning_controller.is_running()
                or self.native_plugin_update_controller.is_running()
                or self.calc_update_controller.is_running()):
            e.ignore()
            QTimer.singleShot(25, self.close)
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
        super().closeEvent(e)

    def _tb_press(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()
            self._drag_edges = (False,) * 4

    def _tb_move(self, e):
        if self._drag_pos is not None and bool(e.buttons() & Qt.LeftButton):
            self.move(self.pos() + e.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = e.globalPosition().toPoint()

    def _tb_release(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = None
            self._drag_edges = (False,) * 4

    def _tb_dbl(self, e):
        if e.button() == Qt.LeftButton:
            self._toggle_max()

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # ── Build

    def _refresh_account_combo(self):
        if not hasattr(self, "account_combo"):
            return
        self.account_combo.blockSignals(True)
        populate_account_combo(
            self.account_combo,
            self._account_manager.read_index(),
            self.app_context.account.active_account_id,
        )
        self.account_combo.blockSignals(False)

    def _on_account_combo_changed(self, index):
        self.equipment_persistence_controller.cancel_transition()
        token = self._equipment_switch_token = object()
        if index < 0:
            return
        account_id = self.account_combo.itemData(index)
        if not account_id or account_id == self.app_context.account.active_account_id:
            return
        def resume():
            if self._equipment_switch_token is not token:
                return
            if not self._switch_account(account_id):
                self._refresh_account_combo()
                if self._equipment_switch_waiting:
                    wait_for_equipment_persistence(self, ("account", account_id), resume)
        resume()

    def _switch_account(self, account_id):
        self._equipment_switch_waiting = False
        from src.ui.controllers.configuration_controller import defer_page_transition
        if defer_page_transition(self, lambda: self._switch_account(account_id)):
            return False
        operation = OperationContext.create(
            "account",
            account_id=self.app_context.account.active_account_id,
            context_generation=self.app_context.generation,
        )
        log_event(
            "INFO",
            "account.switch_started",
            "开始切换账号",
            operation,
            target_account_id=account_id,
        )
        if self._equipment_assembly_is_running() or self.toolbox_page.is_rewind_running():
            QMessageBox.information(
                self,
                "输入任务运行中",
                "装配或倒带仍在使用本账号的数据和游戏状态。请停止任务并等待收尾，再切换账号。",
            )
            log_event(
                "WARNING",
                "account.switch_blocked",
                "装配或倒带运行及收尾期间阻止账号切换",
                operation,
                reason="equipment_input_running",
            )
            return False
        if (
            self.scanning_controller.is_running()
            or self.identification_controller.is_running()
        ):
            QMessageBox.information(
                self,
                "视觉任务运行中",
                "当前扫描或鉴定任务仍在使用本账号的截图与数据库。请等待任务结束或取消扫描后再切换账号。",
            )
            log_event(
                "WARNING",
                "account.switch_blocked",
                "视觉任务运行期间阻止账号切换",
                operation,
                reason="vision_worker_running",
            )
            return False
        if self.battle_report_controller.is_running():
            QMessageBox.information(
                self,
                "战报采集中",
                "当前战报仍在使用本账号和 nte-core 会话。请先结束战报采集，再切换账号。",
            )
            log_event(
                "WARNING",
                "account.switch_blocked",
                "战报采集期间阻止账号切换",
                operation,
                reason="battle_report_running",
            )
            return False
        data = self._account_manager.read_index()
        if not any(a.get("id") == account_id for a in data.get("accounts", [])):
            log_event(
                "ERROR",
                "account.switch_failed",
                "目标账号不存在",
                operation,
                target_account_id=account_id,
                reason="account_not_found",
            )
            return False
        # 维护取消先于账号 owner.stop；尚未物理退场时不推进索引或部分停用旧账号。
        if self.native_plugin_update_controller.is_running():
            self.native_plugin_update_controller.stop()
            QMessageBox.information(self, "正在等待插件更新退场", "本次更新正在收尾，账号保持不变；结束后请重试切换。")
            return False
        # 同步bool切换不排队；只由combo拥有可撤销continuation，索引尚未改变。
        if not self.equipment_persistence_controller.request_stop():
            self._equipment_switch_waiting = True
            return False
        self.equipment_persistence_controller.rebuild()
        try:
            target_account = self._account_manager.activate(account_id)
            with self.auto_sync_controller.account_switch():
                self.app_context.switch_account(
                    target_account, publish=lambda: self._account_manager.set_active_account_id(account_id),
                )
        except Exception as exc:
            if isinstance(exc, AccountSwitchError) and exc.recovery_failed:
                # 回滚 owner/页面仍有故障时停止自动监听并冻结交互，不在混合投影上继续操作。
                try:
                    self.work_mode_service.set_paused(True)  # 先撤权；配置落盘失败仍保持本次内存暂停。
                except Exception:
                    logger.error("账号恢复受阻，安全暂停配置保存失败；本次权限仍已撤销")
                try:
                    self.auto_sync_controller.close()
                except Exception:
                    logger.error("账号恢复受阻，自动监听仍在退场；新的副作用权限已撤销")
                self.setEnabled(False)
                QMessageBox.warning(self, "账号恢复待完成", str(exc))
                return exc.committed
            if not self.equipment_persistence_controller.is_running():
                self.equipment_persistence_controller.rebuild()
            committed = isinstance(exc, AccountSwitchError) and exc.committed
            QMessageBox.warning(self, "账号自动服务待恢复" if committed else "账号切换未完成", str(exc))
            return committed
        log_event("INFO", "account.switch_succeeded", "账号切换完成", operation,
                  target_account_id=account_id, new_context_generation=self.app_context.generation)
        if not (self._inventory_sync_service and self._inventory_sync_service.is_running):
            try:
                self._maybe_auto_start_inventory_sync()
            except Exception as exc:
                QMessageBox.warning(self, "账号自动服务待恢复", str(exc))
        return True

    def _on_app_context_account_changed(self, event: AccountChangedEvent):
        if self._log_enabled:
            disable_session_log(
                reason="account_switch",
                context={
                    "account_id": event.previous.active_account_id,
                    "context_generation": max(0, event.generation - 1),
                },
            )
        self._log_enabled = False
        set_log_dir(event.current.log_dir, reopen_session=False)
        self.native_game_session.close()
        self._account_settings = self.app_context.account_settings
        self._account_settings.migrate_legacy_settings()
        self._account_settings.remove_legacy_theme_preference()
        self._load_hotkey_config()
        self.global_hotkey_manager.update_configuration(
            capture_hotkey=self._hk_capture,
            finish_hotkey=self._hk_finish,
            stop_hotkey=self._hk_stop,
            battle_rerecord_hotkey=self._hk_battle_rerecord,
        )
        self._ui_preferences = self._load_ui_preferences()
        self._toggle_log(
            bool(self._ui_preferences["log_enabled"]),
            reason="account_switch",
        )
        refresh_account_scoped_settings(self)
        self.identification_controller.reset_account_state()
        self.battle_report_controller.reset_account_state()
        self.blueprint_page.reset_account_state()
        self.scanning_controller.reset_account_state()
        self.reset_equipment_account_state()
        self._load_data()
        active_page = self._nav_key_for_index(self.stack.currentIndex())
        if active_page not in {"home", "execute"}:
            self.refresh_current_account_page()
        self._refresh_account_combo()
        if hasattr(self, "_ss_info"):
            self._refresh_ss()
        self._refresh_home()

    def _manage_accounts(self):
        from src.ui.controllers.configuration_controller import defer_page_transition
        if defer_page_transition(self, self._manage_accounts):
            return
        if wait_for_equipment_persistence(self, ("manage",), self._manage_accounts):
            return
        with self.equipment_persistence_controller.management_hold():
            show_account_manager_dialog(
                self, self._current_style_sheet(), self._account_manager,
                self.app_context.account.active_account_id, self._switch_account, self._refresh_account_combo,
            )

def run_gui():
    """先确认启动权限，再准备账号，最后把明确依赖交给 Qt 入口。"""
    from src.ui.gui_startup import run_gui as start_gui
    _ensure_admin()
    with prepare_application_runtime() as runtime:
        start_gui(runtime.context, runtime.theme_settings, lambda: MainWindow(runtime), on_ready=runtime.accept_release)


if __name__ == "__main__":
    run_gui()
