# 管理主窗口日志会话、数据加载、首页、设置页和截图清理状态。
"""MainWindow data, logging and account-scoped page helpers."""
from __future__ import annotations
from pathlib import Path
from time import perf_counter
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox, QVBoxLayout, QWidget
from src.app.workers import WorkerThread
from src.app.constants import APP_VERSION, NETDISK_DOWNLOAD_LINKS
from src.app.theme import theme_color
from src.domain.stat_catalog import StatCatalog
from src.features.home.page import build_home_page, refresh_home_page
from src.features.scanning.file_lifecycle import (
    build_screenshot_cleanup_plan,
    execute_screenshot_cleanup,
    iter_image_files as _iter_image_files,
    managed_screenshot_usage,
)
from src.features.settings.page import build_settings_page
from src.optimizer.scoring import ScoringEngine
from src.observability import log_event
from src.observability.redaction import format_local_exception
from src.services.dashboard_service import DashboardService
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
from src.services.role_fork_template_service import (
    fork_templates_as_weapon_models,
    load_official_role_fork_templates,
)
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
    def _allocation_catalog_source_key(self):
        """Cheap identity check before rebuilding SQLite projections and role cards."""
        paths = self.app_context.paths
        user_db = self.app_context.account.user_database_path
        static_db = paths.equipment_allocation_database_path
        files = (
            paths.config_dir / "stats.json",
            paths.workshop_weight_template_file,
            user_db, Path(str(user_db) + "-wal"),
            static_db, Path(str(static_db) + "-wal"),
        )

        def stamp(path):
            try:
                info = path.stat()
            except OSError:
                return str(path), None
            return str(path), (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

        return self.app_context.generation, tuple(stamp(path) for path in files)

    @staticmethod
    def _read_allocation_catalog(config_dir, user_database_path, allocation_database_path, asset_root):
        """Read SQLite/config inputs without touching Qt widgets."""
        started = perf_counter()
        catalog = StatCatalog.from_config_dir(config_dir)
        stats_config = {
            "gold_base_values": catalog.gold_base_values,
            "tape_main_stats_pool": catalog.tape_main_stats,
            "tape_main_stat_values": catalog.tape_main_values,
            "tape_stat_values": catalog.tape_stat_values,
            "main_only_keywords": catalog.main_only_keywords,
            "stat_alias_mapping": catalog.stat_alias_mapping,
            "benefit_one": catalog.benefit_one,
            "benefit_alias_mapping": catalog.benefit_alias_mapping,
            "weight_pool": catalog.weight_pool,
        }
        weapons_db = fork_templates_as_weapon_models(
            load_official_role_fork_templates(allocation_database_path)
        )
        static_catalog = build_legacy_allocation_static_catalog(
            config_dir=config_dir,
            user_database_path=user_database_path,
            static_database_path=allocation_database_path,
        )
        roles_db = static_catalog.roles_db
        shape_areas = {
            shape_id: int(shape.area)
            for shape_id, shape in static_catalog.shapes_db.items()
        }
        scoring_engine = ScoringEngine(
            str(config_dir), user_database_path=user_database_path, roles_db=roles_db,
        )
        character_art = GameUiAssetCatalog(asset_root)
        character_icon_paths = {
            name: icon
            for name, role in roles_db.items()
            if isinstance(role, dict) and role.get("character_id") is not None
            if (icon := character_art.character_icon(int(role["character_id"]))) is not None
        }
        log_perf(logger, "allocation.catalog_read", elapsed_ms=(perf_counter() - started) * 1000)
        return (stats_config, catalog.tape_main_stats,
                list(catalog.gold_base_values.keys()), weapons_db, roles_db,
                static_catalog.sets_db, shape_areas, scoring_engine,
                character_icon_paths)

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
        self._update_inventory_status()
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

    def _load_data(self, reload_priority=True, *, on_finished=None):
        """Rebuild the allocation catalog without blocking the calling thread.

        账号切换与启动都由 GUI 线程调用这里；一次重建实测约 1.2 秒（会执行上千条
        SQL 与逐角色查询）。GUI 宿主统一走后台 worker，非 QWidget 宿主（测试替身）
        保持同步，避免依赖 Qt 事件循环。``on_finished`` 在目录加载结束（成功、失败
        或结果已过期）后于调用线程执行，用于把依赖已就绪目录的后续步骤排在后面。
        """

        source_key = self._allocation_catalog_source_key()
        if not isinstance(self, QWidget):
            self._read_and_apply_allocation_catalog(
                source_key, reload_priority=reload_priority,
            )
            self._run_catalog_finished(on_finished)
            return
        self._start_allocation_catalog_worker(
            source_key, reload_priority=reload_priority, on_finished=on_finished,
        )

    def _read_and_apply_allocation_catalog(self, source_key, *, reload_priority: bool) -> None:
        """Synchronous read+apply used by non-widget hosts and worker fallbacks."""

        self._allocation_catalog_request = object()
        self._allocation_catalog_pending_key = None
        try:
            config_dir = self.app_context.paths.config_dir
            user_database_path = self.app_context.account.user_database_path
            allocation_database_path = self.app_context.paths.equipment_allocation_database_path
            loaded = self._read_allocation_catalog(
                config_dir, user_database_path, allocation_database_path,
                self.app_context.paths.equipment_allocation_asset_root,
            )
            self._apply_allocation_catalog(
                loaded, reload_priority=reload_priority, source_key=source_key,
            )
        except Exception as e:
            logger.error(f"allocation.catalog_load_failed | {format_local_exception(e)}")

    def _start_allocation_catalog_worker(self, source_key, *, reload_priority, on_finished=None):
        """Read the catalog in a worker thread and apply it on the calling thread."""

        if on_finished is not None:
            queued = list(getattr(self, "_allocation_catalog_finished", ()))
            queued.append(on_finished)
            self._allocation_catalog_finished = queued
        if getattr(self, "_allocation_catalog_pending_key", None) == source_key:
            return  # 同一份输入已在读取；排队的回调会在它结束时统一触发。
        self._allocation_catalog_pending_key = source_key
        if hasattr(self, "btn_run"):
            self.btn_run.setEnabled(False)
            self.btn_run.setToolTip("正在更新计算数据，请稍候。")
        selector = getattr(getattr(self, "scanning_controller", None), "role_selector", None)
        if selector is not None and hasattr(selector, "setEnabled"):
            selector.setEnabled(False)
        request = object()
        self._allocation_catalog_request = request
        paths = self.app_context.paths
        user_db = self.app_context.account.user_database_path
        worker = WorkerThread(
            target=lambda: self._read_allocation_catalog(
                paths.config_dir, user_db,
                paths.equipment_allocation_database_path,
                paths.equipment_allocation_asset_root,
            ), parent=self,
        )
        self._allocation_catalog_worker = worker

        def apply(loaded):
            if request is not self._allocation_catalog_request:
                return
            self._allocation_catalog_pending_key = None
            if source_key != self._allocation_catalog_source_key():
                self._refresh_execute()
            else:
                self._apply_allocation_catalog(
                    loaded, reload_priority=reload_priority, source_key=source_key,
                )
            self._flush_catalog_finished()

        def failed(_error):
            if request is self._allocation_catalog_request:
                self._allocation_catalog_pending_key = None
                logger.warning("计算目录刷新失败；本次未应用新目录")
                if hasattr(self, "btn_run"):
                    self.btn_run.setEnabled(True)
                    self.btn_run.setToolTip("计算数据更新失败，请重新进入计算页重试。")
                self._flush_catalog_finished()

        worker.result_ready.connect(apply)
        worker.error.connect(failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _flush_catalog_finished(self) -> None:
        """Run and clear the queued 'catalog load settled' callbacks."""

        callbacks = list(getattr(self, "_allocation_catalog_finished", ()))
        self._allocation_catalog_finished = []
        for callback in callbacks:
            self._run_catalog_finished(callback)

    @staticmethod
    def _run_catalog_finished(callback) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            logger.warning(f"计算目录加载完成回调失败 | {exc}")

    def _finish_account_switch(self, operation, event) -> None:
        """Finish an account switch after the allocation catalog has settled.

        账号切换完成日志与页面刷新都必须晚于配装目录就绪；否则会向用户与日志宣称
        「已完成」，而执行页仍在使用旧目录。
        """

        if hasattr(self, "weighted_role_selector"):
            self._refresh_weighted_allocation()
        active_page = self._nav_key_for_index(self.stack.currentIndex())
        if active_page not in {"home", "execute"}:
            self.refresh_current_account_page()
        self._refresh_account_combo()
        if hasattr(self, "_ss_info"):
            self._refresh_ss()
        self._refresh_home()
        log_event(
            "INFO",
            "account.switch_succeeded",
            "账号切换完成",
            operation,
            target_account_id=event.current.active_account_id,
            new_context_generation=event.generation,
        )
        self._account_switch_operation = None

    def _refresh_execute(self):
        """Rebuild role cards only when their persisted sources have changed."""

        if getattr(self, "_allocation_catalog_loaded_key", None) != self._allocation_catalog_source_key():
            scanning = getattr(self, "scanning_controller", None)
            if scanning is not None and scanning.is_running():
                return  # The running calculation owns its frozen catalog.
            if not isinstance(self, QWidget):
                self._load_data(reload_priority=False)
                return
            self._start_allocation_catalog_worker(
                self._allocation_catalog_source_key(), reload_priority=False,
            )


    def _update_inventory_status(self):
        try:
            user_database_path = self.app_context.account.user_database_path
            if user_database_path.is_file():
                with UserDataDao(user_database_path) as dao:
                    summary = dao.current_inventory_summary()
                if summary is not None:
                    count = int(summary["stored_item_count"])
                    self.status_lbl.setText(f"稳定背包 {count} 件")
                    self.status_lbl.setStyleSheet("color:#3fb950;font-size:12px")
                    return
        except Exception as exc:
            logger.debug(f"读取 SQLite 背包状态失败: {exc}")
        self.status_lbl.setText("库存为空")
        self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")

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
        if not hasattr(self, "home_account_label"):
            return
        try:
            dashboard = DashboardService(
                self.app_context.account.user_database_path
            ).load()
            refresh_home_page(self, dashboard)
        except Exception as exc:
            self.home_account_label.setText(f"工作台数据暂时不可用：{exc}")
            logger.warning(f"刷新 2.0 工作台失败: {exc}")

    def _page_settings(self):
        return build_settings_page(
            self,
            APP_VERSION,
            self.app_context,
            _iter_image_files,
            NETDISK_DOWNLOAD_LINKS,
        )

    def _page_plugins(self):
        from src.features.plugins.page import PluginsPage
        self.plugins_page = PluginsPage(
            service=self.plugin_service, request_apply=self.work_mode_controller.refresh_plugins,
            open_settings=self.work_mode_controller.open_settings, parent=self,
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
