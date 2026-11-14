# 在规则页顶部同步总量与评级草稿，规则开关由下方列表表达。
from src.ui.common.text_catalog import ElidedTextLabel, plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QGridLayout, QSpinBox, QVBoxLayout, QWidget
from src.app.theme import themed_style
from src.ui.common.widgets import NoWheelComboBox
from src.domain.equipment.scoring.grade_limits import GRADE_LADDER

from src.domain.equipment.warehouse.intelligent_rules import preserve_origin


class IntelligentBudgetBar(QWidget):
    def __init__(self, rules, changed, parent=None, *, advice=lambda: None, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.rules, self.changed = rules, changed
        self.advice = advice
        self.current_rule = None
        self.grade_rule = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        line = QGridLayout()
        label = ElidedTextLabel(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.label'))
        self.amount = QSpinBox()
        self.amount.setRange(0, 100000)
        self.amount.setSpecialValueText(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.amount'))
        self.amount.setSuffix(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.amount_2'))
        self.amount.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.amount_3'))
        self.amount.setMinimumWidth(150)
        label.setBuddy(self.amount)
        grade_label = ElidedTextLabel(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.grade_label'))
        self.grade = NoWheelComboBox()
        self.grade.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.grade_label'))
        self.grade.addItem(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.grade'), "none")
        for value in GRADE_LADDER:
            self.grade.addItem(value + resolve_text_catalog(text_catalog).render('ui.equipment_management.init.grade_2'), value)
        self.grade.setMinimumWidth(140)
        grade_label.setBuddy(self.grade)
        self._fields = label, self.amount, grade_label, self.grade
        self._field_grid, self._wide = line, None
        self._reflow_fields(self.width() >= 800)
        root.addLayout(line)
        self.hint = plain_label()
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        root.addWidget(self.hint)
        self.amount.valueChanged.connect(self._change)
        self.grade.currentIndexChanged.connect(self._change_grade)
        self._apply_style()

    def _reflow_fields(self, wide):
        if self._wide == wide:
            return
        self._wide = wide
        grid = self._field_grid
        while grid.count():
            grid.takeAt(0)
        for column in range(4):
            grid.setColumnStretch(column, 1 if column == 1 or (wide and column == 3) else 0)
        for index, field in enumerate(self._fields):
            grid.addWidget(field, 0 if wide else index // 2, index if wide else index % 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_fields'):
            # 长译文与窄窗采用两行，不让预算栏撑宽整个规则区并挤走行内动作。
            self._reflow_fields(self.width() >= 800)

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
        self.amount.setToolTip('')
        self.grade.setToolTip('')
        suggestion = self.advice() or {}
        if suggestion:
            rate = suggestion.get("drive_rate")
            budget = suggestion.get("budget") or {}
            text = self.text_catalog.render
            reason = (
                text('ui.rules.budget.fits', grade=suggestion['recommended_drive_grade'], target=budget['target'])
                if budget.get("target") and budget.get("fits_target")
                else text('ui.rules.budget.exceeded') if budget.get("target")
                else text('ui.rules.budget.sample', grade=suggestion['recommended_drive_grade'], rate=f"{rate:.1%}")
                if rate is not None else text('ui.rules.budget.no_sample')
            )
            self.grade.setToolTip(plain_tooltip(reason + resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.refresh.grade_2')))

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
