# 构建全量扫描后状态管理配置弹窗。
"""PySide dialog for post-scan discard/lock settings."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.widgets import NoWheelComboBox, match_pinyin


ROLE_SCOPE_OPTIONS = (("所有角色", "all"), ("所选角色", "selected"))
QUALITY_SCOPE_OPTIONS = (("全部", "all"), ("仅金品质", "gold"), ("仅金紫品质", "gold_purple"))
TYPE_SCOPE_OPTIONS = (("全部", "all"), ("仅驱动", "drive"), ("仅卡带", "tape"))
STATE_ACTION_OPTIONS = (("跳过", "skip"), ("正常处理", "normal"))
SUB_MATCH_OPTIONS = (("任意一个", 1), ("任意两个", 2), ("任意三个", 3), ("任意四个", 4))


def scan_post_action_config_path(user_config_dir: Path) -> Path:
    if user_config_dir is None:
        raise ValueError("user_config_dir is required")
    return Path(user_config_dir) / "scan_post_actions.json"


def load_scan_post_action_config(user_config_dir: Path) -> dict:
    if user_config_dir is None:
        return default_post_action_config()
    path = scan_post_action_config_path(user_config_dir)
    return merge_post_action_config(read_json(path, default=default_post_action_config()))


def save_scan_post_action_config(user_config_dir: Path, config: dict) -> None:
    write_json(scan_post_action_config_path(user_config_dir), merge_post_action_config(config), indent=2)


def _set_combo_data(combo: NoWheelComboBox, value: object) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return


def _combo(options, value: object, width: int = 130) -> NoWheelComboBox:
    combo = NoWheelComboBox()
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


def _load_role_options() -> list[tuple[int, str, str]]:
    """Return one official ID, display name and avatar per logical role."""

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
    return sorted(options, key=lambda item: item[0])


def _button_style(checked: bool) -> str:
    if checked:
        return themed_style("QPushButton{border:2px solid #2f81f7;background:#10243f;color:#f0f6fc;border-radius:6px;padding:4px}")
    return themed_style("QPushButton{border:1px solid #30363d;background:#161b22;color:#c9d1d9;border-radius:6px;padding:4px}")


class TypeRangeDialog(QDialog):
    def __init__(
        self,
        parent,
        shape_options: list[tuple[str, int]],
        set_options: list[str],
        selected_shape_ids: list[str],
        selected_set_names: list[str],
    ):
        super().__init__(parent)
        self.setWindowTitle("选择类型范围")
        self.setMinimumSize(760, 560)
        self.shape_options = shape_options
        self.set_options = set_options
        self.shape_buttons: list[tuple[QPushButton, str]] = []
        self.set_checks: list[tuple[QCheckBox, str]] = []

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(self._build_shape_section(set(selected_shape_ids)))
        root.addWidget(self._build_set_section(set(selected_set_names)), 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_shape_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("驱动形状"))
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_shapes(True))
        header.addStretch()
        header.addWidget(select_all)
        layout.addLayout(header)

        grouped: dict[int, list[str]] = {2: [], 3: [], 4: []}
        for shape_id, area in self.shape_options:
            grouped.setdefault(area, []).append(shape_id)

        for area in sorted(grouped):
            shape_ids = grouped.get(area, [])
            if not shape_ids:
                continue
            row = QHBoxLayout()
            row.setSpacing(8)
            title = QLabel(f"{area}型")
            title.setFixedWidth(36)
            row.addWidget(title)
            for shape_id in shape_ids:
                button = QPushButton(shape_id)
                button.setCheckable(True)
                button.setChecked(shape_id in selected)
                button.setToolTip(shape_id)
                button.setMinimumSize(84, 54)
                pixmap = warehouse_shape_pixmap(shape_id, "Gold")
                if not pixmap.isNull():
                    button.setIcon(QIcon(pixmap))
                    button.setIconSize(QSize(32, 32))
                button.setStyleSheet(_button_style(button.isChecked()))
                button.toggled.connect(lambda checked, b=button: b.setStyleSheet(_button_style(checked)))
                self.shape_buttons.append((button, shape_id))
                row.addWidget(button)
            row.addStretch()
            layout.addLayout(row)
        return section

    def _build_set_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("卡带套装"))
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_sets(True))
        header.addStretch()
        header.addWidget(select_all)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for index, set_name in enumerate(self.set_options):
            checkbox = QCheckBox(set_name)
            checkbox.setChecked(set_name in selected)
            self.set_checks.append((checkbox, set_name))
            grid.addWidget(checkbox, index // 2, index % 2)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return section

    def _set_all_shapes(self, checked: bool) -> None:
        for button, _shape_id in self.shape_buttons:
            button.setChecked(checked)

    def _set_all_sets(self, checked: bool) -> None:
        for checkbox, _set_name in self.set_checks:
            checkbox.setChecked(checked)

    def selected_values(self) -> tuple[list[str], list[str]]:
        shape_ids = [shape_id for button, shape_id in self.shape_buttons if button.isChecked()]
        set_names = [set_name for checkbox, set_name in self.set_checks if checkbox.isChecked()]
        return shape_ids, set_names


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


class RoleScopeDialog(QDialog):
    """Select the roles used only by discard/lock scoring."""

    def __init__(
        self,
        parent,
        role_options: list[tuple[int, str, str]],
        selected_character_ids: list[int],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择弃置/锁定评估角色")
        self.setMinimumSize(620, 500)
        self.resize(700, 620)
        self._role_options = list(role_options)
        selected_ids = {int(value) for value in selected_character_ids}

        root = QVBoxLayout(self)
        root.setSpacing(10)
        description = QLabel("这些角色只用于本次弃置/锁定评分，不会改变计算页面的角色选择或优先级。")
        description.setWordWrap(True)
        description.setStyleSheet("color:#8b949e")
        root.addWidget(description)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索角色（支持拼音）")
        self.search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.search_edit)

        toolbar = QHBoxLayout()
        select_all = QPushButton("全选")
        clear_all = QPushButton("清空")
        select_all.clicked.connect(lambda: self._set_visible_items_checked(True))
        clear_all.clicked.connect(lambda: self._set_visible_items_checked(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(clear_all)
        toolbar.addStretch()
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color:#58a6ff;font-weight:700")
        toolbar.addWidget(self.count_label)
        root.addLayout(toolbar)

        self.role_scroll = QScrollArea()
        self.role_scroll.setWidgetResizable(True)
        self.role_scroll.setFrameShape(QFrame.NoFrame)
        self.role_scroll.setMinimumHeight(300)
        self.role_grid_widget = QWidget()
        self.role_grid = QGridLayout(self.role_grid_widget)
        self.role_grid.setContentsMargins(4, 4, 4, 4)
        self.role_grid.setHorizontalSpacing(8)
        self.role_grid.setVerticalSpacing(8)
        self.role_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.role_cards: list[tuple[QToolButton, int, str]] = []
        for character_id, role_name, avatar_path in self._role_options:
            # Bind the parent before the card is ever shown.  A parentless
            # widget briefly becomes a top-level window on Windows, which
            # previously caused a rapid flash while opening this dialog.
            card = QToolButton(self.role_grid_widget)
            card.setCheckable(True)
            card.setChecked(character_id in selected_ids)
            card.setText(role_name)
            card.setToolTip(role_name)
            card.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            card.setIconSize(QSize(76, 76))
            card.setFixedSize(116, 116)
            if avatar_path:
                card.setIcon(QIcon(avatar_path))
            card.setStyleSheet(
                "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:8px;padding:6px;font-size:12px;font-weight:700;}"
                "QToolButton:hover{border-color:#58a6ff;background:#1f6feb22;}"
                "QToolButton:checked{border:2px solid #58a6ff;background:#1f6feb44;color:#fff;}"
            )
            card.toggled.connect(self._update_count)
            self.role_cards.append((card, character_id, role_name))
        self._reflow_cards()
        self.role_scroll.setWidget(self.role_grid_widget)
        root.addWidget(self.role_scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_count()

    def _apply_filter(self, text: str) -> None:
        self._reflow_cards(str(text or "").strip())

    def _reflow_cards(self, keyword: str = "") -> None:
        while self.role_grid.count():
            self.role_grid.takeAt(0)
        visible_cards = [
            card
            for card in self.role_cards
            if not keyword or match_pinyin(card[2], keyword)
        ]
        for card, _character_id, _role_name in self.role_cards:
            card.setVisible(False)
        for index, (card, _character_id, _role_name) in enumerate(visible_cards):
            self.role_grid.addWidget(card, index // 5, index % 5)
            card.setVisible(True)

    def _set_visible_items_checked(self, checked: bool) -> None:
        for card, _character_id, _role_name in self.role_cards:
            if not card.isHidden():
                card.setChecked(checked)
        self._update_count()

    def _update_count(self, _checked: bool | None = None) -> None:
        self.count_label.setText(f"已选{len(self.selected_character_ids())}名")

    def selected_character_ids(self) -> list[int]:
        return [
            character_id
            for card, character_id, _role_name in self.role_cards
            if card.isChecked()
        ]


class ScanPostActionDialog(QDialog):
    def __init__(
        self,
        parent,
        user_config_dir: Path,
        config_dir: Path,
        *,
        window_title: str = "全量扫描管理",
    ):
        super().__init__(parent)
        self.user_config_dir = Path(user_config_dir)
        self.config_dir = Path(config_dir)
        self.setWindowTitle(window_title)
        self.setMinimumWidth(560)
        self.config = load_scan_post_action_config(self.user_config_dir)
        self._widgets = {}
        self._shape_options = _load_drive_shape_options()
        self._set_options = _load_set_name_options()
        self._role_options = _load_role_options()
        self._selected_character_ids = list(self.config.get("selected_character_ids", []))
        self._range_values = {}
        self._preserve_rules = copy.deepcopy(self.config.get("preserve_rules", []))
        self._build_ui()

    def _style_toggle_button(self, button: QPushButton, checked: bool) -> None:
        button.setText("开启" if checked else "关闭")
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
        self.hmt_region_check = QCheckBox("港澳台服")
        self.hmt_region_check.setChecked(self.config.get("server_region") == "hmt")
        self.hmt_region_check.setToolTip("开启后，扫描后弃置/锁定使用港澳台服的十字键左右直控方式。")
        footer.addWidget(self.hmt_region_check)
        footer.addStretch()
        self._scoring_footer = footer

        self._main_tabs = QTabWidget()
        self._main_tabs.addTab(self._build_scoring_page(), "评分处理")
        self._main_tabs.addTab(self._build_preserve_rules_page(), "预留规则")
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
        modules.addWidget(self._build_module_panel("discard", "弃置模块", "最高评分低于等于"), 1)
        modules.addWidget(self._build_module_panel("lock", "锁定模块", "最高评分高于等于"), 1)
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
        title = QLabel("命中规则的装备将被保留或直接锁定")
        title.setStyleSheet("color:#8b949e")
        add_button = QPushButton("新增规则")
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
            empty = QLabel("暂未添加预留规则")
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
        enabled.setToolTip("启用此规则")
        enabled.setChecked(bool(rule.get("enabled", True)))
        enabled.toggled.connect(lambda checked, current=index: self._set_preserve_rule_enabled(current, checked))
        layout.addWidget(enabled)
        details = QVBoxLayout()
        details.setSpacing(3)
        name = QLabel(str(rule.get("name") or "未命名规则"))
        name.setStyleSheet("font-weight:700;color:#c9d1d9")
        summary = QLabel(_preserve_rule_summary(rule))
        summary.setStyleSheet("color:#8b949e")
        summary.setWordWrap(True)
        details.addWidget(name)
        details.addWidget(summary)
        layout.addLayout(details, 1)
        edit_button = QPushButton("编辑")
        copy_button = QPushButton("复制")
        delete_button = QPushButton("删除")
        compact_height = edit_button.sizeHint().height()
        item_type = "卡带" if rule.get("item_type") == "tape" else "驱动"
        type_badge = QLabel(item_type)
        type_badge.setFixedHeight(compact_height)
        type_badge.setStyleSheet("color:#58a6ff;border:1px solid #1f6feb;border-radius:6px;padding:1px 7px")
        action_label = "直接锁定" if rule.get("action") == "lock" else "仅保留"
        action_badge = QLabel(action_label)
        action_badge.setFixedHeight(compact_height)
        action_badge.setStyleSheet(
            "color:#3fb950;border:1px solid #238636;border-radius:6px;padding:1px 7px"
            if rule.get("action") != "lock"
            else "color:#d2a8ff;border:1px solid #8957e5;border-radius:6px;padding:1px 7px"
        )
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
        if QMessageBox.question(self, "删除规则", "确定删除这条预留规则？") != QMessageBox.Yes:
            return
        del self._preserve_rules[index]
        self._render_preserve_rules()

    def _module_help_text(self, key: str) -> str:
        if key == "discard":
            threshold = "最高评分低于等于阈值：适用角色里的最高评分达到该等级或更低时弃置。"
            result = "命中后目标状态是弃置；未命中但当前已弃置时，会取消弃置。"
        else:
            threshold = "最高评分高于等于阈值：适用角色里的最高评分达到该等级或更高时锁定。"
            result = "命中后目标状态是锁定；未命中但当前已锁定时，会取消锁定。"
        return (
            f"{threshold}\n"
            f"{result}\n\n"
            "角色范围：按所有角色或在本管理界面指定的角色评分。\n"
            "品质范围：限制品质，范围外不改状态。\n"
            "处理类别：可只处理驱动或卡带。\n"
            "类型范围：驱动按形状过滤，卡带按套装过滤。\n"
            "遇到锁定/弃置：跳过表示保留现状；正常处理表示按本模块结果改成目标状态。"
        )

    def _show_module_help(self, key: str, title: str) -> None:
        QMessageBox.information(self, f"{title}说明", self._module_help_text(key))

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
        type_range_button = QPushButton("选择")
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
        role_scope_button = QPushButton("选择")
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
        form.addRow("角色范围", role_scope_row)
        form.addRow("品质范围", quality_scope)
        form.addRow("处理类别", type_scope)
        form.addRow("类型范围", type_range_row)
        form.addRow("遇到锁定", on_locked)
        form.addRow("遇到弃置", on_discarded)
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
        summary.setText(f"已选{count}名" if uses_selected else "")
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
        return f"驱动 {len(values['shape_ids'])}/{len(self._shape_options)}，卡带 {len(values['set_names'])}/{len(self._set_options)}"

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
            QMessageBox.warning(self, "配置无效", error)
            return
        save_scan_post_action_config(self.user_config_dir, config)
        self.accept()


def show_scan_post_action_dialog(
    parent,
    user_config_dir: Path,
    config_dir: Path,
    *,
    window_title: str = "全量扫描管理",
) -> bool:
    dialog = ScanPostActionDialog(
        parent,
        user_config_dir,
        config_dir,
        window_title=window_title,
    )
    return dialog.exec() == QDialog.Accepted
