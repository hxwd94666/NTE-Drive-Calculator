# 用明确槽位身份选择倒带推荐的角色方案。
from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.theme import current_style_sheet
from src.domain.equipment.scoring.allocation_rating import loadout_total_grade


class RewindSlotPicker(QDialog):
    def __init__(self, parent, role, selected_reference, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.toolbox.init.self_3', name=f'{role.name}'))
        self.setStyleSheet(current_style_sheet())
        self._references = {}
        root = QVBoxLayout(self)
        root.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.toolbox.init.caption_5')))
        self.group = QButtonGroup(self)
        body = QWidget()
        rows = QVBoxLayout(body)
        for index, slot in enumerate(role.slots):
            score = localized_text(text_catalog, 'auto.features.toolbox.rewind.slot_picker.init.ff57efd48', '{score}分 · {value}', score=f'{slot.score:g}', value=f'{loadout_total_grade(slot.score)}') if slot.score is not None else "总评分资料不足"
            option = QRadioButton(f"{slot.slot_name} · {score}")
            option.setObjectName("rewindSlotOption")
            option.setToolTip(f"{slot.slot_name} · {score}\n{slot.reason}")
            option.setProperty("slotId", slot.reference.slot_id)
            option.setChecked(selected_reference is not None and selected_reference.slot_id == slot.reference.slot_id)
            self.group.addButton(option, index)
            self._references[index] = slot.reference
            rows.addWidget(option)
            detail = plain_label(slot.reason)
            detail.setWordWrap(True)
            rows.addWidget(detail)
        rows.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._confirm = buttons.button(QDialogButtonBox.Ok)
        self._confirm.setEnabled(self.group.checkedId() >= 0)
        self.group.idToggled.connect(lambda *_: self._confirm.setEnabled(self.group.checkedId() >= 0))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(520, 380))

    def selected_reference(self):
        return self._references.get(self.group.checkedId())
