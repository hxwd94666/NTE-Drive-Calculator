# 渲染配装结果、评分和属性汇总。
"""MainWindow methods for allocation."""

from __future__ import annotations

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
from src.features.allocation.bonus_summary import (
    BonusSummaryContext,
    add_stat_total,
    aligned_bonus_comparison_rows,
    bonus_rows_for_mode,
    bonus_summary_mode_label,
    bonus_uses_percent,
    canonical_stat_name,
    collect_added_uids,
    equipment_bonus_rows,
    extra_shape_area,
    fallback_tape_main_value,
    format_bonus_delta_value,
    format_bonus_value,
    get_my_role_entry,
    has_bonus_delta,
    is_highlighted_bonus_stat,
    item_value,
    loadout_uids,
    merge_bonus_row_lists,
    quality_coef,
    resolve_comparison_role_diff,
    role_base_bonus_rows,
    sort_bonus_aligned_rows,
    split_loadout_sources,
    stat_number_value,
    synthesize_character_bonus_rows,
)
from src.features.allocation.plan_diff_pairing import pair_drive_diff_items
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
    '_merge_bonus_row_lists', '_synthesize_character_bonus_rows', '_bonus_rows_for_mode',
    '_bonus_summary_mode_label', '_make_bonus_mode_switch',
    '_clear_layout_widgets', '_format_bonus_value', '_role_stat_priority_stats',
    '_bonus_stat_weight', '_sort_bonus_rows_for_role', '_sort_bonus_aligned_rows_for_role',
    '_bonus_stat_label_style', '_format_panel_value',
    '_sort_bonus_aligned_rows', '_role_bonus_summary_panel', '_refresh_bonus_summary_panel',
    '_aligned_bonus_comparison_rows', '_has_bonus_delta', '_bonus_row_widget', '_bonus_comparison_column',
    '_bonus_delta_row_widget', '_bonus_delta_column',
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
        sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
        slb.addWidget(QLabel("评分")); slb.addWidget(sv)
        role_hdr.addWidget(sf)
        # Grade badge (separate)
        gf=QFrame()
        gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
        gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
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
            bp_row=QHBoxLayout(); bp_row.setSpacing(18)
            bp_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            bp_row.addWidget(PuzzleBoardWidget(board),0,Qt.AlignTop)
            compare_with_saved=bool(role_diff.get(DIFF_CHANGED))
            bp_row.addWidget(
                self._role_bonus_summary_panel(
                    role,
                    tape,
                    drives,
                    compare_with_saved=compare_with_saved,
                    priority_stats=self._role_stat_priority_stats(role),
                ),
                1 if compare_with_saved else 0,
                Qt.AlignTop,
            )
            gl.addLayout(bp_row); gl.addSpacing(8)

        if tape:
            t_score=tape.role_scores.get(role,0) if hasattr(tape,'role_scores') else 0
            t_grade=self._calc_grade(t_score,15)
            tape_uid=str(_diff_value(tape,"uid","") or "")
            tape_changed=bool(_diff_value(tape,"is_changed",False) or tape_uid in changed_uids)
            gl.addWidget(self._section_label("卡带:"))
            gl.addWidget(self._equip_card(tape.set_name,tape.main_stats,tape.sub_stats,None,tape.uid,wts,(t_score,t_grade),tape.quality,is_new=(tape_uid in added_uids and not tape_changed),is_changed=tape_changed,is_discarded=bool(getattr(tape,"discarded",False)),main_weights=main_wts,card_variant="result"))

        if drives:
            gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
            for d in drives:
                score=d.role_scores.get(role,0) if hasattr(d,'role_scores') else 0
                grade=self._calc_grade(score,d.area)
                mvp_tag=f" 👑第{d.pick_order}顺位" if getattr(d,'is_mvp',False) else ""
                drive_uid=str(_diff_value(d,"uid","") or "")
                drive_changed=bool(_diff_value(d,"is_changed",False) or drive_uid in changed_uids)
                gl.addWidget(self._equip_card(d.shape_id,"",d.sub_stats,d.shape_id,d.uid+mvp_tag,wts,(score,grade),d.quality,is_new=(drive_uid in added_uids and not drive_changed),is_changed=drive_changed,is_discarded=bool(getattr(d,"discarded",False)),is_duplicate_drive=bool(getattr(d,"is_duplicate_drive",False)),card_variant="result"))
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

def _loadout_items_from_role_data(role_data):
    if not isinstance(role_data, dict):
        return []
    items = []
    tape = role_data.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, dict):
        items.append(tape)
    items.extend([item for item in role_data.get(ROLE_EQUIPPED_DRIVES, []) or [] if isinstance(item, dict)])
    return items

def _diff_saved_sources(self, role_name):
    role_data=(getattr(self,"equipped_state",{}) or {}).get(role_name,{})
    items=_loadout_items_from_role_data(role_data)
    if items:
        return items
    state_mgr=getattr(self,"state_mgr",None)
    if state_mgr is not None and hasattr(state_mgr,"load_state"):
        try:
            loaded=state_mgr.load_state() or {}
        except Exception:
            loaded={}
        role_data=loaded.get(role_name,{}) if isinstance(loaded,dict) else {}
        return _loadout_items_from_role_data(role_data)
    return []

def _previous_loadout_from_diff(self, role_name, tape, drives, role_diff):
    role_diff=role_diff or {}
    removed=[dict(item) for item in (role_diff.get(DIFF_REMOVED,[]) or []) if isinstance(item,dict)]
    added_uids=collect_added_uids(role_diff)
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
    return split_loadout_sources(old_items)

def _diff_plan_sources(self, role_name):
    plan=(getattr(self,"final_plan",{}) or {}).get(role_name,{})
    if not isinstance(plan,dict):
        return []
    return (
        ([plan.get(PLAN_ASSIGNED_TAPE)] if plan.get(PLAN_ASSIGNED_TAPE) else [])
        + plan_drives(plan)
    )

def _diff_inventory_sources(self):
    path_key=str(getattr(runtime, "USER_DATABASE_PATH", ""))
    cached=getattr(self,"_diff_inventory_index_cache",None)
    if cached and cached[0]==path_key:
        return cached[1]
    index={}
    try:
        from src.services.sqlite_allocation_inventory import load_current_inventory_projection
        data=load_current_inventory_projection(path_key)
    except Exception:
        data=[]
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
        is_duplicate_drive=bool(item.get("is_duplicate_drive", False)),
        main_weights=main_weights,
        card_variant="result",
    )

def _append_equipment_swap_frame(body_layout, role_name, old_item, new_item, diff_item_card):
    pair_frame=QFrame()
    pair_frame.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:8px 10px}"))
    pair_layout=QVBoxLayout(pair_frame); pair_layout.setSpacing(6); pair_layout.setContentsMargins(8,6,8,6)

    old_lbl=QLabel("← 卸下（旧）")
    old_lbl.setStyleSheet(themed_style("font-size:11px;font-weight:700;color:#f85149;border:none;background:transparent;padding:2px 4px"))
    pair_layout.addWidget(old_lbl)
    if old_item is not None:
        pair_layout.addWidget(diff_item_card(role_name,old_item,is_new=False))
    else:
        pair_layout.addWidget(QLabel("  （无需卸下）"))

    arrow=QLabel("  ↓")
    arrow.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#58a6ff;border:none;background:transparent;padding:0 0 0 12px"))
    pair_layout.addWidget(arrow)

    new_lbl=QLabel("→ 换上（新）")
    new_lbl.setStyleSheet(themed_style("font-size:11px;font-weight:700;color:#56d364;border:none;background:transparent;padding:2px 4px"))
    pair_layout.addWidget(new_lbl)
    if new_item is not None:
        pair_layout.addWidget(diff_item_card(role_name,new_item,is_new=True))
    else:
        pair_layout.addWidget(QLabel("  （无需换上）"))

    body_layout.addWidget(pair_frame)

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

        pair_index=0

        if removed_tape or added_tape:
            pair_index+=1
            body_layout.addWidget(section_label(f"变动 {pair_index}：卡带"))
            _append_equipment_swap_frame(
                body_layout,
                role_name,
                removed_tape[0] if removed_tape else None,
                added_tape[0] if added_tape else None,
                diff_item_card,
            )

        drive_pairs,unmatched_old,unmatched_new=pair_drive_diff_items(
            removed_drives,
            added_drives,
            getattr(self,"_shape_areas",{}) or {},
        )

        for old_d,new_d in drive_pairs:
            pair_index+=1
            old_sid=old_d.get(EQUIP_SHAPE_ID,"未知驱动")
            new_sid=new_d.get(EQUIP_SHAPE_ID,"未知驱动")
            title=f"变动 {pair_index}：{old_sid} → {new_sid}" if old_sid!=new_sid else f"变动 {pair_index}：{old_sid}"
            body_layout.addWidget(section_label(title))
            _append_equipment_swap_frame(body_layout,role_name,old_d,new_d,diff_item_card)

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
    return quality_coef(quality)

def _canonical_stat_name(self, stat):
    return canonical_stat_name(stat, BonusSummaryContext.from_window(self).stat_alias_mapping)

def _stat_number_value(self, value):
    return stat_number_value(value)

def _item_value(self, item, key, default=None):
    return item_value(item, key, default)

def _add_stat_total(self, totals, stat, value):
    add_stat_total(totals, stat, value, BonusSummaryContext.from_window(self).stat_alias_mapping)

def _fallback_tape_main_value(self, main_stat, quality):
    ctx=BonusSummaryContext.from_window(self)
    return fallback_tape_main_value(main_stat, quality, ctx.stats_config, ctx.stat_alias_mapping)

def _extra_shape_area(self, role_name):
    return extra_shape_area(role_name, self.roles_db)

def _equipment_bonus_rows(self, role_name, tape, drives):
    return equipment_bonus_rows(BonusSummaryContext.from_window(self), role_name, tape, drives)

def _get_my_role_entry(self, role_name):
    return get_my_role_entry(role_name)

def _role_base_bonus_rows(self, role_name):
    return role_base_bonus_rows(BonusSummaryContext.from_window(self), role_name)

def _merge_bonus_row_lists(self, *sources):
    return merge_bonus_row_lists(BonusSummaryContext.from_window(self), *sources)

def _synthesize_character_bonus_rows(self, rows):
    return synthesize_character_bonus_rows(rows)

def _bonus_rows_for_mode(self, role_name, tape, drives, mode="equipment"):
    return bonus_rows_for_mode(BonusSummaryContext.from_window(self), role_name, tape, drives, mode)

def _bonus_summary_mode_label(self, mode):
    return bonus_summary_mode_label(mode)

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
    return format_bonus_value(stat, value)

def _format_bonus_delta_value(stat, delta):
    return format_bonus_delta_value(stat, delta)

def _is_highlighted_bonus_stat(stat, priority_stats=None):
    return is_highlighted_bonus_stat(stat, priority_stats)

def _bonus_stat_weight(self, role_name, stat, mode="equipment"):
    weights=((getattr(self,"roles_db",{}) or {}).get(role_name,{}) or {}).get("weights",{}) or {}
    if not weights:
        return 0.0
    panel_components={
        "总攻击力": ("攻击力白值","攻击力%","攻击力"),
        "总生命值": ("生命白值","生命值%","生命值"),
        "总防御力": ("防御力白值","防御力%","防御力"),
    }
    candidates=panel_components.get(stat,(stat,)) if mode=="character" else (stat,)
    return max((self._stat_w(candidate,weights) for candidate in candidates),default=0.0)

def _sort_bonus_rows_for_role(self, role_name, rows, mode="equipment"):
    return sorted(
        rows or [],
        key=lambda item:(-self._bonus_stat_weight(role_name,item[0],mode),str(item[0])),
    )

def _sort_bonus_aligned_rows_for_role(self, role_name, aligned, mode="equipment"):
    return sorted(
        aligned or [],
        key=lambda item:(-self._bonus_stat_weight(role_name,item.get("stat",""),mode),str(item.get("stat",""))),
    )

def _bonus_stat_label_style(self, stat, role_name=None, mode="equipment", colored_stats=None):
    if not role_name or (mode=="character" and colored_stats is not None and stat not in colored_stats):
        color=theme_color("#c9d1d9")
    else:
        color=self._stat_c(self._bonus_stat_weight(role_name,stat,mode))
    return f"font-size:10px;font-weight:700;color:{color};border:none;background:transparent"

def _format_panel_value(self, stat, value):
    suffix="%" if bonus_uses_percent(stat) else ""
    value=float(value or 0.0)
    number=f"{value:.0f}" if abs(value-round(value))<0.01 else f"{value:.2f}"
    return f"{number}{suffix}"

def _display_bonus_stat_label(stat):
    """Compact attribute-damage labels without changing their calculation keys."""
    label=str(stat or "")
    if "属性" in label and "伤害" in label:
        return f"{label.split('属性',1)[0]}属性伤害"
    return label

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
    return sort_bonus_aligned_rows(aligned, priority_stats, prioritize_changed_only)

def _has_bonus_delta(self, item):
    return has_bonus_delta(item)

def _aligned_bonus_comparison_rows(self, old_rows, new_rows, limit=None, changes_only=False, priority_stats=None):
    return aligned_bonus_comparison_rows(old_rows, new_rows, limit, changes_only, priority_stats)

def _bonus_comparison_column(self, title, aligned_rows, value_key, empty_text="暂无可汇总属性", priority_stats=None, role_name=None, mode="equipment", colored_stats=None):
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
                layout.addWidget(self._bonus_row_widget(item["stat"], display_text="—", priority_stats=priority_stats, role_name=role_name, mode=mode, colored_stats=colored_stats))
            else:
                layout.addWidget(self._bonus_row_widget(item["stat"], value, priority_stats=priority_stats, role_name=role_name, mode=mode, colored_stats=colored_stats))
    layout.addStretch()
    return column

def _bonus_more_button(on_click=None):
    more=QPushButton("•••")
    more.setObjectName("btnSm")
    more.setFixedSize(68,28)
    more.setCursor(Qt.PointingHandCursor)
    more.setStyleSheet(themed_style("QPushButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;font-size:13px;font-weight:800;padding:0}QPushButton:hover{border-color:#58a6ff;color:#58a6ff}"))
    if on_click is not None:
        more.clicked.connect(on_click)
    return more

def _configure_bonus_more_button(button, on_click=None):
    previous_callback=getattr(button,"_bonus_more_callback",None)
    if previous_callback is not None:
        try:
            button.clicked.disconnect(previous_callback)
        except (RuntimeError, TypeError):
            pass
    button.setVisible(on_click is not None)
    if on_click is not None:
        button.clicked.connect(on_click)
    button._bonus_more_callback=on_click

def _bonus_delta_row_widget(self, stat, delta, old_val, new_val, priority_stats=None, role_name=None, mode="equipment", colored_stats=None):
    if not self._has_bonus_delta({"stat": stat, "delta": delta, "old": old_val, "new": new_val}):
        row=QFrame()
        row.setFixedHeight(26)
        row.setStyleSheet(themed_style("QFrame{background:transparent;border:none;}"))
        return row
    color=theme_color("#56d364") if delta>0 else theme_color("#f85149")
    return self._bonus_row_widget(
        stat,
        display_text=_format_bonus_delta_value(stat, delta),
        priority_stats=priority_stats,
        role_name=role_name,
        mode=mode,
        colored_stats=colored_stats,
        value_style=f"font-size:10px;font-weight:800;color:{color};border:none;background:transparent",
    )

def _bonus_delta_column(self, aligned_rows, priority_stats=None, role_name=None, mode="equipment", colored_stats=None):
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
            layout.addWidget(self._bonus_delta_row_widget(item["stat"], item["delta"], item.get("old"), item.get("new"), priority_stats=priority_stats, role_name=role_name, mode=mode, colored_stats=colored_stats))
    layout.addStretch()
    return column

def _bonus_comparison_widget(self, role_name, old_rows, new_rows, has_old=True, compact=False, priority_stats=None, mode="equipment"):
    priority_stats=list(priority_stats or [])
    if compact:
        aligned=self._aligned_bonus_comparison_rows(old_rows,new_rows,changes_only=True,priority_stats=priority_stats)
    else:
        aligned=self._aligned_bonus_comparison_rows(old_rows,new_rows,priority_stats=priority_stats)
    aligned=self._sort_bonus_aligned_rows_for_role(role_name,aligned,mode)
    if compact:
        aligned=aligned[:4]
    colored_stats={item.get("stat") for item in aligned[:4]} if mode=="character" else None
    old_title="旧" if compact else "旧方案"
    new_title="新" if compact else "新方案"
    old_empty="无已保存配装" if not has_old else ("暂无属性变化" if compact else "暂无可汇总属性")
    old_column=self._bonus_comparison_column(old_title,aligned,"old",old_empty,priority_stats=priority_stats,role_name=role_name,mode=mode,colored_stats=colored_stats)
    new_column=self._bonus_comparison_column(new_title,aligned,"new","暂无属性变化" if compact and not aligned else "暂无可汇总属性",priority_stats=priority_stats,role_name=role_name,mode=mode,colored_stats=colored_stats)

    container=QFrame()
    container.setStyleSheet(themed_style("QFrame{background:transparent;border:none}"))
    layout=QHBoxLayout(container); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
    layout.addWidget(old_column,1)
    layout.addWidget(new_column,1)
    layout.addWidget(self._bonus_delta_column(aligned,priority_stats=priority_stats,role_name=role_name,mode=mode,colored_stats=colored_stats),1)
    return container

def _role_bonus_summary_panel(self, role_name, tape, drives, compare_with_saved=False, priority_stats=None, role_diff=None):
    priority_stats=list(priority_stats if priority_stats is not None else self._role_stat_priority_stats(role_name))
    state={"mode":"equipment"}
    box=QFrame()
    box.setMinimumWidth(560 if compare_with_saved else 300)
    box.setSizePolicy(QSizePolicy.Expanding if compare_with_saved else QSizePolicy.Maximum, QSizePolicy.Preferred)
    box.setStyleSheet(themed_style("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}"))
    layout=QVBoxLayout(box); layout.setContentsMargins(7,5,7,5); layout.setSpacing(4)
    header=QHBoxLayout(); header.setContentsMargins(0,0,0,0); header.setSpacing(4)
    mode_switch=self._make_bonus_mode_switch(state["mode"], lambda mode: self._refresh_bonus_summary_panel(box,role_name,tape,drives,compare_with_saved,priority_stats,mode,role_diff))
    mode_switch.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    header.addWidget(mode_switch,0,Qt.AlignLeft)
    more_button=_bonus_more_button()
    more_button.setVisible(False)
    header.addWidget(more_button)
    header.addStretch()
    layout.addLayout(header)
    content_host=QWidget()
    content_layout=QVBoxLayout(content_host); content_layout.setContentsMargins(0,0,0,0); content_layout.setSpacing(4)
    layout.addWidget(content_host)
    box._bonus_summary_content_layout=content_layout
    box._bonus_summary_more_button=more_button
    box._bonus_summary_state=state
    self._refresh_bonus_summary_panel(box,role_name,tape,drives,compare_with_saved,priority_stats,state["mode"],role_diff)
    layout.addStretch()
    return box

def _refresh_bonus_summary_panel(self, box, role_name, tape, drives, compare_with_saved, priority_stats, mode, role_diff=None):
    box._bonus_summary_state["mode"]=mode
    content_layout=box._bonus_summary_content_layout
    self._clear_layout_widgets(content_layout)
    _configure_bonus_more_button(box._bonus_summary_more_button)
    if compare_with_saved:
        # Saved SQLite plans carry their own immutable diff.  Do not fall back
        # to legacy equipped_state.json when that authoritative payload exists.
        persisted_diff = role_diff if isinstance(role_diff, dict) and role_diff.get(DIFF_CHANGED) else None
        effective_diff = persisted_diff or resolve_comparison_role_diff(self,role_name)
        if persisted_diff is not None:
            old_tape,old_drives=_previous_loadout_from_diff(self,role_name,tape,drives,effective_diff)
        else:
            saved_sources=_diff_saved_sources(self,role_name)
            old_tape,old_drives=split_loadout_sources(saved_sources)
        new_uids=loadout_uids(tape,drives)
        old_uids=loadout_uids(old_tape,old_drives)
        if effective_diff.get(DIFF_CHANGED) and ((not old_tape and not old_drives) or old_uids==new_uids):
            old_tape,old_drives=_previous_loadout_from_diff(self,role_name,tape,drives,effective_diff)
        if old_tape or old_drives:
            old_rows=self._bonus_rows_for_mode(role_name,old_tape,old_drives,mode)
            new_rows=self._bonus_rows_for_mode(role_name,tape,drives,mode)
            old_rows=self._sort_bonus_rows_for_role(role_name,old_rows,mode)
            new_rows=self._sort_bonus_rows_for_role(role_name,new_rows,mode)
            content_layout.addWidget(self._bonus_comparison_widget(role_name,old_rows,new_rows,has_old=True,compact=True,priority_stats=priority_stats,mode=mode))
            _configure_bonus_more_button(
                box._bonus_summary_more_button,
                lambda checked=False,role=role_name,old_r=old_rows,new_r=new_rows,stats=list(priority_stats),summary_mode=mode: self._show_bonus_comparison_dialog(role,old_r,new_r,stats,summary_mode),
            )
            return
    rows=self._bonus_rows_for_mode(role_name,tape,drives,mode)
    rows=self._sort_bonus_rows_for_role(role_name,rows,mode)
    visible=rows[:5]
    colored_stats={stat for stat,_value in visible} if mode=="character" else None
    if not visible:
        empty=QLabel("暂无可汇总属性")
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        content_layout.addWidget(empty)
    for stat,value in visible:
        content_layout.addWidget(self._bonus_row_widget(stat,value,priority_stats=priority_stats,role_name=role_name,mode=mode,colored_stats=colored_stats))
    if rows:
        _configure_bonus_more_button(
            box._bonus_summary_more_button,
            lambda checked=False,role=role_name,summary_rows=rows,summary_mode=mode: self._show_bonus_summary_dialog(role,summary_rows,summary_mode),
        )

def _show_bonus_comparison_dialog(self, role_name, old_rows, new_rows, priority_stats=None, mode="equipment"):
    priority_stats=list(priority_stats if priority_stats is not None else self._role_stat_priority_stats(role_name))
    dlg=QDialog(self)
    dlg.setWindowTitle(f"{role_name} {self._bonus_summary_mode_label(mode)}对比")
    dlg.setMinimumSize(680,360)
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(8)
    layout.addWidget(self._bonus_comparison_widget(role_name,old_rows,new_rows,has_old=True,compact=False,priority_stats=priority_stats,mode=mode))
    buttons=QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()

def _show_bonus_summary_dialog(self, role_name, rows, mode="equipment"):
    dlg=QDialog(self)
    dlg.setWindowTitle(f"{role_name} {self._bonus_summary_mode_label(mode)}")
    dlg.setMinimumSize(360,420)
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setContentsMargins(14,14,14,14); layout.setSpacing(8)
    rows=self._sort_bonus_rows_for_role(role_name,rows,mode)
    colored_stats={stat for stat,_value in rows[:4]} if mode=="character" else None
    for stat,value in rows:
        layout.addWidget(self._bonus_row_widget(stat,value,role_name=role_name,mode=mode,colored_stats=colored_stats))
    buttons=QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()

def _bonus_row_widget(self, stat, value=None, *, priority_stats=None, display_text=None, value_style=None, role_name=None, mode="equipment", colored_stats=None):
    row=QFrame()
    row.setFixedHeight(26)
    row.setMinimumWidth(130)
    row.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}"))
    rl=QHBoxLayout(row); rl.setContentsMargins(6,1,6,1); rl.setSpacing(6)
    name=QLabel(_display_bonus_stat_label(stat)); name.setWordWrap(True); name.setStyleSheet(self._bonus_stat_label_style(stat,role_name,mode,colored_stats))
    if display_text is not None:
        text=display_text
        style=value_style or themed_style("font-size:10px;font-weight:700;color:#6e7681;border:none;background:transparent")
    else:
        text=self._format_panel_value(stat,value) if mode=="character" else self._format_bonus_value(stat,value)
        style=value_style or themed_style("font-size:10px;font-weight:800;color:#f0f6fc;border:none;background:transparent")
    val=QLabel(text); val.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
    val.setStyleSheet(style)
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

def _equip_card(self,label,main_stat,sub_stats,shape_id,uid,weights,score_info=None,quality=None,is_new=False,is_changed=False,is_discarded=False,is_duplicate_drive=False,main_weights=None,replacement_callback=None,card_variant="default"):
    if current_theme_name() == "light":
        QUALITY_COLORS={"Gold":"#9a6700","Purple":"#8250df","Blue":"#0969da"}
    else:
        QUALITY_COLORS={"Gold":"#ffd700","Purple":"#ffe082","Blue":"#58a6ff"}
    QUALITY_LABELS={"Gold":"金","Purple":"紫","Blue":"蓝"}
    w=QWidget(); w.setObjectName("equipmentCard")
    w.setStyleSheet(themed_style("QWidget#equipmentCard{background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:9px 13px;margin:3px 0}"))
    outer=QHBoxLayout(w); outer.setSpacing(12); outer.setContentsMargins(14,2,2,2)

    # Shape image: 与首行标签保持均衡，避免图标显得过小。
    if shape_id:
        # Use a compact frame in both specialised views.  The image label
        # explicitly has no padding below, so the artwork fills the frame
        # instead of becoming a small icon inside a large blank box.
        image_size = {"inventory": 52, "result": 60}.get(card_variant, 64)
        pm=_get_shape_pixmap(shape_id,image_size,quality)
        if not pm.isNull():
            img_lbl=QLabel(); img_lbl.setPixmap(pm); img_lbl.setFixedSize(image_size,image_size); img_lbl.setScaledContents(True)
            img_lbl.setStyleSheet(themed_style("border:1px solid #30363d;border-radius:6px;background:#161b22;padding:0px")); outer.addWidget(img_lbl)

    row_spacing = {"result": 4, "inventory": 5}.get(card_variant, 5)
    inner=QVBoxLayout(); inner.setSpacing(row_spacing); inner.setContentsMargins(0,3,0,3)

    # Header: shape name + quality + main stat block + score|grade
    hdr=QHBoxLayout(); hdr.setSpacing(8)
    label_color = theme_color("#4dd0e1")
    label_bg = theme_rgba("#4dd0e1", 0.10)
    label_border = label_color
    name_lbl = QLabel(f"<b>{label}</b>")
    # Both result and saved-plan cards use a modestly larger, consistent
    # header line.  The stat line stays at 12px, so the hierarchy is clear
    # without making the card disproportionately tall.
    is_feature_card = card_variant in {"result", "inventory"}
    header_font_size = 15 if is_feature_card else None
    name_size = header_font_size if header_font_size else (12 if shape_id else 13)
    name_pad = "5px 10px" if is_feature_card else ("2px 8px" if shape_id else "3px 10px")
    name_lbl.setStyleSheet(f"font-size:{name_size}px;font-weight:800;color:{label_color};border:1px solid {label_border};border-radius:6px;padding:{name_pad};background:{label_bg}")
    hdr.addWidget(name_lbl, 0, Qt.AlignTop)
    status_font_size = header_font_size or 10
    status_pad = "5px 8px" if is_feature_card else "2px 6px"

    def _status_label(text, color, border_color, background):
        status = QLabel(text)
        status.setStyleSheet(
            f"font-size:{status_font_size}px;font-weight:800;color:{color};"
            f"border:1px solid {border_color};border-radius:5px;padding:{status_pad};background:{background}"
        )
        return status

    status_labels = []
    if is_new:
        status_labels.append(_status_label("NEW", theme_color("#58a6ff"), theme_color("#58a6ff"), theme_rgba("#58a6ff", 0.10)))
    if is_changed:
        status_labels.append(_status_label("CHANGE", theme_color("#7ee787"), theme_color("#2ea043"), theme_rgba("#238636", 0.10)))
    if is_discarded:
        status_labels.append(_status_label("弃置", "#ff7b72", "#ff7b72", "rgba(218,54,51,0.16)"))
    if is_duplicate_drive:
        status_labels.append(_status_label("重复", "#ffb86c", "#ff9d3d", "rgba(255,152,0,0.16)"))
    if status_labels and shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label, 0, Qt.AlignTop)
    # 品质标签只在卡带上展示；驱动品质由图标颜色区分。
    if quality and not shape_id:
        qcolor=QUALITY_COLORS.get(quality,theme_color("#8b949e")); qlabel=QUALITY_LABELS.get(quality,quality)
        if quality == "Purple":
            qcolor="#a371f7"
        qbg=theme_rgba(qcolor, 0.10)
        q_lbl=QLabel(qlabel)
        quality_font_size = header_font_size or 11
        quality_pad = "5px 9px" if is_feature_card else "2px 7px"
        q_lbl.setStyleSheet(f"font-size:{quality_font_size}px;font-weight:700;color:{qcolor};border:1px solid {qcolor};border-radius:5px;padding:{quality_pad};background:{qbg}")
        hdr.addWidget(q_lbl, 0, Qt.AlignTop)
    # Main stat as colored block (same style as sub stats)
    if main_stat:
        main_weight_source=main_weights if isinstance(main_weights, dict) else weights
        mw=self._stat_w(main_stat,main_weight_source); mc=self._stat_c(mw); qc=QColor(mc)
        ms_block=QLabel(main_stat); ms_block.setStyleSheet(
            f"border:1px solid {mc};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);"
            f"border-radius:6px;padding:{'5px 12px' if is_feature_card else '4px 12px'};font-size:{header_font_size or 13}px;color:{mc};font-weight:700"
        )
        hdr.addWidget(ms_block, 0, Qt.AlignTop)
    if status_labels and not shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label, 0, Qt.AlignTop)
    hdr.addStretch()

    # Score | Grade side by side.
    score_frame=None
    if score_info is not None:
        score,grade=score_info; gc=GRADE_COLORS.get(grade,"#58a6ff")
        score_frame=QFrame()
        score_pad = "4px 10px" if is_feature_card else "2px 10px"
        score_frame.setStyleSheet(f"QFrame{{background:{theme_rgba(gc, 0.10)};border:1px solid {gc};border-radius:6px;padding:{score_pad}}}")
        score_margin = 0 if is_feature_card else 1
        sf_layout=QHBoxLayout(score_frame); sf_layout.setSpacing(5); sf_layout.setContentsMargins(4,score_margin,4,score_margin)
        score_font_size = header_font_size or 13
        sl=QLabel(f"{score:.1f}"); sl.setStyleSheet(f"font-size:{score_font_size}px;font-weight:800;color:{gc};border:none"); sf_layout.addWidget(sl)
        gl=QLabel(grade); gl.setStyleSheet(f"font-size:{score_font_size}px;font-weight:800;color:{gc};border:none"); sf_layout.addWidget(gl)
        if is_feature_card:
            score_frame.setFixedHeight(name_lbl.sizeHint().height())
    if score_frame is not None:
        hdr.addWidget(score_frame, 0, Qt.AlignTop)
    if replacement_callback:
        replacement_btn=QPushButton("优化" if shape_id else "替换")
        replacement_btn.setObjectName("btnAction")
        if is_feature_card:
            replacement_btn.setFixedSize(74,33)
            replacement_btn.setStyleSheet(themed_style(f"font-size:{header_font_size}px;padding:2px 8px"))
        else:
            replacement_btn.setFixedSize(60,28)
        replacement_btn.clicked.connect(lambda _checked=False: replacement_callback())
        hdr.addWidget(replacement_btn, 0, Qt.AlignTop)
    inner.addLayout(hdr)

    # Stat blocks row
    if sub_stats:
        br=QHBoxLayout(); br.setSpacing(5)
        for sn,sv in sub_stats.items():
            sw=self._stat_w(sn,weights); color=self._stat_c(sw); qc=QColor(color)
            block=QLabel(f"{sn} <b>{sv}</b>"); block.setAlignment(Qt.AlignCenter)
            block.setStyleSheet(f"border:1px solid {color};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);border-radius:6px;padding:5px 12px;font-size:{'13px' if is_feature_card else '12px'};color:{color};font-weight:600")
            block.setToolTip(f"权重: {sw:.2f}"); br.addWidget(block)
        br.addStretch(); inner.addLayout(br)
    if card_variant == "result":
        inner.addStretch(1)
    outer.addLayout(inner,1); return w
