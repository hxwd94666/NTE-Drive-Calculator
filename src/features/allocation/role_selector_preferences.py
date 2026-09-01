# 管理角色选择器的偏好编辑、排序和配置持久化。
"""Role priority selector and per-role equipment preference dialog."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QLineEdit,
)

from src.i18n import tr, display_term
from src.ui.widgets import SearchableComboBox, match_pinyin
from src.domain.crit_threshold import persistable_stat_priority_config
from src.domain.grade_limits import GRADE_LADDER
from src.features.allocation.priority_groups import (
    cycle_priority_link,
    links_to_priority_groups,
    load_priority_selection,
    normalize_priority_links,
    shift_crossed_priority_boundaries,
)
from src.solver.set_effects import FOUR_PIECE, NO_EFFECT, SET_EFFECT_MODES, TWO_PIECE, normalize_set_effect_mode
from src.features.allocation.role_selector_help import (
    CRIT_RATE_CAP_HELP,
    CRIT_THRESHOLD_HELP,
    SET_EFFECT_HELP,
    STAT_PRIORITY_HELP,
)


_ROLE_DEFAULT_POLICY = "no-default-stat-selection-v2"


def resolve_priority_choice(values: list[str], raw_text: str | None, current_data=None) -> str:
    """Resolve a searchable combo selection without confusing prefix-like stats."""

    if current_data is not None and str(current_data) in values:
        return str(current_data)
    raw = str(raw_text or "").strip()
    if raw in values:
        return raw
    return next((value for value in values if match_pinyin(value, raw)), raw)


def resolve_optional_priority_choice(
    values: list[str],
    raw_text: str | None,
    current_data=None,
) -> str:
    """Resolve an optional combo while preserving an explicit clear action."""

    raw = str(raw_text or "").strip()
    if not raw:
        return ""
    return resolve_priority_choice(values, raw, current_data)


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


class RoleSelectorPreferencesMixin:
    def _selected_substat_priority(self, name: str) -> list[str]:
        """Resolve the editor value while preserving an explicit empty override."""

        current = self.stat_priority_configs.get(name)
        if isinstance(current, dict) and "stats" in current:
            values = current.get("stats") or []
        elif name in self.stat_priority_override_roles:
            values = []
        else:
            values = self._default_substat_priority(name)
        return [stat for stat in list(values) if stat in self.drive_sub_stats]

    def _manage_role_preferences(self, name):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("{name} · 管理", name=display_term(str(name))))
        dlg.setMinimumSize(560, 320)
        if self._style_sheet:
            dlg.setStyleSheet(self._style_sheet)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        role_data = self.all_roles.get(name, {})
        current_set = self.custom_sets.get(name) or role_data.get("default_set", self.all_sets[0] if self.all_sets else "")
        default_weapon = self._default_weapon_for_role(name)
        set_box = QGroupBox(tr("角色配置"))
        set_layout = QVBoxLayout(set_box)
        set_layout.setContentsMargins(8, 8, 8, 8)
        set_layout.setSpacing(5)
        set_row = QHBoxLayout()
        set_row.setSpacing(8)
        set_row.addWidget(QLabel(tr("卡带：")))
        set_combo = SearchableComboBox()
        self._fill_search_combo(set_combo, self.all_sets, current_set)
        set_row.addWidget(set_combo, 1)
        set_layout.addLayout(set_row)
        weapon_row = QHBoxLayout()
        weapon_row.setSpacing(8)
        weapon_row.addWidget(QLabel(tr("弧盘：")))
        weapon_combo = SearchableComboBox()
        weapon_names = sorted(self.weapons_db.keys())
        self._fill_search_combo(
            weapon_combo,
            weapon_names,
            self.custom_weapons.get(name, "") or default_weapon,
        )
        if weapon_combo.lineEdit() is not None:
            weapon_combo.lineEdit().setClearButtonEnabled(True)
        weapon_row.addWidget(weapon_combo, 1)
        set_layout.addLayout(weapon_row)
        layout.addWidget(set_box)

        template_box = QGroupBox(tr("自选配置"))
        template_layout = QVBoxLayout(template_box)
        template_layout.setContentsMargins(8, 6, 8, 6)
        template_layout.setSpacing(5)

        selected_main_stats = list(
            self.tape_main_filters.get(name, [])
            if name in self.tape_main_filter_override_roles
            else self._default_tape_main_filter(name)
        )
        main_box = self._build_multi_select_row(
            tr("卡带主词条："),
            self.tape_main_stats,
            selected_main_stats,
            "、",
            empty_text=tr("未选择"),
        )
        template_layout.addWidget(main_box)

        current_stat_cfg = (
            self.stat_priority_configs.get(name, {})
            if isinstance(self.stat_priority_configs.get(name, {}), dict)
            else {}
        )
        selected_stats = self._selected_substat_priority(name)
        stat_box = self._build_multi_select_row(
            tr("卡带/驱动副词条："),
            self.drive_sub_stats,
            selected_stats,
            " > ",
            empty_text=tr("未选择"),
        )
        stat_layout = stat_box.layout()
        selected_blacklist = [
            stat
            for stat in list(current_stat_cfg.get("blacklist", []) or [])
            if stat in self.drive_sub_stats
        ]
        stat_layout.addWidget(
            self._build_multi_select_row(
                tr("副词条黑名单："),
                self.drive_sub_stats,
                selected_blacklist,
                "、",
                empty_text=tr("未选择"),
            )
        )
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.setFixedSize(24, 24)
        help_btn.clicked.connect(lambda: self._show_help("词条自选说明", STAT_PRIORITY_HELP))

        stat_option_row = QHBoxLayout()
        stat_option_row.setContentsMargins(0, 4, 0, 0)
        stat_option_row.setSpacing(16)
        stat_equal = QCheckBox(tr("副词条优先级一致"))
        stat_equal.setChecked(bool(current_stat_cfg.get("equal_priority", False)))
        blacklist_zero_weight = QCheckBox(tr("黑名单为零权重"))
        blacklist_zero_weight.setChecked(
            bool(current_stat_cfg.get("blacklist_zero_weight", False))
        )
        ignore_grade_limit = QCheckBox(tr("不限制评分等级"))
        ignore_grade_limit.setChecked(bool(current_stat_cfg.get("ignore_grade_limit", False)))
        grade_label = QLabel(tr("最低生效等级"))
        grade_combo = QComboBox()
        grade_combo.setFixedWidth(84)
        for grade in GRADE_LADDER:
            grade_combo.addItem(grade, grade)
        current_min_grade = str(current_stat_cfg.get("min_grade_limit") or "A").upper()
        grade_index = grade_combo.findData(current_min_grade)
        grade_combo.setCurrentIndex(grade_index if grade_index >= 0 else grade_combo.findData("A"))
        grade_combo.setEnabled(not ignore_grade_limit.isChecked())
        stat_option_row.addWidget(stat_equal)
        stat_option_row.addWidget(ignore_grade_limit)
        stat_option_row.addWidget(blacklist_zero_weight)
        stat_option_row.addWidget(grade_label)
        stat_option_row.addWidget(grade_combo)
        stat_option_row.addWidget(help_btn)
        stat_option_row.addStretch(1)
        stat_layout.addLayout(stat_option_row)

        crit_threshold_label = QLabel(tr("暴击率最小值"))
        crit_threshold_help = QPushButton("?")
        crit_threshold_help.setObjectName("btnHelp")
        crit_threshold_help.setFixedSize(24, 24)
        crit_threshold_help.clicked.connect(
            lambda: self._show_help("暴击率最小值", CRIT_THRESHOLD_HELP)
        )
        crit_threshold_edit = QLineEdit()
        crit_threshold_edit.setValidator(QIntValidator(0, 100, crit_threshold_edit))
        raw_threshold = current_stat_cfg.get("crit_threshold", current_stat_cfg.get("crit_min_threshold"))
        if raw_threshold is not None:
            try:
                crit_threshold_edit.setText(f"{float(raw_threshold):g}")
            except (TypeError, ValueError):
                pass
        def sync_grade_combo_enabled(checked=False):
            grade_combo.setEnabled(not ignore_grade_limit.isChecked())

        ignore_grade_limit.toggled.connect(sync_grade_combo_enabled)
        template_layout.addWidget(stat_box)
        layout.addWidget(template_box)

        effect_box = QGroupBox(tr("其他配置"))
        effect_layout = QVBoxLayout(effect_box)
        effect_layout.setContentsMargins(8, 8, 8, 8)
        effect_layout.setSpacing(5)
        effect_row = QHBoxLayout()
        effect_row.setSpacing(8)
        effect_row.addWidget(QLabel(tr("套装效果：")))
        effect_combo = QComboBox()
        effect_combo.addItem(tr("四件套"), FOUR_PIECE)
        effect_combo.addItem(tr("二件套"), TWO_PIECE)
        effect_combo.addItem(tr("无效果"), NO_EFFECT)
        current_effect = normalize_set_effect_mode(self.set_effect_modes.get(name))
        effect_index = effect_combo.findData(current_effect)
        effect_combo.setCurrentIndex(effect_index if effect_index >= 0 else 0)
        effect_row.addWidget(effect_combo, 1)
        effect_help_btn = QPushButton("?")
        effect_help_btn.setObjectName("btnHelp")
        effect_help_btn.clicked.connect(lambda: self._show_help("套装效果说明", SET_EFFECT_HELP))
        effect_row.addWidget(effect_help_btn)
        effect_layout.addLayout(effect_row)
        crit_row = QHBoxLayout()
        crit_row.setSpacing(8)
        crit_row.addWidget(crit_threshold_label)
        crit_row.addWidget(crit_threshold_edit, 1)
        crit_row.addWidget(QLabel("%"))
        crit_row.addWidget(crit_threshold_help)
        crit_row.addSpacing(12)
        crit_row.addWidget(QLabel(tr("暴击率上限：")))
        crit_cap_edit = QLineEdit()
        current_cap = self.crit_rate_caps.get(name)
        if current_cap is None:
            current_cap = self._weapon_crit_rate_cap(
                self.custom_weapons.get(name, "") or default_weapon
            )
        if current_cap is not None:
            crit_cap_edit.setText(f"{float(current_cap):g}")
        crit_cap_edit.setPlaceholderText(tr("留空不限制"))

        def apply_weapon_cap(text):
            raw = str(text or "").strip()
            if not raw:
                return
            resolved = resolve_optional_priority_choice(weapon_names, raw)
            cap = self._weapon_crit_rate_cap(resolved) if resolved in weapon_names else None
            if cap is not None:
                crit_cap_edit.setText(f"{float(cap):g}")

        weapon_combo.currentTextChanged.connect(apply_weapon_cap)
        crit_row.addWidget(crit_cap_edit, 1)
        crit_row.addWidget(QLabel("%"))
        crit_cap_help = QPushButton("?")
        crit_cap_help.setObjectName("btnHelp")
        crit_cap_help.setFixedSize(24, 24)
        crit_cap_help.clicked.connect(
            lambda: self._show_help("暴击率上限", CRIT_RATE_CAP_HELP)
        )
        crit_row.addWidget(crit_cap_help)
        effect_layout.addLayout(crit_row)
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
            resolved_weapon = resolve_optional_priority_choice(
                weapon_names,
                weapon_value,
                weapon_combo.currentData(),
            )
            selected_weapon = resolved_weapon if resolved_weapon in weapon_names else ""
            self._set_custom_weapon(name, selected_weapon)
            self._set_tape_main_filter(name, selected_main_stats)
            self._set_stat_priority_config(
                name,
                selected_stats,
                selected_blacklist,
                stat_equal.isChecked(),
                ignore_grade_limit.isChecked(),
                grade_combo.currentData(),
                crit_threshold_edit.text().strip(),
                blacklist_zero_weight=blacklist_zero_weight.isChecked(),
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
        self.tape_main_filter_override_roles.clear()
        self.stat_priority_configs.clear()
        self.stat_priority_override_roles.clear()
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
        self.priority_links = normalize_priority_links(self.selected, self.priority_links)
        shift_crossed_priority_boundaries(self.priority_links, index, new_index)
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

    def get_tape_main_filter_overrides(self):
        return {
            name: list(self.tape_main_filters.get(name, []))
            for name in self.selected
            if self.tape_main_filters.get(name)
        }

    def get_crit_rate_cap_overrides(self):
        return {
            name: float(self.crit_rate_caps.get(name))
            for name in self.selected
            if name in self.crit_rate_caps
        }

    def get_crit_priority_mode_overrides(self):
        return {
            name: dict(self.stat_priority_configs.get(name))
            for name in self.selected
            if self.stat_priority_configs.get(name)
        }

    def get_tape_main_filters(self):
        filters = {}
        for name in self.selected:
            effective = (
                self.tape_main_filters.get(name, [])
                if name in self.tape_main_filter_override_roles
                else self._default_tape_main_filter(name)
            )
            if effective:
                filters[name] = list(effective)
        return filters

    def get_custom_weapons(self):
        return {
            name: weapon
            for name in self.selected
            if (weapon := self._effective_weapon_for_role(name))
        }

    def get_crit_rate_caps(self):
        caps = {}
        for name in self.selected:
            cap = self.crit_rate_caps.get(name)
            if cap is None:
                cap = self._weapon_crit_rate_cap(self._effective_weapon_for_role(name))
            if cap is not None:
                caps[name] = float(cap)
        return caps

    def get_crit_priority_modes(self):
        configs = {}
        for name in self.selected:
            default = self._default_substat_priority(name)
            effective = self.stat_priority_configs.get(name)
            if effective:
                configs[name] = dict(effective)
            elif name not in self.stat_priority_override_roles and default:
                configs[name] = {"stats": list(default)}
        return configs

    def get_set_effect_modes(self):
        return {
            name: normalize_set_effect_mode(self.set_effect_modes.get(name))
            for name in self.selected
            if normalize_set_effect_mode(self.set_effect_modes.get(name)) != FOUR_PIECE
        }

    def save_priority_config(self, show_message: bool = True):
        self._write_priority_config(self._priority_config_path())
        if show_message:
            QMessageBox.information(self, tr("保存成功"), tr("当前角色优先级方案已保存，可随时读取该方案。"))

    def save_temporary_priority_config(self):
        self._write_priority_config(temporary_priority_config_path(self._priority_config_path()))

    def _write_priority_config(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "role_default_policy": _ROLE_DEFAULT_POLICY,
            "default_mag_character_ids": sorted(self.default_mag_character_ids),
            "priority_list": self.selected,
            "priority_groups": self.get_priority_groups(),
            "priority_links": normalize_priority_links(self.selected, self.priority_links),
            "custom_sets": self.get_custom_sets(),
            "custom_set_overrides": self.get_custom_sets(),
            "custom_weapons": self.get_custom_weapons(),
            "crit_rate_caps": self.get_crit_rate_caps(),
            "tape_main_filters": {
                name: list(self.tape_main_filters[name])
                for name in self.selected
                if name in self.tape_main_filters
            },
            "tape_main_filter_override_roles": sorted(
                name
                for name in self.selected
                if name in self.tape_main_filter_override_roles
            ),
            "stat_priority_configs": self.get_crit_priority_mode_overrides(),
            "stat_priority_override_roles": sorted(
                name
                for name in self.selected
                if name in self.stat_priority_override_roles
            ),
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
        self.tape_main_filter_override_roles.clear()
        self.stat_priority_configs.clear()
        self.stat_priority_override_roles.clear()
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
            self.tape_main_filters = {}
            self.tape_main_filter_override_roles = {
                role
                for role in data.get("tape_main_filter_override_roles", [])
                if role in self.all_roles
            }
            for role, values in raw_filters.items():
                if role not in self.all_roles or not isinstance(values, list):
                    continue
                filtered = [value for value in values if value in self.tape_main_stats]
                self.tape_main_filters[role] = filtered
                self.tape_main_filter_override_roles.add(role)
            self.stat_priority_configs = {}
            self.stat_priority_override_roles = {
                role
                for role in data.get("stat_priority_override_roles", [])
                if role in self.all_roles
            }
            allowed_stats = set(self.drive_sub_stats)
            for role, cfg_item in data.get("stat_priority_configs", {}).items():
                if role not in self.all_roles or not isinstance(cfg_item, dict):
                    continue
                cfg = persistable_stat_priority_config(cfg_item, allowed_stats=allowed_stats)
                if cfg:
                    self.stat_priority_configs[role] = cfg
                    self.stat_priority_override_roles.add(role)
            self.set_effect_modes = {}
            for role, mode in data.get("set_effect_modes", {}).items():
                normalized = normalize_set_effect_mode(mode)
                if role in self.all_roles and normalized in SET_EFFECT_MODES and normalized != FOUR_PIECE:
                    self.set_effect_modes[role] = normalized
            self._render_grid(self.search.text())
        except Exception as exc:
            QMessageBox.warning(
                self, tr("恢复优先级"), tr("读取优先级配置失败：{error}", error=exc)
            )

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
