# 控制扫描、离线解析、热键和进度反馈。
"""MainWindow methods for scanning."""

from __future__ import annotations

import ctypes
import re
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from src.app import runtime
from src.app.constants import DRONE_HELP, OFFLINE_HELP, SCAN_HELP
from src.app.dialogs import show_help
from src.app.theme import current_style_sheet
from src.app.workers import GamepadScanParseWorkerThread, GamepadScanWorkerThread, ScanWorkerThread, WorkerThread
from src.features.allocation.execute_page import build_execute_page
from src.features.allocation.preference_modes import role_preference_mode_error
from src.features.allocation.role_selector import RoleSelector
from src.features.scanning.file_lifecycle import ScanFileLifecycle, is_scope_image
from src.features.scanning.manual_recovery import complete_pending_manual_items
from src.features.scanning.post_action_dialog import load_scan_post_action_config, show_scan_post_action_dialog
from src.features.scanning.post_actions import post_actions_enabled, validate_post_action_config
from src.features.scanning.vision_worker import VisionWorkerThread
from src.scanner.batch_processor import BatchProcessor
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_page_execute', '_on_scan_change', '_on_priority_changed', '_open_scan_post_action_manager', '_do_exec', '_scan_lifecycle', '_is_scope_image', '_prepare_incremental_parse', '_matching_scope_files', '_unique_path', '_move_to_failed', '_delete_paths', '_next_full_scan_index', '_rename_incremental_successes', '_move_first_full_scan_to_tail', '_postprocess_vision_files', '_start_vision_processing', '_on_vision_progress', '_on_vision_done', '_on_vision_error', '_on_vision_cancel', '_on_vision_canceled', 'vision_cancel_message', '_start_scan', '_start_gamepad_scan', '_on_gamepad_scan_done', '_on_gamepad_parse_progress', '_on_gamepad_parse_done', '_on_gamepad_post_actions_ready', '_register_scan_hotkeys', '_hotkey_to_vk', '_win_hotkey_loop', '_hotkey_poll_loop', '_unregister_scan_hotkeys', '_on_hk_stop', '_on_hk_capture', '_on_hk_finish', '_on_gamepad_pipeline_done', '_on_gamepad_error', '_on_scan_done', '_on_scan_error']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


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
            priority_config_path_provider=lambda: runtime.USER_CONFIG_DIR / "priority_config.json",
            style_sheet=current_style_sheet(),
            help_callback=show_help,
        ),
        SCAN_HELP,
        DRONE_HELP,
        OFFLINE_HELP,
        show_help,
    )

def _on_scan_change(self,id):
    if hasattr(self,"offline_frame"):
        self.offline_frame.setVisible(id==3)
    self.total_count_frame.setVisible(id==1)
    if hasattr(self,"scan_dual_thread_frame"):
        self.scan_dual_thread_frame.setVisible(id==1)
    self.drone_frame.setVisible(id==2)

def _on_priority_changed(self):
    pass

def _open_scan_post_action_manager(self):
    selected = self.role_selector.get_selected() if hasattr(self, "role_selector") else []
    show_scan_post_action_dialog(self, runtime.USER_CONFIG_DIR, selected)

def _do_exec(self):
    sel=self.role_selector.get_selected()
    sm=str(self.scan_group.checkedId())
    parse_only=(not sel and sm in ("1","2","3"))
    if not sel and not parse_only:
        QMessageBox.warning(self,"提示","请先选择目标角色！"); return
    total_drives=None
    if sm=="1":
        raw_count=self.total_count_edit.text().strip()
        if not raw_count:
            QMessageBox.warning(self,"提示","全量扫描前请先填写库存数量。")
            return
        total_drives=int(raw_count)
        if not 0 < total_drives <= 2000:
            QMessageBox.warning(self,"提示","库存数量必须在 1-2000 之间。")
            return
    if parse_only:
        QMessageBox.information(self,"仅生成库存数据","当前未选择任何角色，本次扫描解析只会写入 SQLite 背包快照，不会进行配装计算。")
    offline_scope=None
    if sm=="3":
        checked=self.offline_group.checkedButton() if hasattr(self,"offline_group") else None
        offline_scope=checked.property("offline_key") if checked else "incremental"
        if offline_scope=="all":
            ret=QMessageBox.warning(
                self,
                "全部截图解析",
                "全部截图解析会读取文件夹根目录下所有截图，可能导致旧截图重复写入库存。\n\n如若产生库存异常，请重新全量扫描。\n\n确定继续吗？",
                QMessageBox.Yes|QMessageBox.No,
                QMessageBox.No,
            )
            if ret!=QMessageBox.Yes:
                return
    pending_drone_mode=None
    if sm=="2":
        pending_drone_mode="auto" if self.drone_group.checkedId()==1 else "semi"
        if pending_drone_mode=="auto" and not (runtime.SCREENSHOT_DIR/"raw_drive_0001.png").exists():
            QMessageBox.warning(self,"需要重新全量扫描","由于版本更新解析逻辑变动，需要重新进行全量扫描")
            return
    strat=["role_priority","drive_priority","global_optimal","update_mode"][max(0,min(3,self.strategy_group.checkedId()))]
    cs=self.role_selector.get_custom_sets()
    cw=self.role_selector.get_custom_weapons() if hasattr(self.role_selector,"get_custom_weapons") else {}
    tmf=self.role_selector.get_tape_main_filters()
    cpm=self.role_selector.get_crit_priority_modes()
    crc=self.role_selector.get_crit_rate_caps()
    sem=self.role_selector.get_set_effect_modes()
    pg=self.role_selector.get_priority_groups() if hasattr(self.role_selector,"get_priority_groups") else None
    preference_error = role_preference_mode_error(strat, tmf, cpm, crc)
    if preference_error:
        QMessageBox.warning(self, "词条自选不可用", preference_error)
        return
    if not parse_only and not self._confirm_unsaved_allocation_before_recompute():
        return
    post_actions_config = None
    if sm == "1":
        post_actions_config = load_scan_post_action_config(runtime.USER_CONFIG_DIR)
        post_action_error = validate_post_action_config(post_actions_config, sel)
        if post_action_error:
            QMessageBox.warning(self, "扫描后管理配置无效", post_action_error)
            return
    self.btn_run.setEnabled(False); self.btn_run.setText("⏳ 计算中..."); self.result_card.setVisible(False)
    self._pending_strat=strat; self._pending_sel=sel; self._pending_cs=cs; self._pending_custom_weapons=cw; self._pending_tape_main_filters=tmf; self._pending_crit_priority_modes=cpm; self._pending_crit_rate_caps=crc; self._pending_set_effect_modes=sem; self._pending_priority_groups=pg
    self._pending_archive_paths=[]
    self._pending_parse_only=parse_only

    if sm=="3":
        scope={"full":"full","incremental":"incremental","all":"all"}.get(offline_scope,"incremental")
        self._start_vision_processing(replace_output=offline_scope_replaces_inventory(scope),parse_scope=scope)
    elif sm=="2":
        drone_mode=pending_drone_mode or ("auto" if self.drone_group.checkedId()==1 else "semi")
        self._start_scan(drone_mode)
    elif sm=="1":
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
        self._worker=WorkerThread(target=lambda:self._run_allocation(strat,sel,cs,tmf,cpm,sem,pg,crc,cw),parent=self)
        self._worker.result_ready.connect(self._on_done); self._worker.error.connect(self._on_exec_error); self._worker.start()

def _scan_lifecycle(self):
    return ScanFileLifecycle(runtime.SCREENSHOT_DIR, runtime.OUTPUT_FILE, runtime.CONFIG_DIR, BatchProcessor)

def _is_scope_image(self,path:Path,parse_scope:str,skip_names=None):
    return is_scope_image(path,parse_scope,skip_names)

def _prepare_incremental_parse(self,parse_scope):
    self._pending_delete_after_parse=[]
    self._pending_probe_duplicate_count=0
    result=self._scan_lifecycle().prepare_incremental_parse(parse_scope)
    if result.baseline_missing:
        QMessageBox.warning(self,"需要重新全量扫描","由于版本更新解析逻辑变动，需要重新进行全量扫描")
        return None
    self._pending_delete_after_parse=list(result.delete_after_parse)
    self._pending_probe_duplicate_count=result.probe_duplicate_count
    return set(result.skip_names)

def _matching_scope_files(self,parse_scope,skip_names=None):
    return self._scan_lifecycle().matching_scope_files(parse_scope,skip_names)

def _unique_path(self,directory:Path,name:str):
    return self._scan_lifecycle().unique_path(directory,name)

def _move_to_failed(self,paths):
    return self._scan_lifecycle().move_to_failed(paths)

def _delete_paths(self,paths):
    return self._scan_lifecycle().delete_paths(paths)

def _next_full_scan_index(self):
    return self._scan_lifecycle().next_full_scan_index()

def _rename_incremental_successes(self,paths):
    return self._scan_lifecycle().rename_incremental_successes(paths)

def _move_first_full_scan_to_tail(self):
    return self._scan_lifecycle().move_first_full_scan_to_tail()

def _postprocess_vision_files(self,stats):
    post=self._scan_lifecycle().postprocess_vision_files(
        stats,
        delete_after_parse=getattr(self,"_pending_delete_after_parse",[]) or [],
        probe_duplicate_count=getattr(self,"_pending_probe_duplicate_count",0),
    )
    self._pending_delete_after_parse=[]
    return post

def _start_vision_processing(self, replace_output=False, parse_scope="all"):
    input_dir=str(runtime.SCREENSHOT_DIR)
    output_file=str(runtime.OUTPUT_FILE)
    self._pending_archive_paths=[]
    self._pending_parse_scope=parse_scope
    skip_names=self._prepare_incremental_parse(parse_scope)
    if skip_names is None:
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
        self._pending_parse_only=False
        return
    matching_files=self._matching_scope_files(parse_scope,skip_names)
    if not matching_files:
        deleted=self._delete_paths(getattr(self,"_pending_delete_after_parse",[]) or [])
        self._pending_delete_after_parse=[]
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
        self._pending_parse_only=False
        self._update_inventory_status()
        QMessageBox.information(self,"解析完成",f"解析成功 0 张，解析失败 0 张，过滤重复 {deleted} 张。")
        return
    self._vision_worker=VisionWorkerThread(
        input_dir,
        output_file,
        self,
        replace_output=replace_output,
        parse_scope=parse_scope,
        skip_names=skip_names,
        config_dir=str(runtime.CONFIG_DIR),
    )
    self._progress_dlg=QProgressDialog("正在解析截图...","取消",0,100,self)
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

def _on_vision_progress(self,current,total,filename):
    self._progress_dlg.setMaximum(total)
    self._progress_dlg.setValue(current)
    self._progress_dlg.setLabelText(f"正在解析 ({current}/{total}): {filename}")

def _on_vision_done(self,stats):
    stats=stats or {}
    self._pending_archive_paths=[]
    logger.info("视觉解析线程完成，准备启动分配计算...")
    if hasattr(self, '_progress_dlg') and self._progress_dlg:
        self._progress_dlg.close()
    if hasattr(self,'_vision_worker') and self._vision_worker.isRunning():
        self._vision_worker.wait(5000)
    post=self._postprocess_vision_files(stats)
    manual_added=0
    pending_manual_count=int(stats.get("pending_manual_count",0) or 0)
    if pending_manual_count:
        try:
            manual_added=complete_pending_manual_items(self, stats, runtime.OUTPUT_FILE, runtime.CONFIG_DIR)
        except Exception as exc:
            logger.error(f"补录待识别装备失败: {exc}")
            QMessageBox.warning(self, "补录失败", f"待补录装备未写入库存：{exc}")
    success_count=int(stats.get("success_count",0) or 0)
    failed_count=int(stats.get("failed_count",0) or 0)
    duplicate_count=int(stats.get("duplicate_count",0) or 0)+int(post.get("probe_duplicates",0) or 0)
    summary=f"解析成功 {success_count} 张，解析失败 {failed_count} 张，过滤重复 {duplicate_count} 张。"
    vision_snapshot_id = stats.get("vision_snapshot_id")
    if isinstance(vision_snapshot_id, int) and vision_snapshot_id > 0:
        summary += f"\n已写入视觉扫描库存快照 #{vision_snapshot_id}；没有抓包快照时可用于计算和自动装配。"
        refresh_home = getattr(self, "_refresh_home", None)
        if callable(refresh_home):
            refresh_home()
    if pending_manual_count:
        summary += f"\n待补录 {pending_manual_count} 件，已补录入库 {manual_added} 件。"
    if stats.get("post_actions_enabled"):
        summary += (
            "\n扫描后管理："
            f"参与计算 {int(stats.get('post_action_candidate_count', 0) or 0)} 件，"
            f"目标变更 {int(stats.get('post_action_target_count',0) or 0)} 个，"
            f"已处理 {int(stats.get('post_action_applied_count',0) or 0)} 个。"
            f"\n弃置 {int(stats.get('discard_set_count',0) or 0)} 个，"
            f"取消弃置 {int(stats.get('discard_clear_count',0) or 0)} 个；"
            f"锁定 {int(stats.get('lock_set_count',0) or 0)} 个，"
            f"取消锁定 {int(stats.get('lock_clear_count',0) or 0)} 个。"
        )
        filtered_parts = []
        if int(stats.get('post_action_quality_filtered_count', 0) or 0):
            filtered_parts.append(f"品质范围过滤 {int(stats.get('post_action_quality_filtered_count', 0) or 0)} 件")
        if int(stats.get('post_action_type_filtered_count', 0) or 0):
            filtered_parts.append(f"处理类别过滤 {int(stats.get('post_action_type_filtered_count', 0) or 0)} 件")
        if int(stats.get('post_action_type_range_filtered_count', 0) or 0):
            filtered_parts.append(f"类型范围过滤 {int(stats.get('post_action_type_range_filtered_count', 0) or 0)} 件")
        if filtered_parts:
            summary += "\n" + "，".join(filtered_parts) + "。"
    details=[]
    if post.get("moved_failed"):
        details.append(f"失败截图已移动到 failed 文件夹 {post['moved_failed']} 张。")
    if post.get("renamed"):
        details.append(f"增量截图已改名接入全量序列 {post['renamed']} 张。")
    if details:
        summary+="\n"+"\n".join(details)
    if getattr(self,"_pending_parse_only",False):
        self._pending_archive_paths=[]
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
        self._update_inventory_status()
        QMessageBox.information(self,"库存数据已生成",summary+"\n\n本次未配置角色优先级，已仅生成/更新 SQLite 背包快照，未进行配装计算。")
        self._pending_parse_only=False
        return
    from PySide6.QtCore import QTimer
    QMessageBox.information(self,"截图解析完成",summary)
    QTimer.singleShot(100, self._start_allocation_worker)

def _on_vision_error(self,err):
    self._progress_dlg.close()
    self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
    self._pending_parse_only=False
    QMessageBox.critical(self,"解析失败",f"截图解析出错:\n{err}")

def _on_vision_cancel(self):
    if hasattr(self,'_vision_worker') and self._vision_worker.isRunning():
        self._vision_worker.request_cancel()
        self._progress_dlg.setCancelButton(None)
        self._progress_dlg.setLabelText("正在取消解析，等待当前截图处理完成...")
        return
    self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")

def _on_vision_canceled(self,count):
    if hasattr(self, '_progress_dlg') and self._progress_dlg:
        self._progress_dlg.close()
    self.btn_run.setEnabled(True); self.btn_run.setText("开始计算")
    self._pending_parse_only=False
    QMessageBox.information(self,"解析已取消",vision_cancel_message(count))

def _start_scan(self,drone_mode):
    self._pending_scan_mode=drone_mode
    self.showMinimized()
    self._scan_worker=ScanWorkerThread(mode=drone_mode,parent=self)
    self._scan_worker.scan_done.connect(self._on_scan_done)
    self._scan_worker.error.connect(self._on_scan_error)
    self._register_scan_hotkeys(drone_mode)
    self.btn_run.setText("⏳  扫描中... (F12 停止)")
    self._scan_worker.start()

def _start_gamepad_scan(self,total_drives, post_actions_config=None, selected_roles=None, parse_during_scan=True, amd_compatibility=False):
    self._replace_inventory_on_next_parse=True
    self._pending_scan_mode="gamepad"
    self._pending_parse_scope="full"
    self._pending_delete_after_parse=[]
    self._pending_probe_duplicate_count=0
    self._gamepad_parse_progress=(0,total_drives,"")
    self._gamepad_pipeline_finished=False
    self._gamepad_post_actions_enabled=bool(post_actions_enabled(post_actions_config))
    self._gamepad_suppress_parse_ui=False
    action_hint = ""
    if self._gamepad_post_actions_enabled:
        action_hint = (
            "\n\n已启用扫描后管理：扫描解析后会继续计算并同步弃置/锁定状态。"
            "\n扫描开始后不要切换排序、筛选、滚动或手动操作背包。"
        )
    ret = QMessageBox.question(
        self,
        "全量扫描准备",
        "点击“确定”后程序会最小化并准备开始全量扫描。\n\n"
        "请切换至游戏的驱动仓库页面，并确保当前选中第一排第一个驱动。\n"
        "程序会在短暂倒计时后接管虚拟手柄进行遍历截图。"
        + action_hint,
        QMessageBox.Ok | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if ret != QMessageBox.Ok:
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
        self._replace_inventory_on_next_parse=False
        self._pending_scan_mode=None
        self._gamepad_post_actions_enabled=False
        self._gamepad_suppress_parse_ui=False
        return
    self.showMinimized()
    self._gamepad_worker=GamepadScanParseWorkerThread(
        total_drives=total_drives,
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
    self._register_scan_hotkeys("gamepad")
    self.btn_run.setText("⏳  手柄扫描/解析中... (F12 停止)")
    self._gamepad_worker.start()

def _on_gamepad_scan_done(self,captured,total):
    if getattr(self,"_gamepad_pipeline_finished",False):
        return
    if getattr(self,"_gamepad_suppress_parse_ui",False):
        return
    self.showNormal(); self.activateWindow()
    current, progress_total, filename = getattr(self,"_gamepad_parse_progress",(0,total,""))
    progress_total=max(int(progress_total or 0),int(total or 0),int(captured or 0),1)
    dlg=getattr(self,"_progress_dlg",None)
    if dlg and dlg.isVisible():
        self._on_gamepad_parse_progress(current,progress_total,filename)
        return
    self._progress_dlg=QProgressDialog("扫描完成，正在解析截图...","",0,progress_total,self)
    self._progress_dlg.setWindowTitle("全量解析进度")
    self._progress_dlg.setMinimumWidth(420)
    self._progress_dlg.setAutoClose(False)
    self._progress_dlg.setAutoReset(False)
    self._progress_dlg.setCancelButton(None)
    self._progress_dlg.show()
    self._on_gamepad_parse_progress(current,progress_total,filename)

def _on_gamepad_parse_progress(self,current,total,filename):
    self._gamepad_parse_progress=(current,total,filename)
    if getattr(self,"_gamepad_suppress_parse_ui",False):
        return
    dlg=getattr(self,"_progress_dlg",None)
    if not dlg:
        return
    dlg.setMaximum(max(int(total or 0),1))
    dlg.setValue(int(current or 0))
    if filename:
        dlg.setLabelText(f"扫描完成，正在解析 ({current}/{total}): {filename}")
    else:
        dlg.setLabelText(f"扫描完成，正在等待解析进度... ({current}/{total})")

def _on_gamepad_parse_done(self):
    dlg=getattr(self,"_progress_dlg",None)
    if dlg:
        dlg.close()
        self._progress_dlg=None

def _on_gamepad_post_actions_ready(self):
    self._gamepad_suppress_parse_ui=True
    self._on_gamepad_parse_done()
    self.showMinimized()
    QApplication.processEvents()
    worker = getattr(self, "_gamepad_worker", None)
    if worker is not None and hasattr(worker, "acknowledge_post_actions_ready"):
        worker.acknowledge_post_actions_ready()

def _register_scan_hotkeys(self, mode):
    """启动热键监听线程"""
    self._hk_mode=mode
    self._hk_active=True
    self._hk_thread_id=None
    import threading
    self._hk_thread=threading.Thread(target=self._hotkey_poll_loop, daemon=True)
    self._hk_thread.start()

def _hotkey_to_vk(self,hotkey):
    text=str(hotkey or "").strip().upper()
    match=re.fullmatch(r"F(\d{1,2})",text)
    if match:
        num=int(match.group(1))
        if 1<=num<=24:
            return 0x70+num-1
    if len(text)==1 and ("A"<=text<="Z" or "0"<=text<="9"):
        return ord(text)
    return None

def _win_hotkey_loop(self):
    if sys.platform!="win32":
        return False
    user32=ctypes.windll.user32
    kernel32=ctypes.windll.kernel32
    WM_HOTKEY=0x0312
    WM_NULL=0x0000
    PM_REMOVE=0x0001
    MOD_NOREPEAT=0x4000

    class POINT(ctypes.Structure):
        _fields_=[("x",ctypes.c_long),("y",ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_=[
            ("hwnd",ctypes.c_void_p),
            ("message",ctypes.c_uint),
            ("wParam",ctypes.c_size_t),
            ("lParam",ctypes.c_size_t),
            ("time",ctypes.c_uint),
            ("pt",POINT),
        ]

    actions={}
    registrations=[]
    hotkeys=[(1,self._hk_stop,"stop")]
    if self._hk_mode in ("semi","identify"):
        hotkeys.extend([(2,self._hk_capture,"capture"),(3,self._hk_finish,"finish")])
    for hotkey_id,hotkey,action in hotkeys:
        vk=self._hotkey_to_vk(hotkey)
        if not vk:
            continue
        if not user32.RegisterHotKey(None,hotkey_id,MOD_NOREPEAT,vk):
            for registered_id in registrations:
                user32.UnregisterHotKey(None,registered_id)
            return False
        registrations.append(hotkey_id)
        actions[hotkey_id]=action
    if not registrations:
        return False

    self._hk_thread_id=kernel32.GetCurrentThreadId()
    msg=MSG()
    try:
        while self._hk_active:
            while user32.PeekMessageW(ctypes.byref(msg),None,0,0,PM_REMOVE):
                if msg.message==WM_HOTKEY:
                    action=actions.get(int(msg.wParam))
                    if action=="stop":
                        self._on_hk_stop()
                    elif action=="capture":
                        self._on_hk_capture()
                    elif action=="finish":
                        self._on_hk_finish()
            time.sleep(0.03)
    finally:
        for hotkey_id in registrations:
            user32.UnregisterHotKey(None,hotkey_id)
        self._hk_thread_id=None
    return True

def _hotkey_poll_loop(self):
    """后台轮询线程，监听全局热键"""
    if self._win_hotkey_loop():
        return
    import keyboard as kb
    while self._hk_active:
        try:
            if kb.is_pressed(self._hk_stop.lower()):
                self._on_hk_stop()
                time.sleep(0.5)
            if self._hk_mode in ("semi","identify"):
                if kb.is_pressed(self._hk_capture.lower()):
                    self._on_hk_capture()
                    time.sleep(0.3)
                if kb.is_pressed(self._hk_finish.lower()):
                    self._on_hk_finish()
                    time.sleep(0.5)
        except Exception as exc:
            logger.debug(f"扫描热键轮询异常，继续监听: {exc}")
        time.sleep(0.05)

def _unregister_scan_hotkeys(self):
    self._hk_active=False
    if sys.platform=="win32" and getattr(self,"_hk_thread_id",None):
        try:
            ctypes.windll.user32.PostThreadMessageW(int(self._hk_thread_id),0,0,0)
        except Exception as exc:
            logger.debug(f"唤醒扫描热键线程失败，可能线程已退出: {exc}")

def _on_hk_stop(self):
    w=getattr(self,'_scan_worker',None) or getattr(self,'_gamepad_worker',None) or getattr(self,'_gamepad_pipeline_worker',None)
    if w and w.scanner:
        logger.warning(f"收到停止热键 {self._hk_stop}，准备停止当前扫描/状态同步任务。")
        if hasattr(w.scanner, "emergency_stop"):
            w.scanner.emergency_stop()
        else:
            w.scanner._stopped=True
        w.scanner._finish_flag=True
    else:
        logger.warning(f"收到停止热键 {self._hk_stop}，但当前没有可停止的扫描器。")

def _on_hk_capture(self):
    if getattr(self,"_hk_mode",None)=="identify":
        self._capture_identify_foreground()
        return
    w=getattr(self,'_scan_worker',None)
    if w and w.scanner: w.scanner._capture_flag=True

def _on_hk_finish(self):
    if getattr(self,"_hk_mode",None)=="identify":
        self.identify_capture_done_signal.emit()
        return
    w=getattr(self,'_scan_worker',None)
    if w and w.scanner: w.scanner._finish_flag=True

def _on_gamepad_error(self,err):
    self._unregister_scan_hotkeys()
    self._gamepad_pipeline_finished=True
    self._gamepad_suppress_parse_ui=False
    self._gamepad_post_actions_enabled=False
    self._replace_inventory_on_next_parse=False
    self.showNormal(); self.activateWindow()
    if hasattr(self,'_progress_dlg') and self._progress_dlg:
        self._progress_dlg.close()
    self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
    self._pending_parse_only=False
    QMessageBox.critical(self,"手柄扫描失败",f"全量扫描出错:\n{err}")

def _on_gamepad_pipeline_done(self,stats):
    self._unregister_scan_hotkeys()
    self._gamepad_pipeline_finished=True
    self._gamepad_suppress_parse_ui=False
    self._gamepad_post_actions_enabled=False
    self.showNormal(); self.activateWindow()
    self._replace_inventory_on_next_parse=False
    self._pending_scan_mode=None
    self._on_vision_done(stats)

def _on_scan_done(self,count):
    self._unregister_scan_hotkeys()
    self.showNormal(); self.activateWindow()
    if count>0:
        replace_output=getattr(self,"_replace_inventory_on_next_parse",False)
        self._replace_inventory_on_next_parse=False
        scan_mode=getattr(self,"_pending_scan_mode",None)
        if replace_output or scan_mode=="gamepad":
            self._start_vision_processing(replace_output=True,parse_scope="full")
        elif scan_mode=="auto":
            self._start_vision_processing(replace_output=False,parse_scope="incremental_auto")
        elif scan_mode=="semi":
            self._start_vision_processing(replace_output=False,parse_scope="incremental_semi")
        else:
            self._start_vision_processing(replace_output=False,parse_scope="incremental")
    else:
        self._replace_inventory_on_next_parse=False
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
        self._pending_parse_only=False
        QMessageBox.information(self,"扫描完成","未捕获到新装备，无需解析。")

def _on_scan_error(self,err):
    self._unregister_scan_hotkeys()
    self.showNormal(); self.activateWindow()
    self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
    self._pending_parse_only=False
    QMessageBox.critical(self,"扫描失败",f"扫描出错:\n{err}")
