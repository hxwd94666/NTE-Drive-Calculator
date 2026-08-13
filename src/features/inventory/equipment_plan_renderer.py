# 把已保存配装方案投影为角色分组、拼图和装备卡片。
"""MainWindow methods for inventory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QIcon,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QVBoxLayout,
)

from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import (
    GRADE_COLORS,
    current_theme_name,
    theme_color,
    theme_rgba,
    themed_style,
)
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
from src.ui.equipment_state_icons import warehouse_lock_icon


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


class _ResponsivePairWidget(QWidget):
    """Place two detail panels side-by-side, then stack them when narrow."""

    def __init__(
        self,
        first: QWidget,
        second: QWidget,
        *,
        breakpoint: int = 920,
        second_stretch: int = 1,
    ) -> None:
        super().__init__()
        self._breakpoint = breakpoint
        self._layout = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(18)
        self._layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._layout.addWidget(first, 0, Qt.AlignLeft | Qt.AlignTop)
        self._layout.addWidget(second, second_stretch, Qt.AlignTop)

    def resizeEvent(self, event: QResizeEvent) -> None:
        direction = (
            QBoxLayout.TopToBottom
            if event.size().width() < self._breakpoint
            else QBoxLayout.LeftToRight
        )
        if self._layout.direction() != direction:
            self._layout.setDirection(direction)
            self._layout.invalidate()
            self._layout.activate()
            self._sync_minimum_height()
            QTimer.singleShot(0, self._sync_minimum_height)
            self.updateGeometry()
        super().resizeEvent(event)

    def _sync_minimum_height(self) -> None:
        self.setMinimumHeight(
            self._layout.sizeHint().height()
            if self._layout.direction() == QBoxLayout.TopToBottom
            else 0
        )
        self.updateGeometry()


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
    """Use the same yellow/gray lock artwork as warehouse equipment cards."""

    return warehouse_lock_icon(locked, size=20)


def _set_allocation_lock_button_state(button: QPushButton, locked: bool) -> None:
    """Reflect the persisted lock state without rebuilding the whole card."""

    button.setText("")
    button.setIcon(_allocation_lock_icon(locked))
    button.setIconSize(QSize(20, 20))
    button.setAccessibleName("解除配装锁定" if locked else "锁定配装")
    button.setToolTip(
        "当前方案已锁定：其装备不会进入其他角色的计算或替换候选"
        if locked
        else "当前方案未锁定：点击后保留本方案及其装备"
    )
    light_locked = locked and current_theme_name() == "light"
    background = "#f2cc60" if light_locked else "#3a2f13" if locked else "#21262d"
    border = "#9a6700" if light_locked else "#e3b341" if locked else "#30363d"
    hover = "#ffdf85" if light_locked else "#4a3a16" if locked else "#30363d"
    hover_border = "#825e00" if light_locked else "#f2cc60" if locked else "#58a6ff"
    button.setStyleSheet(themed_style(
        f"QPushButton{{background:{background};border:1px solid {border};"
        "border-radius:5px;padding:0;min-width:32px;min-height:32px}"
        f"QPushButton:hover{{background:{hover};border-color:{hover_border}}}"
    ))

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
    is_game_mode = bool(rd.get("_game_mode"))

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
    header_height = 34

    grp = QGroupBox("")
    grp.setStyleSheet(
        themed_style(
            "QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"
        )
    )
    gl = QVBoxLayout(grp)
    gl.setSpacing(10)
    role_header = QWidget()
    role_header.setObjectName("equipmentRoleHeader")
    role_hdr = QHBoxLayout(role_header)
    role_hdr.setContentsMargins(0, 0, 0, 0)
    role_hdr.setSpacing(8)
    rnl = QLabel(role_name)
    rnl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    rnl.setFixedHeight(header_height)
    rnl.setStyleSheet(
        f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:0 14px;background:{theme_rgba('#4dd0e1', 0.10)}"
    )
    role_hdr.addWidget(rnl)
    last_diff = rd.get(ROLE_LAST_DIFF, {}) or {}
    if last_diff.get(DIFF_CHANGED):
        diff_btn = QPushButton("变动")
        diff_btn.setFixedSize(76, header_height)
        diff_btn.setStyleSheet(
            themed_style(
                "QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px}QPushButton:hover{background:#388bfd}"
            )
        )
        diff_btn.clicked.connect(lambda _=False, rn=role_name, d=last_diff: self._show_saved_plan_diff_dialog(rn, d))
        role_hdr.addWidget(diff_btn)
    _sm = rd.get("strategy_mode", "")
    if _sm:
        _ml = {
            "role_priority": "角色优先",
            "global_optimal": "全局最优",
            "update_mode": "增量更新",
            "game_inventory": "游戏配装",
        }.get(_sm, _sm)
        sml = QLabel(_ml)
        sml.setStyleSheet(
            themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px")
        )
        role_hdr.addWidget(sml)
    role_hdr.addStretch()
    graduation_frame = QFrame()
    graduation_frame.setObjectName("equipmentRoleGraduation")
    graduation_frame.setFixedHeight(header_height)
    graduation_frame.setStyleSheet(
        f"QFrame{{background:{gbg};border:1px solid {gc};"
        "border-radius:7px;padding:0 10px}"
    )
    graduation_layout = QHBoxLayout(graduation_frame)
    graduation_layout.setSpacing(6)
    graduation_layout.setContentsMargins(4, 0, 4, 0)
    graduation_label = QLabel("毕业率")
    graduation_label.setObjectName("equipmentGraduationLabel")
    graduation_layout.addWidget(graduation_label)
    graduation_value = QLabel("--")
    graduation_value.setObjectName("equipmentGraduationValue")
    graduation_value.setStyleSheet(
        f"font-size:14px;font-weight:800;color:{gc};border:none"
    )
    graduation_layout.addWidget(graduation_value)
    role_hdr.addWidget(graduation_frame)
    request_graduation = getattr(
        self,
        "_request_equipment_graduation_rate",
        None,
    )
    if callable(request_graduation):
        request_graduation(
            role_name,
            rd,
            graduation_value,
            (graduation_label, graduation_frame),
        )

    total_frame = QFrame()
    total_frame.setObjectName("equipmentRoleTotal")
    total_frame.setFixedHeight(header_height)
    total_frame.setStyleSheet(
        f"QFrame{{background:{gbg};border:1px solid {gc};"
        "border-radius:7px;padding:0 10px}"
    )
    total_layout = QHBoxLayout(total_frame)
    total_layout.setSpacing(7)
    total_layout.setContentsMargins(4, 0, 4, 0)
    total_layout.addWidget(QLabel("总评"))
    score_value = QLabel(f"{total_score:.1f}")
    score_value.setObjectName("equipmentTotalScoreValue")
    score_value.setStyleSheet(
        f"font-size:14px;font-weight:800;color:{gc};border:none"
    )
    total_layout.addWidget(score_value)
    grade_value = QLabel(total_grade)
    grade_value.setObjectName("equipmentTotalGradeValue")
    grade_value.setStyleSheet(
        f"font-size:14px;font-weight:800;color:{gc};border:none"
    )
    total_layout.addWidget(grade_value)
    role_hdr.addWidget(total_frame)
    if is_game_mode:
        import_btn = QPushButton("已导入" if rd.get("_game_imported") else "导入")
        import_btn.setObjectName("btnPrimary")
        import_btn.setFixedHeight(header_height)
        import_enabled = bool(rd.get("_game_importable")) and not bool(
            rd.get("_game_imported") or rd.get("_game_existing_plan_locked")
        )
        import_btn.setEnabled(import_enabled)
        if rd.get("_game_existing_plan_locked"):
            import_btn.setToolTip("现有计算器方案已锁定，请先解除锁定")
        elif not rd.get("_game_importable"):
            import_btn.setToolTip(str(rd.get("_game_reason") or "当前方案不完整"))
        import_btn.clicked.connect(
            lambda _=False, rn=role_name: self._import_game_loadout(rn)
        )
        role_hdr.addWidget(import_btn)
    else:
        del_btn = QPushButton("删除")
        del_btn.setObjectName("btnDanger")
        del_btn.setFixedSize(64, header_height)
        del_btn.clicked.connect(lambda _=False, rn=role_name: self._delete_role_equipment(rn))
        role_hdr.addWidget(del_btn)
        import_btn = QPushButton("装配")
        import_btn.setObjectName("btnPrimary")
        import_btn.setFixedHeight(header_height)
        import_btn.clicked.connect(lambda _, rn=role_name: self._preview_assemble_role(rn))
        role_hdr.addWidget(import_btn)
        locked = bool(rd.get("_allocation_locked"))
        lock_btn = QPushButton()
        lock_btn.setFixedSize(header_height, header_height)
        _set_allocation_lock_button_state(lock_btn, locked)

        def toggle_lock(_checked=False):
            updated = self._toggle_role_allocation_lock(role_name)
            if isinstance(updated, bool):
                _set_allocation_lock_button_state(lock_btn, updated)

        lock_btn.clicked.connect(toggle_lock)
        role_hdr.addWidget(lock_btn)
    gl.addWidget(role_header)
    if is_game_mode and rd.get("_game_reason"):
        status_label = QLabel(str(rd["_game_reason"]))
        status_label.setWordWrap(True)
        status_label.setStyleSheet(themed_style("color:#d29922;font-size:12px;padding:2px 4px"))
        gl.addWidget(status_label)
    gl.addSpacing(6)

    bp = rd.get(ROLE_BLUEPRINT_LAYOUT, [])
    drives = rd.get(ROLE_EQUIPPED_DRIVES, [])
    if bp:
        compare_with_saved = bool(last_diff.get(DIFF_CHANGED))
        saved_state = rd.get("_game_saved_state")
        if is_game_mode and isinstance(saved_state, dict):
            bonus_panel = presentation.role_loadout_comparison_panel(
                role_name,
                tape_data,
                drives,
                saved_state.get(ROLE_EQUIPPED_TAPE),
                saved_state.get(ROLE_EQUIPPED_DRIVES, []),
                priority_stats=presentation.role_stat_priority_stats(role_name),
            )
            bonus_stretch = 1
        else:
            bonus_panel = presentation.role_bonus_summary_panel(
                role_name,
                tape_data,
                drives,
                compare_with_saved=compare_with_saved,
                priority_stats=presentation.role_stat_priority_stats(role_name),
                role_diff=last_diff,
            )
            bonus_stretch = 1 if compare_with_saved else 0
        bp_panel = _ResponsivePairWidget(
            PuzzleBoardWidget(bp),
            bonus_panel,
            second_stretch=bonus_stretch,
        )
        gl.addWidget(bp_panel)
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
                replacement_callback=(
                    None
                    if is_game_mode
                    else lambda rn=role_name, item_uid=tape_uid: self._optimize_saved_equipment(
                        rn, "tape", item_uid
                    )
                ),
                card_variant="inventory",
                item_icon_path=tape_data.get("item_icon_path"),
                main_value=tape_data.get("main_value"),
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
                    replacement_callback=(
                        None
                        if is_game_mode
                        else lambda rn=role_name, item_uid=drive_uid: self._optimize_saved_equipment(
                            rn, "drive", item_uid
                        )
                    ),
                    card_variant="inventory",
                    item_icon_path=d.get("item_icon_path"),
                )
            )
    (target_layout or self.equip_content_layout).addWidget(grp)
    return grp
