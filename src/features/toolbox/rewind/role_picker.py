# 提供带配装槽位选择的倒带角色多选窗口。
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)
from src.app.theme import current_style_sheet, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.rewind_loadout import default_slot
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.services.rewind.recommendation import RewindTargetRole
from src.ui.role_portrait import custom_role_portrait
from src.features.toolbox.rewind.role_card import RewindRoleCard
from src.features.toolbox.rewind.role_selection import rewind_role_sort_key
from src.features.toolbox.rewind.slot_picker import RewindSlotPicker
from src.ui.widgets import match_pinyin


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
    ) -> None:
        super().__init__(parent)
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
        note = QLabel(description)
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        root.addWidget(note)

        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setObjectName("rewindRoleSearch")
        self.search_edit.setPlaceholderText("搜索角色（支持拼音）")
        self.search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.search_edit)

        toolbar = QHBoxLayout()
        select_all = QPushButton("全选")
        clear_all = QPushButton("清空")
        for button in (select_all, clear_all):
            button.setStyleSheet(themed_style("padding:5px 14px;font-size:12px"))
        select_all.clicked.connect(lambda: self._set_visible_checked(True))
        clear_all.clicked.connect(lambda: self._set_visible_checked(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(clear_all)
        toolbar.addStretch()
        self.count_label = QLabel()
        self.count_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
        toolbar.addWidget(self.count_label)
        root.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self._grid = QGridLayout(content)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        for column in range(5):
            self._grid.setColumnStretch(column, 1)
        self._grid.setAlignment(Qt.AlignTop)
        for role in sorted(roles, key=rewind_role_sort_key):
            if role.character_id not in self._slots:
                self._slots[role.character_id] = default_slot(role.slots, None)
            dpr = self.devicePixelRatioF()
            avatar_path = catalog.character_icon(role.character_id)
            portrait = custom_role_portrait(64, dpr) if role.is_custom else QPixmap(str(avatar_path)) if avatar_path else QPixmap()
            if not portrait.isNull() and not role.is_custom:
                edge = round(64 * dpr)
                portrait = portrait.scaled(edge, edge, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                portrait.setDevicePixelRatio(dpr)
            wrapper = RewindRoleCard(content, role, self._slots.get(role.character_id), portrait)
            self._wrappers[role.character_id] = wrapper
            card = wrapper.selection_button
            card.setChecked(role.character_id in selected)
            wrapper.slot_button.clicked.connect(lambda _=False, identifier=role.character_id: self._choose_slot(identifier))
            card.toggled.connect(lambda checked, identifier=role.character_id: self._role_toggled(identifier, checked))
            self._cards.append((card, role.character_id, role.name))
        self._reflow_cards()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(themed_style("background:#21262d;border:none;"))
        root.addWidget(divider)
        footer = QHBoxLayout()
        hint = QLabel("点击角色勾选，点击槽位切换方案")
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        footer.addWidget(hint, 1)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        confirm_button = QPushButton("确定")
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
        dialog = RewindSlotPicker(self, role, self._slots.get(identifier))
        if dialog.exec() != QDialog.Accepted:
            return False
        self._slots[identifier] = dialog.selected_reference()
        self._wrappers[identifier].set_reference(role, self._slots[identifier])
        return True

    def selected_slots(self):
        return dict(self._slots)

    def _apply_filter(self, text: str) -> None:
        self._reflow_cards(str(text or "").strip())

    def _reflow_cards(self, keyword: str = "") -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [
            row for row in self._cards
            if not keyword or match_pinyin(row[2], keyword)
        ]
        for card, _character_id, _name in self._cards:
            self._wrappers[_character_id].setVisible(False)
        for index, (card, _character_id, _name) in enumerate(visible):
            wrapper = self._wrappers[_character_id]
            self._grid.addWidget(wrapper, index // 5, index % 5)
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
        self.count_label.setText(f"已选 {len(self.selected_character_ids())} 名")

    def selected_character_ids(self) -> tuple[int, ...]:
        return tuple(
            character_id
            for card, character_id, _role_name in self._cards
            if card.isChecked()
        )
