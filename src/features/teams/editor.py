# 通过头像卡片编辑一支四人队伍；弹窗仅持有草稿，保存由 controller 原子提交。
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLineEdit,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.names.name_search import match_pinyin
from src.domain.names.role_name_order import role_name_sort_key
from src.domain.teams.models import TeamDraft, TeamRole
from src.features.teams.cards import MemberSlots, role_button, set_role
from src.ui.common.text_catalog import plain_label, resolve_text_catalog, set_button_text


class TeamEditor(QDialog):
    save_requested = Signal()

    def __init__(self, *, roles, team=None, parent=None, catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.setWindowTitle(self.text("teams.editor.edit" if team else "teams.editor.create"))
        self.setModal(True)
        self._selected = list(team.character_ids if team else ())
        self._roles = {role.character_id: role for role in roles}
        for character_id in self._selected:
            self._roles.setdefault(character_id, TeamRole(character_id, self.text("teams.editor.unavailable_role", id=character_id)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = plain_label(self.text("teams.editor.name"))
        layout.addWidget(title)
        self.name_edit = QLineEdit(team.name if team else "")
        self.name_edit.setMaxLength(80)
        self.name_edit.setPlaceholderText(self.text("teams.editor.name_hint"))
        self.name_edit.setAccessibleName(self.text("teams.editor.name"))
        title.setBuddy(self.name_edit)
        layout.addWidget(self.name_edit)
        self.count = plain_label()
        layout.addWidget(self.count)
        members = MemberSlots(self, catalog=self.text_catalog)
        self.member_buttons = members.buttons
        for index, button in enumerate(self.member_buttons):
            button.clicked.connect(lambda checked=False, slot=index: self._remove_slot(slot))
        layout.addWidget(members)
        hint = plain_label(self.text("teams.editor.instructions"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.text("teams.editor.search"))
        self.search.setAccessibleName(self.text("teams.editor.search_accessible"))
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.role_buttons = {}
        # 候选池按拼音 A–Z 展示，正式身份兜底稳定排序；不重排已保存的队内位置。
        for role in sorted(self._roles.values(), key=lambda role: (role_name_sort_key(role.name), role.character_id)):
            button = role_button(role, parent=self.grid_widget, checkable=True, catalog=self.text_catalog)
            button.setFixedWidth(150)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.toggled.connect(lambda selected, key=role.character_id: self._toggle(key, selected))
            self.role_buttons[role.character_id] = button
        self.scroll.setWidget(self.grid_widget)
        layout.addWidget(self.scroll, 1)
        self.empty_label = plain_label(self.text("teams.editor.empty"))
        layout.addWidget(self.empty_label)
        self.error = plain_label()
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        set_button_text(self.buttons.button(QDialogButtonBox.Save), self.text("teams.editor.save"))
        set_button_text(self.buttons.button(QDialogButtonBox.Cancel), self.text("common.cancel"))
        self.buttons.accepted.connect(self.save_requested.emit)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.search.textChanged.connect(self._reflow)
        self.name_edit.textChanged.connect(self._update_save)
        self.scroll.viewport().installEventFilter(self)
        self._sync_selection()
        self._reflow()
        fit_dialog_to_available_screen(self, QSize(800, 620))
        self.name_edit.setFocus()

    def draft(self):
        return TeamDraft(self.name_edit.text(), tuple(self._selected))

    def _update_save(self):
        self.buttons.button(QDialogButtonBox.Save).setEnabled(bool(self.name_edit.text().strip()))

    def _toggle(self, character_id, selected):
        if selected and character_id not in self._selected and len(self._selected) < 4:
            self._selected.append(character_id)
        elif not selected and character_id in self._selected:
            self._selected.remove(character_id)
        self._sync_selection()

    def _remove_slot(self, index):
        if index < len(self._selected):
            self._selected.pop(index)
            self._sync_selection()
        else:
            self.search.setFocus()

    def _sync_selection(self):
        size = len(self._selected)
        self.count.setText(self.text("teams.editor.count", count=size))
        for character_id, button in self.role_buttons.items():
            selected = character_id in self._selected
            button.blockSignals(True)
            button.setChecked(selected)
            button.setEnabled(selected or size < 4)
            button.setAccessibleName(self.text("teams.editor.role_selected" if selected else "teams.editor.role_unselected", role=self._roles[character_id].name))
            button.blockSignals(False)
        for index, button in enumerate(self.member_buttons):
            role = self._roles[self._selected[index]] if index < size else None
            set_role(button, role)
            button.setAccessibleName(self.text("teams.editor.remove_member", index=index + 1, role=role.name) if role else self.text("teams.editor.empty_member", index=index + 1))
        self._update_save()

    def _reflow(self, *_):
        keyword = self.search.text().strip()
        columns = max(1, min(5, (self.scroll.viewport().width() - 6) // 158))
        while self.grid.count():
            self.grid.takeAt(0)
        visible = 0
        for character_id, button in self.role_buttons.items():
            matches = not keyword or match_pinyin(self._roles[character_id].name, keyword)
            button.setVisible(matches)
            if matches:
                self.grid.addWidget(button, visible // columns, visible % columns)
                visible += 1
        self.empty_label.setVisible(visible == 0)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._reflow()
        return super().eventFilter(watched, event)
