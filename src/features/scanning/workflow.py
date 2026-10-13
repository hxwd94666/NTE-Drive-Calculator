# 编排扫描、解析、文件生命周期与进度回调。
"""Scanning workflow implementation used by ScanningController."""

from __future__ import annotations

from src.features.input_operation_entry import request_input_entry, show_input_unavailable
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from src.app.constants import DRONE_HELP, OFFLINE_HELP, SCAN_HELP
from src.app.dialogs import show_help
from src.app.theme import current_style_sheet
from src.app.workers import FullVisualScanParseWorkerThread
from src.features.scanning.incremental_workflow import start_incremental_capture, finish_incremental_capture, fail_incremental_capture
from src.features.allocation.execute_page import build_execute_page
from src.features.allocation.preference_modes import role_preference_mode_error
from src.features.allocation.role_selector import RoleSelector
from src.features.scanning.dependencies import (
    current_scanning_dependencies as _current_scanning_dependencies,
    scanning_dependencies_are_current as _scanning_dependencies_are_current,
    task_scanning_dependencies as _task_scanning_dependencies,
)
from src.features.scanning.manual_recovery import complete_pending_manual_items
from src.features.scanning.file_workflow import (
    clear_capture_recovery, remember_capture_recovery, update_capture_actions, connect_full_capture,
)
from src.features.scanning.operation_logging import (
    begin_scan_operation as _begin_scan_operation,
    scan_event as _scan_event,
)
from src.features.scanning.post_action_dialog import load_scan_post_action_config
from src.features.scanning.scan_contracts import (
    offline_scope_replaces_inventory,
    vision_cancel_message,
)
from src.features.scanning.scan_source_warning import confirm_scan_mode_entry, restore_scan_mode_selection
from src.features.scanning.post_action_summary import append_scan_post_action_summary, show_scan_completion
from src.domain.post_actions import post_actions_enabled, validate_post_action_config
from src.features.scanning.vision_worker import VisionWorkerThread
from src.services.inventory.visual_scan_commit import append_tape_main_warning
from src.services.inventory.vision_commit import VisionCommitRequest
from src.utils.logger import logger

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


def _on_scan_change(self, id, checked=True):
    if not checked:
        return
    previous_id = getattr(self, "_confirmed_scan_mode_id", 4)
    if id in {1, 2, 3}:
        dependencies = _current_scanning_dependencies(self)
        if not confirm_scan_mode_entry(
            self.dialog_parent, dependencies.user_database_path,
            navigate_home=lambda: self.navigate("home"),
        ):
            restore_scan_mode_selection(self.scan_group, previous_id)
            return
    self._confirmed_scan_mode_id = id
    if hasattr(self, "offline_frame"):
        self.offline_frame.setVisible(id == 3)
    self.total_count_frame.setVisible(id == 1)
    if hasattr(self, "full_scan_driver_frame"):
        self.full_scan_driver_frame.setVisible(id == 1)
    if hasattr(self, "scan_dual_thread_frame"):
        self.scan_dual_thread_frame.setVisible(id == 1)
    self.drone_frame.setVisible(id == 2)


def _on_priority_changed(self):
    pass


def _do_exec(self):
    sm = str(self.scan_group.checkedId())
    if sm in {"1", "2"} and not request_input_entry(self, "interface_input", "游戏界面扫描"):
        return
    dependencies = _current_scanning_dependencies(self)
    sel = self.role_selector.get_selected()
    parse_only = not sel and sm in ("1", "2", "3")
    if not sel and not parse_only:
        QMessageBox.warning(self.dialog_parent, "提示", "请先选择目标角色！")
        return
    total_drives = None
    capture_driver = "mouse"
    if sm == "1":
        driver_button = (
            self.full_scan_driver_group.checkedButton()
            if hasattr(self, "full_scan_driver_group")
            else None
        )
        if driver_button is not None:
            capture_driver = str(driver_button.property("capture_driver") or "mouse")
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
            "当前未选择任何角色，本次扫描只会生成背包记录，不会进行配装计算。",
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
    strat = ["role_priority", "update_mode"][max(0, min(1, self.strategy_group.checkedId()))]
    cs = self.role_selector.get_custom_sets()
    cw = self.role_selector.get_custom_weapons() if hasattr(self.role_selector, "get_custom_weapons") else {}
    tmf = self.role_selector.get_tape_main_filters()
    cpm = self.role_selector.get_crit_priority_modes()
    crc = self.role_selector.get_crit_rate_caps()
    crb = self.role_selector.get_crit_rate_baselines()
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
    if not parse_only and not self._allocation_filter_settings_are_valid():
        return
    if not parse_only and not self._confirm_unsaved_allocation_before_recompute():
        return
    post_actions_config = None
    if sm == "1":
        post_actions_config = load_scan_post_action_config(
            dependencies.user_config_dir,
            user_database_path=dependencies.user_database_path,
        )
        post_action_error = validate_post_action_config(post_actions_config)
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
    self._pending_crit_rate_baselines = crb
    self._pending_set_effect_modes = sem
    self._pending_priority_groups = pg
    self._pending_filter_settings = self._allocation_filter_settings
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
            selected_roles=None,
            parse_during_scan=parse_during_scan,
            amd_compatibility=amd_compatibility,
            capture_driver=capture_driver,
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
            crit_rate_baselines=crb,
            custom_weapons=cw,
            filter_settings=self._allocation_filter_settings,
            blueprint_combo_limit=self._allocation_filter_settings.blueprint_combo_limit,
        )


def _start_vision_processing(self, replace_output=False, parse_scope="all", *, capture_template=None,
                             capture_dependencies=None, capture_cancel_token=None,
                             capture_stage=None, recognition_resources=None, capture_driver=None):
    dependencies = capture_dependencies or _current_scanning_dependencies(self)
    session = self._manual_recovery
    revision = session.revision
    if (session.is_closed or not _scanning_dependencies_are_current(self, dependencies)
            or capture_cancel_token is not None and capture_cancel_token.is_set()):
        return
    if capture_template is not None:
        capture_template.verify()
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
    # 首图比对也由解析 worker 拥有，界面只排队，不初始化第二个 OCR。
    skip_names = set()
    self._vision_worker = VisionWorkerThread(
        input_dir,
        self,
        replace_output=replace_output,
        parse_scope=parse_scope,
        skip_names=skip_names,
        config_dir=str(dependencies.config_dir),
        static_database_path=dependencies.static_database_path,
        capture_template=capture_template, capture_cancel_token=capture_cancel_token,
        capture_stage=capture_stage, recognition_resources=recognition_resources, capture_driver=capture_driver,
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
    self._progress_dlg.canceled.connect(lambda: self._on_vision_cancel() if is_current() else None)
    self._progress_dlg.show()
    worker = self._vision_worker
    def owns_current():
        return (self._vision_worker is worker and self._manual_recovery is session
                and not session.is_closed and _scanning_dependencies_are_current(self, dependencies))

    def is_current(*, cancelled=False):
        # 硬停止/切账号/退场撤销代次；用户进度取消只允许同 owner 的取消收尾。
        return (owns_current() and session.revision == revision
                and not (capture_cancel_token is not None and capture_cancel_token.is_set())
                and (cancelled or not worker.is_cancelled()))

    def finished():
        if not owns_current():
            return
        if worker.is_cancelled() or session.revision != revision:
            if self._progress_dlg:
                self._progress_dlg.close()
            _restore_vision_controls(self)  # 真正撤销只恢复当前控件，不开提示或提交。
        update_capture_actions(self)

    worker.progress.connect(lambda *args: self._on_vision_progress(*args) if is_current() else None)
    worker.processing_done.connect(lambda stats: self._on_vision_done(stats) if is_current() else None)
    worker.canceled.connect(lambda count: self._on_vision_canceled(count) if is_current(cancelled=True) else None)
    worker.error.connect(lambda error: self._on_vision_error(error) if is_current() else None)
    worker.finished.connect(finished)
    self._vision_worker.start()


def _on_vision_progress(self, current, total, filename):
    self._progress_dlg.setMaximum(total)
    self._progress_dlg.setValue(current)
    self._progress_dlg.setLabelText(f"正在解析 ({current}/{total}): {filename}")


def _restore_vision_controls(self, *, label="⚡  开始计算"):
    """所有终止分支恢复同一操作 owner 的控件；不修改库存或输入文件。"""
    self.btn_run.setEnabled(True)
    self.btn_run.setText(label)
    self._pending_parse_only = False
    update_capture_actions(self)


def _on_vision_done(self, stats):
    dependencies = _task_scanning_dependencies(self)
    stats = stats or {}
    capture_token = stats.get("capture_cancel_token")
    if (self._manual_recovery.is_closed or not _scanning_dependencies_are_current(self, dependencies)
            or capture_token is not None and capture_token.is_set()):
        return
    remember_capture_recovery(self, stats, dependencies)
    self._pending_archive_paths = []
    logger.info("视觉解析线程完成，准备启动分配计算...")
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    manual_items = []
    pending_manual_count = int(stats.get("pending_manual_count", 0) or 0)
    if pending_manual_count:
        session = self._manual_recovery
        revision = session.revision
        capture_token = stats.get("capture_cancel_token")
        def recovery_is_current():
            # 程序停止/退出/账号切换撤销代次；普通用户关窗不推进这份 owner 代次。
            return (self._manual_recovery is session and not session.is_closed
                    and session.revision == revision
                    and _scanning_dependencies_are_current(self, dependencies)
                    and not (capture_token is not None and capture_token.is_set()))
        def finish_revoked_recovery():
            # 同 owner 的停止只恢复可操作性；关闭/新账号/新 session 不投影旧状态。
            if (self._manual_recovery is session and not session.is_closed
                    and _scanning_dependencies_are_current(self, dependencies)):
                _restore_vision_controls(self)
        try:
            manual_result = complete_pending_manual_items(
                self.dialog_parent,
                stats,
                dependencies.config_dir,
                session=session,
                scope=(dependencies.account_id, dependencies.generation, dependencies.user_database_path,
                       dependencies.screenshot_dir, dependencies.static_database_path,
                       dependencies.config_dir, dependencies.template_dir, stats.get("parse_scope")),
                is_current=recovery_is_current,
            )
            # 统一复核本次 owner/session/账号/取消；失效后不再弹框、造 token 或提交。
            if not recovery_is_current():
                finish_revoked_recovery()
                return
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
                    "本次全量视觉扫描未生成背包记录。" + (
                        "\n已填值仅保留在当前会话；继续本次暂存可恢复，仍需确认补录入库。"
                        if stats.get("capture_stage") is not None else
                        "\n已填值仅保留在当前会话；再次解析同一批截图可恢复，仍需确认补录入库。"
                        if self._manual_recovery.has_draft else ""
                    ),
                )
                _restore_vision_controls(self)
                return
            manual_items = manual_result
        except Exception as exc:
            if not recovery_is_current():
                finish_revoked_recovery()
                return
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
                f"本次扫描未生成背包记录：{exc}",
            )
            _restore_vision_controls(self)
            return
    stats = dict(stats, _completed_manual_items=manual_items)
    request = VisionCommitRequest.freeze(dependencies.user_database_path, stats, manual_items)
    if self._vision_commit.submit(dependencies, request):
        self.btn_run.setText("正在保存背包与整理截图...")
    else:
        self.btn_run.setEnabled(True)
        QMessageBox.information(self.dialog_parent, "正在处理", "请等待上一项保存任务完成。")


def _on_vision_commit_failed(self, dependencies, error):
    _restore_vision_controls(self)
    _scan_event(self, "ERROR", "scanning.failed", "视觉结果提交失败", stage="snapshot_commit", error=error)
    QMessageBox.warning(self.dialog_parent, "保存未确认", f"{error}\n原截图及本次暂存保留，请核对背包状态。")


def _on_vision_application_failed(self, dependencies, error):
    saved = self._vision_commit.last_result
    _restore_vision_controls(self)
    state = f"背包已保存 #{saved.snapshot_id}" if saved and saved.snapshot_id else "提交结果已返回"
    QMessageBox.warning(self.dialog_parent, "展示未完成", f"{state}，结果展示未完成。请刷新并核对，不重复保存。\n{error}")


def _on_vision_committed(self, dependencies, result):
    if result.snapshot_id is not None:
        clear_capture_recovery(self)  # 已保存事实之后只能整理文件，不能再解析入库。
    self._manual_recovery.cancel()  # 提交事实已返回，不将旧填值再次套用到后续扫描。
    try:
        _finish_vision_commit(self, dependencies, result)
    except Exception as error:
        _on_vision_application_failed(self, dependencies, str(error))


def _finish_vision_commit(self, dependencies, result):
    stats = result.stats
    vision_snapshot_id = result.snapshot_id
    manual_items = list(stats.get("_completed_manual_items", []))
    pending_manual_count = int(stats.get("pending_manual_count", 0) or 0)
    publication = result.publication
    files_ready = (publication is None or publication.status == "published") and not result.post_commit_error
    if publication is not None and publication.cleanup_pending:
        QMessageBox.warning(self.dialog_parent, "截图已整理", "新截图已发布，部分旧备份尚未清理；请保留恢复记录并稍后核对空间。")
    if not files_ready:
        self._pending_archive_paths = []
        reason = "截图恢复未完成" if publication and publication.status == "restore_incomplete" else "截图整理未完成"
        QMessageBox.warning(self.dialog_parent, "背包已保存", f"背包已保存，{reason}。\n本次暂存及恢复记录保留；仍可用已保存背包计算。")
    # 提交或失效判断之后才变更截图，拒绝/补录取消不先丢失原文件。
    post = self._postprocess_vision_files(stats, before_files=lambda: self._vision_commit.may_continue(dependencies, result)) if files_ready else {}
    success_count = int(stats.get("success_count", 0) or 0)
    failed_count = int(stats.get("failed_count", 0) or 0)
    duplicate_count = int(stats.get("duplicate_count", 0) or 0) + int(post.get("probe_duplicates", 0) or 0)
    summary = f"解析成功 {success_count} 张，解析失败 {failed_count} 张，过滤重复 {duplicate_count} 张。"
    summary = append_tape_main_warning(summary, [*list(stats.get("vision_items") or []), *manual_items])
    if isinstance(vision_snapshot_id, int) and vision_snapshot_id > 0:
        summary += f"\n已写入视觉扫描库存快照 #{vision_snapshot_id}；没有抓包快照时可用于计算和自动装配。"
        refresh_home = getattr(self, "_refresh_home", None)
        if callable(refresh_home):
            refresh_home()
    if pending_manual_count:
        summary += (
            f"\n待补录 {pending_manual_count} 件，已补录 {len(manual_items)} 件并与本次识别结果共同保存。"
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
    summary = append_scan_post_action_summary(summary, stats)
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
        show_scan_completion(
            self.dialog_parent,
            "库存数据已生成" if vision_snapshot_id else "截图解析完成",
            summary + ("\n\n本次仅更新背包，未进行配装计算。" if vision_snapshot_id else "\n\n本次仅完成截图解析与整理，未生成新的正式背包记录。"),
            stats,
        )
        self._pending_parse_only = False
        return
    from PySide6.QtCore import QTimer

    show_scan_completion(self.dialog_parent, "截图解析完成", summary, stats)
    QTimer.singleShot(100, lambda: self._start_allocation_worker()
                      if self._vision_commit.may_continue(dependencies, result) else None)


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
    _restore_vision_controls(self)
    QMessageBox.critical(
        self.dialog_parent,
        "解析失败",
        f"截图解析出错:\n{err}",
    )


def _on_vision_cancel(self):
    vision_worker = getattr(self, "_vision_worker", None)
    if vision_worker is not None:
        vision_worker.request_cancel()  # 结束线程的完成信号也可能仍在排队。
    clear_capture_recovery(self)
    if vision_worker is not None and vision_worker.isRunning():
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
    _restore_vision_controls(self, label="开始计算")
    QMessageBox.information(
        self.dialog_parent,
        "解析已取消",
        vision_cancel_message(count),
    )


def _start_scan(self, drone_mode):
    return start_incremental_capture(self, drone_mode)


def _start_gamepad_scan(
    self, total_drives, post_actions_config=None, selected_roles=None, parse_during_scan=True,
    amd_compatibility=False, capture_driver="mouse",
):
    if not request_input_entry(self, "interface_input", "手柄全量扫描" if capture_driver == "gamepad" else "鼠标全量扫描"):
        return
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    self._replace_inventory_on_next_parse = True
    capture_driver = "gamepad" if capture_driver == "gamepad" else "mouse"
    self._pending_scan_mode = capture_driver
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
        "请切换至游戏的驱动仓库页面。\n"
        + (
            "请把列表滚动到顶部；程序会在倒计时后按网格随机偏移点击并滚动遍历截图。"
            if capture_driver == "mouse"
            else "请确保选中第一排第一个驱动；程序会在倒计时后接管虚拟手柄遍历截图。"
        )
        + action_hint,
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
    self._manual_recovery.cancel()
    self._vision_commit.cancel()
    clear_capture_recovery(self)
    _begin_scan_operation(
        self,
        dependencies,
        route=capture_driver,
        expected_capture_count=int(total_drives or 0),
        parse_during_scan=bool(parse_during_scan),
        post_actions_enabled=bool(self._gamepad_post_actions_enabled),
    )
    self.showMinimized()
    self._gamepad_worker = FullVisualScanParseWorkerThread(
        total_drives=total_drives,
        screenshot_dir=dependencies.screenshot_dir,
        config_dir=dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
        static_database_path=dependencies.static_database_path,
        parent=self,
        post_actions_config=post_actions_config,
        selected_roles=selected_roles,
        parse_during_scan=parse_during_scan,
        amd_compatibility=amd_compatibility,
        capture_driver=capture_driver,
        operation_guard=dependencies.operation_guard,
        result_is_current=lambda: (
            self.app_context.generation == dependencies.generation
            and self.app_context.account.active_account_id == dependencies.account_id
            and self.app_context.account.user_database_path == dependencies.user_database_path
        ),
    )
    connect_full_capture(self, self._gamepad_worker, dependencies)
    self._start_scan_hotkeys(capture_driver)
    label = "鼠标" if capture_driver == "mouse" else "手柄"
    self.btn_run.setText(f"⏳  {label}扫描/解析中... ({self._hotkey_manager.configuration.stop} 停止)")
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
        "全量视觉扫描失败",
        stage="full_visual_pipeline",
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
    _restore_vision_controls(self)
    show_input_unavailable(self, "全量视觉扫描", str(err))


def _on_gamepad_pipeline_done(self, stats):
    # 直接调用与排队信号共用相同门禁：停后的结果不打开补录或创建新提交 token。
    token = stats.get("capture_cancel_token")
    if (self._manual_recovery.is_closed or token is not None and token.is_set()
            or not _scanning_dependencies_are_current(self, _task_scanning_dependencies(self))):
        return
    self._stop_scan_hotkeys()
    self._gamepad_pipeline_finished = True
    self._gamepad_suppress_parse_ui = False
    self._gamepad_post_actions_enabled = False
    self.showNormal()
    self.activateWindow()
    self._replace_inventory_on_next_parse = False
    self._pending_scan_mode = None
    if stats.get("discarded_stale"):
        _restore_vision_controls(self)
        return
    self._on_vision_done(stats)


def _on_scan_done(self, count, *, worker, dependencies):
    return finish_incremental_capture(self, count, worker, dependencies)


def _on_scan_error(self, err):
    return fail_incremental_capture(self, err)
