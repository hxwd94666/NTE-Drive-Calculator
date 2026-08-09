# 编辑扫描后锁定与弃置流程使用的单条保留规则。
"""PySide dialog for post-scan discard/lock settings."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.post_actions import (
    DEFAULT_EXCLUDED_SET_NAMES,
    DEFAULT_EXCLUDED_SHAPE_IDS,
    DEFAULT_PRESERVE_RULE,
    default_post_action_config,
    merge_post_action_config,
)
from src.domain.stat_catalog import StatCatalog
from src.storage.json_store import read_json, write_json
from src.app.theme import themed_style
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.widgets import NoWheelComboBox


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


class PreserveRuleEditor(QDialog):
    """编辑单条预留规则，避免在规则列表中展开复杂多选控件。"""

    def __init__(
        self,
        parent,
        rule: dict | None,
        shape_options: list[tuple[str, int]],
        set_options: list[str],
        config_dir: Path,
    ):
        super().__init__(parent)
        self.setWindowTitle("预留规则")
        self.setMinimumSize(700, 610)
        self.rule = copy.deepcopy(DEFAULT_PRESERVE_RULE)
        if isinstance(rule, dict):
            self.rule.update(rule)
        self.shape_options = shape_options
        self.set_options = set_options
        self._item_type = self.rule.get("item_type", "tape")
        self._action = self.rule.get("action", "keep")
        self._range_values = {
            "shape_ids": self.rule.get("shape_ids"),
            "set_names": self.rule.get("set_names"),
        }
        catalog = StatCatalog.from_config_dir(config_dir)
        self._main_stat_options = catalog.tape_main_stat_pool()
        self._sub_stat_options = catalog.tape_sub_stat_pool()
        self._result_rule = None
        self._build_ui()

    def _build_segment(self, options, current, on_change) -> tuple[QWidget, dict[str, QPushButton]]:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        buttons = {}
        for label, value in options:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(value == current)
            button.setStyleSheet(_button_style(value == current))
            button.clicked.connect(lambda _checked=False, selected=value: on_change(selected))
            layout.addWidget(button)
            buttons[value] = button
        layout.addStretch()
        return widget, buttons

    def _set_segment_value(self, buttons: dict[str, QPushButton], current: str) -> None:
        for value, button in buttons.items():
            button.setChecked(value == current)
            button.setStyleSheet(_button_style(value == current))

    @staticmethod
    def _selected_stats(widget: QListWidget) -> list[str]:
        return [item.text() for item in widget.selectedItems()]

    @staticmethod
    def _set_selected_stats(widget: QListWidget, stats: list[str]) -> None:
        selected = set(stats)
        for index in range(widget.count()):
            widget.item(index).setSelected(widget.item(index).text() in selected)

    def _make_stat_list(self, options: list[str], selected: list[str], height: int) -> QListWidget:
        widget = QListWidget()
        widget.setSelectionMode(QAbstractItemView.MultiSelection)
        widget.setMaximumHeight(height)
        for stat in options:
            item = QListWidgetItem(stat)
            widget.addItem(item)
            item.setSelected(stat in selected)
        return widget

    def _refresh_sub_stat_layout(self) -> None:
        """Keep tape's two columns compact and give drive's list a readable grid."""
        for widget, is_drive in (
            (self.tape_sub_stat_list, False),
            (self.drive_sub_stat_list, True),
            (self.drive_required_sub_stat_list, True),
        ):
            self._configure_sub_stat_list(widget, is_drive)
        self._refresh_drive_grid_item_sizes()

    def _refresh_drive_grid_item_sizes(self) -> None:
        """Fit exactly four readable drive stat tiles in each visible row."""
        for widget in (self.drive_sub_stat_list, self.drive_required_sub_stat_list):
            viewport_width = widget.viewport().width()
            if viewport_width <= 0:
                continue
            # Reserve the viewport edge and a possible vertical scrollbar;
            # otherwise Qt may reflow the fourth tile onto the next row.
            cell_width = max(144, (viewport_width - 16) // 4)
            item_width = max(132, cell_width - 8)
            widget.setGridSize(QSize(cell_width, 52))
            for index in range(widget.count()):
                widget.item(index).setSizeHint(QSize(item_width, 44))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._refresh_drive_grid_item_sizes)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._refresh_drive_grid_item_sizes)

    @staticmethod
    def _configure_sub_stat_list(widget: QListWidget, is_drive: bool) -> None:
        widget.setViewMode(QListView.IconMode if is_drive else QListView.ListMode)
        widget.setFlow(QListView.LeftToRight if is_drive else QListView.TopToBottom)
        widget.setWrapping(is_drive)
        widget.setResizeMode(QListView.Adjust if is_drive else QListView.Fixed)
        widget.setMovement(QListView.Static)
        widget.setUniformItemSizes(is_drive)
        widget.setSpacing(10 if is_drive else 0)
        if is_drive:
            widget.setGridSize(QSize(200, 44))
            widget.setMinimumHeight(160)
            widget.setMaximumHeight(16777215)
            widget.setWordWrap(True)
            widget.setTextElideMode(Qt.ElideNone)
            # IconMode paints each item using its own size hint.  gridSize
            # only controls the distance between cells, so set both values.
            for index in range(widget.count()):
                widget.item(index).setSizeHint(QSize(200, 44))
            widget.setStyleSheet(themed_style(
                "QListWidget{background:transparent;border:none;}"
                "QListWidget::item{border:1px solid #30363d;border-radius:5px;"
                "padding:5px 8px;margin:1px;background:#161b22;color:#c9d1d9;}"
                "QListWidget::item:hover{border-color:#58a6ff;color:#f0f6fc;}"
                "QListWidget::item:selected{border:2px solid #58a6ff;background:#10243f;color:#f0f6fc;}"
                "QListWidget::item:disabled{border-color:#21262d;background:#0d1117;color:#6e7681;}"
            ))
        else:
            widget.setGridSize(QSize())
            widget.setMinimumHeight(0)
            widget.setMaximumHeight(132)
            widget.setWordWrap(False)
            widget.setTextElideMode(Qt.ElideRight)
            widget.setStyleSheet("")

    def _active_sub_match_widgets(self) -> tuple[NoWheelComboBox, QListWidget, QLabel]:
        if self._item_type == "tape":
            return self.tape_sub_match_combo, self.tape_sub_stat_list, self.tape_sub_match_hint
        return self.drive_sub_match_combo, self.drive_sub_stat_list, self.drive_sub_match_hint

    def _active_required_sub_stat_list(self) -> QListWidget:
        return self.tape_required_sub_stat_list if self._item_type == "tape" else self.drive_required_sub_stat_list

    def _sync_required_sub_stat_choices(self) -> None:
        """Required stats are a strict subset of the active match pool."""
        _combo, sub_stat_list, _hint = self._active_sub_match_widgets()
        selected_sub_stats = set(self._selected_stats(sub_stat_list))
        required_list = self._active_required_sub_stat_list()
        previous_blocked = required_list.blockSignals(True)
        try:
            for index in range(required_list.count()):
                item = required_list.item(index)
                allowed = item.text() in selected_sub_stats
                item.setFlags(
                    item.flags() | Qt.ItemIsEnabled
                    if allowed
                    else item.flags() & ~Qt.ItemIsEnabled
                )
                if not allowed and item.isSelected():
                    item.setSelected(False)
        finally:
            required_list.blockSignals(previous_blocked)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        form = QFormLayout()
        self.name_edit = QLineEdit(str(self.rule.get("name") or ""))
        self.name_edit.setPlaceholderText("例如：双爆输出卡带")
        form.addRow("规则名称", self.name_edit)
        type_widget, self._type_buttons = self._build_segment(
            (("卡带", "tape"), ("驱动", "drive")), self._item_type, self._change_item_type
        )
        form.addRow("装备对象", type_widget)
        action_widget, self._action_buttons = self._build_segment(
            (("仅保留", "keep"), ("直接锁定", "lock")), self._action, self._change_action
        )
        form.addRow("命中后处理", action_widget)
        root.addLayout(form)

        self.main_group = QGroupBox("卡带主词条（命中任一）")
        main_layout = QVBoxLayout(self.main_group)
        self.main_stat_list = self._make_stat_list(self._main_stat_options, self.rule.get("main_stats", []), 112)
        main_layout.addWidget(self.main_stat_list)
        root.addWidget(self.main_group)

        self.sub_group = QGroupBox("副词条")
        sub_layout = QVBoxLayout(self.sub_group)
        sub_layout.setContentsMargins(9, 9, 9, 9)
        sub_layout.setSpacing(8)
        selected_sub_stats = self.rule.get("sub_stats", [])
        selected_required = self.rule.get("required_sub_stats", [])

        # 卡带：两个完整的选择模块左右并列，便于一眼核对“命中”和“必须包含”。
        self.tape_sub_content = QWidget()
        tape_columns = QHBoxLayout(self.tape_sub_content)
        tape_columns.setContentsMargins(0, 0, 0, 0)
        tape_columns.setSpacing(12)

        self.tape_match_container = QWidget()
        tape_match_layout = QVBoxLayout(self.tape_match_container)
        tape_match_layout.setContentsMargins(0, 0, 0, 0)
        tape_match_layout.setSpacing(3)
        tape_match_row = QHBoxLayout()
        tape_match_row.addWidget(QLabel("副词条命中"))
        self.tape_sub_match_combo = _combo(SUB_MATCH_OPTIONS, self._normalized_sub_match(), 132)
        self.tape_sub_match_combo.currentIndexChanged.connect(self._change_sub_match)
        tape_match_row.addWidget(self.tape_sub_match_combo)
        self.tape_sub_match_hint = QLabel()
        self.tape_sub_match_hint.setStyleSheet("color:#f85149")
        tape_match_row.addWidget(self.tape_sub_match_hint)
        tape_match_row.addStretch()
        tape_match_layout.addLayout(tape_match_row)
        self.tape_sub_stat_list = self._make_stat_list(self._sub_stat_options, selected_sub_stats, 132)
        self.tape_sub_stat_list.itemSelectionChanged.connect(self._refresh_sub_match_hint)
        tape_match_layout.addWidget(self.tape_sub_stat_list)
        tape_columns.addWidget(self.tape_match_container, 1)

        self.tape_required_container = QWidget()
        tape_required_layout = QVBoxLayout(self.tape_required_container)
        tape_required_layout.setContentsMargins(0, 0, 0, 0)
        tape_required_layout.setSpacing(3)
        tape_required_layout.addWidget(QLabel("必须包含（可多选）"))
        self.tape_required_sub_stat_list = self._make_stat_list(self._sub_stat_options, selected_required, 132)
        self.tape_required_sub_stat_list.itemSelectionChanged.connect(self._refresh_sub_match_hint)
        tape_required_layout.addWidget(self.tape_required_sub_stat_list)
        tape_columns.addWidget(self.tape_required_container, 1)
        sub_layout.addWidget(self.tape_sub_content)

        # 驱动：先完成“副词条命中”的整块选择，再在下方选择必须包含项。
        self.drive_sub_content = QWidget()
        drive_layout = QVBoxLayout(self.drive_sub_content)
        drive_layout.setContentsMargins(0, 0, 0, 0)
        drive_layout.setSpacing(12)
        self.drive_match_container = QWidget()
        drive_match_layout = QVBoxLayout(self.drive_match_container)
        drive_match_layout.setContentsMargins(0, 0, 0, 0)
        drive_match_layout.setSpacing(8)
        drive_match_row = QHBoxLayout()
        drive_match_row.addWidget(QLabel("副词条命中"))
        self.drive_sub_match_combo = _combo(SUB_MATCH_OPTIONS, self._normalized_sub_match(), 132)
        self.drive_sub_match_combo.currentIndexChanged.connect(self._change_sub_match)
        drive_match_row.addWidget(self.drive_sub_match_combo)
        self.drive_sub_match_hint = QLabel()
        self.drive_sub_match_hint.setStyleSheet("color:#f85149")
        drive_match_row.addWidget(self.drive_sub_match_hint)
        drive_match_row.addStretch()
        drive_match_layout.addLayout(drive_match_row)
        self.drive_sub_stat_list = self._make_stat_list(self._sub_stat_options, selected_sub_stats, 132)
        self.drive_sub_stat_list.itemSelectionChanged.connect(self._refresh_sub_match_hint)
        drive_match_layout.addWidget(self.drive_sub_stat_list)
        drive_layout.addWidget(self.drive_match_container)

        self.drive_required_container = QWidget()
        drive_required_layout = QVBoxLayout(self.drive_required_container)
        drive_required_layout.setContentsMargins(0, 0, 0, 0)
        drive_required_layout.setSpacing(8)
        drive_required_layout.addWidget(QLabel("必须包含（可多选）"))
        self.drive_required_sub_stat_list = self._make_stat_list(self._sub_stat_options, selected_required, 92)
        self.drive_required_sub_stat_list.itemSelectionChanged.connect(self._refresh_sub_match_hint)
        drive_required_layout.addWidget(self.drive_required_sub_stat_list)
        drive_layout.addWidget(self.drive_required_container)
        sub_layout.addWidget(self.drive_sub_content)
        root.addWidget(self.sub_group, 1)

        advanced = QFormLayout()
        self.quality_combo = _combo(QUALITY_SCOPE_OPTIONS, self.rule.get("quality_scope", "gold_purple"), 150)
        advanced.addRow("品质范围", self.quality_combo)
        self.range_summary = QLabel()
        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_button = QPushButton("选择范围")
        range_button.clicked.connect(self._open_range_dialog)
        range_layout.addWidget(self.range_summary, 1)
        range_layout.addWidget(range_button)
        advanced.addRow("类型范围", range_row)
        root.addLayout(advanced)
        self._refresh_visibility()
        self._refresh_range_summary()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_rule)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_sub_match_hint()

    def _change_item_type(self, value: str) -> None:
        if value == self._item_type:
            return
        old_combo, old_sub_stat_list, _old_hint = self._active_sub_match_widgets()
        old_sub_stats = self._selected_stats(old_sub_stat_list)
        old_required_stats = self._selected_stats(self._active_required_sub_stat_list())
        self._item_type = value
        new_combo, new_sub_stat_list, _new_hint = self._active_sub_match_widgets()
        _set_combo_data(new_combo, old_combo.currentData())
        self._set_selected_stats(new_sub_stat_list, old_sub_stats)
        self._set_selected_stats(self._active_required_sub_stat_list(), old_required_stats)
        self.rule["sub_match"] = new_combo.currentData()
        self._set_segment_value(self._type_buttons, value)
        self._refresh_visibility()
        self._refresh_range_summary()

    def _change_action(self, value: str) -> None:
        self._action = value
        self._set_segment_value(self._action_buttons, value)

    def _normalized_sub_match(self) -> str | int:
        value = self.rule.get("sub_match", "all")
        if value == "all":
            return max(1, min(len(self.rule.get("sub_stats", []) or []), 4))
        if value == "any":
            return 1
        try:
            return max(1, min(int(value), 4))
        except (TypeError, ValueError):
            return 4

    def _change_sub_match(self, _index: int) -> None:
        combo, _sub_stat_list, _hint = self._active_sub_match_widgets()
        self.rule["sub_match"] = combo.currentData()
        self._refresh_sub_match_hint()

    def _refresh_sub_match_hint(self) -> None:
        combo, sub_stat_list, hint = self._active_sub_match_widgets()
        self._sync_required_sub_stat_choices()
        selected_count = len(self._selected_stats(sub_stat_list))
        required_widget = self._active_required_sub_stat_list()
        required_count = len(self._selected_stats(required_widget))
        match_count = int(combo.currentData() or 1)
        if (selected_count or required_count) and selected_count < match_count:
            hint.setText("不能少于命中数量")
        elif required_count >= match_count:
            hint.setText("必须包含数量必须少于命中数量")
        else:
            hint.clear()

    def _refresh_visibility(self) -> None:
        self.main_group.setVisible(self._item_type == "tape")
        self.tape_sub_content.setVisible(self._item_type == "tape")
        self.drive_sub_content.setVisible(self._item_type == "drive")
        self._refresh_sub_stat_layout()
        self._refresh_sub_match_hint()

    def _range_defaults(self) -> tuple[list[str], list[str]]:
        shapes = self._range_values.get("shape_ids")
        sets = self._range_values.get("set_names")
        if not isinstance(shapes, list):
            shapes = [shape_id for shape_id, _area in self.shape_options if shape_id not in DEFAULT_EXCLUDED_SHAPE_IDS]
        if not isinstance(sets, list):
            sets = [set_name for set_name in self.set_options if set_name not in DEFAULT_EXCLUDED_SET_NAMES]
        return shapes, sets

    def _refresh_range_summary(self) -> None:
        shapes, sets = self._range_defaults()
        label = "卡带套装" if self._item_type == "tape" else "驱动形状"
        count = len(sets) if self._item_type == "tape" else len(shapes)
        total = len(self.set_options) if self._item_type == "tape" else len(self.shape_options)
        self.range_summary.setText(f"{label} {count}/{total}")

    def _open_range_dialog(self) -> None:
        shapes, sets = self._range_defaults()
        dialog = TypeRangeDialog(self, self.shape_options, self.set_options, shapes, sets)
        if dialog.exec() != QDialog.Accepted:
            return
        selected_shapes, selected_sets = dialog.selected_values()
        self._range_values = {"shape_ids": selected_shapes, "set_names": selected_sets}
        self._refresh_range_summary()

    def _save_rule(self) -> None:
        main_stats = self._selected_stats(self.main_stat_list) if self._item_type == "tape" else []
        combo, sub_stat_list, _hint = self._active_sub_match_widgets()
        sub_stats = self._selected_stats(sub_stat_list)
        required_list = self._active_required_sub_stat_list()
        required_sub_stats = self._selected_stats(required_list)
        match_count = int(combo.currentData() or 1)
        if self._item_type == "drive" and not sub_stats:
            QMessageBox.warning(self, "规则无效", "驱动规则至少选择一个副词条。")
            return
        if self._item_type == "tape" and not (main_stats or sub_stats):
            QMessageBox.warning(self, "规则无效", "卡带规则至少选择主词条或副词条。")
            return
        if not set(required_sub_stats).issubset(sub_stats):
            QMessageBox.warning(self, "规则无效", "必须包含的副词条必须同时在副词条命中池中。")
            return
        if (sub_stats or required_sub_stats) and len(sub_stats) < match_count:
            QMessageBox.warning(
                self,
                "规则无效",
                "不能少于命中数量。",
            )
            return
        if len(required_sub_stats) >= match_count:
            QMessageBox.warning(self, "规则无效", "必须包含的副词条数量必须少于命中数量。")
            return
        name = self.name_edit.text().strip() or ("预留卡带" if self._item_type == "tape" else "预留驱动")
        self._result_rule = {
            "enabled": bool(self.rule.get("enabled", True)),
            "name": name,
            "item_type": self._item_type,
            "action": self._action,
            "main_stats": main_stats,
            "sub_stats": sub_stats,
            "required_sub_stats": required_sub_stats,
            "sub_match": match_count,
            "quality_scope": self.quality_combo.currentData(),
            "shape_ids": self._range_values.get("shape_ids"),
            "set_names": self._range_values.get("set_names"),
        }
        self.accept()

    def result_rule(self) -> dict | None:
        return copy.deepcopy(self._result_rule)
