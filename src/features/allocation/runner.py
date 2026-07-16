# 执行分配任务并处理保存和归档。
"""MainWindow methods for allocation."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app import runtime
from src.app.facade import NTEAppFacade
from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread
from src.optimizer.plan_diff import build_plan_diff
from src.features.role.equipment_import import import_all_role_equipment
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_run_allocation', '_start_allocation_worker', '_confirm_unsaved_allocation_before_recompute', '_on_done', '_on_exec_error', '_save_alloc', '_archive_pending_screenshots']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _run_allocation(self,strat,sel,cs,tape_main_filters=None,crit_priority_modes=None,set_effect_modes=None,priority_groups=None,crit_rate_caps=None,custom_weapons=None):
    try:
        logger.info(f"开始分配计算: 策略={strat}, 角色={sel}")
        a=NTEAppFacade(config_dir=str(runtime.CONFIG_DIR),user_config_dir=str(runtime.USER_CONFIG_DIR))
        fp,_=a.execute_allocation(str(runtime.OUTPUT_FILE),sel,cs,strat,tape_main_filters=tape_main_filters or {},crit_priority_modes=crit_priority_modes or {},set_effect_modes=set_effect_modes or {},priority_groups=priority_groups,crit_rate_caps=crit_rate_caps or {},custom_weapons=custom_weapons or {})
        logger.info(f"分配计算完成: result_type={type(fp).__name__}")
        return fp
    except Exception as e:
        import traceback as tb
        logger.error(f"_run_allocation 内部异常: {e}\n{tb.format_exc()}")
        raise

def _start_allocation_worker(self):
    logger.info("启动分配工作线程...")
    self._worker=WorkerThread(target=lambda:self._run_allocation(self._pending_strat,self._pending_sel,self._pending_cs,getattr(self,"_pending_tape_main_filters",{}),getattr(self,"_pending_crit_priority_modes",{}),getattr(self,"_pending_set_effect_modes",{}),getattr(self,"_pending_priority_groups",None),getattr(self,"_pending_crit_rate_caps",{}),getattr(self,"_pending_custom_weapons",{})),parent=self)
    self._worker.result_ready.connect(self._on_done); self._worker.error.connect(self._on_exec_error); self._worker.start()
    logger.info("分配线程已启动")

def _confirm_unsaved_allocation_before_recompute(self):
    if not self.final_plan or not self._allocation_dirty:
        return True
    if self._ui_preferences.get("skip_unsaved_allocation_prompt"):
        self._allocation_dirty=False
        return True
    dlg=QDialog(self)
    dlg.setWindowTitle("当前配装尚未保存")
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setContentsMargins(18,18,18,18); layout.setSpacing(14)
    msg=QLabel("重新执行计算会覆盖当前计算结果，是否先保存当前配装？")
    msg.setWordWrap(True)
    layout.addWidget(msg)
    row=QHBoxLayout(); row.setSpacing(10)
    dont_btn=QPushButton("不再提醒"); dont_btn.setObjectName("btnDanger")
    skip_btn=QPushButton("不保存")
    save_btn=QPushButton("保存"); save_btn.setObjectName("btnPrimary")
    row.addWidget(dont_btn); row.addWidget(skip_btn); row.addWidget(save_btn)
    layout.addLayout(row)
    choice={"value":None}
    dont_btn.clicked.connect(lambda: (choice.__setitem__("value","never"), dlg.accept()))
    skip_btn.clicked.connect(lambda: (choice.__setitem__("value","skip"), dlg.accept()))
    save_btn.clicked.connect(lambda: (choice.__setitem__("value","save"), dlg.accept()))
    dlg.exec()
    if choice["value"]=="save":
        return self._save_alloc(show_message=False)
    if choice["value"]=="never":
        self._ui_preferences["skip_unsaved_allocation_prompt"]=True
        self._save_ui_preferences()
        self._allocation_dirty=False
        return True
    if choice["value"]=="skip":
        self._allocation_dirty=False
        return True
    return False

def _on_done(self,r):
    try:
        logger.info(f"_on_done 收到结果: type={type(r).__name__}, keys={list(r.keys()) if isinstance(r,dict) else 'N/A'}")
        self.final_plan=r; self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
        self._allocation_custom_weapons=dict(getattr(self,"_pending_custom_weapons",{}) or {})
        if r is None: QMessageBox.warning(self,"提示","计算失败，请检查库存文件是否存在。"); return
        old_state=self.state_mgr.load_state() if hasattr(self.state_mgr,"load_state") else {}
        self.allocation_plan_diff=build_plan_diff(old_state,r)
        self._allocation_dirty=True
        self._render_results(r)
        logger.info("_render_results 完成")
    except Exception as e:
        import traceback as tb
        logger.error(f"_on_done 异常: {e}\n{tb.format_exc()}")
        QMessageBox.critical(self,"渲染失败",f"{e}")

def _on_exec_error(self,err):
    self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
    QMessageBox.critical(self,"执行失败",f"发生错误:\n{err}")

def _save_alloc(self, show_message=True):
    if not self.final_plan:
        return False
    try:
        self.state_mgr.save_allocation(self.final_plan, mode=getattr(self,'_pending_strat',''))
        self._load_data(reload_priority=False)
        _sync_saved_allocation_to_roles(self)
        self._allocation_dirty=False
        if show_message:
            QMessageBox.information(self,"保存成功","配装保存成功")
        return True
    except Exception as e:
        QMessageBox.critical(self,"失败",str(e))
        return False


def _sync_saved_allocation_to_roles(self):
    """将新保存的装备锁定同步到角色功能，供优化替换直接使用。"""
    equipped_state = getattr(self, "equipped_state", None)
    if not isinstance(equipped_state, dict) or not equipped_state:
        return
    try:
        result = import_all_role_equipment(equipped_state)
        my_roles = result.get("my_roles", {}) if isinstance(result, dict) else {}
        form_data = getattr(self, "_my_role_form_data", None)
        if isinstance(form_data, dict):
            for role_name in equipped_state:
                if role_name in my_roles:
                    form_data[role_name] = my_roles[role_name]
        logger.info(
            f"保存装备锁定已同步角色功能: 成功 {result.get('imported', 0)}，"
            f"跳过 {result.get('skipped', 0)}，失败 {len(result.get('failed', []) or [])}"
        )
    except Exception as exc:
        # 已保存的装备锁定不能因角色功能的同步异常而回滚。
        logger.warning(f"保存装备锁定后同步角色功能失败: {exc}")

def _archive_pending_screenshots(self):
    paths=list(getattr(self,'_pending_archive_paths',[]) or [])
    if not paths:
        return 0
    archive_dir=runtime.SCREENSHOT_DIR/"archive"
    archive_dir.mkdir(parents=True,exist_ok=True)
    archived_count=0
    for src in paths:
        src_path=Path(src)
        if not src_path.exists():
            continue
        dst=archive_dir/src_path.name
        base=dst.with_suffix("")
        ext=dst.suffix
        suffix=1
        while dst.exists():
            dst=Path(f"{base}_{suffix}{ext}")
            suffix+=1
        shutil.move(str(src_path),str(dst))
        archived_count+=1
    self._pending_archive_paths=[]
    if archived_count:
        logger.success(f"已归档 {archived_count} 张已保存配装的截图。")
    return archived_count
