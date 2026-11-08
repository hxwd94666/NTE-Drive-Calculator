# 编辑动态智能规则的名称、动作和参数，界面不执行仓库计算或游戏操作。
from copy import deepcopy

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.intelligent_rules import KINDS
from src.domain.grade_limits import GRADE_LADDER


class IntelligentRuleEditor(QDialog):
    def __init__(self, parent, rule):
        super().__init__(parent)
        self.rule = deepcopy(rule)
        self.setWindowTitle("编辑智能规则")
        layout = QFormLayout(self)
        self.name = QLineEdit(rule["name"])
        layout.addRow("名称", self.name)
        self.enabled = QCheckBox("启用")
        self.enabled.setChecked(rule["enabled"])
        layout.addRow(self.enabled)
        detail = QLabel(KINDS[rule["smart_kind"]][1].replace("空幕", "卡带"))
        detail.setWordWrap(True)
        layout.addRow("依据", detail)
        self.action = QComboBox()
        choices = (
            [("保留", "keep"), ("锁定", "lock")]
            if rule["smart_kind"] in {"baseline", "current_tape", "current_drive"}
            else [("弃置候选", "discard")]
            if rule["smart_kind"] == "redundant"
            else [("保留", "keep")]
        )
        for label, value in choices:
            self.action.addItem(label, value)
        self.action.setCurrentIndex(self.action.findData(rule["action"]))
        layout.addRow("建议动作", self.action)
        self.reserve = QSpinBox()
        self.reserve.setRange(1, 20)
        self.reserve.setValue(rule["reserve"])
        if rule["smart_kind"] in {"current_tape", "current_drive", "future"}:
            layout.addRow("额外备用候选数" if rule["smart_kind"] == "current_drive" else "每用途候选件数", self.reserve)
        self.grade = QComboBox()
        self.grade.addItems(GRADE_LADDER)
        self.grade.setCurrentText(rule["grade"])
        if rule["smart_kind"] == "grade":
            layout.addRow("最低保留评级", self.grade)
        self.target = QSpinBox()
        self.target.setRange(0, 100000)
        self.target.setSpecialValueText("不限制")
        self.target.setValue(rule.get("target_inventory_size", 1000))
        if rule["smart_kind"] == "future":
            layout.addRow("目标保留总量", self.target)
            hint = QLabel(
                "达到评级门槛、锁定首选、配装需要和明确保留规则优先；只压缩普通备用与额外未来候选，必要时超过目标。"
            )
            hint.setWordWrap(True)
            layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        fit_dialog_to_available_screen(self, QSize(540, 340))

    def result_rule(self):
        rule = deepcopy(self.rule)
        rule.update(
            name=self.name.text().strip() or rule["name"],
            enabled=self.enabled.isChecked(),
            reserve=self.reserve.value(),
            grade=self.grade.currentText(),
            action=self.action.currentData(),
        )
        if rule["smart_kind"] == "future":
            rule["target_inventory_size"] = self.target.value()
        return rule
