# 渲染配装结果、评分和属性汇总。
"""MainWindow methods for allocation."""

from __future__ import annotations

import json
import re
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, current_style_sheet, current_theme_name, theme_color, theme_rgba, themed_style
from src.features.role.dao import load_my_roles
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_ITEM_TYPE,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
    PLAN_ASSIGNED_TAPE,
    PLAN_BLUEPRINT,
    PLAN_CHANGED_UIDS,
    PLAN_SCORE,
    PLAN_VALID,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_SCORE_AREA,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
    plan_drives,
)
from src.ui.puzzle_board import PuzzleBoardWidget, get_shape_pixmap as _get_shape_pixmap
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = [
    '_section_label', '_render_results', '_calc_grade', '_show_plan_diff_dialog', '_build_plan_diff_dialog',
    '_diff_item_card', '_diff_item_score_info', '_plan_diff_text', '_sync_role_drive_replacement',
    '_sync_role_tape_replacement', '_stat_w', '_stat_c', '_weighted_score', '_quality_coef',
    '_canonical_stat_name', '_stat_number_value', '_item_value', '_add_stat_total', '_fallback_tape_main_value',
    '_extra_shape_area', '_equipment_bonus_rows', '_get_my_role_entry', '_role_base_bonus_rows',
    '_merge_bonus_row_lists', '_bonus_rows_for_mode', '_bonus_summary_mode_label', '_make_bonus_mode_switch',
    '_clear_layout_widgets', '_format_bonus_value', '_bonus_summary_widget', '_role_stat_priority_stats',
    '_sort_bonus_aligned_rows', '_role_bonus_summary_panel', '_refresh_bonus_summary_panel',
    '_aligned_bonus_comparison_rows', '_has_bonus_delta', '_bonus_row_widget', '_bonus_placeholder_row_widget',
    '_bonus_spacer_row', '_bonus_comparison_column', '_bonus_delta_row_widget', '_bonus_delta_column',
    '_bonus_comparison_widget', '_show_bonus_summary_dialog', '_show_bonus_comparison_dialog',
    '_score_drive_dict', '_score_tape_dict', '_equip_card',
]


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _section_label(self,text):
    label=QLabel(text)
    label.setStyleSheet(themed_style("font-size:14px;font-weight:700;color:#c9d1d9;border:none;background:transparent;padding:2px 0"))
    return label

def _render_results(self,plan):
    if not plan: return
    self.result_card.setVisible(True)
    while self.result_content_layout.count():
        it=self.result_content_layout.takeAt(0)
        if it.widget(): it.widget().deleteLater()
    mode_labels={"role_priority":"角色优先","drive_priority":"驱动优先","global_optimal":"全局最优","update_mode":"增量更新"}
    mode_name=mode_labels.get(getattr(self,'_pending_strat',''),'')
    plan_diffs=getattr(self,"allocation_plan_diff",{}) or {}
    for role,p in plan.items():
        if not p or not p.get(PLAN_VALID):
            self.result_content_layout.addWidget(QLabel(f"❌ {role}: 无有效配装方案")); continue
        role_diff=plan_diffs.get(role,{}) or {}
        added_uids=set(role_diff.get(DIFF_ADDED_UIDS,set()) or set())
        changed_uids=set(p.get(PLAN_CHANGED_UIDS,set()) or set()) if isinstance(p,dict) else set()
        total_score=p.get(PLAN_SCORE,0); total_grade=self._calc_grade(total_score,ALLOCATION_TOTAL_SCORE_AREA)
        gc=GRADE_COLORS.get(total_grade,"#58a6ff")
        gbg=theme_rgba(gc, 0.10)

        grp=QGroupBox(""); grp.setStyleSheet(themed_style("QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"))
        gl=QVBoxLayout(grp); gl.setSpacing(10)
        # Role header: name + score + grade side by side, compact
        role_hdr=QHBoxLayout(); role_hdr.setSpacing(8)
        # Role name with different color from stat blocks - use teal/cyan tone
        rnl=QLabel(role)
        rnl.setStyleSheet(f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:4px 14px;background:{theme_rgba('#4dd0e1', 0.10)}")
        role_hdr.addWidget(rnl)
        if role_diff.get(DIFF_CHANGED):
            diff_btn=QPushButton("变动")
            diff_btn.setFixedSize(76,32)
            diff_btn.setStyleSheet(themed_style("QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px;min-height:32px}QPushButton:hover{background:#388bfd}"))
            diff_btn.clicked.connect(lambda _checked=False,rn=role,d=role_diff: self._show_plan_diff_dialog(rn,d))
            role_hdr.addWidget(diff_btn)
        if mode_name:
            ml=QLabel(mode_name); ml.setStyleSheet(themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px"))
            role_hdr.addWidget(ml)
        role_hdr.addStretch()
        # Score badge (separate)
        sf=QFrame()
        sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        slb=QHBoxLayout(sf); slb.setSpacing(6); slb.setContentsMargins(4,0,4,0)
        sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:15px;font-weight:800;color:{gc};border:none")
        slb.addWidget(QLabel("评分")); slb.addWidget(sv)
        role_hdr.addWidget(sf)
        # Grade badge (separate)
        gf=QFrame()
        gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
        gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:15px;font-weight:800;color:{gc};border:none")
        glb.addWidget(QLabel("评级")); glb.addWidget(gv)
        role_hdr.addWidget(gf)
        gl.addLayout(role_hdr); gl.addSpacing(6)

        board=p.get(PLAN_BLUEPRINT,{}).get("board",[])
        role_cfg=self.roles_db.get(role,{})
        wts=role_cfg.get("weights",{})
        main_wts=role_cfg.get("main_weights")

        tape=p.get(PLAN_ASSIGNED_TAPE)
        drives=plan_drives(p)
        if board:
            gl.addWidget(self._section_label("拼图图纸:"))
            bp_row=QHBoxLayout(); bp_row.setSpacing(44)
            bp_row.addWidget(PuzzleBoardWidget(board),0,Qt.AlignTop)
            bp_row.addWidget(
                self._role_bonus_summary_panel(
                    role,
                    tape,
                    drives,
                    compare_with_saved=bool(role_diff.get(DIFF_CHANGED)),
                    priority_stats=self._role_stat_priority_stats(role),
                ),
                1,
                Qt.AlignTop,
            )
            gl.addLayout(bp_row); gl.addSpacing(8)

        if tape:
            t_score=tape.role_scores.get(role,0) if hasattr(tape,'role_scores') else 0
            t_grade=self._calc_grade(t_score,15)
            tape_uid=str(_diff_value(tape,"uid","") or "")
            tape_changed=bool(_diff_value(tape,"is_changed",False) or tape_uid in changed_uids)
            gl.addWidget(self._section_label("卡带:"))
            gl.addWidget(self._equip_card(tape.set_name,tape.main_stats,tape.sub_stats,None,tape.uid,wts,(t_score,t_grade),tape.quality,is_new=(tape_uid in added_uids and not tape_changed),is_changed=tape_changed,main_weights=main_wts))

        if drives:
            gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
            for d in drives:
                score=d.role_scores.get(role,0) if hasattr(d,'role_scores') else 0
                grade=self._calc_grade(score,d.area)
                mvp_tag=f" 👑第{d.pick_order}顺位" if getattr(d,'is_mvp',False) else ""
                drive_uid=str(_diff_value(d,"uid","") or "")
                drive_changed=bool(_diff_value(d,"is_changed",False) or drive_uid in changed_uids)
                gl.addWidget(self._equip_card(d.shape_id,"",d.sub_stats,d.shape_id,d.uid+mvp_tag,wts,(score,grade),d.quality,is_new=(drive_uid in added_uids and not drive_changed),is_changed=drive_changed))
        self.result_content_layout.addWidget(grp)
    self.result_content_layout.addStretch()

def _calc_grade(self, score, area):
    max_score = area * 10.0
    if max_score == 0: return "D"
    ratio = score / max_score
    if ratio >= 0.8: return "ACE"
    elif ratio >= 0.7: return "SSS"
    elif ratio >= 0.6: return "SS"
    elif ratio >= 0.5: return "S"
    elif ratio >= 0.4: return "A"
    elif ratio >= 0.3: return "B"
    elif ratio >= 0.2: return "C"
    return "D"

def _plan_diff_text(self, role_name, diff):
    removed=diff.get(DIFF_REMOVED,[]) or []
    added=diff.get(DIFF_ADDED,[]) or []
    if not removed and not added:
        return "本次配装与已保存方案没有装备变动。"
    lines=[f"{role_name} 配装变动："]
    if removed:
        lines.append("\n卸下：")
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n换上：")
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)

def _diff_item_score_info(self, item):
    if EQUIP_SCORE not in item:
        return None
    score=float(item.get(EQUIP_SCORE,0.0) or 0.0)
    grade=item.get(EQUIP_GRADE)
    if not grade:
        area=int(item.get(EQUIP_SCORE_AREA) or item.get(EQUIP_AREA) or (15 if item.get(EQUIP_TYPE)=="tape" else 0) or 0)
        grade=self._calc_grade(score,area) if area else "D"
    return score,str(grade)

def _diff_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)

def _diff_item_type(item):
    explicit=_diff_value(item,EQUIP_TYPE) or _diff_value(item,EQUIP_ITEM_TYPE)
    if explicit:
        return str(explicit)
    if _diff_value(item,EQUIP_SHAPE_ID)=="TAPE_15":
        return "tape"
    main_stats=_diff_value(item,EQUIP_MAIN_STATS)
    return "tape" if isinstance(main_stats,str) and main_stats else "drive"

def _diff_grade(self, score, area):
    calc=getattr(self, "_calc_grade", None)
    if calc:
        return calc(score, area)
    return _calc_grade(self, score, area)

def _diff_snapshot_from_source(self, role_name, source):
    uid=str(_diff_value(source,EQUIP_UID,"") or "")
    if not uid:
        return {}
    item_type=_diff_item_type(source)
    sub_stats=_diff_value(source,EQUIP_SUB_STATS,{}) or {}
    quality=_diff_value(source,EQUIP_QUALITY,"Gold")
    area=int(_diff_value(source,EQUIP_SCORE_AREA) or _diff_value(source,EQUIP_AREA) or (15 if item_type=="tape" else 0) or 0)
    role_scores=_diff_value(source,"role_scores",{}) or {}
    score=_diff_value(source,EQUIP_SCORE)
    if score is None and isinstance(role_scores,dict):
        score=role_scores.get(role_name)
    score_value=None if score is None else round(float(score or 0.0),2)
    grade=_diff_value(source,EQUIP_GRADE)
    if grade is None and score_value is not None and area:
        grade=_diff_grade(self, score_value, area)

    snapshot={
        EQUIP_UID: uid,
        EQUIP_TYPE: item_type,
        EQUIP_DISPLAY_NAME: str(_diff_value(source,EQUIP_DISPLAY_NAME,"") or uid),
        EQUIP_SUB_STATS: sub_stats,
        EQUIP_QUALITY: quality,
    }
    if item_type=="tape":
        snapshot[EQUIP_SET_NAME]=_diff_value(source,EQUIP_SET_NAME,"") or "卡带"
        snapshot[EQUIP_MAIN_STATS]=_diff_value(source,EQUIP_MAIN_STATS,"")
        snapshot[EQUIP_SHAPE_ID]="TAPE_15"
    else:
        snapshot[EQUIP_SHAPE_ID]=_diff_value(source,EQUIP_SHAPE_ID,"") or ""
    if area:
        snapshot[EQUIP_AREA]=area
        snapshot[EQUIP_SCORE_AREA]=area
    if score_value is not None:
        snapshot[EQUIP_SCORE]=score_value
    if grade is not None:
        snapshot[EQUIP_GRADE]=str(grade)
    return snapshot

def _merge_diff_item(base, source):
    merged=dict(base or {})
    for key,value in (source or {}).items():
        if key not in merged or merged[key] in (None,"",{},[]):
            merged[key]=value
    return merged

def _diff_saved_sources(self, role_name):
    role_data=(getattr(self,"equipped_state",{}) or {}).get(role_name,{})
    if not isinstance(role_data,dict):
        role_data={}
    items=[]
    tape=role_data.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape,dict):
        items.append(tape)
    items.extend([item for item in role_data.get(ROLE_EQUIPPED_DRIVES,[]) or [] if isinstance(item,dict)])
    if items:
        return items
    state_mgr=getattr(self,"state_mgr",None)
    if state_mgr is not None and hasattr(state_mgr,"load_state"):
        try:
            loaded=state_mgr.load_state() or {}
        except Exception:
            loaded={}
        role_data=loaded.get(role_name,{}) if isinstance(loaded,dict) else {}
        if isinstance(role_data,dict):
            tape=role_data.get(ROLE_EQUIPPED_TAPE)
            if isinstance(tape,dict):
                items.append(tape)
            items.extend([item for item in role_data.get(ROLE_EQUIPPED_DRIVES,[]) or [] if isinstance(item,dict)])
    return items

def _loadout_uids(tape, drives):
    uids=set()
    uid=str(_diff_value(tape,EQUIP_UID,"") or "") if tape else ""
    if uid:
        uids.add(uid)
    for drive in drives or []:
        drive_uid=str(_diff_value(drive,EQUIP_UID,"") or "")
        if drive_uid:
            uids.add(drive_uid)
    return uids

def _previous_loadout_from_diff(self, role_name, tape, drives, role_diff):
    role_diff=role_diff or {}
    removed=[dict(item) for item in (role_diff.get(DIFF_REMOVED,[]) or []) if isinstance(item,dict)]
    added_uids={str(uid) for uid in (role_diff.get(DIFF_ADDED_UIDS,set()) or set()) if uid}
    for item in role_diff.get(DIFF_ADDED,[]) or []:
        if isinstance(item,dict):
            uid=str(item.get(EQUIP_UID,"") or "")
            if uid:
                added_uids.add(uid)
    kept=[]
    if tape:
        uid=str(_diff_value(tape,EQUIP_UID,"") or "")
        if uid and uid not in added_uids:
            kept.append(tape if isinstance(tape,dict) else _diff_snapshot_from_source(self,role_name,tape))
    for drive in drives or []:
        uid=str(_diff_value(drive,EQUIP_UID,"") or "")
        if uid and uid not in added_uids:
            kept.append(drive if isinstance(drive,dict) else _diff_snapshot_from_source(self,role_name,drive))
    old_items=kept+[_hydrate_diff_item(self,role_name,item) for item in removed]
    return _split_loadout_sources(old_items)

def _resolve_comparison_role_diff(self, role_name):
    plan_diffs=getattr(self,"allocation_plan_diff",{}) or {}
    role_diff=plan_diffs.get(role_name,{}) or {}
    if role_diff.get(DIFF_CHANGED):
        return role_diff
    role_data=(getattr(self,"equipped_state",{}) or {}).get(role_name,{})
    if isinstance(role_data,dict):
        last_diff=role_data.get(ROLE_LAST_DIFF,{}) or {}
        if last_diff.get(DIFF_CHANGED):
            return last_diff
    return role_diff

def _diff_plan_sources(self, role_name):
    plan=(getattr(self,"final_plan",{}) or {}).get(role_name,{})
    if not isinstance(plan,dict):
        return []
    return (
        ([plan.get(PLAN_ASSIGNED_TAPE)] if plan.get(PLAN_ASSIGNED_TAPE) else [])
        + plan_drives(plan)
    )

def _diff_inventory_sources(self):
    output_file=getattr(runtime,"OUTPUT_FILE",None)
    if not output_file:
        return {}
    path_key=str(output_file)
    cached=getattr(self,"_diff_inventory_index_cache",None)
    if cached and cached[0]==path_key:
        return cached[1]
    index={}
    try:
        data=json.loads(output_file.read_text(encoding="utf-8"))
    except Exception:
        data=[]
    if isinstance(data,list):
        for item in data:
            if isinstance(item,dict) and item.get("uid"):
                index[str(item["uid"])]=item
    setattr(self,"_diff_inventory_index_cache",(path_key,index))
    return index

def _parse_diff_display_name(item):
    display=str(item.get(EQUIP_DISPLAY_NAME) or "")
    if not display or "-" not in display:
        return {}
    shape_id, raw_stats=display.split("-",1)
    parsed={"shape_id":shape_id.strip(),"type":"drive"}
    stats={}
    for part in raw_stats.split("|"):
        part=part.strip()
        if "_" not in part:
            continue
        name,value=part.rsplit("_",1)
        try:
            stats[name.strip()]=float(str(value).replace("%","").strip())
        except Exception:
            continue
    if stats:
        parsed["sub_stats"]=stats
    return parsed

def _hydrate_diff_item(self, role_name, item):
    hydrated=dict(item or {})
    uid=str(hydrated.get(EQUIP_UID,"") or "")
    if uid:
        for source in _diff_saved_sources(self, role_name):
            if str(source.get(EQUIP_UID,""))==uid:
                hydrated=_merge_diff_item(hydrated,_diff_snapshot_from_source(self,role_name,source))
                break
        if not hydrated.get(EQUIP_SHAPE_ID) or not hydrated.get(EQUIP_SUB_STATS) or EQUIP_SCORE not in hydrated:
            for source in _diff_plan_sources(self, role_name):
                if str(_diff_value(source,EQUIP_UID,""))==uid:
                    hydrated=_merge_diff_item(hydrated,_diff_snapshot_from_source(self,role_name,source))
                    break
        if not hydrated.get(EQUIP_SHAPE_ID) or not hydrated.get(EQUIP_SUB_STATS) or EQUIP_SCORE not in hydrated:
            source=_diff_inventory_sources(self).get(uid)
            if source:
                hydrated=_merge_diff_item(hydrated,_diff_snapshot_from_source(self,role_name,source))
    if not hydrated.get(EQUIP_SHAPE_ID) or not hydrated.get(EQUIP_SUB_STATS):
        hydrated=_merge_diff_item(hydrated,_parse_diff_display_name(hydrated))
    item_type=hydrated.get(EQUIP_TYPE) or hydrated.get(EQUIP_ITEM_TYPE)
    if item_type:
        hydrated[EQUIP_TYPE]=item_type
    elif hydrated.get(EQUIP_SHAPE_ID)=="TAPE_15":
        hydrated[EQUIP_TYPE]="tape"
    else:
        hydrated[EQUIP_TYPE]="drive"
    if EQUIP_SCORE in hydrated and EQUIP_GRADE not in hydrated:
        area=int(hydrated.get(EQUIP_SCORE_AREA) or hydrated.get(EQUIP_AREA) or (15 if hydrated.get(EQUIP_TYPE)=="tape" else 0) or 0)
        if area:
            hydrated[EQUIP_GRADE]=_diff_grade(self,float(hydrated.get(EQUIP_SCORE) or 0.0),area)
            hydrated[EQUIP_SCORE_AREA]=area
    return hydrated

def _diff_item_card(self, role_name, item, is_new=False):
    item=_hydrate_diff_item(self, role_name, item)
    role_cfg=self.roles_db.get(role_name,{})
    weights=role_cfg.get("weights",{})
    main_weights=role_cfg.get("main_weights")
    score_info=getattr(self, "_diff_item_score_info", None) or (lambda diff_item: _diff_item_score_info(self, diff_item))
    item_type=item.get(EQUIP_TYPE,"drive")
    if item_type=="tape":
        label=item.get(EQUIP_SET_NAME) or "卡带"
        main_stat=item.get(EQUIP_MAIN_STATS,"")
        shape_id=None
    else:
        label=item.get(EQUIP_SHAPE_ID) or item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID,"")
        main_stat=""
        shape_id=item.get(EQUIP_SHAPE_ID) or ""
    return self._equip_card(
        label,
        main_stat,
        item.get(EQUIP_SUB_STATS,{}) or {},
        shape_id,
        item.get(EQUIP_UID,""),
        weights,
        score_info(item),
        item.get(EQUIP_QUALITY,"Gold"),
        is_new=is_new,
        main_weights=main_weights,
    )

def _split_loadout_sources(sources):
    tape=None
    drives=[]
    for item in sources or []:
        if not item:
            continue
        item_type=str(
            item.get(EQUIP_TYPE) if isinstance(item,dict)
            else getattr(item,EQUIP_TYPE,"") or ""
        )
        main_stats=item.get(EQUIP_MAIN_STATS) if isinstance(item,dict) else getattr(item,EQUIP_MAIN_STATS,None)
        shape_id=item.get(EQUIP_SHAPE_ID) if isinstance(item,dict) else getattr(item,EQUIP_SHAPE_ID,None)
        if item_type=="tape" or (isinstance(item,dict) and main_stats and not shape_id):
            tape=item
        else:
            drives.append(item)
    return tape,drives

def _build_plan_diff_dialog(self, role_name, diff):
    dlg=QDialog(self if isinstance(self, QWidget) else None)
    dlg.setWindowTitle(f"{role_name} - 配装变动")
    dlg.setMinimumSize(820,560)
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(10)
    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    body=QWidget(); body_layout=QVBoxLayout(body); body_layout.setContentsMargins(0,0,0,0); body_layout.setSpacing(10)
    section_label=getattr(self, "_section_label", None) or (lambda text: _section_label(self, text))
    diff_item_card=getattr(self, "_diff_item_card", None) or (lambda role, item, is_new=False: _diff_item_card(self, role, item, is_new))

    removed=diff.get(DIFF_REMOVED,[]) or []
    added=diff.get(DIFF_ADDED,[]) or []

    if not removed and not added:
        body_layout.addWidget(QLabel("本次配装与已保存方案没有装备变动。"))
    else:
        removed_tape=[it for it in removed if it.get(EQUIP_TYPE)=="tape"]
        removed_drives=[it for it in removed if it.get(EQUIP_TYPE)!="tape"]
        added_tape=[it for it in added if it.get(EQUIP_TYPE)=="tape"]
        added_drives=[it for it in added if it.get(EQUIP_TYPE)!="tape"]

        _SHAPE_FAMILY = {
            "H_2": "I_2", "V_2": "I_2",
            "H_3": "I_3", "V_3": "I_3",
            "H_4": "I_4", "V_4": "I_4",
            "L_3_TL": "L_3", "L_3_TR": "L_3", "L_3_BL": "L_3", "L_3_BR": "L_3",
            "Trap_4_H": "Trap_4", "Trap_4_V": "Trap_4",
        }
        def _shape_family(sid: str) -> str:
            return _SHAPE_FAMILY.get(sid, sid)

        def _match_pairs(old_list, new_list):
            old_by_shape: dict[str, list] = {}
            for item in old_list:
                sid = str(item.get(EQUIP_SHAPE_ID, "") or "")
                old_by_shape.setdefault(sid, []).append(item)
            new_by_shape: dict[str, list] = {}
            for item in new_list:
                sid = str(item.get(EQUIP_SHAPE_ID, "") or "")
                new_by_shape.setdefault(sid, []).append(item)

            pairs = []
            all_exact_shapes = set(old_by_shape) | set(new_by_shape)
            for sid in sorted(all_exact_shapes):
                old_items = old_by_shape.get(sid, [])
                new_items = new_by_shape.get(sid, [])
                n = min(len(old_items), len(new_items))
                for i in range(n):
                    pairs.append((old_items[i], new_items[i]))
                old_by_shape[sid] = old_items[n:]
                new_by_shape[sid] = new_items[n:]

            old_left: list = []
            for items in old_by_shape.values():
                old_left.extend(items)
            new_left: list = []
            for items in new_by_shape.values():
                new_left.extend(items)

            old_by_family: dict[str, list] = {}
            for item in old_left:
                fam = _shape_family(str(item.get(EQUIP_SHAPE_ID, "") or ""))
                old_by_family.setdefault(fam, []).append(item)
            new_by_family: dict[str, list] = {}
            for item in new_left:
                fam = _shape_family(str(item.get(EQUIP_SHAPE_ID, "") or ""))
                new_by_family.setdefault(fam, []).append(item)

            unmatched_old = []
            unmatched_new = []
            all_families = set(old_by_family) | set(new_by_family)
            for fam in sorted(all_families):
                old_items = old_by_family.get(fam, [])
                new_items = new_by_family.get(fam, [])
                n = min(len(old_items), len(new_items))
                for i in range(n):
                    pairs.append((old_items[i], new_items[i]))
                unmatched_old.extend(old_items[n:])
                unmatched_new.extend(new_items[n:])

            return pairs, unmatched_old, unmatched_new

        pair_index=0

        if removed_tape or added_tape:
            pair_index+=1
            body_layout.addWidget(section_label(f"变动 {pair_index}：卡带"))
            pair_frame=QFrame()
            pair_frame.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:8px 10px}"))
            pair_layout=QVBoxLayout(pair_frame); pair_layout.setSpacing(6); pair_layout.setContentsMargins(8,6,8,6)

            old_lbl=QLabel("← 卸下（旧）")
            old_lbl.setStyleSheet(themed_style("font-size:11px;font-weight:700;color:#f85149;border:none;background:transparent;padding:2px 4px"))
            pair_layout.addWidget(old_lbl)
            if removed_tape:
                pair_layout.addWidget(diff_item_card(role_name,removed_tape[0],is_new=False))
            else:
                pair_layout.addWidget(QLabel("  （无需卸下）"))

            arrow=QLabel("  ↓")
            arrow.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#58a6ff;border:none;background:transparent;padding:0 0 0 12px"))
            pair_layout.addWidget(arrow)

            new_lbl=QLabel("→ 换上（新）")
            new_lbl.setStyleSheet(themed_style("font-size:11px;font-weight:700;color:#56d364;border:none;background:transparent;padding:2px 4px"))
            pair_layout.addWidget(new_lbl)
            if added_tape:
                pair_layout.addWidget(diff_item_card(role_name,added_tape[0],is_new=True))
            else:
                pair_layout.addWidget(QLabel("  （无需换上）"))

            body_layout.addWidget(pair_frame)

        drive_pairs,unmatched_old,unmatched_new=_match_pairs(removed_drives,added_drives)

        for old_d,new_d in drive_pairs:
            pair_index+=1
            old_sid=old_d.get(EQUIP_SHAPE_ID,"未知驱动")
            new_sid=new_d.get(EQUIP_SHAPE_ID,"未知驱动")
            title=f"变动 {pair_index}：{old_sid} → {new_sid}" if old_sid!=new_sid else f"变动 {pair_index}：{old_sid}"
            body_layout.addWidget(section_label(title))

            pair_frame=QFrame()
            pair_frame.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:8px 10px}"))
            pair_layout=QVBoxLayout(pair_frame); pair_layout.setSpacing(6); pair_layout.setContentsMargins(8,6,8,6)

            old_lbl=QLabel("← 卸下（旧）")
            old_lbl.setStyleSheet(themed_style("font-size:11px;font-weight:700;color:#f85149;border:none;background:transparent;padding:2px 4px"))
            pair_layout.addWidget(old_lbl)
            pair_layout.addWidget(diff_item_card(role_name,old_d,is_new=False))

            arrow=QLabel("  ↓")
            arrow.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#58a6ff;border:none;background:transparent;padding:0 0 0 12px"))
            pair_layout.addWidget(arrow)

            new_lbl=QLabel("→ 换上（新）")
            new_lbl.setStyleSheet(themed_style("font-size:11px;font-weight:700;color:#56d364;border:none;background:transparent;padding:2px 4px"))
            pair_layout.addWidget(new_lbl)
            pair_layout.addWidget(diff_item_card(role_name,new_d,is_new=True))

            body_layout.addWidget(pair_frame)

        for old_d in unmatched_old:
            pair_index+=1
            body_layout.addWidget(section_label(f"变动 {pair_index}：卸下 {old_d.get(EQUIP_SHAPE_ID,'未知驱动')}"))
            body_layout.addWidget(diff_item_card(role_name,old_d,is_new=False))

        for new_d in unmatched_new:
            pair_index+=1
            body_layout.addWidget(section_label(f"变动 {pair_index}：新增 {new_d.get(EQUIP_SHAPE_ID,'未知驱动')}"))
            body_layout.addWidget(diff_item_card(role_name,new_d,is_new=True))

    body_layout.addStretch()
    scroll.setWidget(body)
    layout.addWidget(scroll,1)
    buttons=QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    return dlg

def _show_plan_diff_dialog(self, role_name, diff):
    self._build_plan_diff_dialog(role_name,diff).exec()

def _apply_saved_role_equipment_diff(self, role_name):
    last_diffs=getattr(self,"_my_role_equipment_last_diffs",{}) or {}
    role_diff=copy.deepcopy(last_diffs.get(role_name) or {})
    if not role_diff.get(DIFF_CHANGED):
        return False
    plan_diffs=dict(getattr(self,"allocation_plan_diff",{}) or {})
    plan_diffs[role_name]=role_diff
    self.allocation_plan_diff=plan_diffs
    return True

def _saved_equipment_main_stat_text(main_stats):
    if isinstance(main_stats,dict):
        return next(iter(main_stats.keys()),"")
    return str(main_stats or "")

def _saved_equipment_score_total(self, role_name, role_state):
    total=0.0
    tape=role_state.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape,dict):
        total+=float(tape.get(EQUIP_SCORE,0.0) or 0.0)
    for drive in role_state.get(ROLE_EQUIPPED_DRIVES,[]) or []:
        if isinstance(drive,dict):
            total+=float(drive.get(EQUIP_SCORE,0.0) or 0.0)
    role_state[ROLE_TOTAL_SCORE]=round(total,2)
    role_state[ROLE_TOTAL_GRADE]=self._calc_grade(total,ALLOCATION_TOTAL_SCORE_AREA)
    role_state[ROLE_SCORE_AREA]=ALLOCATION_TOTAL_SCORE_AREA

def _persist_saved_equipment_sync(self):
    save=getattr(self,"_save_eq",None)
    if callable(save):
        save()
    refresh=getattr(self,"_refresh_equip",None)
    if callable(refresh):
        try:
            refresh()
        except AttributeError as exc:
            logger.debug(f"刷新配装页面时缺少可选 UI 状态，已忽略: {exc}")

def _sync_saved_drive_replacement(self, role_name, old_uid, new_drive, new_score, new_area):
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name)
    if not isinstance(role_state,dict):
        return False
    drives=role_state.get(ROLE_EQUIPPED_DRIVES,[]) or []
    new_uid=str(new_drive.get(EQUIP_UID,"") or "")
    changed=False
    for drive in drives:
        if not isinstance(drive,dict) or str(drive.get(EQUIP_UID,""))!=str(old_uid):
            continue
        drive.update({
            EQUIP_UID: new_uid,
            EQUIP_SHAPE_ID: new_drive.get(EQUIP_SHAPE_ID,""),
            EQUIP_SUB_STATS: new_drive.get(EQUIP_SUB_STATS,{}) or {},
            EQUIP_QUALITY: new_drive.get(EQUIP_QUALITY,"Gold"),
            EQUIP_AREA: new_area,
            EQUIP_DISPLAY_NAME: new_drive.get(EQUIP_DISPLAY_NAME,""),
            EQUIP_SCORE: new_score,
            EQUIP_GRADE: self._calc_grade(new_score,new_area),
            EQUIP_SCORE_AREA: new_area,
            EQUIP_IS_CHANGED: True,
        })
        if new_drive.get(EQUIP_MAIN_STATS):
            drive[EQUIP_MAIN_STATS]=new_drive.get(EQUIP_MAIN_STATS)
        drive.pop(EQUIP_IS_NEW,None)
        changed=True
        break
    if changed:
        _saved_equipment_score_total(self,role_name,role_state)
    return changed

def _sync_saved_tape_replacement(self, role_name, new_tape, new_score):
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name)
    if not isinstance(role_state,dict):
        return False
    main_stat=_saved_equipment_main_stat_text(new_tape.get(EQUIP_MAIN_STATS,{}) or {})
    role_state[ROLE_EQUIPPED_TAPE]={
        EQUIP_UID: str(new_tape.get(EQUIP_UID,"") or ""),
        EQUIP_SET_NAME: new_tape.get(EQUIP_SET_NAME,""),
        EQUIP_DISPLAY_NAME: new_tape.get(EQUIP_DISPLAY_NAME,""),
        EQUIP_MAIN_STATS: main_stat,
        EQUIP_SUB_STATS: new_tape.get(EQUIP_SUB_STATS,{}) or {},
        EQUIP_QUALITY: new_tape.get(EQUIP_QUALITY,"Gold"),
        EQUIP_SCORE: new_score,
        EQUIP_GRADE: self._calc_grade(new_score,15),
        EQUIP_SCORE_AREA: 15,
        EQUIP_IS_CHANGED: True,
    }
    _saved_equipment_score_total(self,role_name,role_state)
    return True

def _sync_role_drive_replacement(self, role_name, old_uid, new_drive):
    weights=(getattr(self,"roles_db",{}) or {}).get(role_name,{}).get("weights",{})
    new_uid=str(new_drive.get(EQUIP_UID,"") or "")
    if not old_uid or not new_uid:
        return False

    new_shape=new_drive.get(EQUIP_SHAPE_ID,"")
    new_sub_stats=new_drive.get(EQUIP_SUB_STATS,{}) or {}
    new_quality=new_drive.get(EQUIP_QUALITY,"Gold")
    new_score=self._score_drive_dict(new_sub_stats,new_shape,weights,new_quality)
    new_area=int(new_drive.get(EQUIP_AREA) or getattr(self,"_shape_areas",{}).get(new_shape,3) or 3)

    old_state=copy.deepcopy(getattr(self,"equipped_state",{}) or {})
    saved_changed=_sync_saved_drive_replacement(self,role_name,old_uid,new_drive,new_score,new_area)
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name,{}) if isinstance(state,dict) else {}
    existing_diff=role_state.get(ROLE_LAST_DIFF,{}) if isinstance(role_state,dict) else {}
    if not saved_changed and not existing_diff.get(DIFF_CHANGED):
        return False

    if saved_changed:
        state_mgr=getattr(self,"state_mgr",None)
        if state_mgr is not None and hasattr(state_mgr,"_build_role_diff") and isinstance(role_state,dict):
            role_diff=state_mgr._build_role_diff(old_state.get(role_name),role_state)
            if role_diff.get(DIFF_CHANGED):
                role_state[ROLE_LAST_DIFF]=role_diff
                last_diffs=dict(getattr(self,"_my_role_equipment_last_diffs",{}) or {})
                last_diffs[role_name]=role_diff
                self._my_role_equipment_last_diffs=last_diffs
        _apply_saved_role_equipment_diff(self,role_name)
        _persist_saved_equipment_sync(self)
    else:
        _apply_saved_role_equipment_diff(self,role_name)
    return True


def _sync_role_tape_replacement(self, role_name, old_uid, new_tape):
    role_cfg=(getattr(self,"roles_db",{}) or {}).get(role_name,{})
    weights=role_cfg.get("weights",{})
    main_weights=role_cfg.get("main_weights")
    new_uid=str(new_tape.get(EQUIP_UID,"") or "")
    if not new_uid:
        return False

    main_stats=new_tape.get(EQUIP_MAIN_STATS,{}) or {}
    main_stat=next(iter(main_stats.keys()),"") if isinstance(main_stats,dict) else str(main_stats or "")
    sub_stats=new_tape.get(EQUIP_SUB_STATS,{}) or {}
    quality=new_tape.get(EQUIP_QUALITY,"Gold")
    new_score=self._score_tape_dict(main_stat,sub_stats,weights,quality,main_weights)

    old_state=copy.deepcopy(getattr(self,"equipped_state",{}) or {})
    saved_changed=_sync_saved_tape_replacement(self,role_name,new_tape,new_score)
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name,{}) if isinstance(state,dict) else {}
    existing_diff=role_state.get(ROLE_LAST_DIFF,{}) if isinstance(role_state,dict) else {}
    if not saved_changed and not existing_diff.get(DIFF_CHANGED):
        return False
    if saved_changed:
        state_mgr=getattr(self,"state_mgr",None)
        if state_mgr is not None and hasattr(state_mgr,"_build_role_diff") and isinstance(role_state,dict):
            role_diff=state_mgr._build_role_diff(old_state.get(role_name),role_state)
            if role_diff.get(DIFF_CHANGED):
                role_state[ROLE_LAST_DIFF]=role_diff
                last_diffs=dict(getattr(self,"_my_role_equipment_last_diffs",{}) or {})
                last_diffs[role_name]=role_diff
                self._my_role_equipment_last_diffs=last_diffs
        _apply_saved_role_equipment_diff(self,role_name)
        _persist_saved_equipment_sync(self)
    else:
        _apply_saved_role_equipment_diff(self,role_name)
    return True

def _stat_w(self, sn, wts):
    stat_alias_mapping = getattr(self, 'stats_config', {}).get('stat_alias_mapping', {})
    if not wts:
        return 0.0
    # 将驱动词条名映射为规范名
    if stat_alias_mapping:
        sn = stat_alias_mapping.get(sn, sn)  # 若未映射则保留原名
    # 1. 精确匹配权重中的规范名
    if sn in wts:
        return wts[sn]
    # 2. 遍历权重，将权重键也映射后比较
    if stat_alias_mapping:
        for wk, wv in wts.items():
            wk_canon = stat_alias_mapping.get(wk, wk)
            if wk_canon == sn:
                return wv
    return 0.0

def _stat_c(self,w):
    w=max(0.0,min(1.0,w))
    if w<0.3: return theme_color("#8b949e")
    if w<0.5: return "#58a6ff"
    if w<0.7: return "#56d364"
    if w<0.85: return "#d2991d"
    return "#f0883e"

def _weighted_score(self,sub_stats,wts):
    if not sub_stats: return 0
    total=0.0
    for sn,sv in sub_stats.items():
        sw=self._stat_w(sn,wts)
        total+=float(sv)*sw
    return total

def _quality_coef(self, quality):
    return {"Gold":1.0,"Purple":0.8,"Blue":0.6}.get(str(quality or "Gold"),1.0)

def _canonical_stat_name(self, stat):
    stat=str(stat or "").strip()
    if not stat:
        return ""
    aliases={}
    if self.scoring_engine:
        aliases=getattr(self.scoring_engine,"stat_alias_mapping",{}) or {}
    aliases.update(self.stats_config.get("stat_alias_mapping",{}) if isinstance(self.stats_config,dict) else {})
    return aliases.get(stat,stat)

def _stat_number_value(self, value):
    try:
        return float(str(value).replace("%","").strip())
    except Exception:
        return 0.0

def _item_value(self, item, key, default=None):
    if isinstance(item,dict):
        return item.get(key,default)
    return getattr(item,key,default)

def _add_stat_total(self, totals, stat, value):
    stat=self._canonical_stat_name(stat)
    value=self._stat_number_value(value)
    if not stat or value==0:
        return
    totals[stat]=round(totals.get(stat,0.0)+value,4)

def _fallback_tape_main_value(self, main_stat, quality):
    configured=(self.stats_config or {}).get("tape_main_stat_values",{})
    main_stat=str(main_stat or "").strip()
    canonical=self._canonical_stat_name(main_stat)
    if main_stat in configured:
        return self._stat_number_value(configured[main_stat])*self._quality_coef(quality)
    if canonical in configured:
        return self._stat_number_value(configured[canonical])*self._quality_coef(quality)
    if canonical in {"暴击伤害%"}:
        return 60.0*self._quality_coef(quality)
    if canonical in {"暴击率%"}:
        return 30.0*self._quality_coef(quality)
    if canonical in {"攻击力%","防御力%","生命值%"}:
        return 37.5*self._quality_coef(quality)
    if canonical in {"环合强度","倾陷强度"}:
        return 180.0*self._quality_coef(quality)
    if "治疗加成" in canonical:
        return 34.5*self._quality_coef(quality)
    if "伤害增强" in canonical:
        return 37.5*self._quality_coef(quality)
    return 0.0

def _extra_shape_area(self, role_name):
    label=str(self.roles_db.get(role_name,{}).get("extra_shape_label",""))
    m=re.search(r"(\d+)",label)
    return int(m.group(1)) if m else None

def _equipment_bonus_rows(self, role_name, tape, drives):
    totals={}
    if tape:
        main_stat=self._item_value(tape,EQUIP_MAIN_STATS,"")
        main_value=self._item_value(tape,"main_value",None)
        if main_value is None:
            main_value=self._fallback_tape_main_value(main_stat,self._item_value(tape,EQUIP_QUALITY,"Gold"))
        self._add_stat_total(totals,main_stat,main_value)
        for stat,value in (self._item_value(tape,EQUIP_SUB_STATS,{}) or {}).items():
            self._add_stat_total(totals,stat,value)
    drives=list(drives or [])
    for drive in drives:
        for stat,value in (self._item_value(drive,EQUIP_SUB_STATS,{}) or {}).items():
            self._add_stat_total(totals,stat,value)
    role_data=self.roles_db.get(role_name,{})
    extra_buffs=role_data.get("extra_shape_buffs",{}) or {}
    if isinstance(extra_buffs,dict) and len(extra_buffs)>1:
        first_key=next(iter(extra_buffs))
        extra_buffs={first_key:extra_buffs[first_key]}
    target_area=self._extra_shape_area(role_name)
    matched_count=0
    if target_area:
        for drive in drives:
            area=self._item_value(drive,EQUIP_AREA,None)
            if area is None:
                area=self._shape_areas.get(self._item_value(drive,"shape_id",""),0)
            if int(area or 0)==target_area:
                matched_count+=1
    for stat,value in extra_buffs.items():
        self._add_stat_total(totals,stat,self._stat_number_value(value)*matched_count)
    rows=sorted(totals.items(),key=lambda kv: kv[1],reverse=True)
    return [(stat,value) for stat,value in rows if value]

def _get_my_role_entry(self, role_name):
    cache=load_my_roles()
    entry=cache.get(role_name,{}) if isinstance(cache,dict) else {}
    return entry if isinstance(entry,dict) else {}

def _role_base_bonus_rows(self, role_name):
    role_entry=self._get_my_role_entry(role_name)
    totals={}
    for stat,value in (role_entry.get("sub_stats") or {}).items():
        self._add_stat_total(totals,stat,value)
    weapon=role_entry.get("weapon") or {}
    if isinstance(weapon,dict):
        for stat,value in (weapon.get("sub_stats") or {}).items():
            self._add_stat_total(totals,stat,value)
        for effect in weapon.get("skill") or []:
            if not isinstance(effect,dict):
                continue
            key=effect.get("key")
            if not key:
                continue
            try:
                value=float(effect.get("value",0.0) or 0.0)
                cover=float(effect.get("cover",0.8) or 0.8)
                num=float(effect.get("num",1) or 1)
            except (TypeError,ValueError):
                continue
            effect_total=value*cover*num
            if effect_total:
                self._add_stat_total(totals,key,effect_total)
    rows=sorted(totals.items(),key=lambda kv: kv[1],reverse=True)
    return [(stat,value) for stat,value in rows if value]

def _merge_bonus_row_lists(self, *sources):
    totals={}
    for rows in sources:
        for stat,value in rows or []:
            self._add_stat_total(totals,stat,value)
    merged=sorted(totals.items(),key=lambda kv: kv[1],reverse=True)
    return [(stat,value) for stat,value in merged if value]

def _bonus_rows_for_mode(self, role_name, tape, drives, mode="equipment"):
    equipment_rows=self._equipment_bonus_rows(role_name,tape,drives)
    if mode!="character":
        return equipment_rows
    return self._merge_bonus_row_lists(self._role_base_bonus_rows(role_name),equipment_rows)

def _bonus_summary_mode_label(self, mode):
    return "角色属性汇总" if mode=="character" else "空幕属性汇总"

def _make_bonus_mode_switch(self, default_mode, on_change):
    container=QWidget()
    layout=QHBoxLayout(container)
    layout.setContentsMargins(0,0,0,0)
    layout.setSpacing(4)
    btn_group=QButtonGroup(container)
    btn_group.setExclusive(True)
    toggle_style=themed_style(
        "QPushButton{background:#161b22;color:#8b949e;border:1px solid #30363d;border-radius:6px;"
        "font-size:10px;font-weight:700;padding:2px 6px;min-height:22px}"
        "QPushButton:checked{background:#1f6feb22;color:#58a6ff;border-color:#58a6ff}"
        "QPushButton:hover{border-color:#58a6ff;color:#c9d1d9}"
    )
    mode_defs=[("equipment","空幕属性汇总"),("character","角色属性汇总")]
    for index,(mode,label) in enumerate(mode_defs):
        btn=QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(toggle_style)
        btn_group.addButton(btn,index)
        layout.addWidget(btn)
        if mode==default_mode:
            btn.setChecked(True)
    def _on_mode_clicked(button_id):
        mode=mode_defs[button_id][0]
        on_change(mode)
    btn_group.idClicked.connect(_on_mode_clicked)
    layout.addStretch()
    return container

def _clear_layout_widgets(self, layout):
    while layout.count():
        item=layout.takeAt(0)
        widget=item.widget()
        if widget:
            widget.deleteLater()

def _format_bonus_value(self, stat, value):
    suffix="%" if "%" in stat or "伤害增强" in stat or "治疗加成" in stat else ""
    if suffix:
        return f"+{value:.2f}%"
    return f"+{value:.0f}" if abs(value-round(value))<0.01 else f"+{value:.2f}"

def _is_crit_rate_stat(stat):
    normalized=str(stat or "").replace("%","").strip()
    return normalized in {"暴击率","暴击率%"}

def _stats_match(stat, stat_key):
    left=str(stat or "").replace("%","").strip()
    right=str(stat_key or "").replace("%","").strip()
    if not left or not right:
        return False
    return left == right or left in right or right in left

def _is_highlighted_bonus_stat(stat, priority_stats=None):
    if _is_crit_rate_stat(stat):
        return True
    for key in priority_stats or []:
        if _stats_match(stat, key):
            return True
    return False

def _bonus_stat_label_style(stat, priority_stats=None):
    color=theme_color("#d2991d") if _is_highlighted_bonus_stat(stat, priority_stats) else theme_color("#c9d1d9")
    return f"font-size:10px;font-weight:700;color:{color};border:none;background:transparent"

def _role_stat_priority_stats(self, role_name):
    configs=getattr(self,"_pending_crit_priority_modes",None) or {}
    if not configs and hasattr(self,"role_selector"):
        try:
            configs=self.role_selector.get_crit_priority_modes()
        except Exception:
            configs={}
    cfg=configs.get(role_name) or {}
    if not isinstance(cfg,dict):
        return []
    return [str(stat) for stat in cfg.get("stats", []) if stat]

def _sort_bonus_aligned_rows(self, aligned, priority_stats=None, prioritize_changed_only=False):
    priority_stats=list(priority_stats or [])

    def priority_index(stat, item):
        if prioritize_changed_only and not self._has_bonus_delta(item):
            return None
        for idx,key in enumerate(priority_stats):
            if _stats_match(stat,key):
                return idx
        if _is_crit_rate_stat(stat):
            return len(priority_stats)
        return None

    def sort_key(item):
        stat=item.get("stat","")
        idx=priority_index(stat, item)
        if idx is not None:
            return (0,idx)
        max_val=max(float(item.get("old") or 0.0),float(item.get("new") or 0.0))
        return (1,-max_val)

    return sorted(aligned or [], key=sort_key)

def _has_bonus_delta(self, item):
    delta=float(item.get("delta") or 0.0)
    if abs(delta) < 0.0001:
        return False
    old_val=item.get("old")
    new_val=item.get("new")
    if old_val is not None and new_val is not None and old_val==new_val:
        return False
    return True

def _aligned_bonus_comparison_rows(self, old_rows, new_rows, limit=None, changes_only=False, priority_stats=None):
    old_map=dict(old_rows or [])
    new_map=dict(new_rows or [])
    stats=set(old_map) | set(new_map)
    aligned=[]
    for stat in stats:
        old_val=old_map.get(stat)
        new_val=new_map.get(stat)
        if old_val is not None and new_val is not None:
            delta=round(new_val-old_val,4)
        elif old_val is None and new_val is not None:
            delta=round(new_val,4)
        elif new_val is None and old_val is not None:
            delta=round(-old_val,4)
        else:
            delta=0.0
        aligned.append({"stat": stat, "old": old_val, "new": new_val, "delta": delta})
    if changes_only:
        aligned=[item for item in aligned if self._has_bonus_delta(item)]
    aligned=self._sort_bonus_aligned_rows(aligned,priority_stats,prioritize_changed_only=changes_only)
    if limit is not None:
        aligned=aligned[:limit]
    return aligned

def _bonus_spacer_row(self):
    row=QFrame()
    row.setFixedHeight(26)
    row.setStyleSheet(themed_style("QFrame{background:transparent;border:none;}"))
    return row

def _bonus_placeholder_row_widget(self, stat, text="—", priority_stats=None):
    row=QFrame()
    row.setFixedHeight(26)
    row.setMinimumWidth(130)
    row.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}"))
    rl=QHBoxLayout(row); rl.setContentsMargins(6,1,6,1); rl.setSpacing(6)
    name=QLabel(stat); name.setWordWrap(True); name.setStyleSheet(_bonus_stat_label_style(stat,priority_stats))
    val=QLabel(text); val.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
    val.setStyleSheet(themed_style("font-size:10px;font-weight:700;color:#6e7681;border:none;background:transparent"))
    rl.addWidget(name,1); rl.addWidget(val)
    return row

def _bonus_comparison_column(self, title, aligned_rows, value_key, empty_text="暂无可汇总属性", priority_stats=None):
    column=QFrame()
    column.setStyleSheet(themed_style("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}"))
    layout=QVBoxLayout(column); layout.setContentsMargins(7,5,7,5); layout.setSpacing(4)
    header=QLabel(title)
    header.setStyleSheet(themed_style("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent"))
    layout.addWidget(header)
    if not aligned_rows:
        empty=QLabel(empty_text)
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        layout.addWidget(empty)
    else:
        for item in aligned_rows:
            value=item.get(value_key)
            if value is None:
                layout.addWidget(self._bonus_placeholder_row_widget(item["stat"], priority_stats=priority_stats))
            else:
                layout.addWidget(self._bonus_row_widget(item["stat"], value, priority_stats=priority_stats))
    layout.addStretch()
    return column

def _bonus_delta_row_widget(self, stat, delta, old_val, new_val, priority_stats=None):
    if not self._has_bonus_delta({"stat": stat, "delta": delta, "old": old_val, "new": new_val}):
        return self._bonus_spacer_row()
    row=QFrame()
    row.setFixedHeight(26)
    row.setMinimumWidth(130)
    row.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}"))
    rl=QHBoxLayout(row); rl.setContentsMargins(6,1,6,1); rl.setSpacing(6)
    name=QLabel(stat); name.setWordWrap(True); name.setStyleSheet(_bonus_stat_label_style(stat,priority_stats))
    sign="+" if delta>=0 else ""
    suffix="%" if "%" in stat or "伤害增强" in stat or "治疗加成" in stat else ""
    text=f"{sign}{delta:.2f}{suffix}" if suffix else (f"{sign}{delta:.0f}" if abs(delta-round(delta))<0.01 else f"{sign}{delta:.2f}")
    color=theme_color("#56d364") if delta>0 else theme_color("#f85149")
    val=QLabel(text); val.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
    val.setStyleSheet(f"font-size:10px;font-weight:800;color:{color};border:none;background:transparent")
    rl.addWidget(name,1); rl.addWidget(val)
    return row

def _bonus_delta_column(self, aligned_rows, priority_stats=None):
    column=QFrame()
    column.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:6px}"))
    layout=QVBoxLayout(column); layout.setContentsMargins(7,5,7,5); layout.setSpacing(4)
    title=QLabel("变化")
    title.setStyleSheet(themed_style("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent"))
    layout.addWidget(title)
    if not aligned_rows:
        empty=QLabel("无变化")
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        layout.addWidget(empty)
    else:
        for item in aligned_rows:
            layout.addWidget(self._bonus_delta_row_widget(item["stat"], item["delta"], item.get("old"), item.get("new"), priority_stats=priority_stats))
    layout.addStretch()
    return column

def _bonus_comparison_widget(self, role_name, old_rows, new_rows, has_old=True, compact=False, priority_stats=None):
    priority_stats=list(priority_stats or [])
    if compact:
        aligned=self._aligned_bonus_comparison_rows(old_rows,new_rows,changes_only=True,priority_stats=priority_stats)
    else:
        aligned=self._aligned_bonus_comparison_rows(old_rows,new_rows,priority_stats=priority_stats)
    old_title="旧" if compact else "旧方案"
    new_title="新" if compact else "新方案"
    old_empty="无已保存配装" if not has_old else ("暂无属性变化" if compact else "暂无可汇总属性")
    old_column=self._bonus_comparison_column(old_title,aligned,"old",old_empty,priority_stats=priority_stats)
    new_column=self._bonus_comparison_column(new_title,aligned,"new","暂无属性变化" if compact and not aligned else "暂无可汇总属性",priority_stats=priority_stats)

    container=QFrame()
    container.setStyleSheet(themed_style("QFrame{background:transparent;border:none}"))
    layout=QHBoxLayout(container); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
    layout.addWidget(old_column,1)
    layout.addWidget(new_column,1)
    layout.addWidget(self._bonus_delta_column(aligned,priority_stats=priority_stats),1)
    return container

def _role_bonus_summary_panel(self, role_name, tape, drives, compare_with_saved=False, priority_stats=None):
    priority_stats=list(priority_stats if priority_stats is not None else self._role_stat_priority_stats(role_name))
    state={"mode":"equipment"}
    box=QFrame()
    box.setMinimumWidth(560 if compare_with_saved else 300)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    box.setStyleSheet(themed_style("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}"))
    layout=QVBoxLayout(box); layout.setContentsMargins(7,5,7,5); layout.setSpacing(4)
    layout.addWidget(self._make_bonus_mode_switch(state["mode"], lambda mode: self._refresh_bonus_summary_panel(box,role_name,tape,drives,compare_with_saved,priority_stats,mode)))
    content_host=QWidget()
    content_layout=QVBoxLayout(content_host); content_layout.setContentsMargins(0,0,0,0); content_layout.setSpacing(4)
    layout.addWidget(content_host)
    box._bonus_summary_content_layout=content_layout
    box._bonus_summary_state=state
    self._refresh_bonus_summary_panel(box,role_name,tape,drives,compare_with_saved,priority_stats,state["mode"])
    layout.addStretch()
    return box

def _refresh_bonus_summary_panel(self, box, role_name, tape, drives, compare_with_saved, priority_stats, mode):
    if hasattr(box,"_bonus_summary_state"):
        box._bonus_summary_state["mode"]=mode
    content_layout=box._bonus_summary_content_layout
    self._clear_layout_widgets(content_layout)
    if compare_with_saved:
        role_diff=_resolve_comparison_role_diff(self,role_name)
        saved_sources=_diff_saved_sources(self,role_name)
        old_tape,old_drives=_split_loadout_sources(saved_sources)
        new_uids=_loadout_uids(tape,drives)
        old_uids=_loadout_uids(old_tape,old_drives)
        if role_diff.get(DIFF_CHANGED) and ((not old_tape and not old_drives) or old_uids==new_uids):
            old_tape,old_drives=_previous_loadout_from_diff(self,role_name,tape,drives,role_diff)
        if old_tape or old_drives:
            old_rows=self._bonus_rows_for_mode(role_name,old_tape,old_drives,mode)
            new_rows=self._bonus_rows_for_mode(role_name,tape,drives,mode)
            title=QLabel(self._bonus_summary_mode_label(mode))
            title.setStyleSheet(themed_style("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent"))
            content_layout.addWidget(title)
            content_layout.addWidget(self._bonus_comparison_widget(role_name,old_rows,new_rows,has_old=True,compact=True,priority_stats=priority_stats))
            full_rows=self._aligned_bonus_comparison_rows(old_rows,new_rows,priority_stats=priority_stats)
            changed_rows=self._aligned_bonus_comparison_rows(old_rows,new_rows,changes_only=True,priority_stats=priority_stats)
            if len(full_rows)>len(changed_rows):
                more=QPushButton("•••")
                more.setObjectName("btnSm")
                more.setFixedSize(54,22)
                more.setCursor(Qt.PointingHandCursor)
                more.setStyleSheet(themed_style("QPushButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;font-size:13px;font-weight:800;padding:0}QPushButton:hover{border-color:#58a6ff;color:#58a6ff}"))
                more.clicked.connect(
                    lambda checked=False,role=role_name,old_r=old_rows,new_r=new_rows,stats=list(priority_stats),summary_mode=mode: self._show_bonus_comparison_dialog(role,old_r,new_r,stats,summary_mode)
                )
                content_layout.addWidget(more,0,Qt.AlignCenter)
            return
    rows=self._bonus_rows_for_mode(role_name,tape,drives,mode)
    title=QLabel(self._bonus_summary_mode_label(mode))
    title.setStyleSheet(themed_style("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent"))
    content_layout.addWidget(title)
    visible=rows[:4]
    if not visible:
        empty=QLabel("暂无可汇总属性")
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        content_layout.addWidget(empty)
    for stat,value in visible:
        content_layout.addWidget(self._bonus_row_widget(stat,value,priority_stats=priority_stats))
    if len(rows)>len(visible):
        more=QPushButton("•••")
        more.setObjectName("btnSm")
        more.setFixedSize(54,22)
        more.setCursor(Qt.PointingHandCursor)
        more.setStyleSheet(themed_style("QPushButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;font-size:13px;font-weight:800;padding:0}QPushButton:hover{border-color:#58a6ff;color:#58a6ff}"))
        more.clicked.connect(
            lambda checked=False,role=role_name,summary_rows=rows,summary_mode=mode: self._show_bonus_summary_dialog(role,summary_rows,summary_mode)
        )
        content_layout.addWidget(more,0,Qt.AlignCenter)

def _show_bonus_comparison_dialog(self, role_name, old_rows, new_rows, priority_stats=None, mode="equipment"):
    priority_stats=list(priority_stats if priority_stats is not None else self._role_stat_priority_stats(role_name))
    dlg=QDialog(self)
    dlg.setWindowTitle(f"{role_name} {self._bonus_summary_mode_label(mode)}对比")
    dlg.setMinimumSize(680,360)
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(8)
    layout.addWidget(self._bonus_comparison_widget(role_name,old_rows,new_rows,has_old=True,compact=False,priority_stats=priority_stats))
    buttons=QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()

def _bonus_summary_widget(self, role_name, tape, drives):
    return self._role_bonus_summary_panel(role_name,tape,drives,compare_with_saved=False)

def _show_bonus_summary_dialog(self, role_name, rows, mode="equipment"):
    dlg=QDialog(self)
    dlg.setWindowTitle(f"{role_name} {self._bonus_summary_mode_label(mode)}")
    dlg.setMinimumSize(360,420)
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(8)
    for stat,value in rows:
        layout.addWidget(self._bonus_row_widget(stat,value))
    buttons=QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()

def _bonus_row_widget(self, stat, value, priority_stats=None):
    row=QFrame()
    row.setFixedHeight(26)
    row.setMinimumWidth(130)
    row.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}"))
    rl=QHBoxLayout(row); rl.setContentsMargins(6,1,6,1); rl.setSpacing(6)
    name=QLabel(stat); name.setWordWrap(True); name.setStyleSheet(_bonus_stat_label_style(stat,priority_stats))
    val=QLabel(self._format_bonus_value(stat,value)); val.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
    val.setStyleSheet(themed_style("font-size:10px;font-weight:800;color:#f0f6fc;border:none;background:transparent"))
    rl.addWidget(name,1); rl.addWidget(val)
    return row

def _score_drive_dict(self, sub_stats, shape_id, weights, quality="Gold"):
    if not self.scoring_engine: return 0.0
    se=self.scoring_engine
    max_w=se._get_max_theoretical_weight(weights)
    area=self._shape_areas.get(shape_id, 3)
    actual_w=sum(se._get_flexible_weight(sn, weights) for sn in sub_stats.keys())
    if actual_w<=0 or max_w<=0: return 0.0
    quality_coef=se.quality_map.get(quality, 1.0)
    return round((10.0/max_w)*actual_w*area*quality_coef, 2)

def _score_tape_dict(self, main_stats, sub_stats, weights, quality="Gold", main_weights=None):
    if not self.scoring_engine: return 0.0
    se=self.scoring_engine
    max_w=se._get_max_theoretical_weight(weights)
    quality_coef=se.quality_map.get(quality, 1.0)
    main_weight_source=main_weights if isinstance(main_weights, dict) else weights
    main_w=se._get_flexible_weight(main_stats, main_weight_source) if main_stats else 0
    main_score=main_w*50.0*quality_coef
    sub_w=sum(se._get_flexible_weight(sn, weights) for sn in sub_stats.keys())
    sub_score=(10.0/max_w)*sub_w*10.0*quality_coef if max_w>0 else 0
    return round(main_score+sub_score, 2)

def _equip_card(self,label,main_stat,sub_stats,shape_id,uid,weights,score_info=None,quality=None,is_new=False,is_changed=False,main_weights=None):
    if current_theme_name() == "light":
        QUALITY_COLORS={"Gold":"#9a6700","Purple":"#8250df","Blue":"#0969da"}
    else:
        QUALITY_COLORS={"Gold":"#ffd700","Purple":"#ffe082","Blue":"#58a6ff"}
    QUALITY_LABELS={"Gold":"金","Purple":"紫","Blue":"蓝"}
    w=QWidget(); w.setStyleSheet(themed_style("QWidget{background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:9px 13px;margin:3px 0}"))
    outer=QHBoxLayout(w); outer.setSpacing(12); outer.setContentsMargins(2,2,2,2)

    # Shape image (compact)
    if shape_id:
        pm=_get_shape_pixmap(shape_id,64,quality)
        if not pm.isNull():
            img_lbl=QLabel(); img_lbl.setPixmap(pm); img_lbl.setFixedSize(68,68); img_lbl.setScaledContents(True)
            img_lbl.setStyleSheet(themed_style("border:1px solid #30363d;border-radius:6px;background:#161b22")); outer.addWidget(img_lbl)

    inner=QVBoxLayout(); inner.setSpacing(5); inner.setContentsMargins(0,3,0,3)

    # Header: shape name + quality + main stat block + score|grade
    hdr=QHBoxLayout(); hdr.setSpacing(8)
    label_color = theme_color("#4dd0e1")
    label_bg = theme_rgba("#4dd0e1", 0.10)
    label_border = label_color
    name_lbl = QLabel(f"<b>{label}</b>")
    name_size = 12 if shape_id else 13
    name_pad = "2px 8px" if shape_id else "3px 10px"
    name_lbl.setStyleSheet(f"font-size:{name_size}px;font-weight:800;color:{label_color};border:1px solid {label_border};border-radius:6px;padding:{name_pad};background:{label_bg}")
    hdr.addWidget(name_lbl)
    status_labels = []
    if is_new:
        new_lbl=QLabel("NEW")
        new_lbl.setStyleSheet(f"font-size:10px;font-weight:800;color:{theme_color('#58a6ff')};border:1px solid {theme_color('#58a6ff')};border-radius:5px;padding:2px 6px;background:{theme_rgba('#58a6ff', 0.10)}")
        status_labels.append(new_lbl)
    if is_changed:
        change_lbl=QLabel("CHANGE")
        change_lbl.setStyleSheet(f"font-size:10px;font-weight:800;color:{theme_color('#7ee787')};border:1px solid {theme_color('#2ea043')};border-radius:5px;padding:2px 6px;background:{theme_rgba('#238636', 0.10)}")
        status_labels.append(change_lbl)
    if status_labels and shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label)
    # Quality badge: only tapes show text; drive quality is represented by the icon.
    if quality and not shape_id:
        qcolor=QUALITY_COLORS.get(quality,theme_color("#8b949e")); qlabel=QUALITY_LABELS.get(quality,quality)
        qbg=theme_rgba(qcolor, 0.10)
        q_lbl=QLabel(qlabel)
        q_lbl.setStyleSheet(f"font-size:11px;font-weight:700;color:{qcolor};border:1px solid {qcolor};border-radius:5px;padding:2px 7px;background:{qbg}")
        hdr.addWidget(q_lbl)
    # Main stat as colored block (same style as sub stats)
    if main_stat:
        main_weight_source=main_weights if isinstance(main_weights, dict) else weights
        mw=self._stat_w(main_stat,main_weight_source); mc=self._stat_c(mw); qc=QColor(mc)
        ms_block=QLabel(main_stat); ms_block.setStyleSheet(
            f"border:1px solid {mc};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);"
            f"border-radius:6px;padding:4px 12px;font-size:13px;color:{mc};font-weight:700"
        )
        hdr.addWidget(ms_block)
    if status_labels and not shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label)
    hdr.addStretch()

    # Score | Grade side by side
    if score_info is not None:
        score,grade=score_info; gc=GRADE_COLORS.get(grade,"#58a6ff")
        sf=QFrame()
        sf.setStyleSheet(f"QFrame{{background:{theme_rgba(gc, 0.10)};border:1px solid {gc};border-radius:6px;padding:2px 10px}}")
        sf_layout=QHBoxLayout(sf); sf_layout.setSpacing(5); sf_layout.setContentsMargins(4,1,4,1)
        sl=QLabel(f"{score:.1f}"); sl.setStyleSheet(f"font-size:13px;font-weight:800;color:{gc};border:none"); sf_layout.addWidget(sl)
        gl=QLabel(grade); gl.setStyleSheet(f"font-size:11px;font-weight:800;color:{gc};border:none"); sf_layout.addWidget(gl)
        hdr.addWidget(sf)
    uid_lbl=QLabel(f"<span style='color:{theme_color('#6e7681')};font-size:10px;'>{uid}</span>"); hdr.addWidget(uid_lbl)
    inner.addLayout(hdr)

    # Stat blocks row
    if sub_stats:
        br=QHBoxLayout(); br.setSpacing(5)
        for sn,sv in sub_stats.items():
            sw=self._stat_w(sn,weights); color=self._stat_c(sw); qc=QColor(color)
            block=QLabel(f"{sn} <b>{sv}</b>"); block.setAlignment(Qt.AlignCenter)
            block.setStyleSheet(f"border:1px solid {color};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);border-radius:6px;padding:5px 12px;font-size:12px;color:{color};font-weight:600")
            block.setToolTip(f"权重: {sw:.2f}"); br.addWidget(block)
        br.addStretch(); inner.addLayout(br)
    outer.addLayout(inner,1); return w
