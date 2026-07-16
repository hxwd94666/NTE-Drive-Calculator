# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QInputDialog, QLineEdit, QMessageBox, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.features.drive_assembly.ui_bridge import (
    build_all_role_assembly_plan,
    build_single_role_assembly_plan,
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
    summarize_assembly_plan,
)
from src.features.drive_assembly.executor import (
    AssemblyExecutionStopped,
)
from src.features.role.equipment_import import equipment_from_saved_state, import_role_equipment
from src.features.role.drive_widget import _show_drive_optimization, _show_tape_optimization
from src.features.role.dao import save_my_roles
from src.features.role.page import _save_pending_role_equipment_state
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
)
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import match_pinyin as _match_pinyin
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_equipment_compare_signature', '_same_equipment_by_ocr', '_page_equipment', '_refresh_equip',
           '_saved_plan_diff_text', '_show_saved_plan_diff_dialog', '_clear_all_equipment', '_delete_role_equipment',
           '_optimize_saved_equipment', '_preview_assemble_role', '_preview_assemble_all_roles',
           '_save_eq']

EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _equipment_compare_signature(self,item):
    return equipment_compare_signature(item)

def _same_equipment_by_ocr(self,left:Path,right:Path):
    return self._scan_lifecycle().same_equipment_by_ocr(left,right)

def _page_equipment(self):
    page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(8)
    sh=QHBoxLayout(); sh.addWidget(QLabel("搜索"))
    self.equip_search=QLineEdit(); self.equip_search.setPlaceholderText("搜索角色名称（支持拼音）..."); self.equip_search.setClearButtonEnabled(True)
    self.equip_search.textChanged.connect(self._refresh_equip); sh.addWidget(self.equip_search)
    clear_btn=QPushButton("清空配装"); clear_btn.setObjectName("btnDanger"); clear_btn.clicked.connect(self._clear_all_equipment)
    sh.addWidget(clear_btn)
    import_all_btn=QPushButton("一键装配"); import_all_btn.setObjectName("btnPrimary"); import_all_btn.clicked.connect(self._preview_assemble_all_roles)
    sh.addWidget(import_all_btn)
    l.addLayout(sh)
    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    self.equip_content=QWidget(); self.equip_content_layout=QVBoxLayout(self.equip_content); scroll.setWidget(self.equip_content)
    l.addWidget(scroll,1); return page

def _clear_equip_content(self):
    while self.equip_content_layout.count():
        it=self.equip_content_layout.takeAt(0)
        if it.widget(): it.widget().deleteLater()

def _reload_equipped_state_from_disk(self):
    user_config_dir=getattr(runtime,"USER_CONFIG_DIR",None)
    if user_config_dir is None:
        return
    state_path=Path(user_config_dir)/"equipped_state.json"
    if not state_path.exists():
        return
    try:
        with open(state_path,"r",encoding="utf-8") as f:
            data=json.load(f)
        if isinstance(data,dict):
            self.equipped_state=data
    except Exception as exc:
        logger.warning(f"刷新配装时读取 equipped_state.json 失败: {exc}")

def _refresh_equip(self):
    _reload_equipped_state_from_disk(self)
    _clear_equip_content(self)
    eq=self.equipped_state; all_roles=sorted(eq.keys())
    filt=self.equip_search.text().strip() if hasattr(self,'equip_search') else ""
    roles=[]
    for role_name in all_roles:
        if filt and not _match_pinyin(role_name,filt): continue
        rd=eq.get(role_name,{})
        if not isinstance(rd,dict): continue
        roles.append((role_name,rd))

    self._equip_render_token=object()
    token=self._equip_render_token
    self._equip_render_queue=roles
    self._equip_render_index=0
    self._equip_render_stretch_added=False

    if not roles:
        ph=QLabel("暂无已保存的配装。请先执行分配并保存。"); ph.setStyleSheet(themed_style("color:#6e7681;padding:24px")); ph.setAlignment(Qt.AlignCenter); self.equip_content_layout.addWidget(ph)
        self.equip_content_layout.addStretch()
        return

    _render_equip_batch(self, token, EQUIPMENT_INITIAL_RENDER_COUNT)

def _render_equip_batch(self, token, batch_size=None):
    if token is not getattr(self,"_equip_render_token",None):
        return

    queue=getattr(self,"_equip_render_queue",[])
    index=getattr(self,"_equip_render_index",0)
    size=batch_size or EQUIPMENT_RENDER_BATCH_SIZE
    end=min(index+size,len(queue))
    for role_name,rd in queue[index:end]:
        _render_equip_role(self,role_name,rd)
    self._equip_render_index=end
    if end < len(queue):
        QTimer.singleShot(0, lambda: _render_equip_batch(self, token))
    elif not getattr(self,"_equip_render_stretch_added",False):
        self.equip_content_layout.addStretch()
        self._equip_render_stretch_added=True

def _render_equip_role(self, role_name, rd):
    role_cfg=self.roles_db.get(role_name,{})
    wts=role_cfg.get("weights",{})
    main_wts=role_cfg.get("main_weights")

    total_score=0.0
    tape_data=rd.get(ROLE_EQUIPPED_TAPE)
    if ROLE_TOTAL_SCORE in rd and rd.get(ROLE_TOTAL_GRADE):
        total_score=float(rd.get(ROLE_TOTAL_SCORE,0.0) or 0.0)
        total_grade=str(rd.get(ROLE_TOTAL_GRADE) or "D")
    else:
        if tape_data:
            t_q=tape_data.get(EQUIP_QUALITY,"Gold")
            t_s=self._score_tape_dict(tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),wts,t_q,main_wts)
            total_score+=t_s
        for d in rd.get(ROLE_EQUIPPED_DRIVES,[]):
            d_q=d.get(EQUIP_QUALITY,"Gold")
            d_s=self._score_drive_dict(d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),wts,d_q)
            total_score+=d_s
        total_grade=self._calc_grade(total_score,ALLOCATION_TOTAL_SCORE_AREA)
    gc=GRADE_COLORS.get(total_grade,"#58a6ff")
    gbg=theme_rgba(gc, 0.10)

    grp=QGroupBox(""); grp.setStyleSheet(themed_style("QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"))
    gl=QVBoxLayout(grp); gl.setSpacing(10)
    role_hdr=QHBoxLayout(); role_hdr.setSpacing(8)
    rnl=QLabel(role_name)
    rnl.setStyleSheet(f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:4px 14px;background:{theme_rgba('#4dd0e1', 0.10)}")
    role_hdr.addWidget(rnl)
    last_diff=rd.get(ROLE_LAST_DIFF,{}) or {}
    if last_diff.get(DIFF_CHANGED):
        diff_btn=QPushButton("变动")
        diff_btn.setFixedSize(76,32)
        diff_btn.setStyleSheet(themed_style("QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px;min-height:32px}QPushButton:hover{background:#388bfd}"))
        diff_btn.clicked.connect(lambda _=False,rn=role_name,d=last_diff: self._show_saved_plan_diff_dialog(rn,d))
        role_hdr.addWidget(diff_btn)
    _sm=rd.get("strategy_mode","")
    if _sm:
        _ml={"role_priority":"角色优先","drive_priority":"驱动优先","global_optimal":"全局最优","update_mode":"增量更新"}.get(_sm,_sm)
        sml=QLabel(_ml); sml.setStyleSheet(themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px"))
        role_hdr.addWidget(sml)
    role_hdr.addStretch()
    # Score
    sf=QFrame()
    sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    slb=QHBoxLayout(sf); slb.setSpacing(6); slb.setContentsMargins(4,0,4,0)
    sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
    slb.addWidget(QLabel("评分")); slb.addWidget(sv); role_hdr.addWidget(sf)
    # Grade
    gf=QFrame()
    gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
    gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
    glb.addWidget(QLabel("评级")); glb.addWidget(gv); role_hdr.addWidget(gf)
    del_btn=QPushButton("删除"); del_btn.setObjectName("btnDanger")
    del_btn.setFixedSize(64,32)
    del_btn.clicked.connect(lambda _=False, rn=role_name: self._delete_role_equipment(rn))
    role_hdr.addWidget(del_btn)
    import_btn = QPushButton("装配")
    import_btn.setObjectName("btnPrimary")
    import_btn.clicked.connect(lambda _, rn=role_name: self._preview_assemble_role(rn))
    role_hdr.addWidget(import_btn)
    gl.addLayout(role_hdr); gl.addSpacing(6)

    bp=rd.get(ROLE_BLUEPRINT_LAYOUT,[])
    drives=rd.get(ROLE_EQUIPPED_DRIVES,[])
    if bp:
        gl.addWidget(self._section_label("拼图图纸:"))
        compare_with_saved=bool(last_diff.get(DIFF_CHANGED))
        bp_row=QHBoxLayout(); bp_row.setSpacing(18)
        bp_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        bp_row.addWidget(PuzzleBoardWidget(bp),0,Qt.AlignTop)
        bp_row.addWidget(
            self._role_bonus_summary_panel(
                role_name,
                tape_data,
                drives,
                compare_with_saved=compare_with_saved,
                priority_stats=self._role_stat_priority_stats(role_name),
            ),
            1 if compare_with_saved else 0,
            Qt.AlignTop,
        )
        gl.addLayout(bp_row)
    if tape_data:
        t_q=tape_data.get(EQUIP_QUALITY,"Gold")
        if EQUIP_SCORE in tape_data and tape_data.get(EQUIP_GRADE):
            t_s=float(tape_data.get(EQUIP_SCORE,0.0) or 0.0)
            t_g=str(tape_data.get(EQUIP_GRADE) or "D")
        else:
            t_s=self._score_tape_dict(tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),wts,t_q,main_wts)
            t_g=self._calc_grade(t_s,15)
        gl.addWidget(self._section_label("卡带:"))
        tape_changed=bool(tape_data.get(EQUIP_IS_CHANGED))
        tape_uid=tape_data.get(EQUIP_UID,"")
        gl.addWidget(self._equip_card(tape_data.get(EQUIP_SET_NAME,""),tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),None,tape_uid,wts,(t_s,t_g),t_q,is_new=bool(tape_data.get(EQUIP_IS_NEW)) and not tape_changed,is_changed=tape_changed,main_weights=main_wts,replacement_callback=lambda rn=role_name, uid=tape_uid: self._optimize_saved_equipment(rn,"tape",uid),card_variant="inventory"))
    if drives:
        gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
        for d in drives:
            d_q=d.get(EQUIP_QUALITY,"Gold")
            if EQUIP_SCORE in d and d.get(EQUIP_GRADE):
                d_s=float(d.get(EQUIP_SCORE,0.0) or 0.0)
                d_g=str(d.get(EQUIP_GRADE) or "D")
            else:
                d_s=self._score_drive_dict(d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),wts,d_q)
                d_g=self._calc_grade(d_s,self._shape_areas.get(d.get(EQUIP_SHAPE_ID,""),3))
            drive_changed=bool(d.get(EQUIP_IS_CHANGED))
            drive_uid=d.get(EQUIP_UID,"")
            gl.addWidget(self._equip_card(d.get(EQUIP_SHAPE_ID,""),"",d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),drive_uid,wts,(d_s,d_g),d_q,is_new=bool(d.get(EQUIP_IS_NEW)) and not drive_changed,is_changed=drive_changed,replacement_callback=lambda rn=role_name, uid=drive_uid: self._optimize_saved_equipment(rn,"drive",uid),card_variant="inventory"))
    self.equip_content_layout.addWidget(grp)

def _saved_plan_diff_text(self, role_name, diff):
    removed=diff.get(DIFF_REMOVED,[]) or []
    added=diff.get(DIFF_ADDED,[]) or []
    if not removed and not added:
        return "本次保存与上一套方案没有装备变动。"
    lines=[f"{role_name} 配装变动："]
    if removed:
        lines.append("\n卸下：")
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n换上：")
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)

def _show_saved_plan_diff_dialog(self, role_name, diff):
    if hasattr(self, "_build_plan_diff_dialog"):
        self._build_plan_diff_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self,"配装变动",self._saved_plan_diff_text(role_name,diff))

def _clear_all_equipment(self):
    if not self.equipped_state:
        QMessageBox.information(self,"清空配装","当前没有已保存的配装。")
        return
    ret=QMessageBox.question(
        self,
        "清空配装",
        "确定要清空所有角色的已保存配装吗？\n这会解除增量扫描中的装备锁定。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return
    self.equipped_state={}
    self._save_eq()
    self._refresh_equip()
    logger.success("已清空所有角色配装")

def _delete_role_equipment(self, role_name: str):
    if role_name not in self.equipped_state:
        self._refresh_equip()
        return
    ret=QMessageBox.question(
        self,
        "删除角色配装",
        f"确定要删除 [{role_name}] 的已保存配装吗？\n该角色占用的驱动/卡带将不再被锁定。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return
    self.equipped_state.pop(role_name,None)
    self._save_eq()
    self._refresh_equip()
    logger.success(f"已删除角色配装: {role_name}")


def _optimize_saved_equipment(self, role_name: str, item_kind: str, uid: str):
    """从配装卡片打开角色功能的优化替换，并将结果立即同步回装备锁定。"""
    role_state=(getattr(self,"equipped_state",{}) or {}).get(role_name)
    if not isinstance(role_state,dict):
        QMessageBox.warning(self,"优化替换","未找到该角色的已保存配装。")
        return

    role_data=(getattr(self,"_my_role_form_data",{}) or {}).get(role_name)
    current_item=None
    if isinstance(role_data,dict):
        if item_kind=="drive":
            current_item=next((d for d in role_data.get("drive",{}).get("drives",[]) or [] if str(d.get("uid",""))==str(uid)),None)
        else:
            tape=role_data.get("tape",{})
            current_item=tape if isinstance(tape,dict) and str(tape.get("uid",""))==str(uid) else None

    # 兼容旧的装备锁定：首次优化时补齐角色功能所需的数据结构。
    if not current_item:
        try:
            bp_layout, drives, tape=equipment_from_saved_state(role_state)
            my_roles=import_role_equipment(role_name,bp_layout,drives,tape)
            self._my_role_form_data=my_roles
            role_data=my_roles.get(role_name,{})
            if item_kind=="drive":
                current_item=next((d for d in role_data.get("drive",{}).get("drives",[]) or [] if str(d.get("uid",""))==str(uid)),None)
            else:
                tape_data=role_data.get("tape",{})
                current_item=tape_data if isinstance(tape_data,dict) and str(tape_data.get("uid",""))==str(uid) else None
        except Exception as exc:
            logger.error(f"准备 {role_name} 的优化替换数据失败: {exc}")
            QMessageBox.critical(self,"优化替换失败",str(exc))
            return

    if not current_item:
        QMessageBox.warning(self,"优化替换","当前装备已变化，请刷新后重试。")
        return

    def _save_replacement():
        if not save_my_roles(self._my_role_form_data):
            QMessageBox.warning(self,"保存失败","无法保存角色功能的替换结果。")
            return
        _save_pending_role_equipment_state(self,self._my_role_form_data)
        self._my_role_dirty=False
        logger.success(f"已从配装页优化替换 {role_name} 的{item_kind}: {uid}")

    weights=(getattr(self,"roles_db",{}) or {}).get(role_name,{}).get("weights",{})
    if item_kind=="drive":
        _show_drive_optimization(self,role_name,current_item,weights,_save_replacement)
    else:
        _show_tape_optimization(self,role_name,current_item,weights,_save_replacement)


def _assembly_report_dialog(action_name: str, report, expected_role_count: int | None = None):
    """Build a completion/warning dialog from a game assembly execution report."""
    role_count = len(getattr(report, "role_reports", []) or [])
    action_count = getattr(report, "executed_actions", 0)
    missing = list(getattr(report, "missing_roles", []) or [])
    skipped = list(getattr(report, "skipped_roles", []) or [])
    duplicates = list(getattr(report, "duplicate_roles", []) or [])
    unrecognized = list(getattr(report, "unrecognized_roles", []) or [])
    verification_failures = list(getattr(report, "verification_failures", []) or [])

    incomplete = bool(missing or skipped or duplicates or unrecognized or verification_failures)
    if expected_role_count is not None and role_count < expected_role_count:
        incomplete = True
    if role_count == 0:
        incomplete = True

    title = f"{action_name}未完成" if incomplete else f"{action_name}完成"
    lines = [f"已装配 {role_count} 个角色，执行 {action_count} 个动作。"]
    if expected_role_count is not None and role_count < expected_role_count:
        lines.append(f"预计装配 {expected_role_count} 个角色，还有 {expected_role_count - role_count} 个未完成。")
    if missing:
        lines.append("未找到角色：" + "、".join(str(role) for role in missing))
    if skipped:
        lines.append("跳过角色：" + "、".join(str(role) for role in skipped))
    if duplicates:
        lines.append(f"重复识别角色槽位：{len(duplicates)} 个。")
    if unrecognized:
        lines.append(f"未识别角色槽位：{len(unrecognized)} 个。")
        for entry in unrecognized:
            if not isinstance(entry, dict):
                lines.append(f"- {entry}")
                continue
            if entry.get("roster_index") is not None:
                position = f"第 {int(entry['roster_index']) + 1} 个角色"
            elif entry.get("page_index") is not None and entry.get("slot_index") is not None:
                position = f"第 {int(entry['page_index']) + 1} 页第 {int(entry['slot_index']) + 1} 个角色"
            else:
                position = "未知位置"
            raw_text = str(entry.get("raw_text") or "").strip() or "未读取到文字"
            lines.append(f"- {position}（OCR：{raw_text}）")
    if verification_failures:
        lines.append(f"图纸截图校验失败：{len(verification_failures)} 个。")
        for failure in verification_failures:
            if not isinstance(failure, dict):
                continue
            role_name = str(failure.get("role_name") or "未知角色")
            block_ids = [
                str(item.get("block_id"))
                for item in (failure.get("missing_blocks") or [])
                if isinstance(item, dict) and item.get("block_id") is not None
            ]
            if block_ids:
                lines.append(f"- {role_name}：未通过校验的驱动块 #{'、#'.join(block_ids)}")
    if incomplete:
        lines.append("请检查角色识别结果后重新执行。")
    return title, "\n".join(lines), not incomplete


def _prompt_protagonist_alias_if_needed(self, role_names) -> dict[str, str]:
    roles = {str(role).strip() for role in (role_names or []) if str(role).strip()}
    if "主角" not in roles:
        return {}
    default_name = str(getattr(self, "_drive_assembly_protagonist_name", "") or "").strip()
    player_name, ok = QInputDialog.getText(
        self,
        "主角名称",
        "请输入游戏中主角显示的名字：",
        QLineEdit.Normal,
        default_name,
    )
    if not ok:
        return {}
    player_name = str(player_name).strip()
    if not player_name:
        QMessageBox.warning(self, "主角名称", "需要输入主角在游戏中显示的名字。")
        return {}
    self._drive_assembly_protagonist_name = player_name
    return {"主角": player_name}


def _preview_assemble_role(self, role_name: str):
    """生成并执行单个角色的游戏内装配动作计划。"""
    _reload_equipped_state_from_disk(self)
    try:
        plan=build_single_role_assembly_plan(self.equipped_state, role_name)
        summary=summarize_assembly_plan(plan)
        if not plan.get("available"):
            QMessageBox.warning(self,"装配计划",summary)
            return
        role_name_aliases = _prompt_protagonist_alias_if_needed(self, [role_name])
        if role_name == "主角" and not role_name_aliases:
            return
        ret=QMessageBox.question(
            self,
            "装配计划",
            summary + "\n\n确认后将接管鼠标点击/拖拽。请先切回游戏的装配页面，并保持页面不动。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret!=QMessageBox.Yes:
            return
        minimize = getattr(self, "showMinimized", None)
        if callable(minimize):
            minimize()
        report=execute_selected_role_from_current_game_page(
            self.equipped_state,
            role_name,
            role_name_aliases=role_name_aliases,
        )
        title, message, completed = _assembly_report_dialog("单角色装配", report, expected_role_count=1)
        if completed:
            QMessageBox.information(self,title,message)
        else:
            QMessageBox.warning(self,title,message)
        logger.info(f"已执行 [{role_name}] 装配动作：{report.executed_actions}")
    except AssemblyExecutionStopped:
        QMessageBox.warning(self,"装配已停止",f"[{role_name}] 装配执行已停止。")
        logger.warning(f"[{role_name}] 装配执行已停止")
    except Exception as e:
        QMessageBox.critical(self,"装配执行失败",f"执行 [{role_name}] 装配失败：{str(e)}")
        logger.error(f"执行单角色装配失败: {e}")


def _preview_assemble_all_roles(self):
    """生成并执行所有已保存角色的游戏内装配动作计划。"""
    _reload_equipped_state_from_disk(self)
    if not self.equipped_state:
        QMessageBox.information(self,"一键装配","当前没有已保存的配装。")
        return
    try:
        plan=build_all_role_assembly_plan(self.equipped_state)
        summary=summarize_assembly_plan(plan)
        planned_roles = [str(role) for role in (plan.get("roles") or self.equipped_state.keys())]
        role_name_aliases = _prompt_protagonist_alias_if_needed(self, planned_roles)
        if "主角" in planned_roles and not role_name_aliases:
            return
        ret=QMessageBox.question(
            self,
            "一键装配",
            summary + "\n\n确认后将等待 3 秒，请在倒计时内切回游戏的信息页面，并保持右侧角色列表可见。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret!=QMessageBox.Yes:
            return
        minimize = getattr(self, "showMinimized", None)
        if callable(minimize):
            minimize()
        report=execute_all_roles_from_current_game_page(self.equipped_state, role_name_aliases=role_name_aliases)
        title, message, completed = _assembly_report_dialog(
            "一键装配",
            report,
            expected_role_count=len(planned_roles),
        )
        if completed:
            QMessageBox.information(self,title,message)
        else:
            QMessageBox.warning(self,title,message)
        logger.info(f"Assembly executed: {len(report.role_reports)} roles, {report.executed_actions} actions")
        return
        QMessageBox.information(self,"一键装配完成",f"已执行 {len(report.role_reports)} 个角色，{report.executed_actions} 个动作。")
        logger.info(f"已执行一键装配：{len(report.role_reports)} 个角色，{report.executed_actions} 个动作")
    except AssemblyExecutionStopped:
        QMessageBox.warning(self,"一键装配已停止","装配执行已停止。")
        logger.warning("一键装配执行已停止")
    except Exception as e:
        QMessageBox.critical(self,"一键装配失败",f"执行一键装配失败：{str(e)}")
        logger.error(f"执行全角色装配失败: {e}")


def _save_eq(self):
    with open(runtime.USER_CONFIG_DIR/"equipped_state.json","w",encoding="utf-8") as f: json.dump(self.equipped_state,f,ensure_ascii=False,indent=4); logger.success("装备状态已保存")
