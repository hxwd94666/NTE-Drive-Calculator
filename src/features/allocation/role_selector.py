# 管理角色优先级选择和偏好存档。
"""Role priority selector and per-role equipment preference dialog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
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
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from src.ui.widgets import SearchableComboBox, match_pinyin
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
        self.tape_main_stats: list[str] = []
        self.drive_sub_stats: list[str] = []
        self.selected: list[str] = []
        self.custom_sets: dict[str, str] = {}
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
        primary_save_btn.clicked.connect(self.save_priority_config)
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
        tip.setStyleSheet("color:#8b949e;font-size:11px;border:none")
        layout.addWidget(tip)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setMinimumHeight(260)
        self.grid_w = QWidget()
        self.grid_layout = QGridLayout(self.grid_w)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(6)
        self.grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.grid_scroll.setWidget(self.grid_w)
        layout.addWidget(self.grid_scroll, 1)

    _CARD_SEL = "QFrame{background:#1f6feb22;border:2px solid #58a6ff;border-radius:8px}QFrame:hover{border-color:#79c0ff}"
    _CARD_OFF = "QFrame{background:#161b22;border:1px solid #21262d;border-radius:8px}QFrame:hover{border-color:#30363d}"

    def load_roles(self, roles_db, all_sets, tape_main_stats=None, drive_sub_stats=None):
        self.all_roles = roles_db
        self.all_sets = all_sets
        self.tape_main_stats = list(tape_main_stats or [])
        self.drive_sub_stats = list(drive_sub_stats or [])
        self._render_grid(self.search.text() if hasattr(self, "search") else "")

    def _render_grid(self, filter_text=""):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        names = sorted(self.all_roles.keys())
        if filter_text.strip():
            names = [name for name in names if match_pinyin(name, filter_text.strip())]
        col = row = 0
        for name in names:
            self.grid_layout.addWidget(self._make_card(name), row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1

    def _make_card(self, name):
        selected = name in self.selected
        card = QFrame()
        card.setFixedSize(214, 48)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(self._CARD_SEL if selected else self._CARD_OFF)

        layout = QGridLayout(card)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(4)
        layout.setColumnMinimumWidth(0, 22)
        layout.setColumnStretch(1, 1)

        badge = QLabel("")
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignCenter)
        if selected:
            badge.setText(str(self.selected.index(name) + 1))
            badge.setStyleSheet(
                "background:#1f6feb;color:#fff;border-radius:10px;font-size:10px;font-weight:700;border:none"
            )
        else:
            badge.setStyleSheet("background:transparent;border:none")
        layout.addWidget(badge, 0, 0, 1, 1)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_label = QLabel(name)
        name_label.setStyleSheet("font-size:13px;font-weight:700;border:none;background:transparent;color:#c9d1d9")
        name_row.addWidget(name_label, 1)

        manage_btn = QPushButton("管理")
        manage_btn.setObjectName("btnSm")
        manage_btn.setCursor(Qt.PointingHandCursor)
        manage_btn.setStyleSheet(
            "QPushButton{background:#238636;color:#fff;border:1px solid #2ea043;border-radius:5px;"
            "padding:3px 8px;font-size:11px;font-weight:700}QPushButton:hover{background:#2ea043}"
        )
        manage_btn.clicked.connect(lambda checked=False, role=name: self._manage_role_preferences(role))
        name_row.addWidget(manage_btn)
        layout.addLayout(name_row, 0, 1)

        card.mousePressEvent = lambda event, role=name: self._toggle(role)
        self._cards[name] = {"card": card, "badge": badge}
        return card

    def _filter(self, text):
        self._render_grid(text)

    def _set_custom_set(self, name, text):
        self.custom_sets[name] = text
        self.orderChanged.emit()

    def _set_tape_main_filter(self, name, values):
        if values:
            self.tape_main_filters[name] = values
        else:
            self.tape_main_filters.pop(name, None)
        self.orderChanged.emit()

    def _set_stat_priority_config(self, name, stats, equal_priority=False):
        clean = []
        for stat in stats or []:
            if stat and stat in self.drive_sub_stats and stat not in clean:
                clean.append(stat)
        if clean:
            self.stat_priority_configs[name] = {"stats": clean, "equal_priority": bool(equal_priority)}
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

    def _manage_role_preferences(self, name):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{name} · 管理")
        dlg.setMinimumSize(480, 360)
        if self._style_sheet:
            dlg.setStyleSheet(self._style_sheet)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        role_data = self.all_roles.get(name, {})
        current_set = self.custom_sets.get(name) or role_data.get("default_set", self.all_sets[0] if self.all_sets else "")
        set_box = QGroupBox("套装配置")
        set_layout = QHBoxLayout(set_box)
        set_layout.setSpacing(8)
        set_combo = SearchableComboBox()
        self._fill_search_combo(set_combo, self.all_sets, current_set)
        set_layout.addWidget(set_combo, 1)
        layout.addWidget(set_box)

        selected_main_stats = list(self.tape_main_filters.get(name, []))
        main_box = QGroupBox("卡带主词条选项")
        main_layout = QVBoxLayout(main_box)
        main_layout.setSpacing(8)
        main_row = QHBoxLayout()
        main_row.setSpacing(8)
        main_combo = SearchableComboBox()
        self._fill_search_combo(main_combo, self.tape_main_stats)
        main_row.addWidget(main_combo, 1)
        add_main_btn = QPushButton("添加")
        add_main_btn.setObjectName("btnAction")
        clear_main_btn = QPushButton("清空")
        clear_main_btn.setObjectName("btnDanger")
        main_row.addWidget(add_main_btn)
        main_row.addWidget(clear_main_btn)
        main_layout.addLayout(main_row)
        main_label = QLabel()
        main_label.setWordWrap(True)
        main_label.setMinimumHeight(32)
        main_label.setStyleSheet(
            "color:#7ee787;font-size:13px;border:1px solid #238636;border-radius:6px;"
            "background:#0f3d2e;padding:7px 9px"
        )
        main_layout.addWidget(main_label)

        def refresh_main_label():
            text = "Default" if not selected_main_stats else "、".join(selected_main_stats)
            main_label.setText(text)
            main_label.setToolTip(text)

        def add_main_stat():
            value = main_combo.currentText().strip()
            resolved = resolve_priority_choice(self.tape_main_stats, value, main_combo.currentData())
            if resolved in self.tape_main_stats and resolved not in selected_main_stats:
                selected_main_stats.append(resolved)
                refresh_main_label()

        add_main_btn.clicked.connect(add_main_stat)
        clear_main_btn.clicked.connect(lambda: (selected_main_stats.clear(), refresh_main_label()))
        refresh_main_label()
        layout.addWidget(main_box)

        current_stat_cfg = (
            self.stat_priority_configs.get(name, {})
            if isinstance(self.stat_priority_configs.get(name, {}), dict)
            else {}
        )
        selected_stats = [s for s in list(current_stat_cfg.get("stats", []) or []) if s in self.drive_sub_stats]
        stat_box = QGroupBox("词条自选")
        stat_layout = QVBoxLayout(stat_box)
        stat_layout.setSpacing(8)
        stat_row = QHBoxLayout()
        stat_row.setSpacing(8)
        stat_combo = SearchableComboBox()
        self._fill_search_combo(stat_combo, self.drive_sub_stats)
        stat_row.addWidget(stat_combo, 1)
        add_stat_btn = QPushButton("添加")
        add_stat_btn.setObjectName("btnAction")
        clear_stat_btn = QPushButton("清空")
        clear_stat_btn.setObjectName("btnDanger")
        stat_row.addWidget(add_stat_btn)
        stat_row.addWidget(clear_stat_btn)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(lambda: self._show_help("词条自选说明", STAT_PRIORITY_HELP))
        stat_row.addWidget(help_btn)
        stat_layout.addLayout(stat_row)

        stat_equal = QCheckBox("词条自选优先级一致")
        stat_equal.setChecked(bool(current_stat_cfg.get("equal_priority", False)))
        stat_layout.addWidget(stat_equal)

        stat_label = QLabel()
        stat_label.setWordWrap(True)
        stat_label.setMinimumHeight(32)
        stat_label.setStyleSheet(
            "color:#7ee787;font-size:13px;border:1px solid #238636;border-radius:6px;"
            "background:#0f3d2e;padding:7px 9px"
        )
        stat_layout.addWidget(stat_label)

        def refresh_stat_label():
            stat_label.setText("Default" if not selected_stats else " > ".join(selected_stats))

        def add_stat_priority():
            value = stat_combo.currentText().strip()
            resolved = resolve_priority_choice(self.drive_sub_stats, value, stat_combo.currentData())
            if resolved in self.drive_sub_stats and resolved not in selected_stats:
                selected_stats.append(resolved)
                refresh_stat_label()
            stat_combo.setCurrentIndex(-1)
            stat_combo.setEditText("")

        add_stat_btn.clicked.connect(add_stat_priority)
        clear_stat_btn.clicked.connect(lambda: (selected_stats.clear(), refresh_stat_label()))
        refresh_stat_label()
        layout.addWidget(stat_box)

        effect_box = QGroupBox("套装效果")
        effect_layout = QHBoxLayout(effect_box)
        effect_layout.setSpacing(8)
        effect_combo = QComboBox()
        effect_combo.addItem("四件套", FOUR_PIECE)
        effect_combo.addItem("二件套", TWO_PIECE)
        effect_combo.addItem("无效果", NO_EFFECT)
        current_effect = normalize_set_effect_mode(self.set_effect_modes.get(name))
        effect_index = effect_combo.findData(current_effect)
        effect_combo.setCurrentIndex(effect_index if effect_index >= 0 else 0)
        effect_layout.addWidget(effect_combo, 1)
        effect_help_btn = QPushButton("?")
        effect_help_btn.setObjectName("btnHelp")
        effect_help_btn.clicked.connect(lambda: self._show_help("套装效果说明", SET_EFFECT_HELP))
        effect_layout.addWidget(effect_help_btn)
        layout.addWidget(effect_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.Accepted:
            set_value = set_combo.currentText().strip()
            resolved_set = resolve_priority_choice(self.all_sets, set_value, set_combo.currentData())
            self._set_custom_set(name, resolved_set)
            self._set_tape_main_filter(name, selected_main_stats)
            self._set_stat_priority_config(name, selected_stats, stat_equal.isChecked())
            self._set_set_effect_mode(name, effect_combo.currentData())
            self._render_grid(self.search.text())

    def reset_selection(self):
        self.save_temporary_priority_config()
        self.selected.clear()
        self.custom_sets.clear()
        self.tape_main_filters.clear()
        self.stat_priority_configs.clear()
        self.set_effect_modes.clear()
        self._render_grid(self.search.text())

    def _toggle(self, name):
        if name in self.selected:
            self.selected.remove(name)
        else:
            self.selected.append(name)
        self._render_grid(self.search.text())
        self.orderChanged.emit()

    def get_selected(self):
        return list(self.selected)

    def get_custom_sets(self):
        result = {}
        for name in self.selected:
            role_data = self.all_roles.get(name, {})
            result[name] = self.custom_sets.get(name) or role_data.get("default_set", "")
        return result

    def get_tape_main_filters(self):
        return {
            name: list(self.tape_main_filters.get(name, []))
            for name in self.selected
            if self.tape_main_filters.get(name)
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
            "custom_sets": self.get_custom_sets(),
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
        self.custom_sets.clear()
        self.tape_main_filters.clear()
        self.stat_priority_configs.clear()
        self.set_effect_modes.clear()
        if not path.exists():
            self._render_grid(self.search.text())
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.selected = [role for role in data.get("priority_list", []) if role in self.all_roles]
            self.custom_sets = {
                role: set_name
                for role, set_name in data.get("custom_sets", {}).items()
                if role in self.all_roles and set_name
            }
            raw_filters = data.get("tape_main_filters", {})
            self.tape_main_filters = {
                role: [value for value in values if value in self.tape_main_stats]
                for role, values in raw_filters.items()
                if role in self.all_roles and isinstance(values, list)
            }
            self.stat_priority_configs = {}
            for role, cfg_item in data.get("stat_priority_configs", {}).items():
                if role not in self.all_roles or not isinstance(cfg_item, dict):
                    continue
                stats = [s for s in cfg_item.get("stats", []) if s in self.drive_sub_stats]
                if stats:
                    self.stat_priority_configs[role] = {
                        "stats": stats,
                        "equal_priority": bool(cfg_item.get("equal_priority", False)),
                    }
            self.set_effect_modes = {}
            for role, mode in data.get("set_effect_modes", {}).items():
                normalized = normalize_set_effect_mode(mode)
                if role in self.all_roles and normalized in SET_EFFECT_MODES and normalized != FOUR_PIECE:
                    self.set_effect_modes[role] = normalized
            self._render_grid(self.search.text())
        except Exception as exc:
            QMessageBox.warning(self, "恢复优先级", f"读取优先级配置失败：{exc}")


PRIORITY_SAVE_HELP = (
    "保存：把当前角色优先级写入永久档 priority_config.json。\n"
    "读取：从永久档读取上次保存的优先级。\n"
    "重置：先把重置前的当前优先级写入临时档 priority_config.temp.json，再清空当前选择。\n"
    "恢复：从临时档读取重置前的优先级。临时档用于误触重置后的找回，不替代永久保存。"
)


STAT_PRIORITY_HELP = (
    "词条自选只影响该角色挑选驱动/卡带时的候选顺序，不改变词条权重，也不会额外增加最终评分。\n\n"
    "关闭“词条自选优先级一致”时：按照添加顺序作为优先级，先从最高优先级词条的候选池中寻找，没有合适的再逐级向后找，最后回到全局池。\n\n"
    "开启“词条自选优先级一致”时：不看添加顺序，优先使用覆盖所选词条数量更多的装备。\n\n"
    "卡带会先满足卡带主词条筛选，再在满足主词条的池子里应用本规则。为了避免只因词条命中选到低质装备，命中优先只对至少 A 级评分的装备生效。"
)


SET_EFFECT_HELP = (
    "四件套：沿用默认规则，必须凑齐目标套装的 4 个驱动形状。\n\n"
    "二件套：只要求目标套装中任意 2 个驱动形状生效，剩余底盘空间优先填入该角色的额外形状，再用其他形状补满。\n\n"
    "无效果：不强制使用目标套装形状，整张底盘都优先填入该角色的额外形状，再用其他形状补满。\n\n"
    "该选项只影响图纸和驱动匹配逻辑，不修改 roles.json 或 sets.json。旧配置未设置时默认按四件套处理。"
)
