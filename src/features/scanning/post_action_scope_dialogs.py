# 编辑扫描后管理的类型范围和角色作用域。
"""Independent picker dialogs used by the post-scan settings dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
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
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.ui.widgets import match_pinyin


def _button_style(checked: bool) -> str:
    if checked:
        return themed_style("QPushButton{border:2px solid #2f81f7;background:#10243f;color:#f0f6fc;border-radius:6px;padding:4px}")
    return themed_style("QPushButton{border:1px solid #30363d;background:#161b22;color:#c9d1d9;border-radius:6px;padding:4px}")


class TypeRangeDialog(QDialog):
    def __init__(
        self,
        parent,
        shape_options: list[tuple[str, int]],
        set_options: list[str],
        selected_shape_ids: list[str],
        selected_set_names: list[str],
    ):
        super().__init__(parent)
        self.setWindowTitle("选择类型范围")
        self.setMinimumSize(760, 560)
        self.shape_options = shape_options
        self.set_options = set_options
        self.shape_buttons: list[tuple[QPushButton, str]] = []
        self.set_checks: list[tuple[QCheckBox, str]] = []

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(self._build_shape_section(set(selected_shape_ids)))
        root.addWidget(self._build_set_section(set(selected_set_names)), 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_shape_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("驱动形状"))
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_shapes(True))
        header.addStretch()
        header.addWidget(select_all)
        layout.addLayout(header)

        grouped: dict[int, list[str]] = {2: [], 3: [], 4: []}
        for shape_id, area in self.shape_options:
            grouped.setdefault(area, []).append(shape_id)

        for area in sorted(grouped):
            shape_ids = grouped.get(area, [])
            if not shape_ids:
                continue
            row = QHBoxLayout()
            row.setSpacing(8)
            title = QLabel(f"{area}型")
            title.setFixedWidth(36)
            row.addWidget(title)
            for shape_id in shape_ids:
                button = QPushButton(shape_id)
                button.setCheckable(True)
                button.setChecked(shape_id in selected)
                button.setToolTip(shape_id)
                button.setMinimumSize(84, 54)
                pixmap = warehouse_shape_pixmap(shape_id, "Gold")
                if not pixmap.isNull():
                    button.setIcon(QIcon(pixmap))
                    button.setIconSize(QSize(32, 32))
                button.setStyleSheet(_button_style(button.isChecked()))
                button.toggled.connect(lambda checked, b=button: b.setStyleSheet(_button_style(checked)))
                self.shape_buttons.append((button, shape_id))
                row.addWidget(button)
            row.addStretch()
            layout.addLayout(row)
        return section

    def _build_set_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("卡带套装"))
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_sets(True))
        header.addStretch()
        header.addWidget(select_all)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for index, set_name in enumerate(self.set_options):
            checkbox = QCheckBox(set_name)
            checkbox.setChecked(set_name in selected)
            self.set_checks.append((checkbox, set_name))
            grid.addWidget(checkbox, index // 2, index % 2)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return section

    def _set_all_shapes(self, checked: bool) -> None:
        for button, _shape_id in self.shape_buttons:
            button.setChecked(checked)

    def _set_all_sets(self, checked: bool) -> None:
        for checkbox, _set_name in self.set_checks:
            checkbox.setChecked(checked)

    def selected_values(self) -> tuple[list[str], list[str]]:
        shape_ids = [shape_id for button, shape_id in self.shape_buttons if button.isChecked()]
        set_names = [set_name for checkbox, set_name in self.set_checks if checkbox.isChecked()]
        return shape_ids, set_names




class RoleScopeDialog(QDialog):
    """Select the roles used only by discard/lock scoring."""

    def __init__(
        self,
        parent,
        role_options: list[tuple[int, str, str]],
        selected_character_ids: list[int],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择弃置/锁定评估角色")
        self.setMinimumSize(620, 500)
        self.resize(700, 620)
        self._role_options = list(role_options)
        selected_ids = {int(value) for value in selected_character_ids}

        root = QVBoxLayout(self)
        root.setSpacing(10)
        description = QLabel("这些角色只用于本次弃置/锁定评分，不会改变计算页面的角色选择或优先级。")
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(description)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索角色（支持拼音）")
        self.search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.search_edit)

        toolbar = QHBoxLayout()
        select_all = QPushButton("全选")
        clear_all = QPushButton("清空")
        select_all.clicked.connect(lambda: self._set_visible_items_checked(True))
        clear_all.clicked.connect(lambda: self._set_visible_items_checked(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(clear_all)
        toolbar.addStretch()
        self.count_label = QLabel()
        self.count_label.setStyleSheet(
            themed_style("color:#58a6ff;font-weight:700")
        )
        toolbar.addWidget(self.count_label)
        root.addLayout(toolbar)

        self.role_scroll = QScrollArea()
        self.role_scroll.setWidgetResizable(True)
        self.role_scroll.setFrameShape(QFrame.NoFrame)
        self.role_scroll.setMinimumHeight(300)
        self.role_grid_widget = QWidget()
        self.role_grid = QGridLayout(self.role_grid_widget)
        self.role_grid.setContentsMargins(4, 4, 4, 4)
        self.role_grid.setHorizontalSpacing(8)
        self.role_grid.setVerticalSpacing(8)
        self.role_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.role_cards: list[tuple[QToolButton, int, str]] = []
        for character_id, role_name, avatar_path in self._role_options:
            # Bind the parent before the card is ever shown.  A parentless
            # widget briefly becomes a top-level window on Windows, which
            # previously caused a rapid flash while opening this dialog.
            card = QToolButton(self.role_grid_widget)
            card.setCheckable(True)
            card.setChecked(character_id in selected_ids)
            card.setText(role_name)
            card.setToolTip(role_name)
            if avatar_path:
                card.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                card.setIconSize(QSize(76, 76))
                card.setFixedSize(116, 116)
                card.setIcon(QIcon(avatar_path))
            else:
                # Custom roles do not have a game avatar.  Keep their picker
                # cards deliberately text-only instead of rendering a blank icon.
                card.setToolButtonStyle(Qt.ToolButtonTextOnly)
                card.setFixedSize(116, 44)
            card.setStyleSheet(
                themed_style(
                    "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
                    "border-radius:8px;padding:6px;font-size:12px;font-weight:700;}"
                    "QToolButton:hover{border-color:#58a6ff;background:#1f6feb22;}"
                    "QToolButton:checked{border:2px solid #58a6ff;background:#1f6feb;color:#fff;}"
                )
            )
            card.toggled.connect(self._update_count)
            self.role_cards.append((card, character_id, role_name))
        self._reflow_cards()
        self.role_scroll.setWidget(self.role_grid_widget)
        root.addWidget(self.role_scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_count()

    def _apply_filter(self, text: str) -> None:
        self._reflow_cards(str(text or "").strip())

    def _reflow_cards(self, keyword: str = "") -> None:
        while self.role_grid.count():
            self.role_grid.takeAt(0)
        visible_cards = [
            card
            for card in self.role_cards
            if not keyword or match_pinyin(card[2], keyword)
        ]
        for card, _character_id, _role_name in self.role_cards:
            card.setVisible(False)
        for index, (card, _character_id, _role_name) in enumerate(visible_cards):
            self.role_grid.addWidget(card, index // 5, index % 5)
            card.setVisible(True)

    def _set_visible_items_checked(self, checked: bool) -> None:
        for card, _character_id, _role_name in self.role_cards:
            if not card.isHidden():
                card.setChecked(checked)
        self._update_count()

    def _update_count(self, _checked: bool | None = None) -> None:
        self.count_label.setText(f"已选{len(self.selected_character_ids())}名")

    def selected_character_ids(self) -> list[int]:
        return [
            character_id
            for card, character_id, _role_name in self.role_cards
            if card.isChecked()
        ]


