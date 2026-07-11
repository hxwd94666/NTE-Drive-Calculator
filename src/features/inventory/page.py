# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.features.role.equipment_import import equipment_from_saved_state, import_all_role_equipment, import_role_equipment
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
           '_import_to_my_role', '_import_all_to_my_roles', '_save_eq']

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
    import_all_btn=QPushButton("一键导入"); import_all_btn.setObjectName("btnPrimary"); import_all_btn.clicked.connect(self._import_all_to_my_roles)
    sh.addWidget(import_all_btn); l.addLayout(sh)
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
    sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:15px;font-weight:800;color:{gc};border:none")
    slb.addWidget(QLabel("评分")); slb.addWidget(sv); role_hdr.addWidget(sf)
    # Grade
    gf=QFrame()
    gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
    gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:15px;font-weight:800;color:{gc};border:none")
    glb.addWidget(QLabel("评级")); glb.addWidget(gv); role_hdr.addWidget(gf)
    del_btn=QPushButton("删除"); del_btn.setObjectName("btnDanger")
    del_btn.setFixedSize(64,32)
    del_btn.clicked.connect(lambda _=False, rn=role_name: self._delete_role_equipment(rn))
    role_hdr.addWidget(del_btn)
    import_btn = QPushButton("导入")
    import_btn.setObjectName("btnPrimary")  # 蓝色主按钮样式
    import_btn.clicked.connect(lambda _, rn=role_name: self._import_to_my_role(rn))
    role_hdr.addWidget(import_btn)
    gl.addLayout(role_hdr); gl.addSpacing(6)

    bp=rd.get(ROLE_BLUEPRINT_LAYOUT,[])
    drives=rd.get(ROLE_EQUIPPED_DRIVES,[])
    if bp:
        gl.addWidget(self._section_label("拼图图纸:"))
        bp_row=QHBoxLayout(); bp_row.setSpacing(44)
        bp_row.addWidget(PuzzleBoardWidget(bp),0,Qt.AlignTop)
        bp_row.addWidget(
            self._role_bonus_summary_panel(
                role_name,
                tape_data,
                drives,
                compare_with_saved=bool(last_diff.get(DIFF_CHANGED)),
                priority_stats=self._role_stat_priority_stats(role_name) if hasattr(self,"_role_stat_priority_stats") else [],
            ),
            1,
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
        gl.addWidget(self._equip_card(tape_data.get(EQUIP_SET_NAME,""),tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),None,tape_data.get(EQUIP_UID,""),wts,(t_s,t_g),t_q,is_new=bool(tape_data.get(EQUIP_IS_NEW)) and not tape_changed,is_changed=tape_changed,main_weights=main_wts))
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
            gl.addWidget(self._equip_card(d.get(EQUIP_SHAPE_ID,""),"",d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),d.get(EQUIP_UID,""),wts,(d_s,d_g),d_q,is_new=bool(d.get(EQUIP_IS_NEW)) and not drive_changed,is_changed=drive_changed))
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


def _import_to_my_role(self, role_name: str):
    """将当前角色的配装（蓝图布局和驱动列表）导入到 my_roles.json 的 drive 字段"""
    eq = self.equipped_state.get(role_name)
    if not eq:
        QMessageBox.warning(self, "导入失败", f"未找到角色 [{role_name}] 的配装数据。")
        return

    bp_layout, drives, new_tape = equipment_from_saved_state(eq)

    if not bp_layout and not drives and not new_tape:
        QMessageBox.information(self, "导入", f"[{role_name}] 当前配装中蓝图和驱动/空幕均为空，无需导入。")
        return

    try:
        my_roles = import_role_equipment(role_name, bp_layout, drives, new_tape)

        role_entry = my_roles.get(role_name, {})
        if hasattr(self, "_my_role_form_data") and self._my_role_form_data is not None:
            self._my_role_form_data[role_name] = role_entry

        QMessageBox.information(
            self, "导入成功",
            f"[{role_name}] 的配装已导入到 my_roles.json\n蓝图数量：{len(bp_layout)}\n驱动数量：{len(drives)}"
        )

    except Exception as e:
        QMessageBox.critical(self, "导入错误", f"操作失败：{str(e)}")
        logger.error(f"导入配装失败: {e}")


def _import_all_to_my_roles(self):
    """将当前所有已保存配装批量覆盖导入角色配置。"""
    if not self.equipped_state:
        QMessageBox.information(self, "一键导入", "当前没有已保存的配装。")
        return

    ret=QMessageBox.question(
        self,
        "一键导入配装",
        "确定要将当前所有已保存配装覆盖导入角色功能吗？\n"
        "对应角色的图纸、驱动、卡带和套装加成会以当前配装为准；当前配装没有卡带的角色会清除旧卡带。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return

    try:
        result=import_all_role_equipment(self.equipped_state)
        my_roles=result.get("my_roles",{})
        if hasattr(self,"_my_role_form_data") and self._my_role_form_data is not None:
            for role_name in self.equipped_state.keys():
                if role_name in my_roles:
                    self._my_role_form_data[role_name]=my_roles[role_name]

        imported=result.get("imported",0)
        skipped=result.get("skipped",0)
        failed=result.get("failed",[]) or []
        msg=f"已导入 {imported} 个角色配装。"
        if skipped:
            msg+=f"\n跳过 {skipped} 个空配装。"
        if failed:
            msg+=f"\n失败 {len(failed)} 个：\n" + "\n".join(f"- {item.get('role')}: {item.get('error')}" for item in failed[:5])
            QMessageBox.warning(self,"一键导入完成",msg)
        else:
            QMessageBox.information(self,"一键导入完成",msg)
    except Exception as e:
        QMessageBox.critical(self,"一键导入失败",f"操作失败：{str(e)}")
        logger.error(f"批量导入配装失败: {e}")


def _save_eq(self):
    with open(runtime.USER_CONFIG_DIR/"equipped_state.json","w",encoding="utf-8") as f: json.dump(self.equipped_state,f,ensure_ascii=False,indent=4); logger.success("装备状态已保存")
