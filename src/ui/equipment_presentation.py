# 统一渲染计算结果、保存配装、鉴定卡片、评分和属性汇总。
"""Public equipment presentation component shared by allocation-facing UIs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.optimizer.contracts import (
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    PLAN_ASSIGNED_TAPE,
    PLAN_BLUEPRINT,
    PLAN_CHANGED_UIDS,
    PLAN_SCORE,
    PLAN_VALID,
    plan_drives,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.equipment_scoring_service import (
    score_drive_stats,
    score_tape_stats,
)
from src.services.warehouse_visual_catalog import (
    representative_module_item_id,
)
from src.ui.puzzle_board import PuzzleBoardWidget


__all__ = [
    "EquipmentPresentation",
    "_section_label",
    "_render_results",
    "_calc_grade",
    "_show_plan_diff_dialog",
    "_build_plan_diff_dialog",
    "_diff_item_card",
    "_diff_item_score_info",
    "_plan_diff_text",
    "_sync_role_drive_replacement",
    "_sync_role_tape_replacement",
    "_stat_w",
    "_stat_c",
    "_weighted_score",
    "_quality_coef",
    "_canonical_stat_name",
    "_stat_number_value",
    "_item_value",
    "_add_stat_total",
    "_fallback_tape_main_value",
    "_extra_shape_area",
    "_equipment_bonus_rows",
    "_role_base_bonus_rows",
    "_merge_bonus_row_lists",
    "_synthesize_character_bonus_rows",
    "_bonus_rows_for_mode",
    "_bonus_summary_mode_label",
    "_make_bonus_mode_switch",
    "_clear_layout_widgets",
    "_format_bonus_value",
    "_role_stat_priority_stats",
    "_bonus_stat_weight",
    "_sort_bonus_rows_for_role",
    "_sort_bonus_aligned_rows_for_role",
    "_bonus_stat_label_style",
    "_format_panel_value",
    "_sort_bonus_aligned_rows",
    "_role_bonus_summary_panel",
    "_refresh_bonus_summary_panel",
    "_aligned_bonus_comparison_rows",
    "_has_bonus_delta",
    "_bonus_row_widget",
    "_bonus_comparison_column",
    "_bonus_delta_row_widget",
    "_bonus_delta_column",
    "_bonus_comparison_widget",
    "_show_bonus_summary_dialog",
    "_show_bonus_comparison_dialog",
    "_score_drive_dict",
    "_score_tape_dict",
    "_equip_card",
]


def _section_label(self, text):
    label = QLabel(text)
    label.setStyleSheet(
        themed_style("font-size:14px;font-weight:700;color:#c9d1d9;border:none;background:transparent;padding:2px 0")
    )
    return label


@lru_cache(maxsize=256)
def _game_ui_equipment_icon(
    asset_root: str,
    kind: str,
    item_id: str,
) -> str | None:
    """Resolve a packaged official equipment image once per item ID."""
    if not item_id:
        return None
    icon_path = GameUiAssetCatalog(Path(asset_root)).inventory_item_icon(kind, item_id)
    return str(icon_path) if icon_path is not None else None


def _equipment_item_icon_path(
    item,
    kind: str,
    asset_root: str | Path,
) -> str | None:
    """Read a projected official item ID without requiring a legacy model change."""
    item_id = str(_diff_value(item, "item_id", "") or "")
    if not item_id:
        official = _diff_value(item, "official", {})
        if isinstance(official, dict):
            item_id = str(official.get("item_id") or "")
    return _game_ui_equipment_icon(str(asset_root), kind, item_id)


def _representative_drive_pixmap(
    asset_root: str | Path,
    shape_id: str,
    quality: str,
) -> QPixmap:
    quality_text = str(quality or "").casefold()
    if any(
        token in quality_text
        for token in ("gold", "golden", "orange", "金", "橙")
    ):
        quality_key = "gold"
    elif "purple" in quality_text or "紫" in quality_text:
        quality_key = "purple"
    elif "blue" in quality_text or "蓝" in quality_text:
        quality_key = "blue"
    elif "green" in quality_text or "绿" in quality_text:
        quality_key = "green"
    else:
        quality_key = quality_text or "gold"
    item_id = representative_module_item_id(str(shape_id or ""), quality_key)
    icon_path = _game_ui_equipment_icon(
        str(asset_root),
        "module",
        item_id,
    )
    return QPixmap(icon_path) if icon_path else QPixmap()


def _render_results(self, plan):
    locked_roles = tuple(sorted(getattr(self, "_locked_role_names", ()) or ()))
    if not plan and not locked_roles:
        return
    self.result_card.setVisible(True)
    while self.result_content_layout.count():
        it = self.result_content_layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
    mode_labels = {"role_priority": "角色优先", "global_optimal": "全局最优", "update_mode": "增量更新"}
    mode_name = mode_labels.get(getattr(self, "_pending_strat", ""), "")
    plan_diffs = getattr(self, "allocation_plan_diff", {}) or {}
    if locked_roles:
        locked_label = QLabel(
            "已保留锁定方案："
            + "、".join(locked_roles)
            + "。其真实装备已在评分、词条筛选和求解前排除，本次不会覆盖这些角色。"
        )
        locked_label.setWordWrap(True)
        locked_label.setStyleSheet(
            themed_style(
                "color:#ffcc66;border:1px solid #8b6b25;border-radius:7px;"
                "background:rgba(255,204,102,0.08);padding:8px 10px"
            )
        )
        self.result_content_layout.addWidget(locked_label)
    if not plan:
        return
    for role, p in plan.items():
        if not p or not p.get(PLAN_VALID):
            reason = str((p or {}).get("reason") or "无法凑齐图纸所需的卡带或驱动")
            failure = QLabel(f"❌ {role}: 无有效配装方案\n原因：{reason}")
            failure.setWordWrap(True)
            failure.setStyleSheet(themed_style("color:#f85149;padding:8px 2px"))
            self.result_content_layout.addWidget(failure)
            continue
        role_diff = plan_diffs.get(role, {}) or {}
        added_uids = set(role_diff.get(DIFF_ADDED_UIDS, set()) or set())
        changed_uids = set(p.get(PLAN_CHANGED_UIDS, set()) or set()) if isinstance(p, dict) else set()
        total_score = p.get(PLAN_SCORE, 0)
        total_grade = self._calc_grade(total_score, ALLOCATION_TOTAL_SCORE_AREA)
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
        # Role header: name + score + grade side by side, compact
        role_hdr = QHBoxLayout()
        role_hdr.setSpacing(8)
        # Role name with different color from stat blocks - use teal/cyan tone
        rnl = QLabel(role)
        rnl.setStyleSheet(
            f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:4px 14px;background:{theme_rgba('#4dd0e1', 0.10)}"
        )
        role_hdr.addWidget(rnl)
        if role_diff.get(DIFF_CHANGED):
            diff_btn = QPushButton("变动")
            diff_btn.setFixedSize(76, 32)
            diff_btn.setStyleSheet(
                themed_style(
                    "QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px;min-height:32px}QPushButton:hover{background:#388bfd}"
                )
            )
            diff_btn.clicked.connect(lambda _checked=False, rn=role, d=role_diff: self._show_plan_diff_dialog(rn, d))
            role_hdr.addWidget(diff_btn)
        if mode_name:
            ml = QLabel(mode_name)
            ml.setStyleSheet(
                themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px")
            )
            role_hdr.addWidget(ml)
        role_hdr.addStretch()
        # Score badge (separate)
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
        # Grade badge (separate)
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
        gl.addLayout(role_hdr)
        gl.addSpacing(6)

        board = p.get(PLAN_BLUEPRINT, {}).get("board", [])
        role_cfg = self.roles_db.get(role, {})
        wts = role_cfg.get("weights", {})
        main_wts = role_cfg.get("main_weights")

        tape = p.get(PLAN_ASSIGNED_TAPE)
        drives = plan_drives(p)
        if board:
            gl.addWidget(self._section_label("拼图图纸:"))
            bp_row = QHBoxLayout()
            bp_row.setSpacing(18)
            bp_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            bp_row.addWidget(PuzzleBoardWidget(board), 0, Qt.AlignTop)
            compare_with_saved = bool(role_diff.get(DIFF_CHANGED))
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
            gl.addLayout(bp_row)
            gl.addSpacing(8)

        if tape:
            t_score = tape.role_scores.get(role, 0) if hasattr(tape, "role_scores") else 0
            t_grade = self._calc_grade(t_score, 15)
            tape_uid = str(_diff_value(tape, "uid", "") or "")
            tape_changed = bool(_diff_value(tape, "is_changed", False) or tape_uid in changed_uids)
            gl.addWidget(self._section_label("卡带:"))
            gl.addWidget(
                self._equip_card(
                    tape.set_name,
                    tape.main_stats,
                    tape.sub_stats,
                    None,
                    tape.uid,
                    wts,
                    (t_score, t_grade),
                    tape.quality,
                    is_new=(tape_uid in added_uids and not tape_changed),
                    is_changed=tape_changed,
                    is_discarded=bool(getattr(tape, "discarded", False)),
                    main_weights=main_wts,
                    card_variant="result",
                    item_icon_path=_equipment_item_icon_path(
                        tape, "core", Path(self.app_context.paths.asset_dir) / "game_ui"
                    ),
                )
            )

        if drives:
            gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
            for d in drives:
                score = d.role_scores.get(role, 0) if hasattr(d, "role_scores") else 0
                grade = self._calc_grade(score, d.area)
                mvp_tag = f" 👑第{d.pick_order}顺位" if getattr(d, "is_mvp", False) else ""
                drive_uid = str(_diff_value(d, "uid", "") or "")
                drive_changed = bool(_diff_value(d, "is_changed", False) or drive_uid in changed_uids)
                gl.addWidget(
                    self._equip_card(
                        d.shape_id,
                        "",
                        d.sub_stats,
                        d.shape_id,
                        d.uid + mvp_tag,
                        wts,
                        (score, grade),
                        d.quality,
                        is_new=(drive_uid in added_uids and not drive_changed),
                        is_changed=drive_changed,
                        is_discarded=bool(getattr(d, "discarded", False)),
                        is_duplicate_drive=bool(getattr(d, "is_duplicate_drive", False)),
                        card_variant="result",
                    )
                )
        self.result_content_layout.addWidget(grp)
    self.result_content_layout.addStretch()


from src.features.allocation.results_diff_view import (
    _calc_grade,
    _plan_diff_text,
    _diff_item_score_info,
    _diff_value,
    _diff_item_card,
    _build_plan_diff_dialog,
    _show_plan_diff_dialog,
    _sync_role_drive_replacement,
    _sync_role_tape_replacement,
)


from src.features.allocation.results_bonus_view import (
    _stat_w,
    _stat_c,
    _weighted_score,
    _quality_coef,
    _canonical_stat_name,
    _stat_number_value,
    _item_value,
    _add_stat_total,
    _fallback_tape_main_value,
    _extra_shape_area,
    _equipment_bonus_rows,
    _role_base_bonus_rows,
    _merge_bonus_row_lists,
    _synthesize_character_bonus_rows,
    _bonus_rows_for_mode,
    _bonus_summary_mode_label,
    _make_bonus_mode_switch,
    _clear_layout_widgets,
    _format_bonus_value,
    _bonus_stat_weight,
    _sort_bonus_rows_for_role,
    _sort_bonus_aligned_rows_for_role,
    _bonus_stat_label_style,
    _format_panel_value,
    _role_stat_priority_stats,
    _sort_bonus_aligned_rows,
    _has_bonus_delta,
    _aligned_bonus_comparison_rows,
    _bonus_comparison_column,
    _bonus_delta_row_widget,
    _bonus_delta_column,
    _bonus_comparison_widget,
    _role_bonus_summary_panel,
    _refresh_bonus_summary_panel,
    _show_bonus_comparison_dialog,
    _show_bonus_summary_dialog,
    _bonus_row_widget,
)


def _score_drive_dict(self, sub_stats, shape_id, weights, quality="Gold"):
    if not self.scoring_engine:
        return 0.0
    return score_drive_stats(
        self.scoring_engine,
        sub_stat_names=sub_stats.keys(),
        area=self._shape_areas.get(shape_id, 3),
        weights=weights,
        quality=quality,
    )


def _score_tape_dict(self, main_stats, sub_stats, weights, quality="Gold", main_weights=None):
    if not self.scoring_engine:
        return 0.0
    return score_tape_stats(
        self.scoring_engine,
        main_stat_name=main_stats,
        sub_stat_names=sub_stats.keys(),
        weights=weights,
        quality=quality,
        main_weights=main_weights if isinstance(main_weights, dict) else None,
    )


def _format_equipment_stat_display(value):
    """Render captured float32/double values without binary-tail noise."""
    if isinstance(value, str) and "%" in value:
        return value
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _equip_card(
    self,
    label,
    main_stat,
    sub_stats,
    shape_id,
    uid,
    weights,
    score_info=None,
    quality=None,
    is_new=False,
    is_changed=False,
    is_discarded=False,
    is_duplicate_drive=False,
    main_weights=None,
    replacement_callback=None,
    card_variant="default",
    item_icon_path=None,
    replacement_text=None,
):
    # A manual replacement changes an existing slot; it is not a newly acquired
    # item.  Keep the status mutually exclusive even if an older caller supplies
    # both flags.
    is_new = bool(is_new) and not bool(is_changed)
    w = QWidget()
    w.setObjectName("equipmentCard")
    w.setStyleSheet(
        themed_style(
            "QWidget#equipmentCard{background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:9px 13px;margin:3px 0}"
        )
    )
    outer = QHBoxLayout(w)
    outer.setSpacing(12)
    outer.setContentsMargins(14, 2, 2, 2)

    # Shape image: 与首行标签保持均衡，避免图标显得过小。
    if shape_id or item_icon_path:
        # Use a compact frame in both specialised views.  The image label
        # explicitly has no padding below, so the artwork fills the frame
        # instead of becoming a small icon inside a large blank box.
        image_size = {"inventory": 52, "result": 60}.get(card_variant, 64)
        pm = (
            QPixmap(str(item_icon_path)).scaled(
                image_size,
                image_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            if item_icon_path
            else _representative_drive_pixmap(
                Path(self.app_context.paths.asset_dir) / "game_ui",
                shape_id,
                quality or "Gold",
            )
        )
        if not pm.isNull():
            img_lbl = QLabel()
            img_lbl.setPixmap(pm)
            img_lbl.setFixedSize(image_size, image_size)
            img_lbl.setScaledContents(True)
            img_lbl.setStyleSheet(
                themed_style("border:1px solid #30363d;border-radius:6px;background:#161b22;padding:0px")
            )
            outer.addWidget(img_lbl)

    row_spacing = {"result": 4, "inventory": 5}.get(card_variant, 5)
    inner = QVBoxLayout()
    inner.setSpacing(row_spacing)
    inner.setContentsMargins(0, 3, 0, 3)

    # Header: shape name + quality + main stat block + score|grade
    hdr = QHBoxLayout()
    hdr.setSpacing(8)
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
    name_lbl.setStyleSheet(
        f"font-size:{name_size}px;font-weight:800;color:{label_color};border:1px solid {label_border};border-radius:6px;padding:{name_pad};background:{label_bg}"
    )
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
        status_labels.append(
            _status_label("NEW", theme_color("#58a6ff"), theme_color("#58a6ff"), theme_rgba("#58a6ff", 0.10))
        )
    if is_changed:
        status_labels.append(
            _status_label("CHANGE", theme_color("#7ee787"), theme_color("#2ea043"), theme_rgba("#238636", 0.10))
        )
    if is_discarded:
        status_labels.append(_status_label("弃置", "#ff7b72", "#ff7b72", "rgba(218,54,51,0.16)"))
    if is_duplicate_drive:
        status_labels.append(_status_label("重复", "#ffb86c", "#ff9d3d", "rgba(255,152,0,0.16)"))
    if status_labels and shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label, 0, Qt.AlignTop)
    # Main stat as colored block (same style as sub stats)
    if main_stat:
        main_weight_source = main_weights if isinstance(main_weights, dict) else weights
        mw = self._stat_w(main_stat, main_weight_source)
        mc = self._stat_c(mw)
        qc = QColor(mc)
        ms_block = QLabel(main_stat)
        ms_block.setStyleSheet(
            f"border:1px solid {mc};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);"
            f"border-radius:6px;padding:{'5px 12px' if is_feature_card else '4px 12px'};font-size:{header_font_size or 13}px;color:{mc};font-weight:700"
        )
        hdr.addWidget(ms_block, 0, Qt.AlignTop)
    if status_labels and not shape_id:
        for status_label in status_labels:
            hdr.addWidget(status_label, 0, Qt.AlignTop)
    hdr.addStretch()

    # A loadout card represents one item, so its score and grade remain a
    # compact combined badge.  The replacement dialog owns its separate
    # score/grade/direct-damage metrics below.
    score_frame = None
    if score_info is not None:
        score, grade = score_info
        gc = GRADE_COLORS.get(grade, "#58a6ff")
        score_pad = "4px 10px" if is_feature_card else "2px 10px"
        score_margin = 0 if is_feature_card else 1
        score_frame = QFrame()
        score_frame.setStyleSheet(
            f"QFrame{{background:{theme_rgba(gc, 0.10)};border:1px solid {gc};border-radius:6px;padding:{score_pad}}}"
        )
        sf_layout = QHBoxLayout(score_frame)
        sf_layout.setSpacing(5)
        sf_layout.setContentsMargins(4, score_margin, 4, score_margin)
        score_font_size = header_font_size or 13
        sl = QLabel(f"{score:.1f}")
        sl.setStyleSheet(f"font-size:{score_font_size}px;font-weight:800;color:{gc};border:none")
        sf_layout.addWidget(sl)
        gl = QLabel(grade)
        gl.setStyleSheet(f"font-size:{score_font_size}px;font-weight:800;color:{gc};border:none")
        sf_layout.addWidget(gl)
        if is_feature_card:
            score_frame.setFixedHeight(name_lbl.sizeHint().height())
    if score_frame is not None:
        hdr.addWidget(score_frame, 0, Qt.AlignTop)
    if replacement_callback:
        replacement_btn = QPushButton(str(replacement_text or ("优化" if shape_id else "替换")))
        replacement_btn.setObjectName("btnAction")
        if is_feature_card:
            replacement_btn.setFixedSize(74, 33)
            replacement_btn.setStyleSheet(themed_style(f"font-size:{header_font_size}px;padding:2px 8px"))
        else:
            replacement_btn.setFixedSize(60, 28)
        replacement_btn.clicked.connect(lambda _checked=False: replacement_callback())
        hdr.addWidget(replacement_btn, 0, Qt.AlignTop)
    inner.addLayout(hdr)

    # Stat blocks row
    if sub_stats:
        br = QHBoxLayout()
        br.setSpacing(5)
        for sn, sv in sub_stats.items():
            sw = self._stat_w(sn, weights)
            color = self._stat_c(sw)
            qc = QColor(color)
            block = QLabel(f"{sn} <b>{_format_equipment_stat_display(sv)}</b>")
            block.setAlignment(Qt.AlignCenter)
            block.setStyleSheet(
                f"border:1px solid {color};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);border-radius:6px;padding:5px 12px;font-size:{'13px' if is_feature_card else '12px'};color:{color};font-weight:600"
            )
            block.setToolTip(f"权重: {sw:.2f}")
            br.addWidget(block)
        br.addStretch()
        inner.addLayout(br)
    if card_variant == "result":
        inner.addStretch(1)
    outer.addLayout(inner, 1)
    return w


class EquipmentPresentation:
    """Own shared equipment rendering and calculation-result presentation state."""

    _section_label = _section_label
    _render_results = _render_results
    _calc_grade = _calc_grade
    _show_plan_diff_dialog = _show_plan_diff_dialog
    _build_plan_diff_dialog = _build_plan_diff_dialog
    _diff_item_card = _diff_item_card
    _diff_item_score_info = _diff_item_score_info
    _plan_diff_text = _plan_diff_text
    _sync_role_drive_replacement = _sync_role_drive_replacement
    _sync_role_tape_replacement = _sync_role_tape_replacement
    _stat_w = _stat_w
    _stat_c = _stat_c
    _weighted_score = _weighted_score
    _quality_coef = _quality_coef
    _canonical_stat_name = _canonical_stat_name
    _stat_number_value = _stat_number_value
    _item_value = _item_value
    _add_stat_total = _add_stat_total
    _fallback_tape_main_value = _fallback_tape_main_value
    _extra_shape_area = _extra_shape_area
    _equipment_bonus_rows = _equipment_bonus_rows
    _role_base_bonus_rows = _role_base_bonus_rows
    _merge_bonus_row_lists = _merge_bonus_row_lists
    _synthesize_character_bonus_rows = _synthesize_character_bonus_rows
    _bonus_rows_for_mode = _bonus_rows_for_mode
    _bonus_summary_mode_label = _bonus_summary_mode_label
    _make_bonus_mode_switch = _make_bonus_mode_switch
    _clear_layout_widgets = _clear_layout_widgets
    _format_bonus_value = _format_bonus_value
    _role_stat_priority_stats = _role_stat_priority_stats
    _bonus_stat_weight = _bonus_stat_weight
    _sort_bonus_rows_for_role = _sort_bonus_rows_for_role
    _sort_bonus_aligned_rows_for_role = _sort_bonus_aligned_rows_for_role
    _bonus_stat_label_style = _bonus_stat_label_style
    _format_panel_value = _format_panel_value
    _sort_bonus_aligned_rows = _sort_bonus_aligned_rows
    _role_bonus_summary_panel = _role_bonus_summary_panel
    _refresh_bonus_summary_panel = _refresh_bonus_summary_panel
    _aligned_bonus_comparison_rows = _aligned_bonus_comparison_rows
    _has_bonus_delta = _has_bonus_delta
    _bonus_row_widget = _bonus_row_widget
    _bonus_comparison_column = _bonus_comparison_column
    _bonus_delta_row_widget = _bonus_delta_row_widget
    _bonus_delta_column = _bonus_delta_column
    _bonus_comparison_widget = _bonus_comparison_widget
    _show_bonus_summary_dialog = _show_bonus_summary_dialog
    _show_bonus_comparison_dialog = _show_bonus_comparison_dialog
    _score_drive_dict = _score_drive_dict
    _score_tape_dict = _score_tape_dict
    _equip_card = _equip_card

    def __init__(self, *, app_context, dialog_parent) -> None:
        self.app_context = app_context
        self.dialog_parent = dialog_parent
        self.result_card = None
        self.result_content_layout = None
        self.role_selector = None
        self.roles_db: dict = {}
        self.scoring_engine = None
        self._shape_areas: dict = {}
        self.final_plan: dict = {}
        self.allocation_plan_diff: dict = {}
        self._pending_allocation_snapshot_id: int | None = None
        self._pending_strat = ""
        self._allocation_custom_weapons: dict = {}
        self._locked_role_names: frozenset[str] = frozenset()

    def bind_widgets(
        self,
        *,
        result_card,
        result_content_layout,
        role_selector,
    ) -> None:
        self.result_card = result_card
        self.result_content_layout = result_content_layout
        self.role_selector = role_selector

    def update_catalog(
        self,
        *,
        roles_db: dict,
        scoring_engine,
        shape_areas: dict,
    ) -> None:
        self.roles_db = roles_db
        self.scoring_engine = scoring_engine
        self._shape_areas = shape_areas

    def set_plan_context(
        self,
        *,
        final_plan: dict,
        plan_diff: dict,
        snapshot_id: int | None,
        strategy: str,
        custom_weapons: dict,
        locked_role_names: object = (),
    ) -> None:
        self.final_plan = final_plan
        self.allocation_plan_diff = plan_diff
        self._pending_allocation_snapshot_id = snapshot_id
        self._pending_strat = strategy
        self._allocation_custom_weapons = custom_weapons
        self._locked_role_names = frozenset(
            str(role) for role in locked_role_names if str(role).strip()
        )

    def render(self, plan: dict) -> None:
        if self.result_card is None or self.result_content_layout is None:
            raise RuntimeError("allocation result widgets have not been bound")
        self._render_results(plan)

    def equipment_card(self, *args, **kwargs):
        return self._equip_card(*args, **kwargs)

    def section_label(self, text: str):
        return self._section_label(text)

    def role_bonus_summary_panel(self, *args, **kwargs):
        return self._role_bonus_summary_panel(*args, **kwargs)

    def role_stat_priority_stats(self, role_name: str):
        return self._role_stat_priority_stats(role_name)

    def score_drive(self, *args, **kwargs) -> float:
        return float(self._score_drive_dict(*args, **kwargs))

    def score_tape(self, *args, **kwargs) -> float:
        return float(self._score_tape_dict(*args, **kwargs))

    def shape_area(self, shape_id: str, default: int = 3) -> int:
        return int(self._shape_areas.get(shape_id, default))

    def clear(self) -> None:
        self.final_plan = {}
        self.allocation_plan_diff = {}
        self._locked_role_names = frozenset()
        if self.result_card is not None:
            self.result_card.setVisible(False)
