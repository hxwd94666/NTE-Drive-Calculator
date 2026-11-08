# 在规则页顶部同步总量与评级草稿，规则开关由下方列表表达。
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget
from src.app.theme import themed_style
from src.ui.widgets import NoWheelComboBox
from src.domain.grade_limits import GRADE_LADDER

from src.domain.equipment.intelligent_rules import preserve_origin


class IntelligentBudgetBar(QWidget):
    def __init__(self, rules, changed, parent=None, *, advice=lambda: None):
        super().__init__(parent)
        self.rules, self.changed = rules, changed
        self.advice = advice
        self.current_rule = None
        self.grade_rule = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        line = QHBoxLayout()
        label = QLabel("目标保留总量（卡带＋驱动）")
        self.amount = QSpinBox()
        self.amount.setRange(0, 100000)
        self.amount.setSpecialValueText("不限")
        self.amount.setSuffix(" 件")
        self.amount.setAccessibleName("目标保留总量，卡带与驱动合计")
        self.amount.setMinimumWidth(150)
        label.setBuddy(self.amount)
        line.addWidget(label)
        line.addWidget(self.amount)
        grade_label = QLabel("驱动保留评级")
        self.grade = NoWheelComboBox()
        self.grade.setAccessibleName("驱动保留评级")
        self.grade.addItem("不额外保留", "none")
        for value in GRADE_LADDER:
            self.grade.addItem(value + " 及以上", value)
        self.grade.setMinimumWidth(140)
        grade_label.setBuddy(self.grade)
        line.addWidget(grade_label)
        line.addWidget(self.grade)
        line.addStretch()
        root.addLayout(line)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        root.addWidget(self.hint)
        self.amount.valueChanged.connect(self._change)
        self.grade.currentIndexChanged.connect(self._change_grade)
        self._apply_style()

    def _apply_style(self):
        style = themed_style(
            "QSpinBox:disabled,QComboBox:disabled{background:#161b22;color:#6e7681;border-color:#30363d;}"
        )
        if self.styleSheet() != style:
            self.setStyleSheet(style)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.StyleChange, QEvent.ApplicationPaletteChange):
            self._apply_style()

    def refresh(self):
        rules = [r for r in self.rules() if r.get("smart_kind") == "future"]
        enabled = [r for r in rules if r.get("enabled")]
        self.current_rule = enabled[0] if len(enabled) == 1 else None
        display = self.current_rule or (rules[0] if len(rules) == 1 else {})
        self.amount.blockSignals(True)
        self.amount.setValue(display.get("target_inventory_size", 1000))
        self.amount.blockSignals(False)
        self.amount.setEnabled(self.current_rule is not None)
        grades = [r for r in self.rules() if r.get("smart_kind") == "grade" and r.get("enabled")]
        self.grade_rule = grades[0] if len(grades) == 1 else None
        self.grade.blockSignals(True)
        self.grade.setCurrentIndex(self.grade.findData(self.grade_rule["grade"] if self.grade_rule else "none"))
        self.grade.blockSignals(False)
        self.grade.setEnabled(self.grade_rule is not None)
        # 规则是否启用由下方列表直接表达；顶部不重复显示状态提示。
        self.hint.clear()
        self.amount.setToolTip("与下方未来用途储备规则同步。")
        self.grade.setToolTip("与下方驱动评级保护规则同步。")
        suggestion = self.advice() or {}
        if suggestion:
            rate = suggestion.get("drive_rate")
            budget = suggestion.get("budget") or {}
            reason = (
                f"首次建议 {suggestion['recommended_drive_grade']}：目标不超过 {budget['target']} 件，"
                "按评级数量和已有保护估算，选择预算内最宽松的一档。正式计算仍会检查全角色损失。"
                if budget.get("target") and budget.get("fits_target")
                else "必要保护估计已超过目标，不能靠提高评级强行取消保护。"
                if budget.get("target")
                else f"首次建议 {suggestion['recommended_drive_grade']}：驱动质量样本平均得分率 {rate:.1%}。"
                if rate is not None
                else "首次评估缺少有效样本，采用默认 SSS。"
            )
            self.grade.setToolTip(reason + " 已有评级设置不会被后续仓库变化自动覆盖。")

    def _change(self, value):
        if self.current_rule is None:
            return
        updated = preserve_origin(self.current_rule, {**self.current_rule, "target_inventory_size": value})
        self.current_rule.clear()
        self.current_rule.update(updated)
        self.changed()

    def _change_grade(self, _index):
        if self.grade_rule is None:
            return
        grade = self.grade.currentData()
        values = {**self.grade_rule, "enabled": grade != "none"}
        if grade != "none":
            values["grade"] = grade
        updated = preserve_origin(self.grade_rule, values)
        self.grade_rule.clear()
        self.grade_rule.update(updated)
        self.changed()
