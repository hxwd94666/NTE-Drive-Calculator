# 管理角色优先级选择和偏好存档。
"""Role priority selector and per-role equipment preference dialog."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from src.i18n import tr, display_term
from src.integrations.bundled_resources import bundled_config_dir

from src.ui.widgets import SearchableComboBox, match_pinyin
from src.app.theme import current_theme_name, themed_style
from src.domain.crit_threshold import persistable_stat_priority_config
from src.features.allocation.priority_groups import (
    normalize_priority_links,
)
from src.solver.set_effects import FOUR_PIECE, normalize_set_effect_mode


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
        super().__init__(display_term(role))
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


from src.features.allocation.role_selector_preferences import RoleSelectorPreferencesMixin

class RoleSelector(RoleSelectorPreferencesMixin, QWidget):
    """Select role priority and manage per-role set/stat filters."""

    orderChanged = Signal()

    def __init__(
        self,
        parent=None,
        priority_config_path_provider: Callable[[], Path] | None = None,
        style_sheet: str = "",
        help_callback: Callable | None = None,
        preference_dialog_callback: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self._priority_config_path_provider = priority_config_path_provider
        self._style_sheet = style_sheet
        self._help_callback = help_callback
        self._preference_dialog_callback = preference_dialog_callback
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
        self.tape_main_filter_override_roles: set[str] = set()
        self.stat_priority_configs: dict[str, dict] = {}
        self.stat_priority_override_roles: set[str] = set()
        self.set_effect_modes: dict[str, str] = {}
        self.default_mag_character_ids: frozenset[int] = frozenset()
        self._cards: dict = {}
        self._build()

    def _priority_config_path(self) -> Path:
        if self._priority_config_path_provider:
            return Path(self._priority_config_path_provider())
        return bundled_config_dir() / "priority_config.json"

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索角色（支持拼音）..."))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)

        primary_reset_btn = QPushButton(tr("重置"))
        primary_reset_btn.setObjectName("btnDanger")
        primary_reset_btn.clicked.connect(self.reset_selection)
        search_row.addWidget(primary_reset_btn)

        primary_restore_btn = QPushButton(tr("恢复"))
        primary_restore_btn.setObjectName("btnAction")
        primary_restore_btn.clicked.connect(self.restore_temporary_priority_config)
        search_row.addWidget(primary_restore_btn)

        primary_save_btn = QPushButton(tr("保存"))
        primary_save_btn.setObjectName("btnAction")
        primary_save_btn.clicked.connect(lambda _checked=False: self.save_priority_config())
        search_row.addWidget(primary_save_btn)

        primary_load_btn = QPushButton(tr("读取"))
        primary_load_btn.setObjectName("btnAction")
        primary_load_btn.clicked.connect(self.load_priority_config)
        search_row.addWidget(primary_load_btn)

        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(lambda: self._show_help("优先级存档说明", PRIORITY_SAVE_HELP))
        search_row.addWidget(help_btn)
        layout.addLayout(search_row)

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

    def load_roles(
        self,
        roles_db,
        all_sets,
        tape_main_stats=None,
        drive_sub_stats=None,
        weapons_db=None,
        default_mag_character_ids=None,
    ):
        self.all_roles = roles_db
        self.all_sets = all_sets
        self.weapons_db = normalize_weapons_db(weapons_db)
        self.tape_main_stats = list(tape_main_stats or [])
        self.drive_sub_stats = list(drive_sub_stats or [])
        if default_mag_character_ids is not None:
            self.default_mag_character_ids = frozenset(
                int(character_id) for character_id in default_mag_character_ids
            )
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
            empty = QLabel(tr("未选择角色"))
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
            name_btn.setToolTip(tr("点击移出当前优先级；拖动可调整顺序"))
            name_btn.setFixedWidth(self._priority_role_name_width())
            name_size = self._priority_role_name_font_size(name)
            name_btn.setStyleSheet(
                themed_style(
                    "QPushButton{background:transparent;color:#c9d1d9;border:none;"
                    f"padding:3px 5px;font-size:{name_size}px;font-weight:700;text-align:left}}"
                    "QPushButton:hover{color:#c9d1d9}"
                )
            )
            item_layout.addWidget(name_btn)

            manage_btn = QPushButton(tr("管理"))
            manage_btn.setObjectName("btnSm")
            manage_btn.setFixedSize(48, 28)
            manage_btn.setStyleSheet(
                "QPushButton{background:#238636;color:#fff;border:1px solid #2ea043;"
                "border-radius:5px;padding:3px 7px;font-size:11px;font-weight:700}"
                "QPushButton:hover{background:#2ea043}"
            )
            manage_btn.clicked.connect(lambda _checked=False, role=name: self._open_role_preferences(role))
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
                link_btn.setToolTip(tr(">：严格优先；>>：批次边界；=：同批次平级。点击循环切换。"))
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

        name_label = QLabel(display_term(name))
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

    def _default_weapon_for_role(self, name: str) -> str:
        role_data = self.all_roles.get(name, {}) or {}
        weapon = str(role_data.get("default_weapon") or "").strip()
        return weapon if weapon in self.weapons_db else ""

    def _default_tape_main_filter(self, name: str) -> list[str]:
        del name
        return []

    def _default_substat_priority(self, name: str) -> list[str]:
        del name
        return []

    def _effective_weapon_for_role(self, name: str) -> str:
        return str(self.custom_weapons.get(name) or self._default_weapon_for_role(name))

    def _set_custom_weapon(self, name, text):
        weapon = str(text or "").strip()
        if weapon and weapon != self._default_weapon_for_role(name):
            self.custom_weapons[name] = weapon
        else:
            self.custom_weapons.pop(name, None)
        cap = self._weapon_crit_rate_cap(weapon)
        if cap is not None:
            self.crit_rate_caps[name] = cap
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
        self.tape_main_filter_override_roles.add(name)
        self.tape_main_filters[name] = [
            value for value in values or [] if value in self.tape_main_stats
        ]
        self.orderChanged.emit()

    def _set_stat_priority_config(
        self,
        name,
        stats,
        blacklist,
        equal_priority=False,
        ignore_grade_limit=False,
        min_grade_limit="A",
        crit_threshold=None,
        blacklist_zero_weight=False,
    ):
        payload = {
            "stats": stats or [],
            "blacklist": blacklist or [],
            "blacklist_zero_weight": blacklist_zero_weight,
            "equal_priority": equal_priority,
            "ignore_grade_limit": ignore_grade_limit,
            "min_grade_limit": min_grade_limit,
        }
        if crit_threshold not in (None, ""):
            payload["crit_threshold"] = crit_threshold
        cfg = persistable_stat_priority_config(
            payload,
            allowed_stats=set(self.drive_sub_stats),
            dedupe_stats=True,
        )
        self.stat_priority_override_roles.add(name)
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
        # Help copy lives in module-level constants; translate at call time.
        title, text = tr(title), tr(text)
        if self._help_callback:
            self._help_callback(self, title, text)
        else:
            QMessageBox.information(self, title, text)

    def _fill_search_combo(self, combo: SearchableComboBox, values: list[str], current: str | None = None):
        # The label is translated; item data keeps the Chinese key so selection
        # and saving still round-trip through resolve_priority_choice().
        for value in values:
            combo.addItem(display_term(value), value)
        combo.refresh_search_items()
        if current and current in values:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentText(display_term(current))
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

    def _refresh_selected_summary_label(
        self,
        label: QLabel,
        selected: list[str],
        separator: str,
        empty_text: str = "Default",
    ):
        text = empty_text if not selected else separator.join(
            display_term(value) for value in selected
        )
        label.setText(text)
        label.setToolTip(text)

    def _build_multi_select_row(
        self,
        title: str,
        choices: list[str],
        selected: list[str],
        separator: str,
        empty_text: str = "Default",
    ):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        row = QHBoxLayout()
        row.setSpacing(6)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(title_label)
        combo = SearchableComboBox()
        self._fill_search_combo(combo, choices)
        row.addWidget(combo, 1)

        add_btn = QPushButton(tr("添加"))
        add_btn.setObjectName("btnAction")
        add_btn.setFixedWidth(60)
        clear_btn = QPushButton(tr("清空"))
        clear_btn.setObjectName("btnDanger")
        clear_btn.setFixedWidth(74)
        row.addWidget(add_btn)
        row.addWidget(clear_btn)

        summary = self._make_selected_summary_label()
        layout.addLayout(row)
        layout.addWidget(summary)

        def refresh_summary():
            self._refresh_selected_summary_label(
                summary,
                selected,
                separator,
                empty_text,
            )

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

    def _open_role_preferences(self, name: str) -> None:
        if self._preference_dialog_callback is not None:
            self._preference_dialog_callback(name)
            return
        self._manage_role_preferences(name)



from src.features.allocation.role_selector_help import (
    PRIORITY_SAVE_HELP,
)
