# 管理角色优先级选择和偏好存档。
"""Role priority selector and per-role equipment preference dialog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from src.ui.widgets import SearchableComboBox, match_pinyin
from src.app.theme import current_theme_name, themed_style
from src.domain.crit_threshold import DEFAULT_CRIT_THRESHOLD, persistable_stat_priority_config
from src.domain.grade_limits import GRADE_LADDER
from src.features.allocation.priority_groups import (
    cycle_priority_link,
    links_to_priority_groups,
    load_priority_selection,
    normalize_priority_links,
)
from src.solver.set_effects import FOUR_PIECE, NO_EFFECT, SET_EFFECT_MODES, TWO_PIECE, normalize_set_effect_mode


def resolve_priority_choice(values: list[str], raw_text: str | None, current_data=None) -> str:
    """Resolve a searchable combo selection without confusing prefix-like stats."""

    if current_data is not None and str(current_data) in values:
        return str(current_data)
    raw = str(raw_text or "").strip()
    if raw in values:
        return raw
    return next((value for value in values if match_pinyin(value, raw)), raw)


def temporary_priority_config_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.temp{path.suffix}")


def normalize_weapons_db(weapons_db) -> dict:
    if not isinstance(weapons_db, dict):
        return {}
    normalized = {}
    for key, info in weapons_db.items():
        if isinstance(info, dict):
            name = str(info.get("name") or key or "").strip()
            if name:
                normalized[name] = info
    return normalized


class PriorityRoleButton(QPushButton):
    """Role chip button that can be clicked to remove or dragged to reorder."""

    def __init__(self, selector: "RoleSelector", role: str, index: int):
        super().__init__(role)
        self.selector = selector
        self.role = role
        self.index = index
        self._drag_start_pos = None
        self.setAcceptDrops(True)
        self.setCursor(Qt.OpenHandCursor)
        self.clicked.connect(lambda _checked=False: selector._toggle(role))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < 8:
            super().mouseMoveEvent(event)
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self.index))
        drag.setMimeData(mime)
        source_widget = self.parentWidget() or self
        drag.setPixmap(self._make_drag_pixmap(source_widget))
        drag.setHotSpot(self.mapTo(source_widget, event.position().toPoint()))
        drag.exec(Qt.MoveAction)

    def _make_drag_pixmap(self, source_widget):
        raw = source_widget.grab()
        if raw.isNull():
            return raw
        pixmap = QPixmap(raw.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(0.72)
        painter.drawPixmap(0, 0, raw)
        painter.end()
        return pixmap

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            source_index = int(event.mimeData().text())
        except ValueError:
            return
        self.selector._drop_selected_on(source_index, self.index)
        event.acceptProposedAction()


class RoleSelector(QWidget):
    """Select role priority and manage per-role set/stat filters."""

    orderChanged = Signal()

    def __init__(
        self,
        parent=None,
        priority_config_path_provider: Callable[[], Path] | None = None,
        style_sheet: str = "",
        help_callback: Callable | None = None,
    ):
        super().__init__(parent)
        self._priority_config_path_provider = priority_config_path_provider
        self._style_sheet = style_sheet
        self._help_callback = help_callback
        self.all_roles: dict = {}
        self.all_sets: list[str] = []
        self.weapons_db: dict = {}
        self.tape_main_stats: list[str] = []
        self.drive_sub_stats: list[str] = []
        self.selected: list[str] = []
        self.priority_links: list[str] = []
        self.custom_sets: dict[str, str] = {}
        self.custom_weapons: dict[str, str] = {}
        self.crit_rate_caps: dict[str, float] = {}
        self.tape_main_filters: dict[str, list[str]] = {}
        self.stat_priority_configs: dict[str, dict] = {}
        self.set_effect_modes: dict[str, str] = {}
        self._cards: dict = {}
        self._build()

    def _priority_config_path(self) -> Path:
        if self._priority_config_path_provider:
            return Path(self._priority_config_path_provider())
        return Path("config") / "priority_config.json"

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索角色（支持拼音）...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)

        primary_reset_btn = QPushButton("重置")
        primary_reset_btn.setObjectName("btnDanger")
        primary_reset_btn.clicked.connect(self.reset_selection)
        search_row.addWidget(primary_reset_btn)

        primary_restore_btn = QPushButton("恢复")
        primary_restore_btn.setObjectName("btnAction")
        primary_restore_btn.clicked.connect(self.restore_temporary_priority_config)
        search_row.addWidget(primary_restore_btn)

        primary_save_btn = QPushButton("保存")
        primary_save_btn.setObjectName("btnAction")
        primary_save_btn.clicked.connect(lambda _checked=False: self.save_priority_config())
        search_row.addWidget(primary_save_btn)

        primary_load_btn = QPushButton("读取")
        primary_load_btn.setObjectName("btnAction")
        primary_load_btn.clicked.connect(self.load_priority_config)
        search_row.addWidget(primary_load_btn)

        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(lambda: self._show_help("优先级存档说明", PRIORITY_SAVE_HELP))
        search_row.addWidget(help_btn)
        layout.addLayout(search_row)

        tip = QLabel("点击选择角色，选中顺序即优先级；重置只影响当前界面，恢复会读取已保存配置。")
        tip.setStyleSheet(themed_style("color:#8b949e;font-size:11px;border:none"))
        layout.addWidget(tip)

        self.roles_scroll = QScrollArea()
        self.roles_scroll.setWidgetResizable(True)
        self.roles_scroll.setMinimumHeight(260)
        self.roles_w = QWidget()
        self.roles_layout = QVBoxLayout(self.roles_w)
        self.roles_layout.setContentsMargins(0, 0, 0, 0)
        self.roles_layout.setSpacing(14)

        self.priority_w = QWidget()
        self.priority_layout = QGridLayout(self.priority_w)
        self.priority_layout.setContentsMargins(0, 6, 0, 6)
        self.priority_layout.setHorizontalSpacing(8)
        self.priority_layout.setVerticalSpacing(8)
        self.priority_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.roles_layout.addWidget(self.priority_w)

        self.grid_w = QWidget()
        self.grid_layout = QGridLayout(self.grid_w)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(6)
        self.grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.roles_layout.addWidget(self.grid_w)
        self.roles_layout.addStretch(1)
        self.roles_scroll.setWidget(self.roles_w)
        layout.addWidget(self.roles_scroll, 1)

    _CARD_SEL = "QFrame{background:#1f6feb22;border:2px solid #58a6ff;border-radius:8px}QFrame:hover{border-color:#79c0ff}"
    _CARD_OFF = "QFrame{background:#161b22;border:1px solid #21262d;border-radius:8px}QFrame:hover{border-color:#30363d}"

    def load_roles(self, roles_db, all_sets, tape_main_stats=None, drive_sub_stats=None, weapons_db=None):
        self.all_roles = roles_db
        self.all_sets = all_sets
        self.weapons_db = normalize_weapons_db(weapons_db)
        self.tape_main_stats = list(tape_main_stats or [])
        self.drive_sub_stats = list(drive_sub_stats or [])
        self._render_grid(self.search.text() if hasattr(self, "search") else "")

    def _render_grid(self, filter_text=""):
        self._render_priority_row()
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        names = self._available_role_names(filter_text)
        col = row = 0
        for name in names:
            self.grid_layout.addWidget(self._make_card(name), row, col)
            col += 1
            if col >= 8:
                col = 0
                row += 1

    def _available_role_names(self, filter_text=""):
        query = str(filter_text or "").strip()
        names = [name for name in sorted(self.all_roles.keys()) if name not in self.selected]
        if query:
            names = [name for name in names if match_pinyin(name, query)]
        return names

    def _priority_role_frame_width(self, name):
        return self._priority_role_name_width() + 48 + 6 + 5 + 6

    def _priority_role_name_width(self):
        return max(54, self.fontMetrics().horizontalAdvance("MMMM") + 18)

    def _priority_role_name_font_size(self, name):
        available = self._priority_role_name_width() - 18
        text_width = max(1, self.fontMetrics().horizontalAdvance(str(name)))
        if text_width <= available:
            return 12
        return max(9, min(12, int(12 * available / text_width)))

    def _render_priority_row(self):
        if not hasattr(self, "priority_layout"):
            return
        while self.priority_layout.count():
            item = self.priority_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.priority_links = normalize_priority_links(self.selected, self.priority_links)
        query = self.search.text().strip() if hasattr(self, "search") else ""
        visible_indexes = [
            index for index, name in enumerate(self.selected)
            if not query or match_pinyin(name, query)
        ]
        if not visible_indexes:
            empty = QLabel("未选择角色")
            empty.setStyleSheet(themed_style("color:#8b949e;border:none;font-size:12px"))
            self.priority_layout.addWidget(empty, 0, 0)
            return
        for visible_pos, index in enumerate(visible_indexes):
            name = self.selected[index]
            unit = QWidget()
            unit_layout = QHBoxLayout(unit)
            unit_layout.setContentsMargins(0, 0, 0, 0)
            unit_layout.setSpacing(5)

            item = QFrame()
            item.setFixedSize(self._priority_role_frame_width(name), 40)
            item.setStyleSheet(
                themed_style(
                    "QFrame{background:#161b22;border:1px solid #30363d;border-radius:7px}"
                    "QFrame:hover{border-color:#58a6ff;background:#1f6feb22}"
                )
            )
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(6, 5, 6, 5)
            item_layout.setSpacing(5)

            name_btn = PriorityRoleButton(self, name, index)
            name_btn.setObjectName("btnSm")
            name_btn.setToolTip("点击移出当前优先级；拖动可调整顺序")
            name_btn.setFixedWidth(self._priority_role_name_width())
            name_size = self._priority_role_name_font_size(name)
            name_btn.setStyleSheet(
                themed_style(
                    "QPushButton{background:transparent;color:#c9d1d9;border:none;"
                    f"padding:3px 5px;font-size:{name_size}px;font-weight:700;text-align:left}}"
                    "QPushButton:hover{color:#fff}"
                )
            )
            item_layout.addWidget(name_btn)

            manage_btn = QPushButton("管理")
            manage_btn.setObjectName("btnSm")
            manage_btn.setFixedSize(48, 28)
            manage_btn.setStyleSheet(
                "QPushButton{background:#238636;color:#fff;border:1px solid #2ea043;"
                "border-radius:5px;padding:3px 7px;font-size:11px;font-weight:700}"
                "QPushButton:hover{background:#2ea043}"
            )
            manage_btn.clicked.connect(lambda _checked=False, role=name: self._manage_role_preferences(role))
            item_layout.addWidget(manage_btn)
            unit_layout.addWidget(item)

            if index < len(self.selected) - 1 and (not query or index + 1 in visible_indexes):
                link_text = self.priority_links[index]
                link_btn = QPushButton(link_text)
                link_btn.setFixedWidth(42)
                link_btn.setObjectName("btnAction")
                if link_text == ">>":
                    if current_theme_name() == "light":
                        link_btn.setStyleSheet(
                            "QPushButton{color:#cf222e;border:1px solid #cf222e;"
                            "background:#ffffff;border-radius:6px;font-weight:700}"
                            "QPushButton:hover{background:#fff5f5;border-color:#cf222e}"
                        )
                    else:
                        link_btn.setStyleSheet(
                            "QPushButton{color:#ff7b72;border:1px solid #f85149;"
                            "background:#2d1117;border-radius:6px;font-weight:700}"
                            "QPushButton:hover{background:#3c151c;border-color:#ff7b72}"
                        )
                elif current_theme_name() == "light":
                    link_btn.setStyleSheet(
                        "QPushButton{color:#0969da;border:1px solid #0969da;"
                        "background:#ffffff;border-radius:6px;font-weight:700}"
                        "QPushButton:hover{background:#f6f8fa;border-color:#0969da}"
                    )
                link_btn.setToolTip(">：严格优先；>>：批次边界；=：同批次平级。点击循环切换。")
                link_btn.clicked.connect(lambda _checked=False, pos=index: self._cycle_priority_link(pos))
                unit_layout.addWidget(link_btn)
            unit.setFixedSize(unit.sizeHint())
            self.priority_layout.addWidget(unit, visible_pos // 5, visible_pos % 5)

    def _make_card(self, name):
        selected = name in self.selected
        card = QFrame()
        card.setFixedSize(96, 34)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(themed_style(self._CARD_SEL if selected else self._CARD_OFF))

        layout = QHBoxLayout(card)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(0)

        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet(themed_style("font-size:12px;font-weight:700;border:none;background:transparent;color:#c9d1d9"))
        layout.addWidget(name_label, 1)

        card.mousePressEvent = lambda event, role=name: self._toggle(role)
        self._cards[name] = {"card": card}
        return card

    def _filter(self, text):
        self._render_grid(text)

    def _set_custom_set(self, name, text):
        set_name = str(text or "").strip()
        default_set = str((self.all_roles.get(name, {}) or {}).get("default_set", "") or "").strip()
        if set_name and set_name != default_set:
            self.custom_sets[name] = set_name
        else:
            self.custom_sets.pop(name, None)
        self.orderChanged.emit()

    def _set_custom_weapon(self, name, text):
        weapon = str(text or "").strip()
        if weapon:
            self.custom_weapons[name] = weapon
            cap = self._weapon_crit_rate_cap(weapon)
            if cap is not None:
                self.crit_rate_caps[name] = cap
        else:
            self.custom_weapons.pop(name, None)
        self.orderChanged.emit()

    def _set_crit_rate_cap(self, name, value):
        try:
            cap = float(value)
        except (TypeError, ValueError):
            self.crit_rate_caps.pop(name, None)
            self.orderChanged.emit()
            return
        if cap < 0:
            self.crit_rate_caps.pop(name, None)
        else:
            self.crit_rate_caps[name] = round(min(cap, 100.0), 4)
        self.orderChanged.emit()

    def _weapon_crit_rate_cap(self, weapon_name):
        info = self.weapons_db.get(weapon_name)
        if not isinstance(info, dict):
            return None
        stats = {}
        level_stats = info.get("level_sub_stats")
        if isinstance(level_stats, dict) and level_stats:
            stats = level_stats.get("80") or level_stats.get(80) or next(iter(level_stats.values()), {})
        if not isinstance(stats, dict) or not stats:
            stats = info.get("sub_stats", {}) if isinstance(info.get("sub_stats", {}), dict) else {}
        for key, value in stats.items():
            normalized = str(key or "").replace("%", "")
            if "暴击率" in normalized or "鏆村嚮鐜" in normalized:
                try:
                    return round(max(0.0, 100.0 - float(value)), 4)
                except (TypeError, ValueError):
                    return None
        return None

    def _set_tape_main_filter(self, name, values):
        if values:
            self.tape_main_filters[name] = values
        else:
            self.tape_main_filters.pop(name, None)
        self.orderChanged.emit()

    def _set_stat_priority_config(
        self,
        name,
        stats,
        equal_priority=False,
        ignore_grade_limit=False,
        min_grade_limit="A",
        crit_threshold=DEFAULT_CRIT_THRESHOLD,
    ):
        cfg = persistable_stat_priority_config(
            {
                "stats": stats or [],
                "equal_priority": equal_priority,
                "ignore_grade_limit": ignore_grade_limit,
                "min_grade_limit": min_grade_limit,
                "crit_threshold": crit_threshold,
            },
            allowed_stats=set(self.drive_sub_stats),
            dedupe_stats=True,
        )
        if cfg:
            self.stat_priority_configs[name] = cfg
        else:
            self.stat_priority_configs.pop(name, None)
        self.orderChanged.emit()

    def _set_set_effect_mode(self, name, mode):
        normalized = normalize_set_effect_mode(mode)
        if normalized == FOUR_PIECE:
            self.set_effect_modes.pop(name, None)
        else:
            self.set_effect_modes[name] = normalized
        self.orderChanged.emit()

    def _show_help(self, title, text):
        if self._help_callback:
            self._help_callback(self, title, text)
        else:
            QMessageBox.information(self, title, text)

    def _fill_search_combo(self, combo: SearchableComboBox, values: list[str], current: str | None = None):
        for value in values:
            combo.addItem(value, value)
        combo.refresh_search_items()
        if current and current in values:
            combo.setCurrentText(current)
        else:
            combo.setCurrentIndex(-1)
            combo.setEditText("")

    def _make_selected_summary_label(self):
        label = QLabel()
        label.setWordWrap(True)
        label.setMinimumHeight(32)
        label.setMinimumWidth(150)
        if current_theme_name() == "light":
            label.setStyleSheet(
                "color:#24292f;font-size:13px;border:1px solid #d0d7de;border-radius:6px;"
                "background:#f6f8fa;padding:4px 7px"
            )
        else:
            label.setStyleSheet(
                "color:#7ee787;font-size:13px;border:1px solid #238636;border-radius:6px;"
                "background:#0f3d2e;padding:4px 7px"
            )
        return label

    def _refresh_selected_summary_label(self, label: QLabel, selected: list[str], separator: str):
        text = "Default" if not selected else separator.join(selected)
        label.setText(text)
        label.setToolTip(text)

    def _build_multi_select_row(self, title: str, choices: list[str], selected: list[str], separator: str):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        row = QHBoxLayout()
        row.setSpacing(6)
        title_label = QLabel(title)
        title_label.setMinimumWidth(118)
        title_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(title_label)
        combo = SearchableComboBox()
        self._fill_search_combo(combo, choices)
        row.addWidget(combo, 1)

        add_btn = QPushButton("添加")
        add_btn.setObjectName("btnAction")
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("btnDanger")
        row.addWidget(add_btn)
        row.addWidget(clear_btn)

        summary = self._make_selected_summary_label()
        layout.addLayout(row)
        layout.addWidget(summary)

        def refresh_summary():
            self._refresh_selected_summary_label(summary, selected, separator)

        def add_choice():
            value = combo.currentText().strip()
            resolved = resolve_priority_choice(choices, value, combo.currentData())
            if resolved in choices and resolved not in selected:
                selected.append(resolved)
                refresh_summary()
            combo.setCurrentIndex(-1)
            combo.setEditText("")

        add_btn.clicked.connect(add_choice)
        clear_btn.clicked.connect(lambda: (selected.clear(), refresh_summary()))
        refresh_summary()
        return box

    def _manage_role_preferences(self, name):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{name} · 管理")
        dlg.setMinimumSize(560, 320)
        if self._style_sheet:
            dlg.setStyleSheet(self._style_sheet)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        role_data = self.all_roles.get(name, {})
        current_set = self.custom_sets.get(name) or role_data.get("default_set", self.all_sets[0] if self.all_sets else "")
        set_box = QGroupBox("角色配置")
        set_layout = QVBoxLayout(set_box)
        set_layout.setContentsMargins(8, 8, 8, 8)
        set_layout.setSpacing(5)
        set_row = QHBoxLayout()
        set_row.setSpacing(8)
        set_row.addWidget(QLabel("套装："))
        set_combo = SearchableComboBox()
        self._fill_search_combo(set_combo, self.all_sets, current_set)
        set_row.addWidget(set_combo, 1)
        set_layout.addLayout(set_row)
        weapon_row = QHBoxLayout()
        weapon_row.setSpacing(8)
        weapon_row.addWidget(QLabel("弧盘："))
        weapon_combo = SearchableComboBox()
        weapon_names = sorted(self.weapons_db.keys())
        self._fill_search_combo(weapon_combo, weapon_names, self.custom_weapons.get(name, ""))
        weapon_row.addWidget(weapon_combo, 1)
        set_layout.addLayout(weapon_row)
        layout.addWidget(set_box)

        template_box = QGroupBox("自选配置")
        template_layout = QVBoxLayout(template_box)
        template_layout.setContentsMargins(8, 6, 8, 6)
        template_layout.setSpacing(5)

        selected_main_stats = list(self.tape_main_filters.get(name, []))
        main_box = self._build_multi_select_row("卡带主词条：", self.tape_main_stats, selected_main_stats, "、")
        template_layout.addWidget(main_box)

        current_stat_cfg = (
            self.stat_priority_configs.get(name, {})
            if isinstance(self.stat_priority_configs.get(name, {}), dict)
            else {}
        )
        selected_stats = [s for s in list(current_stat_cfg.get("stats", []) or []) if s in self.drive_sub_stats]
        stat_box = self._build_multi_select_row("卡带/驱动副词条：", self.drive_sub_stats, selected_stats, " > ")
        stat_layout = stat_box.layout()
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.setFixedSize(24, 24)
        help_btn.clicked.connect(lambda: self._show_help("词条自选说明", STAT_PRIORITY_HELP))

        stat_option_row = QHBoxLayout()
        stat_option_row.setContentsMargins(0, 4, 0, 0)
        stat_option_row.setSpacing(16)
        stat_equal = QCheckBox("词条自选优先级一致")
        stat_equal.setChecked(bool(current_stat_cfg.get("equal_priority", False)))
        ignore_grade_limit = QCheckBox("不限制评分等级")
        ignore_grade_limit.setChecked(bool(current_stat_cfg.get("ignore_grade_limit", False)))
        stat_option_row.addWidget(stat_equal)
        stat_option_row.addWidget(ignore_grade_limit)
        stat_option_row.addWidget(help_btn)
        stat_option_row.addStretch(1)
        stat_layout.addLayout(stat_option_row)

        limits_row = QHBoxLayout()
        limits_row.setContentsMargins(0, 2, 0, 0)
        limits_row.setSpacing(8)

        grade_label = QLabel("最低生效等级")
        grade_combo = QComboBox()
        grade_combo.setFixedWidth(84)
        for grade in GRADE_LADDER:
            grade_combo.addItem(grade, grade)
        current_min_grade = str(current_stat_cfg.get("min_grade_limit") or "A").upper()
        grade_index = grade_combo.findData(current_min_grade)
        grade_combo.setCurrentIndex(grade_index if grade_index >= 0 else grade_combo.findData("A"))
        grade_combo.setEnabled(not ignore_grade_limit.isChecked())
        limits_row.addWidget(grade_label)
        limits_row.addWidget(grade_combo)
        limits_row.addSpacing(20)

        crit_threshold_label = QLabel("暴击率最小值")
        crit_threshold_spin = QSpinBox()
        crit_threshold_spin.setRange(0, 100)
        crit_threshold_spin.setSuffix("%")
        crit_threshold_spin.setFixedWidth(84)
        raw_threshold = current_stat_cfg.get(
            "crit_threshold",
            current_stat_cfg.get("crit_min_threshold", DEFAULT_CRIT_THRESHOLD),
        )
        try:
            crit_threshold_spin.setValue(int(raw_threshold))
        except (TypeError, ValueError):
            crit_threshold_spin.setValue(int(DEFAULT_CRIT_THRESHOLD))
        limits_row.addWidget(crit_threshold_label)
        limits_row.addWidget(crit_threshold_spin)
        limits_row.addStretch(1)
        stat_layout.addLayout(limits_row)

        def sync_grade_combo_enabled(checked=False):
            grade_combo.setEnabled(not ignore_grade_limit.isChecked())

        ignore_grade_limit.toggled.connect(sync_grade_combo_enabled)
        template_layout.addWidget(stat_box)
        layout.addWidget(template_box)

        effect_box = QGroupBox("其他配置")
        effect_layout = QVBoxLayout(effect_box)
        effect_layout.setContentsMargins(8, 8, 8, 8)
        effect_layout.setSpacing(5)
        effect_row = QHBoxLayout()
        effect_row.setSpacing(8)
        effect_row.addWidget(QLabel("套装效果："))
        effect_combo = QComboBox()
        effect_combo.addItem("四件套", FOUR_PIECE)
        effect_combo.addItem("二件套", TWO_PIECE)
        effect_combo.addItem("无效果", NO_EFFECT)
        current_effect = normalize_set_effect_mode(self.set_effect_modes.get(name))
        effect_index = effect_combo.findData(current_effect)
        effect_combo.setCurrentIndex(effect_index if effect_index >= 0 else 0)
        effect_row.addWidget(effect_combo, 1)
        effect_help_btn = QPushButton("?")
        effect_help_btn.setObjectName("btnHelp")
        effect_help_btn.clicked.connect(lambda: self._show_help("套装效果说明", SET_EFFECT_HELP))
        effect_row.addWidget(effect_help_btn)
        effect_layout.addLayout(effect_row)
        cap_row = QHBoxLayout()
        cap_row.setSpacing(8)
        cap_row.addWidget(QLabel("暴击率上限："))
        crit_cap_edit = QLineEdit()
        current_cap = self.crit_rate_caps.get(name)
        if current_cap is not None:
            crit_cap_edit.setText(f"{float(current_cap):g}")
        crit_cap_edit.setPlaceholderText("留空不限制")

        def apply_weapon_cap(text):
            resolved = resolve_priority_choice(weapon_names, str(text or "").strip(), None)
            cap = self._weapon_crit_rate_cap(resolved) if resolved in weapon_names else None
            if cap is not None:
                crit_cap_edit.setText(f"{float(cap):g}")

        weapon_combo.currentTextChanged.connect(apply_weapon_cap)
        cap_row.addWidget(crit_cap_edit, 1)
        cap_row.addWidget(QLabel("%"))
        effect_layout.addLayout(cap_row)
        layout.addWidget(effect_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.Accepted:
            set_value = set_combo.currentText().strip()
            resolved_set = resolve_priority_choice(self.all_sets, set_value, set_combo.currentData())
            self._set_custom_set(name, resolved_set)
            weapon_value = weapon_combo.currentText().strip()
            resolved_weapon = resolve_priority_choice(weapon_names, weapon_value, None)
            selected_weapon = resolved_weapon if resolved_weapon in weapon_names else ""
            self._set_custom_weapon(name, selected_weapon)
            self._set_tape_main_filter(name, selected_main_stats)
            self._set_stat_priority_config(
                name,
                selected_stats,
                stat_equal.isChecked(),
                ignore_grade_limit.isChecked(),
                grade_combo.currentData(),
                crit_threshold_spin.value(),
            )
            cap_text = crit_cap_edit.text().strip()
            if cap_text or not selected_weapon:
                self._set_crit_rate_cap(name, cap_text)
            self._set_set_effect_mode(name, effect_combo.currentData())
            self._render_grid(self.search.text())

    def reset_selection(self):
        self.save_temporary_priority_config()
        self.selected.clear()
        self.priority_links.clear()
        self.custom_sets.clear()
        self.custom_weapons.clear()
        self.crit_rate_caps.clear()
        self.tape_main_filters.clear()
        self.stat_priority_configs.clear()
        self.set_effect_modes.clear()
        self._render_grid(self.search.text())

    def _toggle(self, name):
        if name in self.selected:
            index = self.selected.index(name)
            self.selected.remove(name)
            if self.priority_links:
                if index < len(self.priority_links):
                    self.priority_links.pop(index)
                elif index - 1 >= 0:
                    self.priority_links.pop(index - 1)
        else:
            if self.selected:
                self.priority_links.append(">")
            self.selected.append(name)
        self.priority_links = normalize_priority_links(self.selected, self.priority_links)
        self._render_grid(self.search.text())
        self.orderChanged.emit()

    def _move_selected(self, index, delta):
        new_index = index + delta
        self._reorder_selected(index, new_index)

    def _drop_selected_on(self, index, target_index):
        if index == target_index:
            return
        insert_index = target_index - 1 if index < target_index else target_index
        self._reorder_selected(index, insert_index)

    def _reorder_selected(self, index, new_index):
        if index < 0 or new_index < 0 or index >= len(self.selected) or new_index >= len(self.selected):
            return
        role = self.selected.pop(index)
        self.selected.insert(new_index, role)
        self.priority_links = normalize_priority_links(self.selected, self.priority_links)
        self._render_grid(self.search.text())
        self.orderChanged.emit()

    def _cycle_priority_link(self, index):
        self.priority_links = normalize_priority_links(self.selected, self.priority_links)
        cycle_priority_link(self.priority_links, index)
        self._render_grid(self.search.text())
        self.orderChanged.emit()

    def get_selected(self):
        return list(self.selected)

    def get_priority_groups(self):
        return links_to_priority_groups(self.selected, self.priority_links)

    def get_custom_sets(self):
        return {
            name: self.custom_sets.get(name)
            for name in self.selected
            if self.custom_sets.get(name)
        }

    def get_tape_main_filters(self):
        return {
            name: list(self.tape_main_filters.get(name, []))
            for name in self.selected
            if self.tape_main_filters.get(name)
        }

    def get_custom_weapons(self):
        return {
            name: self.custom_weapons.get(name)
            for name in self.selected
            if self.custom_weapons.get(name)
        }

    def get_crit_rate_caps(self):
        return {
            name: float(self.crit_rate_caps.get(name))
            for name in self.selected
            if name in self.crit_rate_caps
        }

    def get_crit_priority_modes(self):
        return {
            name: dict(self.stat_priority_configs.get(name))
            for name in self.selected
            if self.stat_priority_configs.get(name)
        }

    def get_set_effect_modes(self):
        return {
            name: normalize_set_effect_mode(self.set_effect_modes.get(name))
            for name in self.selected
            if normalize_set_effect_mode(self.set_effect_modes.get(name)) != FOUR_PIECE
        }

    def save_priority_config(self, show_message: bool = True):
        self._write_priority_config(self._priority_config_path())
        if show_message:
            QMessageBox.information(self, "保存成功", "当前角色优先级方案已保存，可随时读取该方案。")

    def save_temporary_priority_config(self):
        self._write_priority_config(temporary_priority_config_path(self._priority_config_path()))

    def _write_priority_config(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "priority_list": self.selected,
            "priority_groups": self.get_priority_groups(),
            "priority_links": normalize_priority_links(self.selected, self.priority_links),
            "custom_sets": self.get_custom_sets(),
            "custom_set_overrides": self.get_custom_sets(),
            "custom_weapons": self.get_custom_weapons(),
            "crit_rate_caps": self.get_crit_rate_caps(),
            "tape_main_filters": self.get_tape_main_filters(),
            "stat_priority_configs": self.get_crit_priority_modes(),
            "set_effect_modes": self.get_set_effect_modes(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_priority_config(self):
        self._load_priority_config_from(self._priority_config_path())

    def load_startup_priority_config(self):
        temp_path = temporary_priority_config_path(self._priority_config_path())
        if temp_path.exists():
            self._load_priority_config_from(temp_path)
        else:
            self.load_priority_config()

    def restore_temporary_priority_config(self):
        self._load_priority_config_from(temporary_priority_config_path(self._priority_config_path()))

    def _load_priority_config_from(self, path: Path):
        self.selected.clear()
        self.priority_links.clear()
        self.custom_sets.clear()
        self.custom_weapons.clear()
        self.crit_rate_caps.clear()
        self.tape_main_filters.clear()
        self.stat_priority_configs.clear()
        self.set_effect_modes.clear()
        if not path.exists():
            self._render_grid(self.search.text())
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.selected, self.priority_links = load_priority_selection(data, self.all_roles)
            self.custom_sets = self._load_custom_set_overrides(data)
            self.custom_weapons = {
                role: weapon
                for role, weapon in data.get("custom_weapons", {}).items()
                if role in self.all_roles and weapon and (not self.weapons_db or weapon in self.weapons_db)
            }
            self.crit_rate_caps = {}
            for role, value in data.get("crit_rate_caps", {}).items():
                if role not in self.all_roles:
                    continue
                try:
                    self.crit_rate_caps[role] = round(min(max(float(value), 0.0), 100.0), 4)
                except (TypeError, ValueError):
                    continue
            raw_filters = data.get("tape_main_filters", {})
            self.tape_main_filters = {
                role: [value for value in values if value in self.tape_main_stats]
                for role, values in raw_filters.items()
                if role in self.all_roles and isinstance(values, list)
            }
            self.stat_priority_configs = {}
            allowed_stats = set(self.drive_sub_stats)
            for role, cfg_item in data.get("stat_priority_configs", {}).items():
                if role not in self.all_roles or not isinstance(cfg_item, dict):
                    continue
                cfg = persistable_stat_priority_config(cfg_item, allowed_stats=allowed_stats)
                if cfg:
                    self.stat_priority_configs[role] = cfg
            self.set_effect_modes = {}
            for role, mode in data.get("set_effect_modes", {}).items():
                normalized = normalize_set_effect_mode(mode)
                if role in self.all_roles and normalized in SET_EFFECT_MODES and normalized != FOUR_PIECE:
                    self.set_effect_modes[role] = normalized
            self._render_grid(self.search.text())
        except Exception as exc:
            QMessageBox.warning(self, "恢复优先级", f"读取优先级配置失败：{exc}")

    def _load_custom_set_overrides(self, data: dict) -> dict[str, str]:
        if isinstance(data.get("custom_set_overrides"), dict):
            source = data.get("custom_set_overrides", {})
            return {
                role: set_name
                for role, set_name in source.items()
                if role in self.all_roles and set_name
            }

        legacy = data.get("custom_sets", {})
        if not isinstance(legacy, dict):
            return {}
        selected = set(self.selected)
        legacy_roles = {role for role, set_name in legacy.items() if role in self.all_roles and set_name}
        if selected and selected.issubset(legacy_roles):
            return {}
        return {
            role: set_name
            for role, set_name in legacy.items()
            if role in self.all_roles and set_name
        }


PRIORITY_SAVE_HELP = (
    "保存：把当前角色优先级写入永久档 priority_config.json。\n"
    "读取：从永久档读取上次保存的优先级。\n"
    "重置：先把重置前的当前优先级写入临时档 priority_config.temp.json，再清空当前选择。\n"
    "恢复：从临时档读取重置前的优先级。临时档用于误触重置后的找回，不替代永久保存。"
)


STAT_PRIORITY_HELP = (
    "卡带主词条：只允许装配已选择的主词条；没有匹配卡带时不装卡带。\n\n"
    "卡带/驱动副词条：让该角色优先使用带有所选副词条的驱动。\n"
    "关闭“优先级一致”时，按选择顺序逐层优先，例如 A > B > C 会优先使用同时含 A+B+C、再含 A+B、再含 A 的驱动。\n"
    "开启“优先级一致”时，优先使用命中副词条数量更多的驱动。\n\n"
    "未勾选“不限制评分等级”时，可通过“最低生效等级”选择 D 至 ACE 的门槛；"
    "默认 A 级。词条自选加成与暴击率最小值加成都受该门槛约束。\n"
    "勾选“不限制评分等级”后，会按整张图纸的自选副词条覆盖程度优先；覆盖相同时再比较评分。\n\n"
    "暴击率最小值会参考当前配装的累计暴击率：低于该值时，仅对达到最低生效等级的驱动优先选择带暴击词条的；"
    "达到后取消该加成。暴击率上限会硬性限制配装总暴击。"
)


SET_EFFECT_HELP = (
    "四件套：沿用默认规则，必须凑齐目标套装的 4 个驱动形状。\n\n"
    "二件套：只要求目标套装中任意 2 个驱动形状生效，剩余底盘空间优先填入该角色的额外形状，再用其他形状补满。\n\n"
    "无效果：不强制使用目标套装形状，整张底盘都优先填入该角色的额外形状，再用其他形状补满。\n\n"
    "该选项只影响图纸和驱动匹配逻辑，不修改 roles.json 或 sets.json。旧配置未设置时默认按四件套处理。"
)
