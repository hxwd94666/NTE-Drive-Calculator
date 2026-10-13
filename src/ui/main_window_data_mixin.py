# 管理主窗口日志会话、数据加载、首页、设置页和截图清理状态。
"""MainWindow data, logging and account-scoped page helpers."""
from __future__ import annotations
from time import perf_counter
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox, QVBoxLayout
from src.app.constants import APP_VERSION, NETDISK_DOWNLOAD_LINKS
from src.app.theme import theme_color
from src.features.home.page import build_home_page
from src.features.scanning.file_lifecycle import (
    build_screenshot_cleanup_plan,
    execute_screenshot_cleanup,
    iter_image_files as _iter_image_files,
    managed_screenshot_usage,
)
from src.features.settings.page import build_settings_page
from src.services.allocation.catalog import read_allocation_catalog
from src.ui.controllers.allocation_catalog_controller import allocation_catalog_controller
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import (
    disable_session_log,
    enable_session_log,
    logger,
    session_log_path,
)
from src.utils.perf import log_perf

class MainWindowDataMixin:
    def _on_log(self, msg):
        if not self._log_enabled:
            return
        c = theme_color("#8b949e")
        if any(k in msg for k in ("ERROR", "error", "失败", "崩溃")):
            c = "#f85149"
        elif any(k in msg for k in ("WARNING", "warning", "警告")):
            c = "#d2991d"
        elif any(k in msg for k in ("SUCCESS", "完成", "完毕")):
            c = "#3fb950"
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.setTextColor(QColor(c))
        self.log_view.insertPlainText(msg + "\n")
        self.log_view.moveCursor(QTextCursor.End)

    def _clear_log(self):
        self.log_view.clear()

    def _current_log_context(self):
        return {
            "account_id": self.app_context.account.active_account_id,
            "context_generation": self.app_context.generation,
        }

    def _refresh_log_session_status(self):
        label = getattr(self, "_log_session_status_label", None)
        if label is None:
            return
        path = session_log_path()
        if self._log_enabled and path is not None:
            label.setText(f"详细日志：{path.name}")
            label.setToolTip(str(path))
            return
        label.setText("详细日志：未启用")
        label.setToolTip("")

    def _toggle_log(self, enabled, *, reason="settings"):
        toggle = getattr(self, "_log_toggle", None)
        if toggle is not None and toggle.isChecked() != bool(enabled):
            toggle.blockSignals(True)
            toggle.setChecked(bool(enabled))
            toggle.blockSignals(False)
        if enabled:
            try:
                log_path = enable_session_log(context=self._current_log_context())
            except Exception as exc:
                logger.error(f"创建运行日志文件失败: {exc}")
                if toggle is not None:
                    toggle.blockSignals(True)
                    toggle.setChecked(False)
                    toggle.blockSignals(False)
                self._ui_preferences["log_enabled"] = False
                self._save_ui_preferences()
                QMessageBox.warning(self, "运行日志", "无法创建运行日志文件，请检查日志目录是否可写")
                self._refresh_log_session_status()
                return
            self._log_enabled = True
            self._ui_preferences["log_enabled"] = True
            self._save_ui_preferences()
            self.log_frame.setVisible(True)
            logger.info(
                "logging.ui_enabled | 设置已启用详细运行日志"
                f" | file={log_path.name} reason={reason}"
            )
            self._refresh_log_session_status()
            return
        disable_session_log(
            reason=reason,
            context=self._current_log_context(),
        )
        self._log_enabled = False
        self._ui_preferences["log_enabled"] = False
        self._save_ui_preferences()
        self.log_frame.setVisible(False)
        self.log_view.clear()
        self.log_view.insertPlainText("(日志已关闭)\n")
        self._refresh_log_session_status()

    # ── Data
    def on_configuration_changed(self):
        """Invalidate readers after a commit; never reload synchronously."""
        controller = self._catalog_controller()
        controller.invalidate()
        if hasattr(self, "stack") and self._nav_key_for_index(self.stack.currentIndex()) == "execute":
            self._refresh_execute()
        self._refresh_home()

    def _catalog_controller(self):
        return allocation_catalog_controller(self)

    @staticmethod
    def _read_allocation_catalog(config_dir, user_database_path, allocation_database_path, asset_root):
        return read_allocation_catalog(config_dir, user_database_path, allocation_database_path, asset_root)

    def _apply_allocation_catalog(self, loaded, *, reload_priority, source_key):
        started = perf_counter()
        (stats_config, tape_main_stats, drive_sub_stats, weapons_db, roles_db,
         sets_db, shape_areas, scoring_engine, icon_paths) = loaded
        catalog_unchanged = (
            not reload_priority
            and getattr(self, "roles_db", None) == roles_db
            and getattr(self, "sets_db", None) == sets_db
            and getattr(self, "_shape_areas", None) == shape_areas
            and getattr(self, "stats_config", None) == stats_config
        )
        role_cards_unchanged = (
            catalog_unchanged
            and getattr(self, "tape_main_stats", None) == tape_main_stats
            and getattr(self, "drive_sub_stats", None) == drive_sub_stats
            and getattr(self, "weapons_db", None) == weapons_db
            and getattr(self, "_allocation_icon_paths", None) == icon_paths
        )
        self.stats_config = stats_config
        self.tape_main_stats = tape_main_stats
        self.drive_sub_stats = drive_sub_stats
        self.weapons_db = weapons_db
        self.roles_db = roles_db
        self.sets_db = sets_db
        self.all_set_names = list(sets_db)
        self._shape_areas = shape_areas
        if reload_priority:
            self.equipped_state = {}
        self.scoring_engine = (
            getattr(self, "scoring_engine", scoring_engine)
            if catalog_unchanged else scoring_engine
        )
        self._allocation_icon_paths = icon_paths
        if not role_cards_unchanged:
            self.scanning_controller.role_selector.load_roles(
                roles_db, self.all_set_names, tape_main_stats, drive_sub_stats,
                weapons_db=weapons_db, character_icon_paths=icon_paths,
            )
        if reload_priority:
            self.scanning_controller.role_selector.load_startup_priority_config()
        if not catalog_unchanged:
            self.equipment_presentation.update_catalog(
                roles_db=roles_db, scoring_engine=self.scoring_engine,
                shape_areas=shape_areas, stats_config=stats_config,
            )
            self.identification_controller.update_catalog(
                shape_areas=shape_areas, set_names=self.all_set_names,
                scoring_engine=self.scoring_engine,
            )
        self._allocation_catalog_loaded_key = source_key
        if hasattr(self, "btn_run"):
            self.btn_run.setEnabled(not self.scanning_controller.is_running())
            self.btn_run.setToolTip("")
        selector = self.scanning_controller.role_selector
        if hasattr(selector, "setEnabled"):
            selector.setEnabled(True)
        logger.info(f"已从 SQLite 加载 {len(roles_db)} 角色，{len(sets_db)} 套装")
        log_perf(
            logger, "allocation.catalog_apply",
            elapsed_ms=(perf_counter() - started) * 1000,
            cards_rebuilt=not role_cards_unchanged,
        )

    def _load_data(self, reload_priority=True):
        if reload_priority:
            # 先撤销旧草稿的保存资格，再派发目录读取；取消/退出不覆盖新账号未读存档。
            self.scanning_controller.role_selector.prepare_priority_account()
        self._catalog_controller().refresh(reload_priority=reload_priority)

    def set_allocation_catalog_ready(self, ready):
        """Account replacement keeps dependent pages inert until their catalog is current."""
        was_ready = getattr(self, "_allocation_catalog_ready", False)
        self._allocation_catalog_ready = ready
        if ready and was_ready:
            return
        if not hasattr(self, "stack"):
            return
        from src.ui.navigation import nav_index_map
        previous = getattr(self, "_catalog_page_enabled_state", {})
        for key in ("equipment", "identify", "warehouse"):
            index = nav_index_map().get(key)
            page = self.stack.widget(index) if index is not None else None
            if page is not None:
                if not ready and key not in previous:
                    previous[key] = page.isEnabled()
                page.setEnabled(previous.get(key, True) if ready else False)
                page.setToolTip("" if ready else "正在准备当前账号的计算资料，请稍候。")
        self._catalog_page_enabled_state = {} if ready else previous
        if ready and not was_ready and self._nav_key_for_index(self.stack.currentIndex()) in {"equipment", "identify", "warehouse"}:
            self.refresh_current_account_page()

    def _refresh_execute(self):
        self._catalog_controller().refresh()

    def prepare_calculation(self, continuation):
        self._catalog_controller().refresh(continuation=continuation, verify=True)

    def _update_inventory_status(self):
        try:
            user_database_path = self.app_context.account.user_database_path
            if user_database_path.is_file():
                with UserDataDao(user_database_path) as dao:
                    summary = dao.current_inventory_summary()
                self._apply_inventory_status(summary)
                return
        except Exception as exc:
            logger.debug(f"读取 SQLite 背包状态失败: {exc}")
        self.status_lbl.setText("库存为空")
        self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")

    def _apply_inventory_status(self, summary):
        if hasattr(self, "auto_sync_controller"):
            return  # The top-bar status belongs to the live synchronization owner.
        if summary is None:
            self.status_lbl.setText("库存为空")
            self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")
        else:
            self.status_lbl.setText(f"稳定背包 {int(summary['stored_item_count'])} 件")
            self.status_lbl.setStyleSheet("color:#3fb950;font-size:12px")

    def _card(self, title):
        c = QFrame()
        c.setObjectName("card")
        l = QVBoxLayout(c)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(8)
        lb = QLabel(title)
        lb.setObjectName("cardTitle")
        l.addWidget(lb)
        return c

    # ── Page: Home / 2.0 Dashboard
    def _page_home(self):
        return build_home_page(self)

    def _refresh_home(self):
        if hasattr(self, "dashboard_controller"):
            self.dashboard_controller.refresh()

    def _page_settings(self):
        return build_settings_page(
            self,
            APP_VERSION,
            self.app_context,
            _iter_image_files,
            NETDISK_DOWNLOAD_LINKS,
            language_settings=self._language_settings,
        )

    def _page_plugins(self):
        from src.features.plugins.page import PluginsPage
        self.plugins_page = PluginsPage(
            service=self.plugin_service, request_apply=self.work_mode_controller.refresh_plugins,
            show_detection=lambda: self.work_mode_controller.check(show=True, retry_deployment=True), parent=self,
        )
        self.work_mode_controller.observed.connect(self.plugins_page.refresh)
        self.work_mode_controller.plugins_applied.connect(self.plugins_page.refresh)
        return self.plugins_page

    def _refresh_plugins(self):
        self.plugins_page.refresh()

    def _refresh_ss(self):
        account = self.app_context.account
        usage = managed_screenshot_usage(
            account.screenshot_dir,
            account.account_data_root,
        )
        self._ss_info.setText(f"当前截图: {usage.count} 个 · {usage.size_mb:.1f} MB")

    def _clear_ss(self):
        account = self.app_context.account
        plan = build_screenshot_cleanup_plan(
            account.screenshot_dir,
            account.account_data_root,
        )
        if plan.total_count == 0:
            QMessageBox.information(self, "清理", "没有需要清理的文件。")
            return
        if (
            QMessageBox.question(
                self, "确认清理", plan.confirmation_text(), QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            == QMessageBox.Yes
        ):
            result = execute_screenshot_cleanup(plan)
            self._refresh_ss()
            logger.success(f"已清理 {result.deleted} 个截图")
            if result.failed_files:
                QMessageBox.warning(self, "清理完成", f"有 {len(result.failed_files)} 个文件删除失败，可能正在被占用。")
            if plan.baseline_missing:
                QMessageBox.warning(
                    self, "清理完成", "注意：丢失用于对比的截图，请重新全量扫描，或不要使用全自动增量扫描。"
                )
