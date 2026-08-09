# 把已保存配装方案投影为角色分组、拼图和装备卡片。
"""MainWindow methods for inventory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.domain.allocation_rating import allocation_grade
from src.optimizer.contracts import (
    DIFF_CHANGED,
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
from src.features.inventory.equipment_display_context import (
    equipment_presentation,
)
from src.ui.puzzle_board import PuzzleBoardWidget


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_refresh_equip",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
]

EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3


def _saved_plan_contains_virtual_equipment(
    role_data: Mapping[str, Any],
) -> bool:
    tape = role_data.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, dict) and bool(tape.get("virtual")):
        return True
    return any(
        isinstance(drive, dict) and bool(drive.get("virtual"))
        for drive in role_data.get(ROLE_EQUIPPED_DRIVES, ()) or ()
    )


def _saved_plan_requires_score_recalculation(
    role_data: Mapping[str, Any],
) -> bool:
    return (
        not bool(role_data.get("_sqlite_assignment_scores_complete"))
        or _saved_plan_contains_virtual_equipment(role_data)
    )


def _allocation_lock_icon(locked: bool) -> QIcon:
    """Draw a compact Material-style lock without depending on an icon font."""

    color = QColor(theme_color("#58a6ff" if locked else "#8b949e"))
    canvas = QPixmap(24, 24)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    body = QRectF(5.0, 10.0, 14.0, 10.5)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(body, 2.3, 2.3)

    shackle = QPainterPath()
    shackle.moveTo(7.5, 10.0)
    shackle.lineTo(7.5, 8.0)
    shackle.cubicTo(7.5, 3.1, 16.5, 3.1, 16.5, 8.0)
    shackle.lineTo(16.5, 10.0 if locked else 8.8)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(color, 2.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(shackle)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(theme_color("#161b22")))
    painter.drawEllipse(QRectF(10.25, 13.1, 3.5, 3.5))
    painter.drawRoundedRect(QRectF(11.25, 15.4, 1.5, 2.9), 0.75, 0.75)
    painter.end()
    return QIcon(canvas)


def _set_allocation_lock_button_state(button: QPushButton, locked: bool) -> None:
    """Reflect the persisted lock state without rebuilding the whole card."""

    button.setText("")
    button.setIcon(_allocation_lock_icon(locked))
    button.setIconSize(QSize(18, 18))
    button.setAccessibleName("解除配装锁定" if locked else "锁定配装")
    button.setToolTip(
        "当前方案已锁定：其装备不会进入其他角色的计算或替换候选"
        if locked
        else "当前方案未锁定：点击后保留本方案及其装备"
    )
    button.setStyleSheet(
        themed_style(
            "QPushButton{background:#21262d;border:1px solid #30363d;border-radius:5px;"
            "padding:0;min-width:32px;min-height:32px}"
            "QPushButton:hover{background:#30363d;border-color:#58a6ff}"
        )
    )

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力",
    "AtkUp": "攻击力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%",
    "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力",
    "DefUp": "防御力%",
    "HealUp": "治疗加成",
    "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2",
    "hen3": "H_3",
    "hen4": "H_4",
    "shu2": "V_2",
    "shu3": "V_3",
    "shu4": "V_4",
    "z3": "Trap_4_H",
    "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL",
    "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


def _render_equip_batch(self, token, batch_size=None):
    """Compatibility path for non-Qt callers; production uses viewport slots."""
    if token is not getattr(self, "_equip_render_token", None):
        return
    queue = getattr(self, "_equip_render_queue", [])
    index = getattr(self, "_equip_render_index", 0)
    size = batch_size or EQUIPMENT_RENDER_BATCH_SIZE
    end = min(index + size, len(queue))
    for role_name, rd in queue[index:end]:
        _render_equip_role(self, role_name, rd)
    self._equip_render_index = end
    if end < len(queue):
        QTimer.singleShot(0, lambda: _render_equip_batch(self, token))
    elif not getattr(self, "_equip_render_stretch_added", False):
        self.equip_content_layout.addStretch()
        self._equip_render_stretch_added = True


def _render_equip_role(self, role_name, rd, *, target_layout=None):
    presentation = equipment_presentation(self)
    role_cfg = self.roles_db.get(role_name, {})
    wts = role_cfg.get("weights", {})
    main_wts = role_cfg.get("main_weights")
    is_sqlite_plan = "_sqlite_plan_id" in rd

    total_score = 0.0
    tape_data = rd.get(ROLE_EQUIPPED_TAPE)
    if is_sqlite_plan and not _saved_plan_requires_score_recalculation(rd):
        total_score = float(rd.get(ROLE_TOTAL_SCORE, 0.0) or 0.0)
        total_grade = allocation_grade(total_score, ALLOCATION_TOTAL_SCORE_AREA)
    elif not is_sqlite_plan and ROLE_TOTAL_SCORE in rd and rd.get(ROLE_TOTAL_GRADE):
        total_score = float(rd.get(ROLE_TOTAL_SCORE, 0.0) or 0.0)
        total_grade = str(rd.get(ROLE_TOTAL_GRADE) or "D")
    else:
        if tape_data:
            t_q = tape_data.get(EQUIP_QUALITY, "Gold")
            t_s = presentation.score_tape(
                tape_data.get(EQUIP_MAIN_STATS, ""), tape_data.get(EQUIP_SUB_STATS, {}), wts, t_q, main_wts
            )
            total_score += t_s
        for d in rd.get(ROLE_EQUIPPED_DRIVES, []):
            d_q = d.get(EQUIP_QUALITY, "Gold")
            d_s = presentation.score_drive(
                d.get(EQUIP_SUB_STATS, {}),
                d.get(EQUIP_SHAPE_ID, ""),
                wts,
                d_q,
            )
            total_score += d_s
        total_grade = allocation_grade(total_score, ALLOCATION_TOTAL_SCORE_AREA)
    gc = GRADE_COLORS.get(total_grade, "#58a6ff")
    gbg = theme_rgba(gc, 0.10)

    grp = QGroupBox("")
    grp.setStyleSheet(
        themed_style(
            "QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"
        )
    )
    gl = QVBoxLayout(grp)
    gl.setSpacing(10)
    role_hdr = QHBoxLayout()
    role_hdr.setSpacing(8)
    rnl = QLabel(role_name)
    rnl.setStyleSheet(
        f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:4px 14px;background:{theme_rgba('#4dd0e1', 0.10)}"
    )
    role_hdr.addWidget(rnl)
    last_diff = rd.get(ROLE_LAST_DIFF, {}) or {}
    if last_diff.get(DIFF_CHANGED):
        diff_btn = QPushButton("变动")
        diff_btn.setFixedSize(76, 32)
        diff_btn.setStyleSheet(
            themed_style(
                "QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px;min-height:32px}QPushButton:hover{background:#388bfd}"
            )
        )
        diff_btn.clicked.connect(lambda _=False, rn=role_name, d=last_diff: self._show_saved_plan_diff_dialog(rn, d))
        role_hdr.addWidget(diff_btn)
    _sm = rd.get("strategy_mode", "")
    if _sm:
        _ml = {"role_priority": "角色优先", "global_optimal": "全局最优", "update_mode": "增量更新"}.get(_sm, _sm)
        sml = QLabel(_ml)
        sml.setStyleSheet(
            themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px")
        )
        role_hdr.addWidget(sml)
    role_hdr.addStretch()
    # Score
    sf = QFrame()
    sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    slb = QHBoxLayout(sf)
    slb.setSpacing(6)
    slb.setContentsMargins(4, 0, 4, 0)
    sv = QLabel(f"{total_score:.1f}")
    sv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
    slb.addWidget(QLabel("评分"))
    slb.addWidget(sv)
    role_hdr.addWidget(sf)
    # Grade
    gf = QFrame()
    gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    glb = QHBoxLayout(gf)
    glb.setSpacing(6)
    glb.setContentsMargins(4, 0, 4, 0)
    gv = QLabel(total_grade)
    gv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
    glb.addWidget(QLabel("评级"))
    glb.addWidget(gv)
    role_hdr.addWidget(gf)
    del_btn = QPushButton("删除")
    del_btn.setObjectName("btnDanger")
    del_btn.setFixedSize(64, 32)
    del_btn.clicked.connect(lambda _=False, rn=role_name: self._delete_role_equipment(rn))
    role_hdr.addWidget(del_btn)
    import_btn = QPushButton("装配")
    import_btn.setObjectName("btnPrimary")
    import_btn.clicked.connect(lambda _, rn=role_name: self._preview_assemble_role(rn))
    role_hdr.addWidget(import_btn)
    locked = bool(rd.get("_allocation_locked"))
    lock_btn = QPushButton()
    lock_btn.setFixedSize(32, 32)
    _set_allocation_lock_button_state(lock_btn, locked)

    def toggle_lock(_checked=False):
        updated = self._toggle_role_allocation_lock(role_name)
        if isinstance(updated, bool):
            _set_allocation_lock_button_state(lock_btn, updated)

    lock_btn.clicked.connect(
        toggle_lock
    )
    role_hdr.addWidget(lock_btn)
    gl.addLayout(role_hdr)
    gl.addSpacing(6)

    bp = rd.get(ROLE_BLUEPRINT_LAYOUT, [])
    drives = rd.get(ROLE_EQUIPPED_DRIVES, [])
    if bp:
        gl.addWidget(presentation.section_label("拼图图纸:"))
        compare_with_saved = bool(last_diff.get(DIFF_CHANGED))
        bp_row = QHBoxLayout()
        bp_row.setSpacing(18)
        bp_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        bp_row.addWidget(PuzzleBoardWidget(bp), 0, Qt.AlignTop)
        bp_row.addWidget(
            presentation.role_bonus_summary_panel(
                role_name,
                tape_data,
                drives,
                compare_with_saved=compare_with_saved,
                priority_stats=presentation.role_stat_priority_stats(role_name),
                role_diff=last_diff,
            ),
            1 if compare_with_saved else 0,
            Qt.AlignTop,
        )
        gl.addLayout(bp_row)
    if tape_data:
        t_q = tape_data.get(EQUIP_QUALITY, "Gold")
        if EQUIP_SCORE in tape_data and tape_data.get(EQUIP_GRADE):
            t_s = float(tape_data.get(EQUIP_SCORE, 0.0) or 0.0)
            t_g = str(tape_data.get(EQUIP_GRADE) or "D")
        else:
            t_s = presentation.score_tape(
                tape_data.get(EQUIP_MAIN_STATS, ""), tape_data.get(EQUIP_SUB_STATS, {}), wts, t_q, main_wts
            )
            t_g = allocation_grade(t_s, 15)
        gl.addWidget(presentation.section_label("卡带:"))
        tape_changed = bool(tape_data.get(EQUIP_IS_CHANGED))
        tape_uid = tape_data.get(EQUIP_UID, "")
        gl.addWidget(
            presentation.equipment_card(
                tape_data.get(EQUIP_SET_NAME, ""),
                tape_data.get(EQUIP_MAIN_STATS, ""),
                tape_data.get(EQUIP_SUB_STATS, {}),
                None,
                tape_uid,
                wts,
                (t_s, t_g),
                t_q,
                is_new=bool(tape_data.get(EQUIP_IS_NEW)) and not tape_changed,
                is_changed=tape_changed,
                is_discarded=bool(tape_data.get("discarded")),
                main_weights=main_wts,
                replacement_callback=lambda rn=role_name, item_uid=tape_uid: self._optimize_saved_equipment(
                    rn, "tape", item_uid
                ),
                card_variant="inventory",
                item_icon_path=tape_data.get("item_icon_path"),
            )
        )
    if drives:
        gl.addWidget(presentation.section_label(f"驱动 ({len(drives)}个):"))
        for d in drives:
            d_q = d.get(EQUIP_QUALITY, "Gold")
            if EQUIP_SCORE in d and d.get(EQUIP_GRADE):
                d_s = float(d.get(EQUIP_SCORE, 0.0) or 0.0)
                d_g = str(d.get(EQUIP_GRADE) or "D")
            else:
                d_s = presentation.score_drive(
                    d.get(EQUIP_SUB_STATS, {}),
                    d.get(EQUIP_SHAPE_ID, ""),
                    wts,
                    d_q,
                )
                d_g = allocation_grade(
                    d_s,
                    presentation.shape_area(
                        d.get(EQUIP_SHAPE_ID, ""),
                        3,
                    ),
                )
            drive_changed = bool(d.get(EQUIP_IS_CHANGED))
            drive_uid = d.get(EQUIP_UID, "")
            gl.addWidget(
                presentation.equipment_card(
                    d.get(EQUIP_SHAPE_ID, ""),
                    "",
                    d.get(EQUIP_SUB_STATS, {}),
                    d.get(EQUIP_SHAPE_ID, ""),
                    drive_uid,
                    wts,
                    (d_s, d_g),
                    d_q,
                    is_new=bool(d.get(EQUIP_IS_NEW)) and not drive_changed,
                    is_changed=drive_changed,
                    is_discarded=bool(d.get("discarded")),
                    is_duplicate_drive=bool(d.get("is_duplicate_drive")),
                    replacement_callback=lambda rn=role_name, item_uid=drive_uid: self._optimize_saved_equipment(
                        rn, "drive", item_uid
                    ),
                    card_variant="inventory",
                    item_icon_path=d.get("item_icon_path"),
                )
            )
    (target_layout or self.equip_content_layout).addWidget(grp)
    return grp
