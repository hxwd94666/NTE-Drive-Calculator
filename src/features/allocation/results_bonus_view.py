# 渲染配装结果的属性加成汇总、差值比较和详情弹窗。
"""MainWindow methods for allocation."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import current_style_sheet, theme_color, themed_style
from src.features.allocation.bonus_summary import (
    BonusSummaryContext,
    add_stat_total,
    aligned_bonus_comparison_rows,
    bonus_rows_for_mode,
    bonus_summary_mode_label,
    bonus_uses_percent,
    canonical_stat_name,
    equipment_bonus_rows,
    extra_shape_area,
    fallback_tape_main_value,
    format_bonus_delta_value,
    format_bonus_value,
    has_bonus_delta,
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
from src.optimizer.contracts import (
    DIFF_CHANGED,
)
from src.features.allocation.results_diff_view import (
    _diff_saved_sources,
    _previous_loadout_from_diff,
)


__all__ = [
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



def _stat_w(self, sn, wts):
    stat_alias_mapping = getattr(self, "stats_config", {}).get("stat_alias_mapping", {})
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


def _stat_c(self, w):
    w = max(0.0, min(1.0, w))
    if w < 0.3:
        return theme_color("#8b949e")
    if w < 0.5:
        return "#58a6ff"
    if w < 0.7:
        return "#56d364"
    if w < 0.85:
        return "#d2991d"
    return "#f0883e"


def _weighted_score(self, sub_stats, wts):
    if not sub_stats:
        return 0
    total = 0.0
    for sn, sv in sub_stats.items():
        sw = self._stat_w(sn, wts)
        total += float(sv) * sw
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
    ctx = BonusSummaryContext.from_window(self)
    return fallback_tape_main_value(main_stat, quality, ctx.stats_config, ctx.stat_alias_mapping)


def _extra_shape_area(self, role_name):
    return extra_shape_area(role_name, self.roles_db)


def _equipment_bonus_rows(self, role_name, tape, drives):
    return equipment_bonus_rows(BonusSummaryContext.from_window(self), role_name, tape, drives)


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
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    btn_group = QButtonGroup(container)
    btn_group.setExclusive(True)
    toggle_style = themed_style(
        "QPushButton{background:#161b22;color:#8b949e;border:1px solid #30363d;border-radius:6px;"
        "font-size:10px;font-weight:700;padding:2px 6px;min-height:22px}"
        "QPushButton:checked{background:#1f6feb22;color:#58a6ff;border-color:#58a6ff}"
        "QPushButton:hover{border-color:#58a6ff;color:#c9d1d9}"
    )
    mode_defs = [("equipment", "空幕属性汇总"), ("character", "角色属性汇总")]
    for index, (mode, label) in enumerate(mode_defs):
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(toggle_style)
        btn_group.addButton(btn, index)
        layout.addWidget(btn)
        if mode == default_mode:
            btn.setChecked(True)

    def _on_mode_clicked(button_id):
        mode = mode_defs[button_id][0]
        on_change(mode)

    btn_group.idClicked.connect(_on_mode_clicked)
    layout.addStretch()
    return container


def _clear_layout_widgets(self, layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()


def _format_bonus_value(self, stat, value):
    return format_bonus_value(stat, value)


def _format_bonus_delta_value(stat, delta):
    return format_bonus_delta_value(stat, delta)


def _bonus_stat_weight(self, role_name, stat, mode="equipment"):
    weights = ((getattr(self, "roles_db", {}) or {}).get(role_name, {}) or {}).get("weights", {}) or {}
    if not weights:
        return 0.0
    panel_components = {
        "总攻击力": ("攻击力白值", "攻击力%", "攻击力"),
        "总生命值": ("生命白值", "生命值%", "生命值"),
        "总防御力": ("防御力白值", "防御力%", "防御力"),
    }
    candidates = panel_components.get(stat, (stat,)) if mode == "character" else (stat,)
    return max((self._stat_w(candidate, weights) for candidate in candidates), default=0.0)


def _sort_bonus_rows_for_role(self, role_name, rows, mode="equipment"):
    return sorted(
        rows or [],
        key=lambda item: (-self._bonus_stat_weight(role_name, item[0], mode), str(item[0])),
    )


def _sort_bonus_aligned_rows_for_role(self, role_name, aligned, mode="equipment"):
    return sorted(
        aligned or [],
        key=lambda item: (-self._bonus_stat_weight(role_name, item.get("stat", ""), mode), str(item.get("stat", ""))),
    )


def _bonus_stat_label_style(self, stat, role_name=None, mode="equipment", colored_stats=None):
    if not role_name or (mode == "character" and colored_stats is not None and stat not in colored_stats):
        color = theme_color("#c9d1d9")
    else:
        color = self._stat_c(self._bonus_stat_weight(role_name, stat, mode))
    return f"font-size:10px;font-weight:700;color:{color};border:none;background:transparent"


def _format_panel_value(self, stat, value):
    suffix = "%" if bonus_uses_percent(stat) else ""
    value = float(value or 0.0)
    number = f"{value:.0f}" if abs(value - round(value)) < 0.01 else f"{value:.2f}"
    return f"{number}{suffix}"


def _display_bonus_stat_label(stat):
    """Compact attribute-damage labels without changing their calculation keys."""
    label = str(stat or "")
    if "属性" in label and "伤害" in label:
        return f"{label.split('属性', 1)[0]}属性伤害"
    return label


def _role_stat_priority_stats(self, role_name):
    configs = getattr(self, "_pending_crit_priority_modes", None) or {}
    if not configs and hasattr(self, "role_selector"):
        try:
            configs = self.role_selector.get_crit_priority_modes()
        except Exception:
            configs = {}
    cfg = configs.get(role_name) or {}
    if not isinstance(cfg, dict):
        return []
    return [str(stat) for stat in cfg.get("stats", []) if stat]


def _sort_bonus_aligned_rows(self, aligned, priority_stats=None, prioritize_changed_only=False):
    return sort_bonus_aligned_rows(aligned, priority_stats, prioritize_changed_only)


def _has_bonus_delta(self, item):
    return has_bonus_delta(item)


def _aligned_bonus_comparison_rows(self, old_rows, new_rows, limit=None, changes_only=False, priority_stats=None):
    return aligned_bonus_comparison_rows(old_rows, new_rows, limit, changes_only, priority_stats)


def _bonus_comparison_column(
    self,
    title,
    aligned_rows,
    value_key,
    empty_text="暂无可汇总属性",
    priority_stats=None,
    role_name=None,
    mode="equipment",
    colored_stats=None,
):
    column = QFrame()
    column.setStyleSheet(
        themed_style("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}")
    )
    layout = QVBoxLayout(column)
    layout.setContentsMargins(7, 5, 7, 5)
    layout.setSpacing(4)
    header = QLabel(title)
    header.setStyleSheet(
        themed_style("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent")
    )
    layout.addWidget(header)
    if not aligned_rows:
        empty = QLabel(empty_text)
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        layout.addWidget(empty)
    else:
        for item in aligned_rows:
            value = item.get(value_key)
            if value is None:
                layout.addWidget(
                    self._bonus_row_widget(
                        item["stat"],
                        display_text="—",
                        priority_stats=priority_stats,
                        role_name=role_name,
                        mode=mode,
                        colored_stats=colored_stats,
                    )
                )
            else:
                layout.addWidget(
                    self._bonus_row_widget(
                        item["stat"],
                        value,
                        priority_stats=priority_stats,
                        role_name=role_name,
                        mode=mode,
                        colored_stats=colored_stats,
                    )
                )
    layout.addStretch()
    return column


def _bonus_more_button(on_click=None):
    more = QPushButton("•••")
    more.setObjectName("btnSm")
    more.setFixedSize(68, 28)
    more.setCursor(Qt.PointingHandCursor)
    more.setStyleSheet(
        themed_style(
            "QPushButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;font-size:13px;font-weight:800;padding:0}QPushButton:hover{border-color:#58a6ff;color:#58a6ff}"
        )
    )
    if on_click is not None:
        more.clicked.connect(on_click)
    return more


def _configure_bonus_more_button(button, on_click=None):
    previous_callback = getattr(button, "_bonus_more_callback", None)
    if previous_callback is not None:
        try:
            button.clicked.disconnect(previous_callback)
        except (RuntimeError, TypeError):
            pass
    button.setVisible(on_click is not None)
    if on_click is not None:
        button.clicked.connect(on_click)
    button._bonus_more_callback = on_click


def _bonus_delta_row_widget(
    self, stat, delta, old_val, new_val, priority_stats=None, role_name=None, mode="equipment", colored_stats=None
):
    if not self._has_bonus_delta({"stat": stat, "delta": delta, "old": old_val, "new": new_val}):
        row = QFrame()
        row.setFixedHeight(26)
        row.setStyleSheet(themed_style("QFrame{background:transparent;border:none;}"))
        return row
    color = theme_color("#56d364") if delta > 0 else theme_color("#f85149")
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
    column = QFrame()
    column.setStyleSheet(
        themed_style("QFrame{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:6px}")
    )
    layout = QVBoxLayout(column)
    layout.setContentsMargins(7, 5, 7, 5)
    layout.setSpacing(4)
    title = QLabel("变化")
    title.setStyleSheet(themed_style("font-size:11px;font-weight:800;color:#8b949e;border:none;background:transparent"))
    layout.addWidget(title)
    if not aligned_rows:
        empty = QLabel("无变化")
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        layout.addWidget(empty)
    else:
        for item in aligned_rows:
            layout.addWidget(
                self._bonus_delta_row_widget(
                    item["stat"],
                    item["delta"],
                    item.get("old"),
                    item.get("new"),
                    priority_stats=priority_stats,
                    role_name=role_name,
                    mode=mode,
                    colored_stats=colored_stats,
                )
            )
    layout.addStretch()
    return column


def _bonus_comparison_widget(
    self,
    role_name,
    old_rows,
    new_rows,
    has_old=True,
    compact=False,
    priority_stats=None,
    mode="equipment",
    old_title=None,
    new_title=None,
):
    priority_stats = list(priority_stats or [])
    if compact:
        aligned = self._aligned_bonus_comparison_rows(
            old_rows, new_rows, changes_only=True, priority_stats=priority_stats
        )
    else:
        aligned = self._aligned_bonus_comparison_rows(old_rows, new_rows, priority_stats=priority_stats)
    aligned = self._sort_bonus_aligned_rows_for_role(role_name, aligned, mode)
    if compact:
        aligned = aligned[:4]
    colored_stats = {item.get("stat") for item in aligned[:4]} if mode == "character" else None
    old_title = old_title or ("旧" if compact else "旧方案")
    new_title = new_title or ("新" if compact else "新方案")
    old_empty = "无已保存配装" if not has_old else ("暂无属性变化" if compact else "暂无可汇总属性")
    old_column = self._bonus_comparison_column(
        old_title,
        aligned,
        "old",
        old_empty,
        priority_stats=priority_stats,
        role_name=role_name,
        mode=mode,
        colored_stats=colored_stats,
    )
    new_column = self._bonus_comparison_column(
        new_title,
        aligned,
        "new",
        "暂无属性变化" if compact and not aligned else "暂无可汇总属性",
        priority_stats=priority_stats,
        role_name=role_name,
        mode=mode,
        colored_stats=colored_stats,
    )

    container = QFrame()
    container.setStyleSheet(themed_style("QFrame{background:transparent;border:none}"))
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(old_column, 1)
    layout.addWidget(new_column, 1)
    layout.addWidget(
        self._bonus_delta_column(
            aligned, priority_stats=priority_stats, role_name=role_name, mode=mode, colored_stats=colored_stats
        ),
        1,
    )
    return container


def _role_bonus_summary_panel(
    self, role_name, tape, drives, compare_with_saved=False, priority_stats=None, role_diff=None
):
    priority_stats = list(priority_stats if priority_stats is not None else self._role_stat_priority_stats(role_name))
    state = {"mode": "equipment"}
    box = QFrame()
    box.setMinimumWidth(560 if compare_with_saved else 300)
    box.setSizePolicy(QSizePolicy.Expanding if compare_with_saved else QSizePolicy.Maximum, QSizePolicy.Preferred)
    box.setStyleSheet(themed_style("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}"))
    layout = QVBoxLayout(box)
    layout.setContentsMargins(7, 5, 7, 5)
    layout.setSpacing(4)
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(4)
    mode_switch = self._make_bonus_mode_switch(
        state["mode"],
        lambda mode: self._refresh_bonus_summary_panel(
            box, role_name, tape, drives, compare_with_saved, priority_stats, mode, role_diff
        ),
    )
    mode_switch.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    header.addWidget(mode_switch, 0, Qt.AlignLeft)
    more_button = _bonus_more_button()
    more_button.setVisible(False)
    header.addWidget(more_button)
    header.addStretch()
    layout.addLayout(header)
    content_host = QWidget()
    content_layout = QVBoxLayout(content_host)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(4)
    layout.addWidget(content_host)
    box._bonus_summary_content_layout = content_layout
    box._bonus_summary_more_button = more_button
    box._bonus_summary_state = state
    self._refresh_bonus_summary_panel(
        box, role_name, tape, drives, compare_with_saved, priority_stats, state["mode"], role_diff
    )
    layout.addStretch()
    return box


def _refresh_bonus_summary_panel(
    self, box, role_name, tape, drives, compare_with_saved, priority_stats, mode, role_diff=None
):
    box._bonus_summary_state["mode"] = mode
    content_layout = box._bonus_summary_content_layout
    self._clear_layout_widgets(content_layout)
    _configure_bonus_more_button(box._bonus_summary_more_button)
    if compare_with_saved:
        # Saved SQLite plans carry their own immutable diff.
        persisted_diff = role_diff if isinstance(role_diff, dict) and role_diff.get(DIFF_CHANGED) else None
        effective_diff = persisted_diff or resolve_comparison_role_diff(self, role_name)
        if persisted_diff is not None:
            old_tape, old_drives = _previous_loadout_from_diff(self, role_name, tape, drives, effective_diff)
        else:
            saved_sources = _diff_saved_sources(self, role_name)
            old_tape, old_drives = split_loadout_sources(saved_sources)
        new_uids = loadout_uids(tape, drives)
        old_uids = loadout_uids(old_tape, old_drives)
        if effective_diff.get(DIFF_CHANGED) and ((not old_tape and not old_drives) or old_uids == new_uids):
            old_tape, old_drives = _previous_loadout_from_diff(self, role_name, tape, drives, effective_diff)
        if old_tape or old_drives:
            old_rows = self._bonus_rows_for_mode(role_name, old_tape, old_drives, mode)
            new_rows = self._bonus_rows_for_mode(role_name, tape, drives, mode)
            old_rows = self._sort_bonus_rows_for_role(role_name, old_rows, mode)
            new_rows = self._sort_bonus_rows_for_role(role_name, new_rows, mode)
            content_layout.addWidget(
                self._bonus_comparison_widget(
                    role_name, old_rows, new_rows, has_old=True, compact=True, priority_stats=priority_stats, mode=mode
                )
            )
            _configure_bonus_more_button(
                box._bonus_summary_more_button,
                lambda checked=False, role=role_name, old_r=old_rows, new_r=new_rows, stats=list(priority_stats), summary_mode=mode: (
                    self._show_bonus_comparison_dialog(role, old_r, new_r, stats, summary_mode)
                ),
            )
            return
    rows = self._bonus_rows_for_mode(role_name, tape, drives, mode)
    rows = self._sort_bonus_rows_for_role(role_name, rows, mode)
    visible = rows[:5]
    colored_stats = {stat for stat, _value in visible} if mode == "character" else None
    if not visible:
        empty = QLabel("暂无可汇总属性")
        empty.setStyleSheet(themed_style("color:#6e7681;border:none;background:transparent"))
        content_layout.addWidget(empty)
    for stat, value in visible:
        content_layout.addWidget(
            self._bonus_row_widget(
                stat, value, priority_stats=priority_stats, role_name=role_name, mode=mode, colored_stats=colored_stats
            )
        )
    if rows:
        _configure_bonus_more_button(
            box._bonus_summary_more_button,
            lambda checked=False, role=role_name, summary_rows=rows, summary_mode=mode: self._show_bonus_summary_dialog(
                role, summary_rows, summary_mode
            ),
        )


def _show_bonus_comparison_dialog(
    self,
    role_name,
    old_rows,
    new_rows,
    priority_stats=None,
    mode="equipment",
    old_title=None,
    new_title=None,
):
    priority_stats = list(priority_stats if priority_stats is not None else self._role_stat_priority_stats(role_name))
    dlg = QDialog(getattr(self, "dialog_parent", None))
    dlg.setWindowTitle(f"{role_name} {self._bonus_summary_mode_label(mode)}对比")
    dlg.setMinimumSize(680, 360)
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(8)
    layout.addWidget(
        self._bonus_comparison_widget(
            role_name,
            old_rows,
            new_rows,
            has_old=True,
            compact=False,
            priority_stats=priority_stats,
            mode=mode,
            old_title=old_title,
            new_title=new_title,
        )
    )
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()


def _show_bonus_summary_dialog(self, role_name, rows, mode="equipment"):
    dlg = QDialog(getattr(self, "dialog_parent", None))
    dlg.setWindowTitle(f"{role_name} {self._bonus_summary_mode_label(mode)}")
    dlg.setMinimumSize(360, 420)
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(8)
    rows = self._sort_bonus_rows_for_role(role_name, rows, mode)
    colored_stats = {stat for stat, _value in rows[:4]} if mode == "character" else None
    for stat, value in rows:
        layout.addWidget(
            self._bonus_row_widget(stat, value, role_name=role_name, mode=mode, colored_stats=colored_stats)
        )
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()


def _bonus_row_widget(
    self,
    stat,
    value=None,
    *,
    priority_stats=None,
    display_text=None,
    value_style=None,
    role_name=None,
    mode="equipment",
    colored_stats=None,
):
    row = QFrame()
    row.setFixedHeight(26)
    row.setMinimumWidth(130)
    row.setStyleSheet(
        themed_style("QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}")
    )
    rl = QHBoxLayout(row)
    rl.setContentsMargins(6, 1, 6, 1)
    rl.setSpacing(6)
    name = QLabel(_display_bonus_stat_label(stat))
    name.setWordWrap(True)
    name.setStyleSheet(self._bonus_stat_label_style(stat, role_name, mode, colored_stats))
    if display_text is not None:
        text = display_text
        style = value_style or themed_style(
            "font-size:10px;font-weight:700;color:#6e7681;border:none;background:transparent"
        )
    else:
        text = self._format_panel_value(stat, value) if mode == "character" else self._format_bonus_value(stat, value)
        style = value_style or themed_style(
            "font-size:10px;font-weight:800;color:#f0f6fc;border:none;background:transparent"
        )
    val = QLabel(text)
    val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    val.setStyleSheet(style)
    rl.addWidget(name, 1)
    rl.addWidget(val)
    return row
