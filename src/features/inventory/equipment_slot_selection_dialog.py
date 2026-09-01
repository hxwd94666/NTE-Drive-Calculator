# 提供配装槽位选择对话框。
"""Shared named-slot chooser for multi-role equipment assembly."""

from __future__ import annotations

from src.i18n import tr
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)


def select_assembly_slot_ids(parent: Any, selections: tuple[Any, ...]) -> list[int] | None:
    """Return one current slot per character, prompting only multi-slot roles."""

    by_character: dict[int, list[Any]] = {}
    for selection in selections:
        by_character.setdefault(int(selection.character_id), []).append(selection)
    if not by_character:
        QMessageBox.information(parent, tr("装配"), tr("当前没有已保存的配装槽位方案。"))
        return None

    defaults: dict[int, Any] = {
        character_id: next(
            (row for row in slots if row.slot_key == "primary"),
            slots[0],
        )
        for character_id, slots in by_character.items()
    }
    if all(len(slots) == 1 for slots in by_character.values()):
        return [selection.slot_id for selection in defaults.values()]

    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("选择装配槽位"))
    dialog.setModal(True)
    dialog.setMinimumWidth(390)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(tr("请选择多槽位角色本次要装配的方案。"), dialog))
    form = QFormLayout()
    selectors: dict[int, QComboBox] = {}
    for character_id, slots in by_character.items():
        if len(slots) == 1:
            continue
        selector = QComboBox(dialog)
        for slot in slots:
            selector.addItem(str(slot.slot_name), int(slot.slot_id))
        default_slot_id = int(defaults[character_id].slot_id)
        selector.setCurrentIndex(max(0, selector.findData(default_slot_id)))
        form.addRow(f"{slots[0].role_name}：", selector)
        selectors[character_id] = selector
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return None
    return [
        int(selectors[character_id].currentData()) if character_id in selectors else int(default.slot_id)
        for character_id, default in defaults.items()
    ]
