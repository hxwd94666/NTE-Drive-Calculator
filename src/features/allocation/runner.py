# 执行分配任务并处理保存和归档。
"""MainWindow methods for allocation."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app import runtime
from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread
from src.optimizer.contracts import (
    DIFF_ADDED, DIFF_ADDED_UIDS, DIFF_CHANGED, DIFF_REMOVED,
    EQUIP_IS_CHANGED, EQUIP_UID, PLAN_ASSIGNED_TAPE, PLAN_BLUEPRINT, PLAN_CHANGED_UIDS, PLAN_SCORE, PLAN_VALID,
    ROLE_BLUEPRINT_LAYOUT, ROLE_EQUIPPED_DRIVES, ROLE_EQUIPPED_TAPE, plan_drives,
)
from src.optimizer.plan_diff import build_plan_diff
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.services.saved_state_loadout_bridge import (
    SavedStateLoadoutBridge, resolve_character_id_for_static_role,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_run_allocation', '_start_allocation_worker', '_confirm_unsaved_allocation_before_recompute', '_on_done', '_on_exec_error', '_save_alloc', '_archive_pending_screenshots']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _run_allocation(self,strat,sel,cs,tape_main_filters=None,crit_priority_modes=None,set_effect_modes=None,priority_groups=None,crit_rate_caps=None,custom_weapons=None):
    try:
        logger.info(f"开始分配计算: 策略={strat}, 角色={sel}")
        if not runtime.USER_DATABASE_PATH.is_file():
            raise RuntimeError("尚无官方背包数据，请先完成背包同步并生成稳定快照。")
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao, StaticGameDataDao() as static_dao:
            projection = SqliteAllocationInventory(user_dao, static_dao).build()
        allocation_options = {
            "tape_main_filters": tape_main_filters or {},
            "crit_priority_modes": crit_priority_modes or {},
            "set_effect_modes": set_effect_modes or {},
            "priority_groups": priority_groups,
            "crit_rate_caps": crit_rate_caps or {},
            "custom_weapons": custom_weapons or {},
        }
        logger.info(
            f"使用官方背包稳定快照 {projection.snapshot_id} 计算："
            f"候选 {len(projection.items)} 件（其中弃置标记 {projection.discarded_count} 件，仍参与计算）"
        )
        # 求解器只接收本次固定 SQLite 快照的内存投影，不再回退到旧背包 JSON。
        from src.app.facade import NTEAppFacade
        a=NTEAppFacade(config_dir=str(runtime.CONFIG_DIR),user_config_dir=str(runtime.USER_CONFIG_DIR))
        fp,_=a.execute_allocation_inventory(list(projection.items),sel,cs,strat,**allocation_options)
        self._pending_allocation_snapshot_id = projection.snapshot_id
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


def _active_sqlite_loadout_state(database_path) -> dict:
    """Build the diff baseline from active official SQLite plans only."""

    state = {}
    with UserDataDao(database_path) as user_dao:
        for role_name, plan in user_dao.list_active_loadout_plans_by_role().items():
            tape = None
            drives = []
            for assignment in plan.get("assignments") or []:
                kind = str(assignment.get("kind") or "")
                try:
                    uid = f"nte-{'module' if kind == 'module' else 'core'}-{int(assignment['uid_slot'])}-{int(assignment['uid_serial'])}"
                except (KeyError, TypeError, ValueError):
                    continue
                if kind == "core":
                    tape = {EQUIP_UID: uid}
                elif kind == "module":
                    drives.append({EQUIP_UID: uid})
            state[role_name] = {
                ROLE_EQUIPPED_TAPE: tape,
                ROLE_EQUIPPED_DRIVES: drives,
            }
    return state


def _sqlite_allocation_plan_diff(database_path, final_plan: dict) -> dict:
    """Compare a calculation with the currently displayed SQLite loadouts."""

    return build_plan_diff(_active_sqlite_loadout_state(database_path), final_plan)


def _calculation_plan_diff(self, final_plan: dict) -> dict:
    """Prefer active SQLite plans; retain a no-database test-host fallback."""

    database_path = getattr(runtime, "USER_DATABASE_PATH", None)
    if database_path:
        try:
            return _sqlite_allocation_plan_diff(database_path, final_plan)
        except Exception as exc:
            logger.warning(f"读取 SQLite 配装差异失败，改用无数据库兼容基线：{exc}")
    state_manager = getattr(self, "state_mgr", None)
    if state_manager is not None and hasattr(state_manager, "load_state"):
        try:
            return build_plan_diff(state_manager.load_state() or {}, final_plan)
        except Exception:
            pass
    return build_plan_diff({}, final_plan)


def _persistable_plan_diff(role_diff: dict | None) -> dict:
    """Convert in-memory diff sets to JSON-compatible plan payload data."""

    source = role_diff or {}
    return {
        DIFF_CHANGED: bool(source.get(DIFF_CHANGED)),
        DIFF_ADDED_UIDS: sorted(str(uid) for uid in (source.get(DIFF_ADDED_UIDS) or ()) if uid),
        DIFF_ADDED: [dict(item) for item in (source.get(DIFF_ADDED) or ()) if isinstance(item, dict)],
        DIFF_REMOVED: [dict(item) for item in (source.get(DIFF_REMOVED) or ()) if isinstance(item, dict)],
    }


def _plan_changed_uids(plan: dict) -> set[str]:
    """Collect explicit green CHANGE markers from a calculated plan."""

    changed = {str(uid) for uid in (plan.get(PLAN_CHANGED_UIDS, set()) or ()) if uid}
    for item in [plan.get(PLAN_ASSIGNED_TAPE), *plan_drives(plan)]:
        value = item.get(EQUIP_IS_CHANGED) if isinstance(item, dict) else getattr(item, EQUIP_IS_CHANGED, False)
        uid = item.get(EQUIP_UID) if isinstance(item, dict) else getattr(item, EQUIP_UID, "")
        if value and uid:
            changed.add(str(uid))
    return changed

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
        self.final_plan=r; self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
        self._allocation_custom_weapons=dict(getattr(self,"_pending_custom_weapons",{}) or {})
        if r is None: QMessageBox.warning(self,"提示","计算失败，请确认已同步到稳定的官方背包快照。"); return
        # The old JSON-state path was removed.  Comparing with the active
        # SQLite plans restores NEW/CHANGE labels and the per-role diff button.
        self.allocation_plan_diff=_calculation_plan_diff(self, r)
        self._allocation_dirty=True
        self._render_results(r)
        logger.info("_render_results 完成")
    except Exception as e:
        import traceback as tb
        logger.error(f"_on_done 异常: {e}\n{tb.format_exc()}")
        QMessageBox.critical(self,"渲染失败",f"{e}")

def _on_exec_error(self,err):
    self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始计算")
    QMessageBox.critical(self,"计算失败",f"发生错误:\n{err}")

def _save_alloc(self, show_message=True):
    if not self.final_plan:
        return False
    try:
        snapshot_id=getattr(self,"_pending_allocation_snapshot_id",None)
        if snapshot_id is None:
            raise RuntimeError("本次计算未绑定官方背包快照，请重新执行计算。")
        saved_roles=[]
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao, StaticGameDataDao() as static_dao:
            bridge=SavedStateLoadoutBridge(user_dao,static_dao)
            for role_name,plan in self.final_plan.items():
                if not isinstance(plan,dict) or not plan.get(PLAN_VALID):
                    continue
                character_id=resolve_character_id_for_static_role(role_name,static_dao,user_dao,snapshot_id=snapshot_id)
                role_diff = (getattr(self, "allocation_plan_diff", {}) or {}).get(role_name, {})
                bridge.save_role_plan(
                    role_name=role_name, role_state=_role_state_from_plan(plan),
                    character_id=character_id, snapshot_id=snapshot_id,
                    name=f"计算方案：{role_name}", score=float(plan.get(PLAN_SCORE,0.0) or 0.0),
                    payload={
                        "schema": "allocation-official-snapshot-v1",
                        "source": "allocation",
                        "source_role_name": role_name,
                        "strategy": getattr(self, "_pending_strat", ""),
                        "last_diff": _persistable_plan_diff(role_diff),
                        "changed_uids": sorted(_plan_changed_uids(plan)),
                    },
                )
                saved_roles.append(role_name)
        if not saved_roles:
            raise RuntimeError("本次计算没有可保存的有效方案。")
        self._allocation_dirty=False
        # Active plans are the character-page equipment source.  Refresh both
        # projections immediately so a saved calculation is visible as the
        # role's drive/core context without writing any template/profile data.
        refresh_roles = getattr(self, "_refresh_my_role", None)
        if callable(refresh_roles):
            refresh_roles()
        refresh_equipment = getattr(self, "_refresh_equip", None)
        if callable(refresh_equipment):
            refresh_equipment()
        if show_message:
            QMessageBox.information(self,"保存成功",f"已将 {len(saved_roles)} 个方案保存到官方 SQLite 数据库，并同步到角色与配装页面。")
        return True
    except Exception as e:
        QMessageBox.critical(self,"失败",str(e))
        return False


def _role_state_from_plan(plan: dict) -> dict:
    """构造仅存在于内存中的转换对象；不读写旧 JSON。"""
    board=[]
    for row in plan.get(PLAN_BLUEPRINT,{}).get("board",[]) or []:
        board.append(["XX" if cell == -1 else "0" if cell == 0 else str(cell) for cell in row])
    tape=plan.get(PLAN_ASSIGNED_TAPE)
    return {
        ROLE_BLUEPRINT_LAYOUT: board,
        ROLE_EQUIPPED_TAPE: {EQUIP_UID:tape.uid} if tape is not None else None,
        ROLE_EQUIPPED_DRIVES: [{EQUIP_UID:drive.uid,"shape_id":drive.shape_id} for drive in plan_drives(plan)],
    }

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
