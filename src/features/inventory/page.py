# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QInputDialog, QLineEdit, QMessageBox, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.app.workers import WorkerThread
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.services.equipment_apply_service import EquipmentApplyService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
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
           '_preview_assemble_role', '_preview_assemble_all_roles', '_resume_nte_core_equipment_apply']

EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力", "AtkUp": "攻击力%", "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%", "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%", "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%", "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%", "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%", "DefAdd": "防御力", "DefUp": "防御力%",
    "HealUp": "治疗加成", "HPMaxAdd": "生命值", "HPMaxUp": "生命值%",
    "MagBase": "环合强度", "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2", "hen3": "H_3", "hen4": "H_4", "shu2": "V_2",
    "shu3": "V_3", "shu4": "V_4", "z3": "Trap_4_H", "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL", "zhijiao2": "L_3_TL", "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


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
    resume_btn=QPushButton("继续未完成装配"); resume_btn.clicked.connect(self._resume_nte_core_equipment_apply)
    sh.addWidget(resume_btn)
    l.addLayout(sh)
    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    self.equip_content=QWidget(); self.equip_content_layout=QVBoxLayout(self.equip_content); scroll.setWidget(self.equip_content)
    l.addWidget(scroll,1); return page

def _clear_equip_content(self):
    while self.equip_content_layout.count():
        it=self.equip_content_layout.takeAt(0)
        if it.widget(): it.widget().deleteLater()

def _refresh_equip(self):
    _clear_equip_content(self)
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao, StaticGameDataDao() as static_dao:
            plans = user_dao.list_active_loadout_plans_by_role()
            eq = {
                role_name: _sqlite_plan_display_state(plan, user_dao, static_dao)
                for role_name, plan in plans.items()
            }
    except Exception as exc:
        logger.error(f"刷新 SQLite 配装展示失败: {exc}")
        eq = {}
    all_roles=sorted(eq.keys())
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


def _official_stat_values(stats):
    values={}
    for stat in stats or []:
        property_id=str(stat.get("property_id") or "")
        label=_OFFICIAL_STAT_LABELS.get(property_id, property_id or "未知属性")
        value=float(stat.get("value",0.0) or 0.0)
        if stat.get("percent"):
            value*=100.0
        values[label]=round(value,6)
    return values


def _display_shape_id(geometry):
    value=str(geometry or "").removeprefix("EquipmentGeometry_").casefold()
    return _OFFICIAL_SHAPE_LABELS.get(value, str(geometry or "未知形状"))


def _sqlite_plan_display_state(plan, user_dao, static_dao):
    """将活动 SQLite 方案转换为配装页展示模型；不读取旧 JSON。"""
    snapshot_id=int(plan["source_snapshot_id"])
    items={(row["uid_serial"],row["uid_slot"]): row for row in user_dao.list_inventory_items(snapshot_id)}
    shape_cells={shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
    suit_names={str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()}
    board=[["0" for _ in range(5)] for _ in range(5)]
    drives=[]
    tape=None
    for assignment in plan["assignments"]:
        item=items.get((assignment["uid_serial"],assignment["uid_slot"]))
        if item is None:
            continue
        raw=assignment.get("raw_assignment") or {}
        uid_prefix="module" if item["kind"] == "module" else "core"
        uid=f"nte-{uid_prefix}-{item['uid_slot']}-{item['uid_serial']}"
        if item["kind"] == "core":
            main_stats=_official_stat_values(item.get("main_stats"))
            tape={
                EQUIP_UID: uid, EQUIP_SET_NAME: suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "未知套装")),
                EQUIP_MAIN_STATS: next(iter(main_stats), "未知主词条"), EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange":"Gold","purple":"Purple","blue":"Blue"}.get(str(item.get("quality")).casefold(),"Gold"),
                "discarded": bool(item.get("discarded")),
            }
            continue
        geometry=item.get("geometry")
        shape_id=_display_shape_id(geometry)
        drives.append({
            EQUIP_UID: uid, EQUIP_SHAPE_ID: shape_id, EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
            EQUIP_QUALITY: {"orange":"Gold","purple":"Purple","blue":"Blue"}.get(str(item.get("quality")).casefold(),"Gold"),
            "discarded": bool(item.get("discarded")),
        })
        row,column=assignment.get("target_row"),assignment.get("target_column")
        official_shape="EquipmentGeometry_" + str(geometry or "").removeprefix("EquipmentGeometry_")
        for cell in shape_cells.get(official_shape,[]):
            target_row=int(row)+int(cell["x"])-1
            target_column=int(column)+int(cell["y"])-1
            if 0 <= target_row < 5 and 0 <= target_column < 5:
                board[target_row][target_column]=shape_id
    payload=plan.get("payload") or {}
    return {
        ROLE_BLUEPRINT_LAYOUT: board, ROLE_EQUIPPED_TAPE: tape, ROLE_EQUIPPED_DRIVES: drives,
        ROLE_TOTAL_SCORE: float(plan.get("score") or 0.0), ROLE_TOTAL_GRADE: "",
        "strategy_mode": payload.get("strategy", ""), "_sqlite_plan_id": plan["plan_id"],
        "_sqlite_source_snapshot_id": snapshot_id,
    }

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
    is_sqlite_plan="_sqlite_plan_id" in rd

    total_score=0.0
    tape_data=rd.get(ROLE_EQUIPPED_TAPE)
    if is_sqlite_plan:
        total_score=float(rd.get(ROLE_TOTAL_SCORE,0.0) or 0.0)
        total_grade=self._calc_grade(total_score,ALLOCATION_TOTAL_SCORE_AREA)
    elif ROLE_TOTAL_SCORE in rd and rd.get(ROLE_TOTAL_GRADE):
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
    if is_sqlite_plan:
        snapshot_label=QLabel(f"快照 #{rd['_sqlite_source_snapshot_id']}")
        snapshot_label.setToolTip("展示的是该方案保存时的官方库存快照，不会随之后的同步结果变化。")
        snapshot_label.setStyleSheet(themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px"))
        role_hdr.addWidget(snapshot_label)
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
        gl.addWidget(self._equip_card(tape_data.get(EQUIP_SET_NAME,""),tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),None,tape_uid,wts,(t_s,t_g),t_q,is_new=bool(tape_data.get(EQUIP_IS_NEW)) and not tape_changed,is_changed=tape_changed,is_discarded=bool(tape_data.get("discarded")),main_weights=main_wts,replacement_callback=None,card_variant="inventory"))
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
            gl.addWidget(self._equip_card(d.get(EQUIP_SHAPE_ID,""),"",d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),drive_uid,wts,(d_s,d_g),d_q,is_new=bool(d.get(EQUIP_IS_NEW)) and not drive_changed,is_changed=drive_changed,is_discarded=bool(d.get("discarded")),replacement_callback=None,card_variant="inventory"))
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
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        plans=dao.list_active_loadout_plans_by_role()
    if not plans:
        QMessageBox.information(self,"清空配装","当前没有已保存的配装。")
        return
    ret=QMessageBox.question(
        self,
        "清空配装",
        "确定要从当前配装页移除所有已保存方案吗？\n方案历史和任务记录会保留，但这些方案不再参与装配。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        for plan in plans.values():
            dao.deactivate_loadout_plan(plan["plan_id"])
    self._refresh_equip()
    logger.success("已清空所有角色配装")

def _delete_role_equipment(self, role_name: str):
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        plan=dao.get_active_loadout_plan_for_role(role_name)
    if plan is None:
        self._refresh_equip()
        return
    ret=QMessageBox.question(
        self,
        "删除角色配装",
        f"确定要从当前配装页移除 [{role_name}] 的已保存方案吗？\n方案历史会保留，但该方案不再参与装配。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        dao.deactivate_loadout_plan(plan["plan_id"])
    self._refresh_equip()
    logger.success(f"已删除角色配装: {role_name}")


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


def _return_to_equipment_after_assembly(self) -> None:
    """Restore the calculator window and return to the equipment page."""
    show_normal = getattr(self, "showNormal", None)
    if callable(show_normal):
        show_normal()
    go_to_page = getattr(self, "_go", None)
    if callable(go_to_page):
        go_to_page("equipment")
    raise_window = getattr(self, "raise_", None)
    if callable(raise_window):
        raise_window()
    activate_window = getattr(self, "activateWindow", None)
    if callable(activate_window):
        activate_window()


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


def _uses_nte_core_equipment_apply(self) -> bool:
    """读取当前是否启用了 nte-core 的官方 SQLite 装配通道。"""

    settings_reader = getattr(self, "_get_sync_settings", None)
    if not callable(settings_reader):
        return False
    try:
        return settings_reader().get("equipment_apply_method") == "nte_core"
    except Exception as exc:
        logger.warning(f"读取装配方式失败，无法启用 nte-core 装配：{exc}")
        return False


def _is_equipment_plugin_unavailable_error(error: object) -> bool:
    """识别核心已启动但游戏内装备插件桥接不可用的不可重试错误。"""

    return "EQUIPMENT_PLUGIN_UNAVAILABLE" in str(error)


def _run_nte_core_equipment_apply(
    self,
    role_names: list[str],
    *,
    identity_overrides: dict[str, dict] | None = None,
    job_id: int | None = None,
) -> dict:
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None:
        raise RuntimeError("背包同步服务尚未启动，请先在首页启动后台同步")

    identity_overrides = identity_overrides or {}
    applied: list[dict] = []
    with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
        apply_service = EquipmentApplyService(user_dao, sync_service)
        if job_id is not None:
            job = user_dao.get_equipment_apply_job(job_id)
            if job is None:
                raise RuntimeError(f"装配任务 {job_id} 不存在")
            user_dao.reset_failed_equipment_apply_job_items(job_id)
            prepared = [
                {
                    "job_item_id": row["job_item_id"],
                    "role_name": row["role_name"],
                    "character_id": row["character_id"],
                    "character_uid": row["character_uid"],
                    "plan_id": row["plan_id"],
                }
                for row in job["items"] if row["status"] in {"pending", "running", "failed"}
            ]
            if not prepared:
                return {"job_id": job_id, "applied": [], "completed": job["status"] == "completed"}
        else:
            initial_snapshot_id = user_dao.current_inventory_snapshot_id()
            if initial_snapshot_id is None:
                raise RuntimeError("用户数据库中还没有稳定背包快照")

            # 必须在第一条装配指令前缓存全部角色 UID。后续角色可能因装备被前面的
            # 方案移走而暂时全身为空，此时再从当前快照解析会失败。
            prepared: list[dict] = []
            identity_requests: list[dict] = []
            for role_name in role_names:
                plan = user_dao.get_active_loadout_plan_for_role(role_name)
                if plan is None:
                    raise RuntimeError(
                        f"装配前检查 [{role_name}] 失败，尚未发送任何装配指令："
                        "没有来自官方背包快照的已保存方案，请重新计算并保存。"
                    )
                override = identity_overrides.get(role_name)
                try:
                    character_id = int(override["character_id"]) if override else int(plan["character_id"])
                    if character_id != int(plan["character_id"]):
                        raise RuntimeError("手动选择的角色 ID 与该 SQLite 方案不匹配")
                    character_uid = apply_service.resolve_character_uid(
                        character_id, initial_snapshot_id,
                        explicit_uid=override.get("character_uid") if override else None,
                    )
                except Exception as exc:
                    identity_requests.append(
                        {
                            "role_name": role_name,
                            "candidate_character_ids": [int(plan["character_id"])],
                            "reason": str(exc),
                        }
                    )
                    continue
                prepared.append({
                    "role_name": role_name, "character_id": character_id,
                    "character_uid": character_uid, "plan_id": plan["plan_id"],
                    "module_count": sum(1 for row in plan["assignments"] if row["kind"] == "module"),
                })
            if identity_requests:
                return {"identity_requests": identity_requests}
            job_id = user_dao.create_equipment_apply_job(initial_snapshot_id, prepared)
            for entry, prepared_role in zip(user_dao.get_equipment_apply_job(job_id)["items"], prepared):
                prepared_role["job_item_id"] = entry["job_item_id"]

        for prepared_role in prepared:
            role_name = prepared_role["role_name"]
            user_dao.mark_equipment_apply_job_item(prepared_role["job_item_id"], status="running")
            try:
                result = apply_service.apply_plan(
                    prepared_role["plan_id"],
                    character_uid=prepared_role["character_uid"],
                    timeout=30.0,
                )
                user_dao.mark_equipment_apply_job_item(
                    prepared_role["job_item_id"], status="succeeded",
                    before_snapshot_id=result.before_snapshot_id,
                    after_snapshot_id=result.after_snapshot_id,
                )
                applied.append(
                    {
                        "role_name": role_name,
                        "character_id": prepared_role["character_id"],
                        "plan_id": prepared_role["plan_id"],
                        "module_count": prepared_role.get("module_count"),
                        "snapshot_id": result.after_snapshot_id,
                        "already_applied": result.already_applied,
                    }
                )
            except Exception as exc:
                user_dao.mark_equipment_apply_job_item(prepared_role["job_item_id"], status="failed", error=str(exc))
                return {"job_id": job_id, "applied": applied, "failed_role": role_name, "error": str(exc), "completed": False}
        completed = user_dao.complete_equipment_apply_job_if_done(job_id)
    return {"job_id": job_id, "applied": applied, "completed": completed}


def _prompt_character_identity_requests(self, requests: list[dict]) -> dict[str, dict] | None:
    overrides: dict[str, dict] = {}
    for request in requests:
        role_name = request["role_name"]
        choices = [str(value) for value in request.get("candidate_character_ids") or []]
        if not choices:
            QMessageBox.warning(self, "角色实例", f"[{role_name}] 没有可选的官方角色 ID。")
            return None
        character_id, ok = QInputDialog.getItem(
            self, "选择角色实例", f"[{role_name}] 无法自动确定身份。\n原因：{request['reason']}\n\n请选择官方角色 ID：", choices, 0, False,
        )
        if not ok:
            return None
        uid_text, ok = QInputDialog.getText(
            self, "输入角色实例 UID", f"请输入 [{role_name}] 的实例 UID（slot,serial）：", QLineEdit.Normal,
        )
        if not ok:
            return None
        try:
            slot_text, serial_text = [value.strip() for value in str(uid_text).split(",", 1)]
            uid = {"slot": int(slot_text), "serial": int(serial_text)}
            with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
                user_dao.upsert_character_instance_mapping(int(character_id), uid, source="manual")
        except Exception as exc:
            QMessageBox.warning(self, "角色实例", f"实例 UID 无效或无法保存：{exc}")
            return None
        overrides[role_name] = {"character_id": int(character_id), "character_uid": uid}
    return overrides


def _start_nte_core_equipment_apply(self, role_names: list[str], *, identity_overrides: dict[str, dict] | None = None, job_id: int | None = None) -> None:
    current_worker = getattr(self, "_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "正在装配", "已有装配任务正在执行，请等待结果验证完成。")
        return

    worker = WorkerThread(
        target=lambda: _run_nte_core_equipment_apply(self, role_names, identity_overrides=identity_overrides, job_id=job_id),
        parent=self,
    )
    self._equipment_apply_worker = worker

    def on_result(report: dict) -> None:
        requests = report.get("identity_requests") or []
        if requests:
            overrides = _prompt_character_identity_requests(self, requests)
            if overrides is not None:
                _start_nte_core_equipment_apply(self, role_names, identity_overrides=overrides)
            return
        applied = report.get("applied") or []
        details = "\n".join(
            f"• {row['role_name']}"
            + (f"：{row['module_count']} 个驱动 + 1 个核心" if row.get("module_count") is not None else "：已确认")
            + ("（原本已装好）" if row.get("already_applied") else "")
            for row in applied
        )
        changed_count = sum(not row.get("already_applied") for row in applied)
        unchanged_count = len(applied) - changed_count
        summary = f"已确认 {len(applied)} 个角色的配装"
        if unchanged_count:
            summary += f"（实际装配 {changed_count} 个，原本已装好 {unchanged_count} 个）"
        if report.get("failed_role"):
            error_message = str(report.get("error") or "未知错误")
            if _is_equipment_plugin_unavailable_error(error_message):
                QMessageBox.warning(
                    self,
                    "装备插件不可用",
                    f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n"
                    "本地核心组件已连接，但未能连接游戏内装备插件（命名管道不可用或超时）。\n\n"
                    "请先确认：\n"
                    "1. 当前运行的 HTGame.exe 已加载与本地核心组件版本匹配的装备插件；\n"
                    "2. 游戏保持登录，随后从首页重新启动背包同步并等待“后台监听”；\n"
                    "3. 完成上述检查后，再点击“继续未完成装配”。\n\n"
                    f"此前已确认 {len(applied)} 个角色；任务日志已保存。此次不会立即重试。",
                )
                return
            retry = QMessageBox.question(
                self, "装配暂停",
                f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n{error_message}\n\n"
                f"此前已确认 {len(applied)} 个角色；任务日志已保存。是否重试失败角色并继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if retry == QMessageBox.Yes:
                _start_nte_core_equipment_apply(self, [], job_id=report["job_id"])
            return
        QMessageBox.information(self, "装配完成", f"{summary}。\n任务 #{report.get('job_id')} 已保存日志。\n\n{details}")
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        QMessageBox.critical(
            self,
            "装配失败",
            f"本地组件未能完成装配：\n{message}\n\n"
            "请确认游戏已登录、插件已加载，且首页背包同步处于“后台监听”。",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _resume_nte_core_equipment_apply(self) -> None:
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            job = user_dao.latest_resumable_equipment_apply_job()
        if job is None:
            QMessageBox.information(self, "继续装配", "没有可继续的装配任务。")
            return
        _start_nte_core_equipment_apply(self, [], job_id=job["job_id"])
    except Exception as exc:
        QMessageBox.warning(self, "继续装配", f"无法读取未完成任务：{exc}")


def _preview_nte_core_assemble_role(self, role_name: str) -> None:
    ret = QMessageBox.question(
        self,
        "瞬间装配",
        f"将通过本地组件把 [{role_name}] 的已保存方案直接装入游戏。\n\n"
        "若当前已经是目标配装会立即完成，否则发送指令并等待稳定背包快照确认；"
        "不需要切换到游戏配装页面。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, [role_name])


def _preview_nte_core_assemble_all_roles(self) -> None:
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            role_names = sorted(user_dao.list_active_loadout_plans_by_role())
    except Exception as exc:
        QMessageBox.warning(self, "一键装配", f"无法读取官方 SQLite 方案：{exc}")
        return
    if not role_names:
        QMessageBox.information(self, "一键装配", "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。")
        return
    ret = QMessageBox.question(
        self,
        "一键瞬间装配",
        f"将依次向本地组件发送 {len(role_names)} 个角色的装配指令，"
        "已经正确装配的角色会直接跳过，其余角色在稳定背包快照确认后再处理下一个。"
        "\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, role_names)


def _preview_assemble_role(self, role_name: str):
    """仅使用官方 SQLite 方案，经 nte-core 完成单角色装配。"""
    if not _uses_nte_core_equipment_apply(self):
        QMessageBox.warning(self, "装配方式", "当前配装页只支持官方 SQLite 方案。请在设置中启用 nte-core 装配。")
        return
    _preview_nte_core_assemble_role(self, role_name)


def _preview_assemble_all_roles(self):
    """仅使用官方 SQLite 方案，经 nte-core 完成批量装配。"""
    if not _uses_nte_core_equipment_apply(self):
        QMessageBox.warning(self, "装配方式", "当前配装页只支持官方 SQLite 方案。请在设置中启用 nte-core 装配。")
        return
    _preview_nte_core_assemble_all_roles(self)
