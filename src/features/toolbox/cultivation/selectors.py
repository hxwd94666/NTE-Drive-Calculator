# 提供养成计算器的角色与弧盘选择控件。
"""Single- and multi-choice image-card selectors for cultivation targets."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.role_name_order import role_name_sort_key
from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.image_scaling import asset_pixmap
from src.ui.widgets import match_pinyin


class CultivationImageSelector(QDialog):
    """A searchable image-card dialog with optional multi-role selection."""

    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        description: str,
        options: tuple[tuple[str, str, str | None], ...],
        selected_id: str | None,
        selected_ids: tuple[str, ...] = (),
        multi_select: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("cultivationImageSelector")
        self._options = (
            tuple(sorted(options, key=lambda item: (role_name_sort_key(item[1]), item[0])))
            if multi_select or title == "选择角色" else options
        )
        self._multi_select = multi_select
        self._cards: list[tuple[QToolButton, str, str]] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(not multi_select)
        self._build(description, set(selected_ids) if multi_select else {selected_id})
        fit_dialog_to_available_screen(self, QSize(760, 620))

    def _build(self, description: str, selected_ids: set[str | None]) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        note = QLabel(description, self)
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(note)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索（支持拼音）")
        self._search.setInputMethodHints(
            Qt.InputMethodHint.ImhLatinOnly | Qt.InputMethodHint.ImhNoPredictiveText
        )
        self._search.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)
        if self._multi_select:
            toolbar = QHBoxLayout()
            select_all = QPushButton("全选", self)
            clear_all = QPushButton("清空", self)
            select_all.clicked.connect(lambda: self._set_all_checked(True))
            clear_all.clicked.connect(lambda: self._set_all_checked(False))
            toolbar.addWidget(select_all)
            toolbar.addWidget(clear_all)
            toolbar.addStretch(1)
            self._count = QLabel(self)
            toolbar.addWidget(self._count)
            root.addLayout(toolbar)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._grid_widget = QWidget(self._scroll)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for option_id, name, icon_path in self._options:
            card = QToolButton(self._grid_widget)
            card.setCheckable(True)
            card.setChecked(option_id in selected_ids)
            card.setText(name)
            card.setToolTip(name)
            card.setFixedSize(116, 132)
            card.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            card.setIconSize(QSize(76, 76))
            if icon_path:
                pixmap = asset_pixmap(icon_path, 76, card.devicePixelRatioF())
                if not pixmap.isNull():
                    card.setIcon(QIcon(pixmap))
                    side = min(76, round(pixmap.width() / pixmap.devicePixelRatio()))
                    card.setIconSize(QSize(side, side))
            card.setStyleSheet(themed_style(
                "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:8px;padding:6px;font-size:12px;font-weight:700;}"
                "QToolButton:hover{border-color:#58a6ff;background:#1f6feb22;}"
                "QToolButton:checked{border:2px solid #58a6ff;background:#1f6feb;color:#fff;}"
            ))
            self._group.addButton(card)
            self._cards.append((card, option_id, name))
            if self._multi_select:
                card.toggled.connect(self._refresh_count)
        if self._multi_select:
            self._refresh_count()
        self._reflow()
        self._scroll.setWidget(self._grid_widget)
        root.addWidget(self._scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        self._reflow(str(text or "").strip())

    def _reflow(self, keyword: str = "") -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [
            item for item in self._cards
            if not keyword or match_pinyin(item[2], keyword)
        ]
        for card, _option_id, _name in self._cards:
            card.setVisible(False)
        for index, (card, _option_id, _name) in enumerate(visible):
            self._grid.addWidget(card, index // 5, index % 5)
            card.setVisible(True)

    def selected_id(self) -> str | None:
        return next((
            option_id for card, option_id, _name in self._cards if card.isChecked()
        ), None)

    def selected_ids(self) -> tuple[str, ...]:
        return tuple(option_id for card, option_id, _name in self._cards if card.isChecked())

    def _set_all_checked(self, checked: bool) -> None:
        for card, _option_id, _name in self._cards:
            card.setChecked(checked)

    def _refresh_count(self, *_args: object) -> None:
        self._count.setText(f"已选 {len(self.selected_ids())} 名")


def select_cultivation_item(
    parent: QWidget,
    *,
    title: str,
    description: str,
    options: tuple[tuple[str, str, str | None], ...],
    selected_id: str | None,
) -> str | None:
    """Run a selector and return the newly confirmed identity, if any."""

    dialog = CultivationImageSelector(
        parent,
        title=title,
        description=description,
        options=options,
        selected_id=selected_id,
    )
    try:
        return dialog.selected_id() if dialog.exec() == QDialog.DialogCode.Accepted else None
    finally:
        dialog.deleteLater()


def select_cultivation_items(
    parent: QWidget,
    *,
    title: str,
    description: str,
    options: tuple[tuple[str, str, str | None], ...],
    selected_ids: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Return the entire confirmed selection; cancellation leaves the draft intact."""

    dialog = CultivationImageSelector(
        parent,
        title=title,
        description=description,
        options=options,
        selected_id=None,
        selected_ids=selected_ids,
        multi_select=True,
    )
    try:
        return dialog.selected_ids() if dialog.exec() == QDialog.DialogCode.Accepted else None
    finally:
        dialog.deleteLater()


__all__ = ["CultivationImageSelector", "select_cultivation_item", "select_cultivation_items"]
