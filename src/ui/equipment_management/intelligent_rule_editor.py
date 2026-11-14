# 编辑动态智能规则的名称、动作和参数，界面不执行仓库计算或游戏操作。
from src.ui.common.text_catalog import add_plain_form_row, plain_label, resolve_text_catalog, set_button_text
from src.ui.common.enum_text import ui_enum_text
from copy import deepcopy

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.warehouse.intelligent_rules import KINDS
from src.domain.equipment.scoring.grade_limits import GRADE_LADDER


class IntelligentRuleEditor(QDialog):
    def __init__(self, parent, rule, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.rule = deepcopy(rule)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.self'))
        layout = QFormLayout(self)
        self.name = QLineEdit(rule["name"])
        add_plain_form_row(layout, resolve_text_catalog(text_catalog).render('ui.equipment_management.init.layout'), self.name)
        self.enabled = QCheckBox(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.enabled'))
        self.enabled.setChecked(rule["enabled"])
        layout.addRow(self.enabled)
        detail = plain_label(KINDS[rule["smart_kind"]][1].replace("空幕", "卡带"))
        detail.setWordWrap(True)
        add_plain_form_row(layout, resolve_text_catalog(text_catalog).render('ui.battle_report.factor.self_3'), detail)
        self.action = QComboBox()
        choices = (
            [("保留", "keep"), ("锁定", "lock")]
            if rule["smart_kind"] in {"baseline", "current_tape", "current_drive"}
            else [("弃置候选", "discard")]
            if rule["smart_kind"] == "redundant"
            else [("保留", "keep")]
        )
        for label, value in choices:
            self.action.addItem(ui_enum_text(getattr(self, 'text_catalog', None), label), value)
        self.action.setCurrentIndex(self.action.findData(rule["action"]))
        add_plain_form_row(layout, resolve_text_catalog(text_catalog).render('ui.equipment_management.init.layout_2'), self.action)
        self.reserve = QSpinBox()
        self.reserve.setRange(1, 20)
        self.reserve.setValue(rule["reserve"])
        if rule["smart_kind"] in {"current_tape", "current_drive", "future"}:
            layout.addRow(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.layout_3') if rule["smart_kind"] == "current_drive" else resolve_text_catalog(text_catalog).render('ui.equipment_management.init.layout_4'), self.reserve)
        self.grade = QComboBox()
        self.grade.addItems(GRADE_LADDER)
        self.grade.setCurrentText(rule["grade"])
        if rule["smart_kind"] == "grade":
            add_plain_form_row(layout, resolve_text_catalog(text_catalog).render('ui.equipment_management.init.layout_5'), self.grade)
        self.target = QSpinBox()
        self.target.setRange(0, 100000)
        self.target.setSpecialValueText(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.target'))
        self.target.setValue(rule.get("target_inventory_size", 1000))
        if rule["smart_kind"] == "future":
            add_plain_form_row(layout, resolve_text_catalog(text_catalog).render('ui.equipment_management.init.layout_6'), self.target)
            hint = plain_label(
                resolve_text_catalog(text_catalog).render('ui.equipment_management.init.hint')
            )
            hint.setWordWrap(True)
            layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        set_button_text(buttons.button(QDialogButtonBox.Save), resolve_text_catalog(text_catalog).render('common.save'))
        set_button_text(buttons.button(QDialogButtonBox.Cancel), resolve_text_catalog(text_catalog).render('common.cancel'))
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
