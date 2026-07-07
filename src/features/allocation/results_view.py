# 渲染配装结果、评分和属性汇总。
"""MainWindow methods for allocation."""

from __future__ import annotations

import json
import re
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_BGS, GRADE_COLORS, STYLE
from src.ui.puzzle_board import PuzzleBoardWidget, get_shape_pixmap as _get_shape_pixmap

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_section_label', '_render_results', '_calc_grade', '_show_plan_diff_dialog', '_build_plan_diff_dialog', '_diff_item_card', '_diff_item_score_info', '_plan_diff_text', '_sync_role_drive_replacement', '_sync_role_tape_replacement', '_stat_w', '_stat_c', '_weighted_score', '_quality_coef', '_canonical_stat_name', '_stat_number_value', '_item_value', '_add_stat_total', '_fallback_tape_main_value', '_extra_shape_area', '_equipment_bonus_rows', '_format_bonus_value', '_bonus_summary_widget', '_bonus_row_widget', '_show_bonus_summary_dialog', '_score_drive_dict', '_score_tape_dict', '_equip_card']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _section_label(self,text):
    label=QLabel(text)
    label.setStyleSheet("font-size:14px;font-weight:700;color:#c9d1d9;border:none;background:transparent;padding:2px 0")
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
        if not p or not p.get("valid"):
            self.result_content_layout.addWidget(QLabel(f"❌ {role}: 无有效配装方案")); continue
        role_diff=plan_diffs.get(role,{}) or {}
        added_uids=set(role_diff.get("added_uids",set()) or set())
        changed_uids=set(p.get("changed_uids",set()) or set()) if isinstance(p,dict) else set()
        total_score=p.get('score',0); total_grade=self._calc_grade(total_score,ALLOCATION_TOTAL_SCORE_AREA)
        gc=GRADE_COLORS.get(total_grade,"#58a6ff"); gbg=GRADE_BGS.get(total_grade,f"{gc}15")

        grp=QGroupBox(""); grp.setStyleSheet("QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}")
        gl=QVBoxLayout(grp); gl.setSpacing(10)
        # Role header: name + score + grade side by side, compact
        role_hdr=QHBoxLayout(); role_hdr.setSpacing(8)
        # Role name with different color from stat blocks - use teal/cyan tone
        rnl=QLabel(role)
        rnl.setStyleSheet("font-size:15px;font-weight:800;color:#4dd0e1;border:1px solid #4dd0e1;border-radius:7px;padding:4px 14px;background:#4dd0e122")
        role_hdr.addWidget(rnl)
        if role_diff.get("changed"):
            diff_btn=QPushButton("变动")
            diff_btn.setFixedSize(76,32)
            diff_btn.setStyleSheet("QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px;min-height:32px}QPushButton:hover{background:#388bfd}")
            diff_btn.clicked.connect(lambda _checked=False,rn=role,d=role_diff: self._show_plan_diff_dialog(rn,d))
            role_hdr.addWidget(diff_btn)
        if mode_name:
            ml=QLabel(mode_name); ml.setStyleSheet("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px")
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

        board=p.get("blueprint",{}).get("board",[])
        role_cfg=self.roles_db.get(role,{})
        wts=role_cfg.get("weights",{})
        main_wts=role_cfg.get("main_weights")

        tape=p.get("assigned_tape")
        drives=p.get("assigned_set_drives",[])+p.get("assigned_extra_drives",[])
        if board:
            gl.addWidget(self._section_label("拼图图纸:"))
            bp_row=QHBoxLayout(); bp_row.setSpacing(44)
            bp_row.addWidget(PuzzleBoardWidget(board),0,Qt.AlignTop)
            bp_row.addWidget(self._bonus_summary_widget(role,tape,drives),0,Qt.AlignTop)
            bp_row.addStretch(1)
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
    removed=diff.get("removed",[]) or []
    added=diff.get("added",[]) or []
    if not removed and not added:
        return "本次配装与已保存方案没有装备变动。"
    lines=[f"{role_name} 配装变动："]
    if removed:
        lines.append("\n卸下：")
        lines.extend(f"- {item.get('display_name') or item.get('uid')}" for item in removed)
    if added:
        lines.append("\n换上：")
        lines.extend(f"+ {item.get('display_name') or item.get('uid')}" for item in added)
    return "\n".join(lines)

def _diff_item_score_info(self, item):
    if "score" not in item:
        return None
    score=float(item.get("score",0.0) or 0.0)
    grade=item.get("grade")
    if not grade:
        area=int(item.get("score_area") or item.get("area") or (15 if item.get("type")=="tape" else 0) or 0)
        grade=self._calc_grade(score,area) if area else "D"
    return score,str(grade)

def _diff_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)

def _diff_item_type(item):
    explicit=_diff_value(item,"type") or _diff_value(item,"item_type")
    if explicit:
        return str(explicit)
    if _diff_value(item,"shape_id")=="TAPE_15":
        return "tape"
    main_stats=_diff_value(item,"main_stats")
    return "tape" if isinstance(main_stats,str) and main_stats else "drive"

def _diff_grade(self, score, area):
    calc=getattr(self, "_calc_grade", None)
    if calc:
        return calc(score, area)
    return _calc_grade(self, score, area)

def _diff_snapshot_from_source(self, role_name, source):
    uid=str(_diff_value(source,"uid","") or "")
    if not uid:
        return {}
    item_type=_diff_item_type(source)
    sub_stats=_diff_value(source,"sub_stats",{}) or {}
    quality=_diff_value(source,"quality","Gold")
    area=int(_diff_value(source,"score_area") or _diff_value(source,"area") or (15 if item_type=="tape" else 0) or 0)
    role_scores=_diff_value(source,"role_scores",{}) or {}
    score=_diff_value(source,"score")
    if score is None and isinstance(role_scores,dict):
        score=role_scores.get(role_name)
    score_value=None if score is None else round(float(score or 0.0),2)
    grade=_diff_value(source,"grade")
    if grade is None and score_value is not None and area:
        grade=_diff_grade(self, score_value, area)

    snapshot={
        "uid": uid,
        "type": item_type,
        "display_name": str(_diff_value(source,"display_name","") or uid),
        "sub_stats": sub_stats,
        "quality": quality,
    }
    if item_type=="tape":
        snapshot["set_name"]=_diff_value(source,"set_name","") or "卡带"
        snapshot["main_stats"]=_diff_value(source,"main_stats","")
        snapshot["shape_id"]="TAPE_15"
    else:
        snapshot["shape_id"]=_diff_value(source,"shape_id","") or ""
    if area:
        snapshot["area"]=area
        snapshot["score_area"]=area
    if score_value is not None:
        snapshot["score"]=score_value
    if grade is not None:
        snapshot["grade"]=str(grade)
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
        return []
    items=[]
    tape=role_data.get("equipped_tape")
    if isinstance(tape,dict):
        items.append(tape)
    items.extend([item for item in role_data.get("equipped_drives",[]) or [] if isinstance(item,dict)])
    return items

def _diff_plan_sources(self, role_name):
    plan=(getattr(self,"final_plan",{}) or {}).get(role_name,{})
    if not isinstance(plan,dict):
        return []
    return (
        ([plan.get("assigned_tape")] if plan.get("assigned_tape") else [])
        + list(plan.get("assigned_set_drives",[]) or [])
        + list(plan.get("assigned_extra_drives",[]) or [])
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
    display=str(item.get("display_name") or "")
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
    uid=str(hydrated.get("uid","") or "")
    if uid:
        for source in _diff_saved_sources(self, role_name):
            if str(source.get("uid",""))==uid:
                hydrated=_merge_diff_item(hydrated,_diff_snapshot_from_source(self,role_name,source))
                break
        if not hydrated.get("shape_id") or not hydrated.get("sub_stats") or "score" not in hydrated:
            for source in _diff_plan_sources(self, role_name):
                if str(_diff_value(source,"uid",""))==uid:
                    hydrated=_merge_diff_item(hydrated,_diff_snapshot_from_source(self,role_name,source))
                    break
        if not hydrated.get("shape_id") or not hydrated.get("sub_stats") or "score" not in hydrated:
            source=_diff_inventory_sources(self).get(uid)
            if source:
                hydrated=_merge_diff_item(hydrated,_diff_snapshot_from_source(self,role_name,source))
    if not hydrated.get("shape_id") or not hydrated.get("sub_stats"):
        hydrated=_merge_diff_item(hydrated,_parse_diff_display_name(hydrated))
    item_type=hydrated.get("type") or hydrated.get("item_type")
    if item_type:
        hydrated["type"]=item_type
    elif hydrated.get("shape_id")=="TAPE_15":
        hydrated["type"]="tape"
    else:
        hydrated["type"]="drive"
    if "score" in hydrated and "grade" not in hydrated:
        area=int(hydrated.get("score_area") or hydrated.get("area") or (15 if hydrated.get("type")=="tape" else 0) or 0)
        if area:
            hydrated["grade"]=_diff_grade(self,float(hydrated.get("score") or 0.0),area)
            hydrated["score_area"]=area
    return hydrated

def _diff_item_card(self, role_name, item, is_new=False):
    item=_hydrate_diff_item(self, role_name, item)
    role_cfg=self.roles_db.get(role_name,{})
    weights=role_cfg.get("weights",{})
    main_weights=role_cfg.get("main_weights")
    score_info=getattr(self, "_diff_item_score_info", None) or (lambda diff_item: _diff_item_score_info(self, diff_item))
    item_type=item.get("type","drive")
    if item_type=="tape":
        label=item.get("set_name") or "卡带"
        main_stat=item.get("main_stats","")
        shape_id=None
    else:
        label=item.get("shape_id") or item.get("display_name") or item.get("uid","")
        main_stat=""
        shape_id=item.get("shape_id") or ""
    return self._equip_card(
        label,
        main_stat,
        item.get("sub_stats",{}) or {},
        shape_id,
        item.get("uid",""),
        weights,
        score_info(item),
        item.get("quality","Gold"),
        is_new=is_new,
        main_weights=main_weights,
    )

def _build_plan_diff_dialog(self, role_name, diff):
    dlg=QDialog(self if isinstance(self, QWidget) else None)
    dlg.setWindowTitle(f"{role_name} - 配装变动")
    dlg.setMinimumSize(760,520)
    dlg.setStyleSheet(STYLE)
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(10)
    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    body=QWidget(); body_layout=QVBoxLayout(body); body_layout.setContentsMargins(0,0,0,0); body_layout.setSpacing(8)
    section_label=getattr(self, "_section_label", None) or (lambda text: _section_label(self, text))
    diff_item_card=getattr(self, "_diff_item_card", None) or (lambda role, item, is_new=False: _diff_item_card(self, role, item, is_new))
    removed=diff.get("removed",[]) or []
    added=diff.get("added",[]) or []
    if removed:
        body_layout.addWidget(section_label("卸下装备"))
        for item in removed:
            body_layout.addWidget(diff_item_card(role_name,item,is_new=False))
    else:
        body_layout.addWidget(QLabel("本次没有卸下装备。"))
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
    if not role_diff.get("changed"):
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
    tape=role_state.get("equipped_tape")
    if isinstance(tape,dict):
        total+=float(tape.get("score",0.0) or 0.0)
    for drive in role_state.get("equipped_drives",[]) or []:
        if isinstance(drive,dict):
            total+=float(drive.get("score",0.0) or 0.0)
    role_state["total_score"]=round(total,2)
    role_state["total_grade"]=self._calc_grade(total,ALLOCATION_TOTAL_SCORE_AREA)
    role_state["score_area"]=ALLOCATION_TOTAL_SCORE_AREA

def _persist_saved_equipment_sync(self):
    save=getattr(self,"_save_eq",None)
    if callable(save):
        save()
    refresh=getattr(self,"_refresh_equip",None)
    if callable(refresh):
        try:
            refresh()
        except AttributeError:
            pass

def _sync_saved_drive_replacement(self, role_name, old_uid, new_drive, new_score, new_area):
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name)
    if not isinstance(role_state,dict):
        return False
    drives=role_state.get("equipped_drives",[]) or []
    new_uid=str(new_drive.get("uid","") or "")
    changed=False
    for drive in drives:
        if not isinstance(drive,dict) or str(drive.get("uid",""))!=str(old_uid):
            continue
        drive.update({
            "uid": new_uid,
            "shape_id": new_drive.get("shape_id",""),
            "sub_stats": new_drive.get("sub_stats",{}) or {},
            "quality": new_drive.get("quality","Gold"),
            "area": new_area,
            "display_name": new_drive.get("display_name",""),
            "score": new_score,
            "grade": self._calc_grade(new_score,new_area),
            "score_area": new_area,
            "is_changed": True,
        })
        if new_drive.get("main_stats"):
            drive["main_stats"]=new_drive.get("main_stats")
        drive.pop("is_new",None)
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
    main_stat=_saved_equipment_main_stat_text(new_tape.get("main_stats",{}) or {})
    role_state["equipped_tape"]={
        "uid": str(new_tape.get("uid","") or ""),
        "set_name": new_tape.get("set_name",""),
        "display_name": new_tape.get("display_name",""),
        "main_stats": main_stat,
        "sub_stats": new_tape.get("sub_stats",{}) or {},
        "quality": new_tape.get("quality","Gold"),
        "score": new_score,
        "grade": self._calc_grade(new_score,15),
        "score_area": 15,
        "is_changed": True,
    }
    _saved_equipment_score_total(self,role_name,role_state)
    return True

def _sync_role_drive_replacement(self, role_name, old_uid, new_drive):
    weights=(getattr(self,"roles_db",{}) or {}).get(role_name,{}).get("weights",{})
    new_uid=str(new_drive.get("uid","") or "")
    if not old_uid or not new_uid:
        return False

    new_shape=new_drive.get("shape_id","")
    new_sub_stats=new_drive.get("sub_stats",{}) or {}
    new_quality=new_drive.get("quality","Gold")
    new_score=self._score_drive_dict(new_sub_stats,new_shape,weights,new_quality)
    new_area=int(new_drive.get("area") or getattr(self,"_shape_areas",{}).get(new_shape,3) or 3)

    old_state=copy.deepcopy(getattr(self,"equipped_state",{}) or {})
    saved_changed=_sync_saved_drive_replacement(self,role_name,old_uid,new_drive,new_score,new_area)
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name,{}) if isinstance(state,dict) else {}
    existing_diff=role_state.get("last_diff",{}) if isinstance(role_state,dict) else {}
    if not saved_changed and not existing_diff.get("changed"):
        return False

    if saved_changed:
        state_mgr=getattr(self,"state_mgr",None)
        if state_mgr is not None and hasattr(state_mgr,"_build_role_diff") and isinstance(role_state,dict):
            role_diff=state_mgr._build_role_diff(old_state.get(role_name),role_state)
            if role_diff.get("changed"):
                role_state["last_diff"]=role_diff
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
    new_uid=str(new_tape.get("uid","") or "")
    if not new_uid:
        return False

    main_stats=new_tape.get("main_stats",{}) or {}
    main_stat=next(iter(main_stats.keys()),"") if isinstance(main_stats,dict) else str(main_stats or "")
    sub_stats=new_tape.get("sub_stats",{}) or {}
    quality=new_tape.get("quality","Gold")
    new_score=self._score_tape_dict(main_stat,sub_stats,weights,quality,main_weights)

    old_state=copy.deepcopy(getattr(self,"equipped_state",{}) or {})
    saved_changed=_sync_saved_tape_replacement(self,role_name,new_tape,new_score)
    state=getattr(self,"equipped_state",{}) or {}
    role_state=state.get(role_name,{}) if isinstance(state,dict) else {}
    existing_diff=role_state.get("last_diff",{}) if isinstance(role_state,dict) else {}
    if not saved_changed and not existing_diff.get("changed"):
        return False
    if saved_changed:
        state_mgr=getattr(self,"state_mgr",None)
        if state_mgr is not None and hasattr(state_mgr,"_build_role_diff") and isinstance(role_state,dict):
            role_diff=state_mgr._build_role_diff(old_state.get(role_name),role_state)
            if role_diff.get("changed"):
                role_state["last_diff"]=role_diff
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
    if w<0.3: return "#8b949e"
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
        main_stat=self._item_value(tape,"main_stats","")
        main_value=self._item_value(tape,"main_value",None)
        if main_value is None:
            main_value=self._fallback_tape_main_value(main_stat,self._item_value(tape,"quality","Gold"))
        self._add_stat_total(totals,main_stat,main_value)
        for stat,value in (self._item_value(tape,"sub_stats",{}) or {}).items():
            self._add_stat_total(totals,stat,value)
    drives=list(drives or [])
    for drive in drives:
        for stat,value in (self._item_value(drive,"sub_stats",{}) or {}).items():
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
            area=self._item_value(drive,"area",None)
            if area is None:
                area=self._shape_areas.get(self._item_value(drive,"shape_id",""),0)
            if int(area or 0)==target_area:
                matched_count+=1
    for stat,value in extra_buffs.items():
        self._add_stat_total(totals,stat,self._stat_number_value(value)*matched_count)
    rows=sorted(totals.items(),key=lambda kv: kv[1],reverse=True)
    return [(stat,value) for stat,value in rows if value]

def _format_bonus_value(self, stat, value):
    suffix="%" if "%" in stat or "伤害增强" in stat or "治疗加成" in stat else ""
    if suffix:
        return f"+{value:.2f}%"
    return f"+{value:.0f}" if abs(value-round(value))<0.01 else f"+{value:.2f}"

def _bonus_summary_widget(self, role_name, tape, drives):
    rows=self._equipment_bonus_rows(role_name,tape,drives)
    box=QFrame()
    box.setFixedWidth(240)
    box.setStyleSheet("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}")
    layout=QVBoxLayout(box); layout.setContentsMargins(7,5,7,5); layout.setSpacing(4)
    title=QLabel("属性汇总")
    title.setStyleSheet("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent")
    layout.addWidget(title)
    visible=rows[:4]
    if not visible:
        empty=QLabel("暂无可汇总属性")
        empty.setStyleSheet("color:#6e7681;border:none;background:transparent")
        layout.addWidget(empty)
    for stat,value in visible:
        layout.addWidget(self._bonus_row_widget(stat,value))
    if len(rows)>len(visible):
        more=QPushButton("•••")
        more.setObjectName("btnSm")
        more.setFixedSize(54,22)
        more.setCursor(Qt.PointingHandCursor)
        more.setStyleSheet("QPushButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;font-size:13px;font-weight:800;padding:0}QPushButton:hover{border-color:#58a6ff;color:#58a6ff}")
        more.clicked.connect(lambda checked=False,r=rows,role=role_name: self._show_bonus_summary_dialog(role,r))
        layout.addWidget(more,0,Qt.AlignCenter)
    layout.addStretch()
    return box

def _bonus_row_widget(self, stat, value):
    row=QFrame()
    row.setStyleSheet("QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}")
    rl=QHBoxLayout(row); rl.setContentsMargins(6,1,6,1); rl.setSpacing(6)
    name=QLabel(stat); name.setStyleSheet("font-size:10px;font-weight:700;color:#c9d1d9;border:none;background:transparent")
    val=QLabel(self._format_bonus_value(stat,value)); val.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
    val.setStyleSheet("font-size:10px;font-weight:800;color:#f0f6fc;border:none;background:transparent")
    rl.addWidget(name,1); rl.addWidget(val)
    return row

def _show_bonus_summary_dialog(self, role_name, rows):
    dlg=QDialog(self)
    dlg.setWindowTitle(f"{role_name} 属性汇总")
    dlg.setMinimumSize(360,420)
    dlg.setStyleSheet(STYLE)
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(8)
    for stat,value in rows:
        layout.addWidget(self._bonus_row_widget(stat,value))
    buttons=QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()

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
    QUALITY_COLORS={"Gold":"#ffd700","Purple":"#ffe082","Blue":"#58a6ff"}
    QUALITY_LABELS={"Gold":"金","Purple":"紫","Blue":"蓝"}
    QUALITY_BGS={"Gold":"#332600","Purple":"#6f2dbd","Blue":"#0d2748"}
    w=QWidget(); w.setStyleSheet("QWidget{background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:9px 13px;margin:3px 0}")
    outer=QHBoxLayout(w); outer.setSpacing(12); outer.setContentsMargins(2,2,2,2)

    # Shape image (compact)
    if shape_id:
        pm=_get_shape_pixmap(shape_id,64,quality)
        if not pm.isNull():
            img_lbl=QLabel(); img_lbl.setPixmap(pm); img_lbl.setFixedSize(68,68); img_lbl.setScaledContents(True)
            img_lbl.setStyleSheet("border:1px solid #30363d;border-radius:6px;background:#161b22"); outer.addWidget(img_lbl)

    inner=QVBoxLayout(); inner.setSpacing(5); inner.setContentsMargins(0,3,0,3)

    # Header: shape name + quality + main stat block + score|grade
    hdr=QHBoxLayout(); hdr.setSpacing(8)
    label_color = "#4dd0e1"
    label_bg = "#4dd0e122" if not shape_id else f"{label_color}15"
    label_border = label_color
    name_lbl = QLabel(f"<b>{label}</b>")
    name_size = 12 if shape_id else 13
    name_pad = "2px 8px" if shape_id else "3px 10px"
    name_lbl.setStyleSheet(f"font-size:{name_size}px;font-weight:800;color:{label_color};border:1px solid {label_border};border-radius:6px;padding:{name_pad};background:{label_bg}")
    hdr.addWidget(name_lbl)
    status_labels = []
    if is_new:
        new_lbl=QLabel("NEW")
        new_lbl.setStyleSheet("font-size:10px;font-weight:800;color:#58a6ff;border:1px solid #58a6ff;border-radius:5px;padding:2px 6px;background:#1f6feb22")
        status_labels.append(new_lbl)
    if is_changed:
        change_lbl=QLabel("CHANGE")
        change_lbl.setStyleSheet("font-size:10px;font-weight:800;color:#7ee787;border:1px solid #2ea043;border-radius:5px;padding:2px 6px;background:#23863622")
        status_labels.append(change_lbl)
    if status_labels and shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label)
    # Quality badge: only tapes show text; drive quality is represented by the icon.
    if quality and not shape_id:
        qcolor=QUALITY_COLORS.get(quality,"#8b949e"); qlabel=QUALITY_LABELS.get(quality,quality)
        qbg=QUALITY_BGS.get(quality,f"{qcolor}15")
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
        sf.setStyleSheet(f"QFrame{{background:{gc}15;border:1px solid {gc};border-radius:6px;padding:2px 10px}}")
        sf_layout=QHBoxLayout(sf); sf_layout.setSpacing(5); sf_layout.setContentsMargins(4,1,4,1)
        sl=QLabel(f"{score:.1f}"); sl.setStyleSheet(f"font-size:13px;font-weight:800;color:{gc};border:none"); sf_layout.addWidget(sl)
        gl=QLabel(grade); gl.setStyleSheet(f"font-size:11px;font-weight:800;color:{gc};border:none"); sf_layout.addWidget(gl)
        hdr.addWidget(sf)
    uid_lbl=QLabel(f"<span style='color:#6e7681;font-size:10px;'>{uid}</span>"); hdr.addWidget(uid_lbl)
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
