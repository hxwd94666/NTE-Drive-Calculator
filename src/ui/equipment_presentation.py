# 统一渲染计算结果、保存配装、鉴定卡片、评分和属性汇总。
"""Public equipment presentation component shared by allocation-facing UIs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.services.equipment_scoring_service import (
    score_drive_stats,
    score_tape_stats,
)
from src.features.allocation.results_loadout_compare_view import EquipmentLoadoutComparisonPresentationMixin


from src.features.allocation.results_diff_view import (
    _calc_grade,
    _plan_diff_text,
    _diff_item_score_info,
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


from src.ui.equipment_result_rendering import _render_results, _representative_drive_pixmap


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


def _score_tape_dict(
    self,
    main_stats,
    sub_stats,
    weights,
    quality="Gold",
    main_weights=None,
    main_value=None,
):
    if not self.scoring_engine:
        return 0.0
    return score_tape_stats(
        self.scoring_engine,
        main_stat_name=main_stats,
        sub_stat_names=sub_stats.keys(),
        weights=weights,
        quality=quality,
        main_weights=main_weights if isinstance(main_weights, dict) else None,
        main_value=main_value,
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
    main_value=None,
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
        main_text = str(main_stat)
        if main_value is not None:
            main_text = f"{main_text} {_format_equipment_stat_display(main_value)}{'%' if '%' in main_text else ''}"
        ms_block = QLabel(main_text)
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


class EquipmentPresentation(EquipmentLoadoutComparisonPresentationMixin):
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
