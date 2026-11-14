# 提供带配装槽位选择的倒带角色多选窗口。
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)
from src.app.theme import current_style_sheet, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.rewind.rewind_loadout import default_slot
from src.integrations.runtime.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.services.rewind.recommendation import RewindTargetRole
from src.ui.roles.role_portrait import custom_role_portrait, temporary_role_portrait
from src.ui.roles.role_selection_sort import RoleSelectionSort
from src.features.toolbox.rewind.role_card import RewindRoleCard
from src.features.toolbox.rewind.role_selection import rewind_role_sort_key
from src.features.toolbox.rewind.slot_picker import RewindSlotPicker
from src.ui.common.widgets import match_pinyin
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_label, resolve_text_catalog


class _RoleSelectionDialog(QDialog):
    """Avatar-card picker shared by target-role and main-role selections."""

    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        description: str,
        roles: tuple[RewindTargetRole, ...],
        selected_character_ids: set[int],
        selected_slots=None,
        asset_root=None,
        catalog=None,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.setWindowTitle(title)
        self.setStyleSheet(current_style_sheet())
        self._cards: list[tuple[QToolButton, int, str]] = []
        self._slots = dict(selected_slots or {})
        self._roles = {role.character_id: role for role in roles}
        self._wrappers = {}
        selected = {int(value) for value in selected_character_ids}
        catalog = GameUiAssetCatalog(asset_root or bundled_game_ui_asset_root())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        note = plain_label(description)
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        root.addWidget(note)

        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setObjectName("rewindRoleSearch")
        self.search_edit.setPlaceholderText(self.text("selector.search_roles"))
        self.search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.search_edit)

        toolbar = QHBoxLayout()
        select_all = TextActionButton(self.text("selector.select_all"))
        clear_all = TextActionButton(self.text("selector.clear"))
        for button in (select_all, clear_all):
            button.setStyleSheet(themed_style("padding:5px 14px;font-size:12px"))
        select_all.clicked.connect(lambda: self._set_visible_checked(True))
        clear_all.clicked.connect(lambda: self._set_visible_checked(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(clear_all)
        toolbar.addStretch()
        sort_label = ElidedTextLabel(self.text("common.sort"), self)
        sort_label.setMaximumWidth(70)
        toolbar.addWidget(sort_label)
        self.sort_selector = RoleSelectionSort(self, catalog=self.text_catalog)
        toolbar.addWidget(self.sort_selector)
        self.count_label = ElidedTextLabel("", self)
        self.count_label.setMaximumWidth(180)
        self.count_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
        toolbar.addWidget(self.count_label)
        root.addLayout(toolbar)

        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        self._grid = QGridLayout(content)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        self._grid.setAlignment(Qt.AlignTop)
        for role in sorted(roles, key=rewind_role_sort_key):
            if role.character_id not in self._slots:
                self._slots[role.character_id] = default_slot(role.slots, None)
            dpr = self.devicePixelRatioF()
            avatar_path = catalog.character_icon(role.character_id)
            portrait = (
                custom_role_portrait(64, dpr)
                if role.is_custom
                else temporary_role_portrait(str(avatar_path), 64, dpr) if avatar_path else QPixmap()
            )
            wrapper = RewindRoleCard(content, role, self._slots.get(role.character_id), portrait, catalog=self.text_catalog)
            self._wrappers[role.character_id] = wrapper
            card = wrapper.selection_button
            card.setChecked(role.character_id in selected)
            wrapper.slot_button.clicked.connect(lambda _=False, identifier=role.character_id: self._choose_slot(identifier))
            card.toggled.connect(lambda checked, identifier=role.character_id: self._role_toggled(identifier, checked))
            self._cards.append((card, role.character_id, role.name))
            wrapper.score_changed.connect(self._reflow_cards)
        self.sort_selector.currentIndexChanged.connect(lambda _index: self._reflow_cards())
        self._reflow_cards()
        scroll.setWidget(content)
        scroll.viewport().installEventFilter(self)
        root.addWidget(scroll, 1)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(themed_style("background:#21262d;border:none;"))
        root.addWidget(divider)
        footer = QHBoxLayout()
        hint = ElidedTextLabel(self.text("selector.click_slots"))
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        footer.addWidget(hint, 1)
        cancel_button = TextActionButton(self.text("common.cancel"))
        cancel_button.clicked.connect(self.reject)
        confirm_button = TextActionButton(self.text("selector.confirm"))
        confirm_button.setObjectName("rewindRoleConfirm")
        confirm_button.setDefault(True)
        confirm_button.setStyleSheet(themed_style(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #58a6ff;padding:6px 20px;}"
            "QPushButton:hover{background:#388bfd;}"
            "QPushButton:focus{border:2px solid #79c0ff;}"
        ))
        confirm_button.clicked.connect(self.accept)
        footer.addWidget(cancel_button)
        footer.addWidget(confirm_button)
        root.addLayout(footer)
        self._update_count()
        fit_dialog_to_available_screen(self, QSize(920, 780))

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._reflow_cards()
        return super().eventFilter(watched, event)

    def _role_toggled(self, identifier, checked):
        role = self._roles[identifier]
        if checked and len(role.slots) == 1:
            self._slots[identifier] = role.slots[0].reference
            self._wrappers[identifier].set_reference(role, self._slots[identifier])
        if checked and len(role.slots) > 1 and not self._choose_slot(identifier):
            card = next(card for card, key, _ in self._cards if key == identifier)
            card.blockSignals(True)
            card.setChecked(False)
            card.blockSignals(False)
            self._wrappers[identifier].sync_checked()
        self._update_count()

    def _choose_slot(self, identifier):
        role = self._roles[identifier]
        dialog = RewindSlotPicker(self, role, self._slots.get(identifier), text_catalog=getattr(self, 'text_catalog', None))
        if dialog.exec() != QDialog.Accepted:
            return False
        self._slots[identifier] = dialog.selected_reference()
        self._wrappers[identifier].set_reference(role, self._slots[identifier])
        return True

    def selected_slots(self):
        return dict(self._slots)

    def _apply_filter(self, text: str) -> None:
        self._reflow_cards(str(text or "").strip())

    def _reflow_cards(self, keyword: str | None = None) -> None:
        keyword = self.search_edit.text().strip() if keyword is None else keyword
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [
            row for row in self._cards
            if not keyword or match_pinyin(row[2], keyword)
        ]
        visible.sort(key=lambda row: self.sort_selector.sort_key(
            row[0].property("roleScore"), row[2], row[1]))
        visible_ids = {row[1] for row in visible}
        # 随实际滚动视口重排；不改角色/槽位草稿，也不以横向滚动隐藏右侧角色。
        columns = max(1, min(5, (self.scroll.viewport().width() + 12) // 174))
        for column in range(5):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        for _card, character_id, _name in self._cards:
            self._wrappers[character_id].setVisible(character_id in visible_ids)
        for index, (card, _character_id, _name) in enumerate(visible):
            wrapper = self._wrappers[_character_id]
            self._grid.addWidget(wrapper, index // columns, index % columns)
            wrapper.setVisible(True)

    def _set_visible_checked(self, checked: bool) -> None:
        for card, _character_id, _name in self._cards:
            if not self._wrappers[_character_id].isHidden():
                card.blockSignals(True)
                card.setChecked(checked)
                card.blockSignals(False)
                self._wrappers[_character_id].sync_checked()
        self._update_count()

    def _update_count(self, _checked: bool | None = None) -> None:
        self.count_label.setText(self.text("selector.selected_roles", count=len(self.selected_character_ids())))

    def selected_character_ids(self) -> tuple[int, ...]:
        return tuple(
            character_id
            for card, character_id, _role_name in self._cards
            if card.isChecked()
        )
