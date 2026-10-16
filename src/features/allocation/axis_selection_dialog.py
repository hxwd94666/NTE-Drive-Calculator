# 明确选择本账号报告、事件范围、正式参与角色和A配置，取消不更改计算或持久状态。
from __future__ import annotations

from concurrent.futures import CancelledError
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.widgets import NoWheelComboBox


class AxisAllocationSelectionDialog(QDialog):
    def __init__(self, *, service, choices, initial_request=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("战报轴伤优化 · 冻结选择")
        self._service, self._choices = service, choices
        self.selection = None
        layout = QVBoxLayout(self)
        description = QLabel("只比较所选保存事件域的全队期望估算，沿用A的冻结养成与目标。\n"
            "装备采用实际品质、等级与词条；当前边际草稿不会自动带入，不承诺完整战斗最高伤害。")
        description.setWordWrap(True)
        description.setTextFormat(Qt.PlainText)
        layout.addWidget(description)
        members = QLabel("本次优化成员：" + "、".join(choices.selected_roles))
        members.setWordWrap(True)
        members.setTextFormat(Qt.PlainText)
        layout.addWidget(members)
        form = QFormLayout()
        self.report_combo = NoWheelComboBox()
        self.report_combo.setAccessibleName("优化战报")
        self.report_combo.addItem("请选择本账号战报", None)
        for entry in choices.entries:
            self.report_combo.addItem(f"#{entry.battle_record_id} · {entry.saved_at_utc} · {entry.total_hits}击", entry.battle_record_id)
        form.addRow("战报", self.report_combo)
        self.role_combo = NoWheelComboBox()
        self.role_combo.setAccessibleName("轴优化角色")
        form.addRow("优化参与范围", self.role_combo)
        self.scope_combo = NoWheelComboBox()
        self.scope_combo.setAccessibleName("优化事件范围")
        form.addRow("保存事件范围", self.scope_combo)
        self.anchor_combo = NoWheelComboBox()
        self.anchor_combo.setAccessibleName("冻结A配置")
        form.addRow("A配置", self.anchor_combo)
        self.time_widget = QWidget()
        time_layout = QHBoxLayout(self.time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        self.start_spin, self.end_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for control, label in ((self.start_spin, "开始秒"), (self.end_spin, "结束秒")):
            control.setRange(0, 86_400)
            control.setDecimals(6)
            control.setSuffix(" 秒")
            control.setAccessibleName(label)
            time_layout.addWidget(control)
        self.time_label = QLabel("指定时段")
        form.addRow(self.time_label, self.time_widget)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("冻结本次选择")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.report_combo.currentIndexChanged.connect(self._report_changed)
        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        self._report_changed()
        if initial_request is not None:
            # 来自用户正在看的边际页，只预填该明确报告/范围；不替用户决定A配置。
            index = self.report_combo.findData(initial_request.battle_record_id)
            if index >= 1:
                self.report_combo.setCurrentIndex(index)
                self.role_combo.setCurrentIndex(self.role_combo.findData("__all_groups__"))
                scope = "range" if initial_request.start_us is not None or initial_request.end_us is not None else (initial_request.detail_scope or "all")
                self.scope_combo.setCurrentIndex(self.scope_combo.findData(scope))
                if scope == "range":
                    self.start_spin.setValue((initial_request.start_us or 0) / 1_000_000)
                    self.end_spin.setValue((initial_request.end_us or 0) / 1_000_000)
        fit_dialog_to_available_screen(self, QSize(680, 420))

    def _report_changed(self, *_):
        identifier = self.report_combo.currentData()
        entry = next((row for row in self._choices.entries if row.battle_record_id == identifier), None)
        self.role_combo.clear()
        self.role_combo.addItem("请选择参与角色", None)
        self.scope_combo.clear()
        self.scope_combo.addItem("请选择范围", None)
        self.anchor_combo.clear()
        self.anchor_combo.addItem("请选择A配置", None)
        if entry is not None:
            self.role_combo.addItem("本次计算所选角色 · 按全队收益优化", "__all_groups__")
            self.scope_combo.addItem("全部保存观测事件", "all")
            if entry.has_first_half:
                self.scope_combo.addItem("上半场保存事件", "first")
            if entry.has_second_half:
                self.scope_combo.addItem("下半场保存事件", "second")
            self.scope_combo.addItem("指定时段", "range")
            self.anchor_combo.addItem("原始A · 沿用战报冻结养成", "original")
            if identifier in self._choices.active_edit_record_ids:
                self.anchor_combo.addItem("已启用修改副本A · 沿用副本冻结养成", "effective")
        self.error_label.setText("")
        self._scope_changed()

    def _scope_changed(self, *_):
        visible = self.scope_combo.currentData() == "range"
        self.time_label.setVisible(visible)
        self.time_widget.setVisible(visible)

    def _accept_selection(self):
        scope = self.scope_combo.currentData()
        try:
            self.selection = self._service.select(self._choices,
                battle_record_id=self.report_combo.currentData(), selected_role=self.role_combo.currentData(),
                detail_scope=scope, anchor_mode=self.anchor_combo.currentData(),
                start_us=round(self.start_spin.value() * 1_000_000) if scope == "range" else None,
                end_us=round(self.end_spin.value() * 1_000_000) if scope == "range" else None)
        except CancelledError:
            self.reject()
            return
        except (ValueError, RuntimeError) as error:
            self.error_label.setText(str(error))
            return
        self.accept()
