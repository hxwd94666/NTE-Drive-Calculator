# 编辑扫描后锁定与弃置流程使用的单条处理规则。
"""PySide dialog for post-scan discard/lock settings."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.domain.post_actions import (
    DEFAULT_EXCLUDED_SET_NAMES,
    DEFAULT_EXCLUDED_SHAPE_IDS,
    DEFAULT_PRESERVE_RULE,
)
from src.domain.stat_catalog import StatCatalog
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.scanning.post_action_scope_dialogs import TypeRangeDialog, type_range_summary
from src.ui.widgets import NoWheelComboBox


QUALITY_SCOPE_OPTIONS = (("全部", "all"), ("仅金品质", "gold"), ("仅金紫品质", "gold_purple"))
SUB_MATCH_OPTIONS = (("任意一个", 1), ("任意两个", 2), ("任意三个", 3), ("任意四个", 4))


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


def _button_style(checked: bool) -> str:
    if checked:
        return themed_style("QPushButton{border:2px solid #2f81f7;background:#10243f;color:#f0f6fc;border-radius:6px;padding:4px}")
    return themed_style("QPushButton{border:1px solid #30363d;background:#161b22;color:#c9d1d9;border-radius:6px;padding:4px}")


class PreserveRuleEditor(QDialog):
    """编辑单条处理规则，避免在规则列表中展开复杂多选控件。"""

    def __init__(
        self,
        parent,
        rule: dict | None,
        shape_options: list[tuple[str, int]],
        set_options: list[str],
        config_dir: Path,
    ):
        super().__init__(parent)
        self.setWindowTitle("规则处理")
        self.setMinimumSize(560, 0)
        self.resize(780, 660)
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
        widget.currentItemChanged.connect(lambda item, _previous, w=widget: self._reveal_stat(w, item))
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
        """按字体、宽度和两页真实内容测量，稳定页脚并把高度差分配给驱动格子。"""
        self._grid_layout_pending = False
        if not hasattr(self, "_selection_pages"):
            return
        for group in (self.main_group, self.tape_sub_group, self.sub_group):
            title_height = group.fontMetrics().height()
            group.setStyleSheet(f"QGroupBox{{padding:0;margin-top:{title_height}px;}}")
        # 卡带保留原生列表多选和局部滚动；七个自然行不会再被组框 padding 挤成五行。
        for widget in (self.main_stat_list, self.tape_sub_stat_list, self.tape_required_sub_stat_list):
            row_height = max(widget.fontMetrics().height(), widget.sizeHintForRow(0))
            widget.setFixedHeight(min(7, widget.count()) * row_height + 2 * widget.frameWidth())
        grids = []
        for widget in (self.drive_sub_stat_list, self.drive_required_sub_stat_list):
            group_margins = self.sub_group.contentsMargins()
            layout_margins = self.sub_group.layout().contentsMargins()
            available = max(1, self._selection_pages.contentsRect().width()
                            - group_margins.left() - group_margins.right()
                            - layout_margins.left() - layout_margins.right())
            minimum = max(120, max((widget.fontMetrics().horizontalAdvance(widget.item(i).text())
                                   for i in range(widget.count())), default=0) + 24)
            columns = max(1, min(4, available // minimum))
            rows = (widget.count() + columns - 1) // columns
            cell_height = widget.fontMetrics().height() + 18
            grids.append((widget, columns, rows, cell_height, available))
            self._size_drive_grid(widget, columns, rows, cell_height, available)
        for page in (self._tape_page, self._drive_page):
            for layout in reversed(page.findChildren(QLayout)):
                layout.invalidate()
                layout.activate()
        tape_height = self._tape_page.layout().minimumSize().height()
        drive_height = self._drive_page.layout().minimumSize().height()
        target_height = max(tape_height, drive_height)
        row_count = sum(rows for _widget, _columns, rows, _height, _width in grids)
        extra_per_row = max(0, target_height - drive_height) // max(1, row_count)
        for widget, columns, rows, cell_height, available in grids:
            self._size_drive_grid(widget, columns, rows, cell_height + extra_per_row, available)
        # 共同高度来自两页最小内容需求，不随窗口剩余空间无谓放大；不足一行的余数由布局吸收。
        self._selection_pages.setFixedHeight(target_height)

    @staticmethod
    def _size_drive_grid(widget, columns, rows, cell_height, available):
        cell_width = max(1, available - 2) // columns
        widget.setGridSize(QSize(cell_width, cell_height))
        for index in range(widget.count()):
            widget.item(index).setSizeHint(QSize(max(1, cell_width - 6), cell_height - 6))
        widget.setFixedHeight(rows * cell_height + 2 * widget.frameWidth() + 4)
        widget.doItemsLayout()

    def _reveal_stat(self, widget, item) -> None:
        if item is None:
            return
        def reveal():
            if not widget.isVisible():
                return
            # 等列表完成自身滚动，再将实际末项露到唯一外层视口，避免卡带先滚内层后外层错位。
            rect = widget.visualItemRect(item)
            center = widget.viewport().mapTo(self._editor_scroll.widget(), rect.center())
            self._editor_scroll.ensureVisible(center.x(), center.y(), rect.width() // 2 + 4, rect.height() // 2 + 4)
        QTimer.singleShot(0, self, reveal)

    def _queue_stat_geometry(self):
        if hasattr(self, "drive_sub_stat_list") and not getattr(self, "_grid_layout_pending", False):
            self._grid_layout_pending = True
            QTimer.singleShot(0, self._refresh_drive_grid_item_sizes)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        fit_dialog_to_available_screen(self)
        self._queue_stat_geometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._queue_stat_geometry()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._queue_stat_geometry()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self._queue_stat_geometry()
        return super().eventFilter(watched, event)

    @staticmethod
    def _configure_sub_stat_list(widget: QListWidget, is_drive: bool) -> None:
        widget.setViewMode(QListView.IconMode if is_drive else QListView.ListMode)
        widget.setFlow(QListView.LeftToRight if is_drive else QListView.TopToBottom)
        widget.setWrapping(is_drive)
        widget.setResizeMode(QListView.Adjust if is_drive else QListView.Fixed)
        widget.setMovement(QListView.Static)
        widget.setUniformItemSizes(is_drive)
        widget.setSpacing(0)
        if is_drive:
            widget.setGridSize(QSize(200, 44))
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            widget.setWordWrap(False)
            widget.setTextElideMode(Qt.ElideNone)
            # IconMode paints each item using its own size hint.  gridSize
            # only controls the distance between cells, so set both values.
            for index in range(widget.count()):
                widget.item(index).setSizeHint(QSize(200, 44))
            widget.setStyleSheet(themed_style(
                "QListWidget{background:transparent;border:none;}"
                "QListWidget::item{border:1px solid #30363d;border-radius:5px;"
                "padding:3px 6px;margin:1px;background:#161b22;color:#c9d1d9;}"
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
        outer = QVBoxLayout(self)
        scroll = self._editor_scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(scroll)
        root = QVBoxLayout(content)
        # 编辑内容保持自然最小高度，受限工作区只滚动内容，保存/取消始终在外层。
        root.setSizeConstraint(QLayout.SetMinimumSize)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)
        form = QFormLayout()
        self.name_edit = QLineEdit(str(self.rule.get("name") or ""))
        self.name_edit.setPlaceholderText("例如：双爆输出卡带")
        form.addRow("规则名称", self.name_edit)
        type_widget, self._type_buttons = self._build_segment(
            (("卡带", "tape"), ("驱动", "drive")), self._item_type, self._change_item_type
        )
        form.addRow("装备对象", type_widget)
        action_widget, self._action_buttons = self._build_segment(
            (("仅保留", "keep"), ("锁定", "lock"), ("弃置", "discard")), self._action, self._change_action
        )
        action_widget.setToolTip("弃置遵守弃置模块的“遇到锁定/遇到弃置”设置，不按评分阈值保留。")
        form.addRow("命中后处理", action_widget)
        root.addLayout(form)

        self._selection_pages = QStackedWidget()
        self._selection_pages.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._tape_page, self._drive_page = QWidget(), QWidget()
        tape_page_layout, drive_page_layout = QVBoxLayout(self._tape_page), QVBoxLayout(self._drive_page)
        for page_layout in (tape_page_layout, drive_page_layout):
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(root.spacing())
        self.main_group = QGroupBox("卡带主词条（命中任一）")
        main_layout = QVBoxLayout(self.main_group)
        main_layout.setContentsMargins(8, 6, 8, 6)
        self.main_stat_list = self._make_stat_list(self._main_stat_options, self.rule.get("main_stats", []), 112)
        main_layout.addWidget(self.main_stat_list)
        tape_page_layout.addWidget(self.main_group)

        self.tape_sub_group, self.sub_group = QGroupBox("副词条"), QGroupBox("副词条")
        tape_sub_layout = QVBoxLayout(self.tape_sub_group)
        tape_sub_layout.setContentsMargins(9, 9, 9, 9)
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
        tape_sub_layout.addWidget(self.tape_sub_content)
        tape_page_layout.addWidget(self.tape_sub_group)

        # 驱动：先完成“副词条命中”的整块选择，再在下方选择必须包含项。
        self.drive_sub_content = QWidget()
        drive_layout = QVBoxLayout(self.drive_sub_content)
        drive_layout.setContentsMargins(0, 0, 0, 0)
        drive_layout.setSpacing(6)
        self.drive_match_container = QWidget()
        drive_match_layout = QVBoxLayout(self.drive_match_container)
        drive_match_layout.setContentsMargins(0, 0, 0, 0)
        drive_match_layout.setSpacing(4)
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
        drive_required_layout.setSpacing(4)
        drive_required_layout.addWidget(QLabel("必须包含（可多选）"))
        self.drive_required_sub_stat_list = self._make_stat_list(self._sub_stat_options, selected_required, 92)
        self.drive_required_sub_stat_list.itemSelectionChanged.connect(self._refresh_sub_match_hint)
        drive_required_layout.addWidget(self.drive_required_sub_stat_list)
        drive_layout.addWidget(self.drive_required_container)
        sub_layout.addWidget(self.drive_sub_content)
        drive_page_layout.addWidget(self.sub_group)
        self._selection_pages.addWidget(self._tape_page)
        self._selection_pages.addWidget(self._drive_page)
        root.addWidget(self._selection_pages)
        scroll.viewport().installEventFilter(self)

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
        outer.addWidget(buttons)
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
        self._selection_pages.setCurrentWidget(self._tape_page if self._item_type == "tape" else self._drive_page)
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
        self.range_summary.setWordWrap(False)
        self.range_summary.setText(type_range_summary(
            self._item_type, len(shapes), len(self.shape_options), len(sets), len(self.set_options),
        ))

    def _open_range_dialog(self) -> None:
        shapes, sets = self._range_defaults()
        dialog = TypeRangeDialog(self, self.shape_options, self.set_options, shapes, sets, type_scope=self._item_type)
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
        name = self.name_edit.text().strip() or ("卡带处理规则" if self._item_type == "tape" else "驱动处理规则")
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
