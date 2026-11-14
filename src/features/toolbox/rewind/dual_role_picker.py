# 在同一头像选择窗口内编辑互斥的两组角色与保存槽位，取消只丢弃本窗草稿。
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QHBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.toolbox.rewind.role_picker import _RoleSelectionDialog
from src.ui.common.text_catalog import plain_label, plain_tooltip


class DualRoleSelectionDialog(_RoleSelectionDialog):
    def __init__(self, parent, *, roles, groups, active_group, asset_root=None, catalog=None):
        self._active_group = active_group
        self._groups = {key: dict(value) for key, value in groups.items()}
        super().__init__(parent, title=catalog.render('rewind.dual.picker_title'),
            description=catalog.render('rewind.dual.picker_hint'), roles=roles,
            selected_character_ids=set(self._groups[active_group]), selected_slots=self._groups[active_group],
            asset_root=asset_root, catalog=catalog)
        row = QHBoxLayout()
        label = plain_label(self.text('rewind.dual.edit_group'))
        self.group_selector = QComboBox()
        for key in ('improve', 'push'):
            self.group_selector.addItem(self.text('rewind.dual.' + key), key)
        self.group_selector.setCurrentIndex(self.group_selector.findData(active_group))
        label.setBuddy(self.group_selector)
        row.addWidget(label)
        row.addWidget(self.group_selector, 1)
        self.layout().insertLayout(1, row)
        self.group_selector.currentIndexChanged.connect(self._switch_group)
        self._apply_other_group_exclusion()
        fit_dialog_to_available_screen(self, QSize(920, 780))

    def _store_active(self):
        self._groups[self._active_group] = {identifier: self._slots.get(identifier) for identifier in self.selected_character_ids()}

    def _apply_other_group_exclusion(self):
        other = 'push' if self._active_group == 'improve' else 'improve'
        for card, identifier, _ in self._cards:
            enabled = identifier not in self._groups[other]
            wrapper = self._wrappers[identifier]
            wrapper.set_reference(self._roles[identifier], self._slots.get(identifier))
            card.setEnabled(enabled)
            if not enabled:
                wrapper.slot_button.setEnabled(False)
                reason = self.text('rewind.dual.other_group', group=self.text('rewind.dual.' + other))
                card.setToolTip(plain_tooltip(reason))
                card.setAccessibleDescription(reason)

    def _switch_group(self):
        self._store_active()
        self._active_group = self.group_selector.currentData()
        selected = self._groups[self._active_group]
        self._slots.update(selected)
        for card, identifier, _ in self._cards:
            card.blockSignals(True)
            card.setChecked(identifier in selected)
            card.blockSignals(False)
            wrapper = self._wrappers[identifier]
            wrapper.sync_checked()
        self._apply_other_group_exclusion()
        self._update_count()

    def _set_visible_checked(self, checked):
        for card, identifier, _ in self._cards:
            if card.isEnabled() and not self._wrappers[identifier].isHidden():
                card.blockSignals(True)
                card.setChecked(checked)
                card.blockSignals(False)
                self._wrappers[identifier].sync_checked()
        self._update_count()

    def selected_groups(self):
        self._store_active()
        return {key: dict(value) for key, value in self._groups.items()}
