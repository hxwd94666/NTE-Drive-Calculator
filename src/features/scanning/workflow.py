# 编排扫描、解析、文件生命周期与进度回调。
"""Scanning workflow implementation used by ScanningController."""


from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from src.app.constants import DRONE_HELP, OFFLINE_HELP, SCAN_HELP
from src.app.dialogs import show_help
from src.app.theme import current_style_sheet
from src.app.workers import GamepadScanParseWorkerThread, ScanWorkerThread
from src.features.allocation.execute_page import build_execute_page
from src.features.allocation.preference_modes import role_preference_mode_error
from src.features.allocation.role_selector import RoleSelector
from src.features.scanning.dependencies import ScanningDependencies
from src.features.scanning.manual_recovery import complete_pending_manual_items
from src.features.scanning.operation_logging import (
    begin_scan_operation as _begin_scan_operation,
    scan_event as _scan_event,
)
from src.features.scanning.post_action_dialog import load_scan_post_action_config, show_scan_post_action_dialog
from src.domain.post_actions import post_actions_enabled, validate_post_action_config
from src.features.scanning.vision_worker import VisionWorkerThread
from src.services.vision_inventory_snapshot import import_vision_inventory
from src.utils.logger import logger

def _current_scanning_dependencies(self) -> ScanningDependencies:
    return ScanningDependencies.from_app_context(self.app_context)


def _task_scanning_dependencies(self) -> ScanningDependencies:
    dependencies = getattr(self, "_scan_dependencies", None)
    return dependencies or _current_scanning_dependencies(self)


def _scanning_is_running(self) -> bool:
    for name in ("_scan_worker", "_gamepad_worker", "_vision_worker"):
        worker = getattr(self, name, None)
        if worker is not None and callable(getattr(worker, "isRunning", None)):
            if worker.isRunning():
                return True
    return False


def offline_scope_replaces_inventory(scope: str) -> bool:
    return scope in ("full", "all")


def vision_cancel_message(parsed_count: int) -> str:
    return (
        f"已停止继续解析，本次已解析 {int(parsed_count or 0)} 张截图。\n\n"
        "由于解析任务已取消，本次结果未写入/更新 SQLite 背包快照。"
    )


def _page_execute(self):
    return build_execute_page(
        self,
        lambda: RoleSelector(
            priority_config_path_provider=lambda: (
                _current_scanning_dependencies(self).user_config_dir / "priority_config.json"
            ),
            style_sheet=current_style_sheet(),
            help_callback=show_help,
        ),
        SCAN_HELP,
        DRONE_HELP,
        OFFLINE_HELP,
        show_help,
    )


def _on_scan_change(self, id):
    if hasattr(self, "offline_frame"):
        self.offline_frame.setVisible(id == 3)
    self.total_count_frame.setVisible(id == 1)
    if hasattr(self, "scan_dual_thread_frame"):
        self.scan_dual_thread_frame.setVisible(id == 1)
    self.drone_frame.setVisible(id == 2)


def _on_priority_changed(self):
    pass


def _open_scan_post_action_manager(self):
    selected = self.role_selector.get_selected() if hasattr(self, "role_selector") else []
    dependencies = _current_scanning_dependencies(self)
    show_scan_post_action_dialog(
        self.dialog_parent,
        dependencies.user_config_dir,
        dependencies.config_dir,
        selected,
    )


def _do_exec(self):
    dependencies = _current_scanning_dependencies(self)
    sel = self.role_selector.get_selected()
    sm = str(self.scan_group.checkedId())
    parse_only = not sel and sm in ("1", "2", "3")
    if not sel and not parse_only:
        QMessageBox.warning(self.dialog_parent, "提示", "请先选择目标角色！")
        return
    total_drives = None
    if sm == "1":
        raw_count = self.total_count_edit.text().strip()
        if not raw_count:
            QMessageBox.warning(
                self.dialog_parent,
                "提示",
                "全量扫描前请先填写库存数量。",
            )
            return
        total_drives = int(raw_count)
        if not 0 < total_drives <= 2000:
            QMessageBox.warning(
                self.dialog_parent,
                "提示",
                "库存数量必须在 1-2000 之间。",
            )
            return
    if parse_only:
        QMessageBox.information(
            self.dialog_parent,
            "仅生成库存数据",
            "当前未选择任何角色，本次扫描解析只会写入 SQLite 背包快照，不会进行配装计算。",
        )
    offline_scope = None
    if sm == "3":
        checked = self.offline_group.checkedButton() if hasattr(self, "offline_group") else None
        offline_scope = checked.property("offline_key") if checked else "incremental"
        if offline_scope == "all":
            ret = QMessageBox.warning(
                self.dialog_parent,
                "全部截图解析",
                "全部截图解析会读取文件夹根目录下所有截图，可能导致旧截图重复写入库存。\n\n如若产生库存异常，请重新全量扫描。\n\n确定继续吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
    pending_drone_mode = None
    if sm == "2":
        pending_drone_mode = "auto" if self.drone_group.checkedId() == 1 else "semi"
        if pending_drone_mode == "auto" and not (dependencies.screenshot_dir / "raw_drive_0001.png").exists():
            QMessageBox.warning(
                self.dialog_parent,
                "需要重新全量扫描",
                "由于版本更新解析逻辑变动，需要重新进行全量扫描",
            )
            return
    strat = ["role_priority", "global_optimal", "update_mode"][max(0, min(2, self.strategy_group.checkedId()))]
    cs = self.role_selector.get_custom_sets()
    cw = self.role_selector.get_custom_weapons() if hasattr(self.role_selector, "get_custom_weapons") else {}
    tmf = self.role_selector.get_tape_main_filters()
    cpm = self.role_selector.get_crit_priority_modes()
    crc = self.role_selector.get_crit_rate_caps()
    sem = self.role_selector.get_set_effect_modes()
    pg = self.role_selector.get_priority_groups() if hasattr(self.role_selector, "get_priority_groups") else None
    preference_error = role_preference_mode_error(strat, tmf, cpm, crc)
    if preference_error:
        QMessageBox.warning(
            self.dialog_parent,
            "词条自选不可用",
            preference_error,
        )
        return
    if not parse_only and not self._confirm_unsaved_allocation_before_recompute():
        return
    post_actions_config = None
    if sm == "1":
        post_actions_config = load_scan_post_action_config(dependencies.user_config_dir)
        post_action_error = validate_post_action_config(post_actions_config, sel)
        if post_action_error:
            QMessageBox.warning(
                self.dialog_parent,
                "扫描后管理配置无效",
                post_action_error,
            )
            return
    self.btn_run.setEnabled(False)
    self.btn_run.setText("⏳ 计算中...")
    self.result_card.setVisible(False)
    self._pending_strat = strat
    self._pending_sel = sel
    self._pending_cs = cs
    self._pending_custom_weapons = cw
    self._pending_tape_main_filters = tmf
    self._pending_crit_priority_modes = cpm
    self._pending_crit_rate_caps = crc
    self._pending_set_effect_modes = sem
    self._pending_priority_groups = pg
    self._pending_archive_paths = []
    self._pending_parse_only = parse_only

    if sm == "3":
        scope = {"full": "full", "incremental": "incremental", "all": "all"}.get(offline_scope, "incremental")
        self._start_vision_processing(replace_output=offline_scope_replaces_inventory(scope), parse_scope=scope)
    elif sm == "2":
        drone_mode = pending_drone_mode or ("auto" if self.drone_group.checkedId() == 1 else "semi")
        self._start_scan(drone_mode)
    elif sm == "1":
        parse_during_scan = True
        if hasattr(self, "scan_dual_thread_check"):
            parse_during_scan = bool(self.scan_dual_thread_check.isChecked())
        amd_compatibility = False
        if hasattr(self, "scan_amd_compat_check"):
            amd_compatibility = bool(self.scan_amd_compat_check.isChecked())
        if amd_compatibility:
            parse_during_scan = False
        self._start_gamepad_scan(
            total_drives,
            post_actions_config=post_actions_config,
            selected_roles=sel,
            parse_during_scan=parse_during_scan,
            amd_compatibility=amd_compatibility,
        )
    else:
        self._allocation_controller.start(
            strategy=strat,
            selected_roles=sel,
            custom_sets=cs,
            tape_main_filters=tmf,
            crit_priority_modes=cpm,
            set_effect_modes=sem,
            priority_groups=pg,
            crit_rate_caps=crc,
            custom_weapons=cw,
        )


def _start_vision_processing(self, replace_output=False, parse_scope="all"):
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    if not getattr(self, "_scan_operation_active", False):
        _begin_scan_operation(
            self,
            dependencies,
            route="offline_parse",
            parse_scope=parse_scope,
        )
    input_dir = str(dependencies.screenshot_dir)
    self._pending_archive_paths = []
    self._pending_parse_scope = parse_scope
    skip_names = self._prepare_incremental_parse(parse_scope)
    if skip_names is None:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  开始计算")
        self._pending_parse_only = False
        return
    matching_files = self._matching_scope_files(parse_scope, skip_names)
    if not matching_files:
        deleted = self._delete_paths(getattr(self, "_pending_delete_after_parse", []) or [])
        self._pending_delete_after_parse = []
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  开始计算")
        self._pending_parse_only = False
        self._update_inventory_status()
        _scan_event(
            self,
            "INFO",
            "scanning.succeeded",
            "扫描解析完成",
            success_count=0,
            failed_count=0,
            duplicate_count=deleted,
            snapshot_written=False,
        )
        QMessageBox.information(
            self.dialog_parent,
            "解析完成",
            f"解析成功 0 张，解析失败 0 张，过滤重复 {deleted} 张。",
        )
        return
    self._vision_worker = VisionWorkerThread(
        input_dir,
        self,
        replace_output=replace_output,
        parse_scope=parse_scope,
        skip_names=skip_names,
        config_dir=str(dependencies.config_dir),
    )
    self._progress_dlg = QProgressDialog(
        "正在解析截图...",
        "取消",
        0,
        100,
        self.dialog_parent,
    )
    self._progress_dlg.setWindowTitle("截图解析进度")
    self._progress_dlg.setMinimumWidth(400)
    self._progress_dlg.setAutoClose(False)
    self._progress_dlg.setAutoReset(False)
    self._progress_dlg.canceled.connect(self._on_vision_cancel)
    self._progress_dlg.show()
    self._vision_worker.progress.connect(self._on_vision_progress)
    self._vision_worker.processing_done.connect(self._on_vision_done)
    self._vision_worker.canceled.connect(self._on_vision_canceled)
    self._vision_worker.error.connect(self._on_vision_error)
    self._vision_worker.start()


def _on_vision_progress(self, current, total, filename):
    self._progress_dlg.setMaximum(total)
    self._progress_dlg.setValue(current)
    self._progress_dlg.setLabelText(f"正在解析 ({current}/{total}): {filename}")


def _on_vision_done(self, stats):
    dependencies = _task_scanning_dependencies(self)
    stats = stats or {}
    self._pending_archive_paths = []
    logger.info("视觉解析线程完成，准备启动分配计算...")
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    vision_worker = getattr(self, "_vision_worker", None)
    if vision_worker is not None and vision_worker.isRunning():
        vision_worker.wait(5000)
    post = self._postprocess_vision_files(stats)
    manual_items = []
    pending_manual_count = int(stats.get("pending_manual_count", 0) or 0)
    if pending_manual_count:
        try:
            manual_result = complete_pending_manual_items(
                self.dialog_parent,
                stats,
                dependencies.config_dir,
            )
            if manual_result is None:
                _scan_event(
                    self,
                    "WARNING",
                    "scanning.cancelled",
                    "用户取消扫描补录",
                    pending_manual_count=pending_manual_count,
                )
                QMessageBox.information(
                    self.dialog_parent,
                    "补录已取消",
                    "本次全量视觉扫描未写入 SQLite 背包快照。",
                )
                return
            manual_items = manual_result
        except Exception as exc:
            logger.error(f"补录待识别装备失败: {exc}")
            _scan_event(
                self,
                "ERROR",
                "scanning.failed",
                "扫描补录失败",
                stage="manual_recovery",
                error=exc,
            )
            QMessageBox.warning(
                self.dialog_parent,
                "补录失败",
                f"本次扫描未写入 SQLite 背包快照：{exc}",
            )
            return
    success_count = int(stats.get("success_count", 0) or 0)
    failed_count = int(stats.get("failed_count", 0) or 0)
    duplicate_count = int(stats.get("duplicate_count", 0) or 0) + int(post.get("probe_duplicates", 0) or 0)
    summary = f"解析成功 {success_count} 张，解析失败 {failed_count} 张，过滤重复 {duplicate_count} 张。"
    vision_snapshot_id = None
    vision_items = list(stats.get("vision_items") or [])
    if vision_items and str(stats.get("parse_scope") or "") in {"full", "all"}:
        try:
            vision_snapshot_id = import_vision_inventory(
                dependencies.user_database_path,
                [*vision_items, *manual_items],
            )
        except Exception as exc:
            logger.error(f"视觉扫描 SQLite 快照写入失败: {exc}")
            _scan_event(
                self,
                "ERROR",
                "scanning.failed",
                "视觉库存快照写入失败",
                stage="snapshot_commit",
                error=exc,
            )
            QMessageBox.warning(
                self.dialog_parent,
                "库存写入失败",
                f"本次扫描未写入 SQLite 背包快照：{exc}",
            )
            return
    if isinstance(vision_snapshot_id, int) and vision_snapshot_id > 0:
        summary += f"\n已写入视觉扫描库存快照 #{vision_snapshot_id}；没有抓包快照时可用于计算和自动装配。"
        refresh_home = getattr(self, "_refresh_home", None)
        if callable(refresh_home):
            refresh_home()
    if pending_manual_count:
        summary += (
            f"\n待补录 {pending_manual_count} 件，已补录 {len(manual_items)} 件并与本次识别结果共同写入 SQLite 快照。"
        )
    _scan_event(
        self,
        "INFO",
        "scanning.succeeded",
        "扫描解析完成",
        parse_scope=stats.get("parse_scope"),
        success_count=success_count,
        failed_count=failed_count,
        duplicate_count=duplicate_count,
        pending_manual_count=pending_manual_count,
        recovered_manual_count=len(manual_items),
        snapshot_id=vision_snapshot_id,
        snapshot_written=bool(vision_snapshot_id),
    )
    if stats.get("post_actions_enabled"):
        summary += (
            "\n扫描后管理："
            f"参与计算 {int(stats.get('post_action_candidate_count', 0) or 0)} 件，"
            f"目标变更 {int(stats.get('post_action_target_count', 0) or 0)} 个，"
            f"已处理 {int(stats.get('post_action_applied_count', 0) or 0)} 个。"
            f"\n弃置 {int(stats.get('discard_set_count', 0) or 0)} 个，"
            f"取消弃置 {int(stats.get('discard_clear_count', 0) or 0)} 个；"
            f"锁定 {int(stats.get('lock_set_count', 0) or 0)} 个，"
            f"取消锁定 {int(stats.get('lock_clear_count', 0) or 0)} 个。"
        )
        filtered_parts = []
        if int(stats.get("post_action_quality_filtered_count", 0) or 0):
            filtered_parts.append(f"品质范围过滤 {int(stats.get('post_action_quality_filtered_count', 0) or 0)} 件")
        if int(stats.get("post_action_type_filtered_count", 0) or 0):
            filtered_parts.append(f"处理类别过滤 {int(stats.get('post_action_type_filtered_count', 0) or 0)} 件")
        if int(stats.get("post_action_type_range_filtered_count", 0) or 0):
            filtered_parts.append(f"类型范围过滤 {int(stats.get('post_action_type_range_filtered_count', 0) or 0)} 件")
        if filtered_parts:
            summary += "\n" + "，".join(filtered_parts) + "。"
    details = []
    if post.get("moved_failed"):
        details.append(f"失败截图已移动到 failed 文件夹 {post['moved_failed']} 张。")
    if post.get("renamed"):
        details.append(f"增量截图已改名接入全量序列 {post['renamed']} 张。")
    if details:
        summary += "\n" + "\n".join(details)
    if getattr(self, "_pending_parse_only", False):
        self._pending_archive_paths = []
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  开始计算")
        self._update_inventory_status()
        QMessageBox.information(
            self.dialog_parent,
            "库存数据已生成",
            summary + "\n\n本次未配置角色优先级，已仅生成/更新 SQLite 背包快照，未进行配装计算。",
        )
        self._pending_parse_only = False
        return
    from PySide6.QtCore import QTimer

    QMessageBox.information(self.dialog_parent, "截图解析完成", summary)
    QTimer.singleShot(100, self._start_allocation_worker)


def _on_vision_error(self, err):
    _scan_event(
        self,
        "ERROR",
        "scanning.failed",
        "截图解析失败",
        stage="vision_parse",
        error=err,
    )
    self._progress_dlg.close()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  开始计算")
    self._pending_parse_only = False
    QMessageBox.critical(
        self.dialog_parent,
        "解析失败",
        f"截图解析出错:\n{err}",
    )


def _on_vision_cancel(self):
    vision_worker = getattr(self, "_vision_worker", None)
    if vision_worker is not None and vision_worker.isRunning():
        vision_worker.request_cancel()
        self._progress_dlg.setCancelButton(None)
        self._progress_dlg.setLabelText("正在取消解析，等待当前截图处理完成...")
        return
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  开始计算")


def _on_vision_canceled(self, count):
    _scan_event(
        self,
        "WARNING",
        "scanning.cancelled",
        "用户取消截图解析",
        parsed_count=int(count or 0),
    )
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("开始计算")
    self._pending_parse_only = False
    QMessageBox.information(
        self.dialog_parent,
        "解析已取消",
        vision_cancel_message(count),
    )


def _start_scan(self, drone_mode):
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    _begin_scan_operation(self, dependencies, route=str(drone_mode))
    self._pending_scan_mode = drone_mode
    self.showMinimized()
    self._scan_worker = ScanWorkerThread(
        output_dir=dependencies.screenshot_dir,
        template_path=dependencies.template_dir / "new_tag.png",
        mode=drone_mode,
        parent=self,
    )
    self._scan_worker.scan_done.connect(self._on_scan_done)
    self._scan_worker.error.connect(self._on_scan_error)
    self._start_scan_hotkeys(drone_mode)
    self.btn_run.setText("⏳  扫描中... (F12 停止)")
    self._scan_worker.start()


def _start_gamepad_scan(
    self, total_drives, post_actions_config=None, selected_roles=None, parse_during_scan=True, amd_compatibility=False
):
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    self._replace_inventory_on_next_parse = True
    self._pending_scan_mode = "gamepad"
    self._pending_parse_scope = "full"
    self._pending_delete_after_parse = []
    self._pending_probe_duplicate_count = 0
    self._gamepad_parse_progress = (0, total_drives, "")
    self._gamepad_pipeline_finished = False
    self._gamepad_post_actions_enabled = bool(post_actions_enabled(post_actions_config))
    self._gamepad_suppress_parse_ui = False
    action_hint = ""
    if self._gamepad_post_actions_enabled:
        action_hint = (
            "\n\n已启用扫描后管理：扫描解析后会继续计算并同步弃置/锁定状态。"
            "\n扫描开始后不要切换排序、筛选、滚动或手动操作背包。"
        )
    ret = QMessageBox.question(
        self.dialog_parent,
        "全量扫描准备",
        "点击“确定”后程序会最小化并准备开始全量扫描。\n\n"
        "请切换至游戏的驱动仓库页面，并确保当前选中第一排第一个驱动。\n"
        "程序会在短暂倒计时后接管虚拟手柄进行遍历截图。" + action_hint,
        QMessageBox.Ok | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if ret != QMessageBox.Ok:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  开始计算")
        self._replace_inventory_on_next_parse = False
        self._pending_scan_mode = None
        self._gamepad_post_actions_enabled = False
        self._gamepad_suppress_parse_ui = False
        return
    _begin_scan_operation(
        self,
        dependencies,
        route="gamepad",
        expected_capture_count=int(total_drives or 0),
        parse_during_scan=bool(parse_during_scan),
        post_actions_enabled=bool(self._gamepad_post_actions_enabled),
    )
    self.showMinimized()
    self._gamepad_worker = GamepadScanParseWorkerThread(
        total_drives=total_drives,
        screenshot_dir=dependencies.screenshot_dir,
        config_dir=dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
        parent=self,
        post_actions_config=post_actions_config,
        selected_roles=selected_roles,
        parse_during_scan=parse_during_scan,
        amd_compatibility=amd_compatibility,
    )
    self._gamepad_worker.scan_done.connect(self._on_gamepad_scan_done)
    self._gamepad_worker.progress.connect(self._on_gamepad_parse_progress)
    self._gamepad_worker.parse_done.connect(self._on_gamepad_parse_done)
    self._gamepad_worker.post_actions_ready.connect(self._on_gamepad_post_actions_ready)
    self._gamepad_worker.processing_done.connect(self._on_gamepad_pipeline_done)
    self._gamepad_worker.error.connect(self._on_gamepad_error)
    self._start_scan_hotkeys("gamepad")
    self.btn_run.setText("⏳  手柄扫描/解析中... (F12 停止)")
    self._gamepad_worker.start()


def _on_gamepad_scan_done(self, captured, total):
    if getattr(self, "_gamepad_pipeline_finished", False):
        return
    if getattr(self, "_gamepad_suppress_parse_ui", False):
        return
    self.showNormal()
    self.activateWindow()
    current, progress_total, filename = getattr(self, "_gamepad_parse_progress", (0, total, ""))
    progress_total = max(int(progress_total or 0), int(total or 0), int(captured or 0), 1)
    dlg = getattr(self, "_progress_dlg", None)
    if dlg and dlg.isVisible():
        self._on_gamepad_parse_progress(current, progress_total, filename)
        return
    self._progress_dlg = QProgressDialog(
        "扫描完成，正在解析截图...",
        "",
        0,
        progress_total,
        self.dialog_parent,
    )
    self._progress_dlg.setWindowTitle("全量解析进度")
    self._progress_dlg.setMinimumWidth(420)
    self._progress_dlg.setAutoClose(False)
    self._progress_dlg.setAutoReset(False)
    self._progress_dlg.setCancelButton(None)
    self._progress_dlg.show()
    self._on_gamepad_parse_progress(current, progress_total, filename)


def _on_gamepad_parse_progress(self, current, total, filename):
    self._gamepad_parse_progress = (current, total, filename)
    if getattr(self, "_gamepad_suppress_parse_ui", False):
        return
    dlg = getattr(self, "_progress_dlg", None)
    if not dlg:
        return
    dlg.setMaximum(max(int(total or 0), 1))
    dlg.setValue(int(current or 0))
    if filename:
        dlg.setLabelText(f"扫描完成，正在解析 ({current}/{total}): {filename}")
    else:
        dlg.setLabelText(f"扫描完成，正在等待解析进度... ({current}/{total})")


def _on_gamepad_parse_done(self):
    dlg = getattr(self, "_progress_dlg", None)
    if dlg:
        dlg.close()
        self._progress_dlg = None


def _on_gamepad_post_actions_ready(self):
    self._gamepad_suppress_parse_ui = True
    self._on_gamepad_parse_done()
    self.showMinimized()
    QApplication.processEvents()
    worker = getattr(self, "_gamepad_worker", None)
    if worker is not None and hasattr(worker, "acknowledge_post_actions_ready"):
        worker.acknowledge_post_actions_ready()


def _on_gamepad_error(self, err):
    _scan_event(
        self,
        "ERROR",
        "scanning.failed",
        "手柄扫描失败",
        stage="gamepad_pipeline",
        error=err,
    )
    self._stop_scan_hotkeys()
    self._gamepad_pipeline_finished = True
    self._gamepad_suppress_parse_ui = False
    self._gamepad_post_actions_enabled = False
    self._replace_inventory_on_next_parse = False
    self.showNormal()
    self.activateWindow()
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  开始计算")
    self._pending_parse_only = False
    QMessageBox.critical(
        self.dialog_parent,
        "手柄扫描失败",
        f"全量扫描出错:\n{err}",
    )


def _on_gamepad_pipeline_done(self, stats):
    self._stop_scan_hotkeys()
    self._gamepad_pipeline_finished = True
    self._gamepad_suppress_parse_ui = False
    self._gamepad_post_actions_enabled = False
    self.showNormal()
    self.activateWindow()
    self._replace_inventory_on_next_parse = False
    self._pending_scan_mode = None
    self._on_vision_done(stats)


def _on_scan_done(self, count):
    self._stop_scan_hotkeys()
    self.showNormal()
    self.activateWindow()
    if count > 0:
        replace_output = getattr(self, "_replace_inventory_on_next_parse", False)
        self._replace_inventory_on_next_parse = False
        scan_mode = getattr(self, "_pending_scan_mode", None)
        if replace_output or scan_mode == "gamepad":
            self._start_vision_processing(replace_output=True, parse_scope="full")
        elif scan_mode == "auto":
            self._start_vision_processing(replace_output=False, parse_scope="incremental_auto")
        elif scan_mode == "semi":
            self._start_vision_processing(replace_output=False, parse_scope="incremental_semi")
        else:
            self._start_vision_processing(replace_output=False, parse_scope="incremental")
    else:
        self._replace_inventory_on_next_parse = False
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  开始计算")
        self._pending_parse_only = False
        _scan_event(
            self,
            "INFO",
            "scanning.succeeded",
            "扫描完成但没有捕获新装备",
            captured_count=0,
            snapshot_written=False,
        )
        QMessageBox.information(
            self.dialog_parent,
            "扫描完成",
            "未捕获到新装备，无需解析。",
        )


def _on_scan_error(self, err):
    _scan_event(
        self,
        "ERROR",
        "scanning.failed",
        "扫描捕获失败",
        stage="capture",
        error=err,
    )
    self._stop_scan_hotkeys()
    self.showNormal()
    self.activateWindow()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  开始计算")
    self._pending_parse_only = False
    QMessageBox.critical(
        self.dialog_parent,
        "扫描失败",
        f"扫描出错:\n{err}",
    )


