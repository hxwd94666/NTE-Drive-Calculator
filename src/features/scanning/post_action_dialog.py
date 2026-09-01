# 构建全量扫描后状态管理配置弹窗。
"""PySide dialog for post-scan discard/lock settings."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr
from src.domain.post_actions import (
    DEFAULT_EXCLUDED_SET_NAMES,
    DEFAULT_EXCLUDED_SHAPE_IDS,
    DEFAULT_PRESERVE_RULE,
    GRADE_ORDER,
    default_post_action_config,
    merge_post_action_config,
    validate_post_action_config,
)
from src.storage.json_store import read_json, write_json
from src.app.theme import themed_style
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.account_settings_service import AccountSettingsService
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.widgets import NoWheelComboBox


ROLE_SCOPE_OPTIONS = ((tr("所有角色"), "all"), (tr("所选角色"), "selected"))
QUALITY_SCOPE_OPTIONS = ((tr("全部"), "all"), (tr("仅金品质"), "gold"), (tr("仅金紫品质"), "gold_purple"))
TYPE_SCOPE_OPTIONS = ((tr("全部"), "all"), (tr("仅驱动"), "drive"), (tr("仅卡带"), "tape"))
STATE_ACTION_OPTIONS = ((tr("跳过"), "skip"), (tr("正常处理"), "normal"))
SUB_MATCH_OPTIONS = ((tr("任意一个"), 1), (tr("任意两个"), 2), (tr("任意三个"), 3), (tr("任意四个"), 4))


def scan_post_action_config_path(user_config_dir: Path) -> Path:
    if user_config_dir is None:
        raise ValueError("user_config_dir is required")
    return Path(user_config_dir) / "scan_post_actions.json"


def load_scan_post_action_config(
    user_config_dir: Path | None,
    *,
    user_database_path: Path | None = None,
) -> dict:
    if user_database_path is not None:
        preferences = AccountSettingsService(user_database_path).load("ui")
        stored = preferences.get("full_scan_post_action_config")
        if isinstance(stored, dict) and stored:
            return merge_post_action_config(stored)
    if user_config_dir is None:
        return default_post_action_config()
    legacy = read_json(scan_post_action_config_path(user_config_dir), default=None)
    config = merge_post_action_config(legacy)
    if user_database_path is not None and isinstance(legacy, dict) and legacy:
        save_scan_post_action_config(
            user_config_dir,
            config,
            user_database_path=user_database_path,
        )
    return config


def save_scan_post_action_config(
    user_config_dir: Path | None,
    config: dict,
    *,
    user_database_path: Path | None = None,
) -> None:
    normalized = merge_post_action_config(config)
    if user_database_path is not None:
        settings = AccountSettingsService(user_database_path)
        preferences = settings.load("ui")
        preferences["full_scan_post_action_config"] = normalized
        settings.save("ui", preferences)
        return
    if user_config_dir is None:
        raise ValueError("user_config_dir is required when account storage is unavailable")
    write_json(scan_post_action_config_path(user_config_dir), normalized, indent=2)


def _set_combo_data(combo: NoWheelComboBox, value: object) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return


def _combo(options, value: object, width: int = 130) -> NoWheelComboBox:
    combo = NoWheelComboBox()
    combo.setSizeAdjustPolicy(NoWheelComboBox.AdjustToContents)
    for label, data in options:
        combo.addItem(label, data)
    _set_combo_data(combo, value)
    combo.setMaximumWidth(width)
    return combo


def _load_drive_shape_options() -> list[tuple[str, int]]:
    with StaticGameDataDao() as static_dao:
        options = [
            (legacy_shape_id(shape["shape_id"]), int(shape["cell_count"]))
            for shape in static_dao.list_shapes()
        ]
    return sorted(options, key=lambda item: (item[1], item[0]))


def _load_set_name_options() -> list[str]:
    with StaticGameDataDao() as static_dao:
        return [str(suit["name_zh"]) for suit in static_dao.list_suits()]


def _load_role_options(user_database_path: Path | None = None) -> list[tuple[int, str, str]]:
    """Return selectable official and custom roles with an optional avatar."""

    asset_catalog = GameUiAssetCatalog(bundled_game_ui_asset_root())
    with StaticGameDataDao() as static_dao:
        options = [
            (
                int(character["character_id"]),
                str(character.get("name_zh") or character["character_id"]),
                str(asset_catalog.character_icon(int(character["character_id"])) or ""),
            )
            for character in static_dao.list_role_template_characters()
        ]
    if user_database_path is not None and Path(user_database_path).is_file():
        with UserDataDao(user_database_path) as user_dao:
            official_ids = {character_id for character_id, _name, _avatar in options}
            options.extend(
                (
                    character_id,
                    str(role.get("name_zh") or character_id),
                    "",
                )
                for role in user_dao.list_custom_characters()
                if (character_id := int(role["character_id"])) not in official_ids
            )
    return sorted(options, key=lambda item: item[0])


def _rule_summary_values(values: list[str], limit: int = 2) -> str:
    values = [str(value) for value in values if str(value)]
    if len(values) <= limit:
        return "、".join(values)
    return "、".join(values[:limit]) + f" 等 {len(values)} 项"


def _preserve_rule_summary(rule: dict) -> str:
    parts = []
    if rule.get("item_type") == "tape" and rule.get("main_stats"):
        parts.append(f"主：{_rule_summary_values(rule['main_stats'])}")
    if rule.get("sub_stats"):
        raw_mode = rule.get("sub_match", "all")
        if raw_mode == "all":
            mode = "任意四个"
        else:
            try:
                mode = {1: "任意一个", 2: "任意两个", 3: "任意三个", 4: "任意四个"}.get(int(raw_mode), "任意一个")
            except (TypeError, ValueError):
                mode = "任意一个"
        parts.append(f"副：{_rule_summary_values(rule['sub_stats'])}（{mode}）")
    if rule.get("required_sub_stats"):
        parts.append(f"必含：{_rule_summary_values(rule['required_sub_stats'])}")
    return "｜".join(parts) or "未设置词条条件"


from src.features.scanning.preserve_rule_editor import PreserveRuleEditor


from src.features.scanning.post_action_scope_dialogs import (
    RoleScopeDialog,
    TypeRangeDialog,
)


class ScanPostActionDialog(QDialog):
    def __init__(
        self,
        parent,
        user_config_dir: Path,
        config_dir: Path,
        *,
        user_database_path: Path | None = None,
        window_title: str = "",
    ):
        super().__init__(parent)
        self.user_config_dir = Path(user_config_dir)
        self.config_dir = Path(config_dir)
        self.user_database_path = user_database_path
        self.setWindowTitle(window_title or tr("全量扫描管理"))
        self.setMinimumWidth(560)
        self.config = load_scan_post_action_config(
            self.user_config_dir,
            user_database_path=self.user_database_path,
        )
        self._widgets = {}
        self._shape_options = _load_drive_shape_options()
        self._set_options = _load_set_name_options()
        self._role_options = _load_role_options(self.user_database_path)
        self._selected_character_ids = list(self.config.get("selected_character_ids", []))
        self._range_values = {}
        self._preserve_rules = copy.deepcopy(self.config.get("preserve_rules", []))
        self._build_ui()

    def _style_toggle_button(self, button: QPushButton, checked: bool) -> None:
        button.setText(tr("开启中") if checked else tr("关闭中"))
        if checked:
            button.setStyleSheet(
                "QPushButton{background:#238636;color:#fff;border:1px solid #2ea043;"
                "border-radius:12px;padding:4px 14px;font-weight:700;}"
                "QPushButton:hover{background:#2ea043;}"
            )
        else:
            button.setStyleSheet(
                "QPushButton{background:#da3633;color:#fff;border:1px solid #f85149;"
                "border-radius:12px;padding:4px 14px;font-weight:700;}"
                "QPushButton:hover{background:#f85149;}"
            )

    def _make_toggle_button(self, checked: bool) -> QPushButton:
        button = QPushButton()
        button.setCheckable(True)
        button.setChecked(checked)
        button.setFixedWidth(72)
        self._style_toggle_button(button, checked)
        button.toggled.connect(lambda value, b=button: self._style_toggle_button(b, value))
        return button

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        footer = QHBoxLayout()
        self.hmt_region_check = QCheckBox(tr("港澳台服"))
        self.hmt_region_check.setChecked(self.config.get("server_region") == "hmt")
        self.hmt_region_check.setToolTip(tr("开启后，扫描后弃置/锁定使用港澳台服的十字键左右直控方式。"))
        footer.addWidget(self.hmt_region_check)
        footer.addStretch()
        self._scoring_footer = footer

        self._main_tabs = QTabWidget()
        self._main_tabs.addTab(self._build_scoring_page(), tr("评分处理"))
        self._main_tabs.addTab(self._build_preserve_rules_page(), tr("预留规则"))
        root.addWidget(self._main_tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_scoring_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)
        modules = QHBoxLayout()
        modules.setSpacing(14)
        modules.addWidget(self._build_module_panel("discard", tr("弃置模块"), tr("最高评分低于等于")), 1)
        modules.addWidget(self._build_module_panel("lock", tr("锁定模块"), tr("最高评分高于等于")), 1)
        root.addLayout(modules)
        root.addLayout(self._scoring_footer)
        root.addStretch()
        return page

    def _build_preserve_rules_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(tr("命中规则的装备将被保留或直接锁定"))
        title.setStyleSheet("color:#8b949e")
        add_button = QPushButton(tr("新增规则"))
        add_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:5px 12px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        add_button.clicked.connect(self._add_preserve_rule)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(add_button)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self._preserve_rules_layout = QVBoxLayout(content)
        self._preserve_rules_layout.setContentsMargins(0, 2, 0, 2)
        self._preserve_rules_layout.setSpacing(8)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self._render_preserve_rules()
        return page

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_preserve_rules(self) -> None:
        self._clear_layout(self._preserve_rules_layout)
        if not self._preserve_rules:
            empty = QLabel(tr("暂未添加预留规则"))
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#8b949e;padding:48px 0")
            self._preserve_rules_layout.addWidget(empty)
        else:
            for index, rule in enumerate(self._preserve_rules):
                self._preserve_rules_layout.addWidget(self._build_preserve_rule_row(index, rule))
        self._preserve_rules_layout.addStretch()

    def _build_preserve_rule_row(self, index: int, rule: dict) -> QWidget:
        row = QFrame()
        row.setObjectName("preserveRuleRow")
        row.setStyleSheet(themed_style("QFrame#preserveRuleRow{background:#161b22;border:1px solid #30363d;border-radius:6px;}"))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(10)
        enabled = QCheckBox()
        enabled.setToolTip(tr("启用此规则"))
        enabled.setChecked(bool(rule.get("enabled", True)))
        enabled.toggled.connect(lambda checked, current=index: self._set_preserve_rule_enabled(current, checked))
        layout.addWidget(enabled)
        details = QVBoxLayout()
        details.setSpacing(3)
        name = QLabel(str(rule.get("name") or tr("未命名规则")))
        name.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
        summary = QLabel(_preserve_rule_summary(rule))
        summary.setStyleSheet(themed_style("color:#8b949e"))
        summary.setWordWrap(True)
        details.addWidget(name)
        details.addWidget(summary)
        layout.addLayout(details, 1)
        edit_button = QPushButton(tr("编辑"))
        copy_button = QPushButton(tr("复制"))
        delete_button = QPushButton(tr("删除"))
        compact_height = edit_button.sizeHint().height()
        item_type = "卡带" if rule.get("item_type") == "tape" else "驱动"
        type_badge = QLabel(item_type)
        type_badge.setFixedHeight(compact_height)
        type_badge.setStyleSheet(themed_style(
            "color:#58a6ff;border:1px solid #1f6feb;border-radius:6px;padding:1px 7px"
        ))
        action_label = "直接锁定" if rule.get("action") == "lock" else "仅保留"
        action_badge = QLabel(action_label)
        action_badge.setFixedHeight(compact_height)
        action_badge.setStyleSheet(themed_style(
            "color:#3fb950;border:1px solid #238636;border-radius:6px;padding:1px 7px"
            if rule.get("action") != "lock"
            else "color:#d2a8ff;border:1px solid #8957e5;border-radius:6px;padding:1px 7px"
        ))
        layout.addWidget(type_badge, 0, Qt.AlignVCenter)
        layout.addWidget(action_badge, 0, Qt.AlignVCenter)
        edit_button.clicked.connect(lambda _checked=False, current=index: self._edit_preserve_rule(current))
        copy_button.clicked.connect(lambda _checked=False, current=index: self._duplicate_preserve_rule(current))
        delete_button.setStyleSheet("QPushButton{color:#f85149}")
        delete_button.clicked.connect(lambda _checked=False, current=index: self._delete_preserve_rule(current))
        layout.addWidget(edit_button)
        layout.addWidget(copy_button)
        layout.addWidget(delete_button)
        return row

    def _set_preserve_rule_enabled(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self._preserve_rules):
            self._preserve_rules[index]["enabled"] = enabled

    def _add_preserve_rule(self) -> None:
        editor = PreserveRuleEditor(
            self,
            DEFAULT_PRESERVE_RULE,
            self._shape_options,
            self._set_options,
            self.config_dir,
        )
        if editor.exec() != QDialog.Accepted:
            return
        rule = editor.result_rule()
        if rule is not None:
            self._preserve_rules.append(rule)
            self._render_preserve_rules()

    def _edit_preserve_rule(self, index: int) -> None:
        if not 0 <= index < len(self._preserve_rules):
            return
        editor = PreserveRuleEditor(
            self,
            self._preserve_rules[index],
            self._shape_options,
            self._set_options,
            self.config_dir,
        )
        if editor.exec() != QDialog.Accepted:
            return
        rule = editor.result_rule()
        if rule is not None:
            self._preserve_rules[index] = rule
            self._render_preserve_rules()

    def _duplicate_preserve_rule(self, index: int) -> None:
        if not 0 <= index < len(self._preserve_rules):
            return
        duplicate = copy.deepcopy(self._preserve_rules[index])
        duplicate["name"] = f"{duplicate.get('name') or '未命名规则'} 副本"
        self._preserve_rules.insert(index + 1, duplicate)
        self._render_preserve_rules()

    def _delete_preserve_rule(self, index: int) -> None:
        if not 0 <= index < len(self._preserve_rules):
            return
        if QMessageBox.question(self, tr("删除规则"), tr("确定删除这条预留规则？")) != QMessageBox.Yes:
            return
        del self._preserve_rules[index]
        self._render_preserve_rules()

    def _module_help_text(self, key: str) -> str:
        if key == "discard":
            action = "· 评分不高于设定等级时弃置\n· 不满足规则时取消弃置"
        else:
            action = "· 评分达到设定等级时锁定\n· 不满足规则时解除锁定"
        return (
            f"{action}\n"
            "· 角色范围：决定按哪些角色评分\n"
            "· 品质、类别和类型：决定处理哪些装备\n"
            "· 选择跳过时，会保留装备当前状态"
        )

    def _show_module_help(self, key: str, title: str) -> None:
        QMessageBox.information(self, tr("{title}说明", title=title), self._module_help_text(key))

    def _build_module_panel(self, key: str, title: str, grade_label: str) -> QWidget:
        module = self.config[key]
        panel = QWidget()
        panel.setObjectName("postActionPanel")
        panel.setStyleSheet(
            themed_style("QWidget#postActionPanel{background:#161b22;border:1px solid #30363d;border-radius:8px;}")
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)
        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:14px;font-weight:700;color:#58a6ff")
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setFixedWidth(28)
        help_button.clicked.connect(lambda _checked=False, k=key, t=title: self._show_module_help(k, t))
        enabled = self._make_toggle_button(bool(module.get("enabled")))
        header.addWidget(title_label)
        header.addWidget(help_button)
        header.addStretch()
        header.addWidget(enabled)
        outer.addLayout(header)

        form = QFormLayout()
        grade_combo = _combo([(grade, grade) for grade in GRADE_ORDER], module.get("grade"), 100)
        role_scope = _combo(ROLE_SCOPE_OPTIONS, module.get("role_scope"), 110)
        quality_scope = _combo(QUALITY_SCOPE_OPTIONS, module.get("quality_scope"), 130)
        type_scope = _combo(TYPE_SCOPE_OPTIONS, module.get("type_scope"), 130)
        on_locked = _combo(STATE_ACTION_OPTIONS, module.get("on_locked"), 130)
        on_discarded = _combo(STATE_ACTION_OPTIONS, module.get("on_discarded"), 130)
        self._range_values[key] = {
            "shape_ids": self._selected_or_default(module.get("shape_ids"), self._default_shape_ids()),
            "set_names": self._selected_or_default(module.get("set_names"), self._default_set_names()),
        }
        type_range_row = QWidget()
        type_range_layout = QHBoxLayout(type_range_row)
        type_range_layout.setContentsMargins(0, 0, 0, 0)
        type_range_layout.setSpacing(8)
        type_range_summary = QLabel(self._type_range_summary(key))
        type_range_button = QPushButton(tr("选择"))
        type_range_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:3px 12px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        type_range_button.clicked.connect(lambda _checked=False, module_key=key: self._open_type_range_dialog(module_key))
        type_range_layout.addWidget(type_range_summary, 1)
        type_range_layout.addWidget(type_range_button)
        role_scope_row = QWidget()
        role_scope_layout = QHBoxLayout(role_scope_row)
        role_scope_layout.setContentsMargins(0, 0, 0, 0)
        role_scope_layout.setSpacing(8)
        role_scope_summary = QLabel()
        role_scope_button = QPushButton(tr("选择"))
        role_scope_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:3px 10px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        role_scope_button.clicked.connect(self._open_role_scope_dialog)
        role_scope_layout.addWidget(role_scope)
        role_scope_layout.addWidget(role_scope_summary, 1)
        role_scope_layout.addWidget(role_scope_button)
        form.addRow(grade_label, grade_combo)
        form.addRow(tr("角色范围"), role_scope_row)
        form.addRow(tr("品质范围"), quality_scope)
        form.addRow(tr("处理类别"), type_scope)
        form.addRow(tr("类型范围"), type_range_row)
        form.addRow(tr("遇到锁定"), on_locked)
        form.addRow(tr("遇到弃置"), on_discarded)
        outer.addLayout(form)
        self._widgets[key] = {
            "enabled": enabled,
            "grade": grade_combo,
            "role_scope": role_scope,
            "role_scope_summary": role_scope_summary,
            "role_scope_button": role_scope_button,
            "quality_scope": quality_scope,
            "type_scope": type_scope,
            "type_range_summary": type_range_summary,
            "on_locked": on_locked,
            "on_discarded": on_discarded,
        }
        role_scope.currentIndexChanged.connect(
            lambda _index, module_key=key: self._update_role_scope_summary(module_key)
        )
        self._update_role_scope_summary(key)
        return panel

    def _update_role_scope_summary(self, key: str) -> None:
        widgets = self._widgets.get(key, {})
        combo = widgets.get("role_scope")
        summary = widgets.get("role_scope_summary")
        button = widgets.get("role_scope_button")
        if combo is None or summary is None or button is None:
            return
        uses_selected = combo.currentData() == "selected"
        count = len(self._selected_character_ids)
        summary.setText(tr("已选{count}名", count=count) if uses_selected else "")
        summary.setStyleSheet("color:#58a6ff" if count else "color:#f85149")
        button.setVisible(uses_selected)

    def _open_role_scope_dialog(self) -> None:
        dialog = RoleScopeDialog(
            self,
            self._role_options,
            self._selected_character_ids,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._selected_character_ids = dialog.selected_character_ids()
        for key in self._widgets:
            self._update_role_scope_summary(key)

    def _default_shape_ids(self) -> list[str]:
        return [shape_id for shape_id, _area in self._shape_options if shape_id not in DEFAULT_EXCLUDED_SHAPE_IDS]

    def _default_set_names(self) -> list[str]:
        return [set_name for set_name in self._set_options if set_name not in DEFAULT_EXCLUDED_SET_NAMES]

    def _selected_or_default(self, values: list[str] | None, defaults: list[str]) -> list[str]:
        if values is None:
            return list(defaults)
        return [str(value) for value in values if str(value)]

    def _type_range_summary(self, key: str) -> str:
        values = self._range_values[key]
        return tr(
            "驱动 {shapes}/{shape_total}，卡带 {sets}/{set_total}",
            shapes=len(values["shape_ids"]), shape_total=len(self._shape_options),
            sets=len(values["set_names"]), set_total=len(self._set_options),
        )

    def _update_type_range_summary(self, key: str) -> None:
        label = self._widgets[key].get("type_range_summary")
        if label is not None:
            label.setText(self._type_range_summary(key))

    def _open_type_range_dialog(self, key: str) -> None:
        values = self._range_values[key]
        dialog = TypeRangeDialog(
            self,
            self._shape_options,
            self._set_options,
            values["shape_ids"],
            values["set_names"],
        )
        if dialog.exec() != QDialog.Accepted:
            return
        shape_ids, set_names = dialog.selected_values()
        self._range_values[key] = {"shape_ids": shape_ids, "set_names": set_names}
        self._update_type_range_summary(key)

    def _collect_config(self) -> dict:
        config = default_post_action_config()
        config["server_region"] = "hmt" if self.hmt_region_check.isChecked() else "default"
        config["selected_character_ids"] = list(self._selected_character_ids)
        for key, widgets in self._widgets.items():
            module = config[key]
            module["enabled"] = widgets["enabled"].isChecked()
            for field in ("grade", "role_scope", "quality_scope", "type_scope", "on_locked", "on_discarded"):
                module[field] = widgets[field].currentData()
            module["shape_ids"] = list(self._range_values[key]["shape_ids"])
            module["set_names"] = list(self._range_values[key]["set_names"])
        config["preserve_rules"] = copy.deepcopy(self._preserve_rules)
        return merge_post_action_config(config)

    def _save(self) -> None:
        config = self._collect_config()
        error = validate_post_action_config(config)
        if error:
            QMessageBox.warning(self, tr("配置无效"), error)
            return
        save_scan_post_action_config(
            self.user_config_dir,
            config,
            user_database_path=self.user_database_path,
        )
        self.accept()


def show_scan_post_action_dialog(
    parent,
    user_config_dir: Path,
    config_dir: Path,
    *,
    user_database_path: Path | None = None,
    window_title: str = "",
) -> bool:
    dialog = ScanPostActionDialog(
        parent,
        user_config_dir,
        config_dir,
        user_database_path=user_database_path,
        window_title=window_title,
    )
    return dialog.exec() == QDialog.Accepted
