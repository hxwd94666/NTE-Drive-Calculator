# 以正式身份展示带头像的批量角色选择卡片，筛选和布局变化不丢失勾选。
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QMenu,
    QSizePolicy,
    QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.theme import current_style_sheet, themed_style
from src.integrations.runtime.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.ui.roles.role_selection_card import RoleSelectionCard
from src.ui.roles.role_selection_sort import RoleSelectionSort
from src.ui.common.image_scaling import asset_pixmap
from src.ui.roles.role_portrait import custom_role_portrait
from src.ui.common.widgets import match_pinyin
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_label, plain_tooltip, set_button_text
from src.features.inventory.equipment.assembly.assembly_surface import AssemblySurface


@dataclass(frozen=True)
class BatchChoice:
    key: int
    label: str
    detail: str = ""
    enabled: bool = True
    options: tuple[tuple[int, str], ...] = ()
    default_id: int | None = None
    character_id: int | None = None
    subtitle: str = ""
    score: float | None = None
    option_scores: tuple[tuple[int, float | None], ...] = ()
    score_source: str = "calculation"


class _ChoiceCard(RoleSelectionCard):
    def __init__(self, parent, choice, portrait, catalog):
        super().__init__(parent, choice.label, portrait, catalog=catalog)
        self.choice = choice
        self.button = self.selection_button
        self.button.setProperty("batchChoiceKey", choice.key)
        self.button.setEnabled(choice.enabled)
        self._selected_id = choice.key
        self.menu = None
        if choice.options:
            self.menu = QMenu(self)
            self.menu.setAccessibleName(self.text("selector.role_slots", role=choice.label))
            self._selected_id = next(
                (key for key, _name in choice.options if key == choice.default_id), choice.options[0][0]
            )
            for key, name in choice.options:
                action = self.menu.addAction(name)
                action.setCheckable(True)
                action.setData(key)
                action.triggered.connect(lambda _checked=False, identifier=key: self._select_slot(identifier))
            self.slot_button.clicked.connect(
                lambda: self.menu.popup(self.slot_button.mapToGlobal(self.slot_button.rect().bottomLeft()))
            )
        self._refresh_slot()
        self.setToolTip(
            plain_tooltip(choice.detail if not choice.enabled else "")
        )

    def _select_slot(self, identifier):
        self._selected_id = identifier
        self._refresh_slot()

    def _refresh_slot(self):
        choice = self.choice
        caption = next(
            (name for key, name in choice.options if key == self._selected_id),
            choice.subtitle or choice.detail or self.text("selector.saved_plan"),
        )
        if not choice.enabled and choice.detail:
            caption = self.text("selector.caption_detail", caption=caption, detail=choice.detail)
        if self.menu is not None:
            for action in self.menu.actions():
                action.setChecked(action.data() == self._selected_id)
        score = dict(choice.option_scores).get(self._selected_id, choice.score)
        self.set_score(score)
        self.slot_button.set_caption(caption, available=bool(choice.options) and choice.enabled)
        self.slot_button.setAccessibleName(self.text("selector.slot_accessible", role=choice.label, slot=caption))
        source = self.text("selector.game_source" if choice.score_source == "game" else "selector.calculation_source")
        details = self.text(
            "selector.score_detail",
            source=source,
            slot=caption,
            score=self.score_label.text(),
            grade=self.grade_label.text(),
        )
        if choice.detail:
            details += "\n" + choice.detail
        self.score_label.setToolTip(plain_tooltip(details))
        self.score_label.setAccessibleDescription(source)
        self.button.setToolTip(plain_tooltip(details))
        self.button.setAccessibleDescription(details)
        self.slot_button.setToolTip(plain_tooltip(details))

    def selected_id(self):
        return int(self._selected_id)


class BatchSelectionDialog(AssemblySurface):
    def __init__(
        self, parent, choices, *, title, description, accept_text, show_hints=True, initial_ids=(), catalog=None
    ):
        super().__init__(parent, catalog=catalog)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(current_style_sheet())
        self.setMinimumSize(620, 480)
        root = self.body_layout
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        hint = plain_label(description, self)
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
        root.addWidget(hint)
        hint.setVisible(bool(description) and show_hints)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(self.text("selector.search"))
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)
        actions = QHBoxLayout()
        select_all = TextActionButton(self.text("selector.select_all"), self)
        select_all.setToolTip(plain_tooltip(self.text("selector.select_all_hint")))
        select_none = TextActionButton(self.text("selector.clear"), self)
        select_none.setToolTip(plain_tooltip(self.text("selector.clear_hint")))
        for button in (select_all, select_none):
            button.setStyleSheet(themed_style("padding:5px 14px;font-size:12px"))
        actions.addWidget(select_all)
        actions.addWidget(select_none)
        actions.addStretch()
        sort_label = ElidedTextLabel(self.text("common.sort"), self)
        sort_label.setMaximumWidth(70)
        actions.addWidget(sort_label)
        self.sort_selector = RoleSelectionSort(self, catalog=self.text_catalog)
        actions.addWidget(self.sort_selector)
        self.count = ElidedTextLabel("", self)
        self.count.setMaximumWidth(180)
        self.count.setStyleSheet(themed_style("color:#58a6ff;font-weight:700;"))
        actions.addWidget(self.count)
        root.addLayout(actions)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(self.scroll)
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        context = getattr(parent, "app_context", None)
        paths = getattr(context, "paths", None)
        asset_root = (
            getattr(paths, "equipment_allocation_asset_root", None)
            or getattr(parent, "game_ui_asset_root", None)
            or bundled_game_ui_asset_root()
        )
        catalog = GameUiAssetCatalog(asset_root)
        self._cards = []
        for choice in choices:
            # 目标key可能是槽位ID；头像只按显式角色ID解析，绝不把槽位ID猜成角色ID。
            path = catalog.character_icon(choice.character_id) if choice.character_id is not None else None
            portrait = asset_pixmap(path, 64, self.devicePixelRatioF()) if path else None
            if portrait is None or portrait.isNull():
                portrait = custom_role_portrait(64, self.devicePixelRatioF())
            card = _ChoiceCard(content, choice, portrait, self.text_catalog)
            initial = next((identity for identity, _name in choice.options if identity in initial_ids), None)
            if initial is not None:
                card._select_slot(initial)
            card.button.setChecked(initial is not None or (not choice.options and choice.key in initial_ids))
            card.setVisible(True)
            card.button.toggled.connect(self._changed)
            card.score_changed.connect(self._reflow)
            self._cards.append(card)
        self.empty = plain_label(self.text("selector.empty"), content)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(themed_style("color:#8b949e;padding:24px;"))
        self.empty.hide()
        self.scroll.viewport().installEventFilter(self)
        divider = QFrame(self)
        divider.setFixedHeight(1)
        divider.setStyleSheet(themed_style("background:#30363d;border:0"))
        root.addWidget(divider)
        footer = QHBoxLayout()
        # 提示必须先归属本页再显示；无父标签 setVisible(True) 会短暂映射成独立 Windows 小窗。
        note = ElidedTextLabel(
            self.text("selector.click_slots")
            if any(card.menu is not None for card in self._cards)
            else self.text("selector.click_roles"),
            self.body_page,
        )
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
        footer.addWidget(note, 1)
        note.setVisible(show_hints)
        if not show_hints:
            footer.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        # 与倒带动作区保持同序；仅反转此按钮组，不改变页面文字或角色排列方向。
        self.buttons.setLayoutDirection(Qt.RightToLeft)
        # 反向按钮组自带内部弹性空间；限制为内容宽度，空白只能由外层左侧留白吸收。
        self.buttons.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        set_button_text(self.buttons.button(QDialogButtonBox.Ok), accept_text)
        self.buttons.button(QDialogButtonBox.Ok).setDefault(True)
        self.buttons.button(QDialogButtonBox.Ok).setStyleSheet(
            themed_style(
                "QPushButton{background:#1f6feb;color:#fff;border:1px solid #58a6ff;padding:6px 20px;}"
                "QPushButton:hover{background:#388bfd;}QPushButton:focus{border:2px solid #79c0ff;}"
            )
        )
        set_button_text(self.buttons.button(QDialogButtonBox.Cancel), self.text("common.cancel"))
        if not show_hints:
            self.buttons.button(QDialogButtonBox.Ok).setAutoDefault(False)
            self.buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        QWidget.setTabOrder(self.buttons.button(QDialogButtonBox.Cancel), self.buttons.button(QDialogButtonBox.Ok))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons, 0, Qt.AlignRight)
        root.addLayout(footer)
        select_all.clicked.connect(lambda: self.set_all(True))
        select_none.clicked.connect(lambda: self.set_all(False))
        self.search.textChanged.connect(self._filter)
        self.sort_selector.currentIndexChanged.connect(lambda _index: self._reflow())
        self._changed()
        fit_dialog_to_available_screen(self, QSize(920, 780))
        self._reflow()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._reflow()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self)
        self._reflow()

    def selected_ids(self) -> list[int]:
        return [card.selected_id() for card in self._cards if card.choice.enabled and card.button.isChecked()]

    def set_all(self, checked: bool) -> None:
        for card in self._cards:
            if card.choice.enabled and (not checked or not card.isHidden()):
                card.button.setChecked(checked)
        self._changed()

    def _filter(self, text):
        text = text.strip()
        for card in self._cards:
            choice = card.choice
            haystack = (
                f"{choice.label} {choice.subtitle} {choice.detail} {' '.join(name for _, name in choice.options)}"
            )
            card.setHidden(bool(text) and not match_pinyin(haystack, text))
        self._reflow()

    def _reflow(self):
        while self.grid.count():
            self.grid.takeAt(0)
        columns = max(1, min(5, (self.scroll.viewport().width() + 12) // 174))
        for column in range(5):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        visible = [card for card in self._cards if not card.isHidden()]
        visible.sort(
            key=lambda card: self.sort_selector.sort_key(
                card.selection_button.property("roleScore"), card.choice.label, card.choice.key
            )
        )
        for index, card in enumerate(visible):
            self.grid.addWidget(card, index // columns, index % columns)
        self.empty.setVisible(not visible)
        if not visible:
            self.grid.addWidget(self.empty, 0, 0, 1, columns)
        self.count.setText(self.text("selector.selected_count", count=len(self.selected_ids())))

    def _changed(self, *_args):
        count = len(self.selected_ids())
        self.count.setText(self.text("selector.selected_count", count=count))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(count > 0)


def choose_batch_items(
    parent, choices, *, title, description, accept_text, retain=False, show_hints=True, initial_ids=()
) -> list[int] | None:
    dialog = BatchSelectionDialog(
        parent,
        choices,
        title=title,
        description=description,
        accept_text=accept_text,
        show_hints=show_hints,
        initial_ids=initial_ids,
        catalog=getattr(parent, "text_catalog", None),
    )
    retained = False
    try:
        if dialog.exec() != QDialog.Accepted:
            return None
        if retain:
            parent._assembly_surface = dialog
            retained = True
        return dialog.selected_ids()
    finally:
        if not retained:
            dialog.deleteLater()
