# 编辑扫描后管理的类型范围和角色作用域。
"""Independent picker dialogs used by the post-scan settings dialog."""

from __future__ import annotations

from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from PySide6.QtCore import QEvent, Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import theme_color, themed_style
from src.features.inventory.warehouse.display.cards import warehouse_shape_pixmap
from src.ui.roles.role_portrait import custom_role_portrait, temporary_role_portrait
from src.ui.common.widgets import match_pinyin
from src.domain.names.role_name_order import role_name_sort_key
from src.features.toolbox.rewind.role_card import RewindRoleCard
from src.domain.rewind.rewind_loadout import default_slot
from src.services.rewind.recommendation import RewindTargetRole


def _button_style(checked: bool) -> str:
    if checked:
        return themed_style(
            "QPushButton{border:2px solid #2f81f7;background:#10243f;color:#f0f6fc;border-radius:6px;padding:4px}"
        )
    return themed_style(
        "QPushButton{border:1px solid #30363d;background:#161b22;color:#c9d1d9;border-radius:6px;padding:4px}"
    )


def type_range_summary(type_scope: str, shape_count: int, shape_total: int, set_count: int, set_total: int) -> str:
    """选择保留在草稿中，只有处理类别内的范围计入本次生效数量。"""
    parts = []
    for kind, label, count, total in (
        ("drive", "驱动", shape_count, shape_total),
        ("tape", "卡带", set_count, set_total),
    ):
        parts.append(
            f"{label} {count}/{total}"
            if type_scope in {"all", kind}
            else f'<span style="color:{theme_color("#f85149")}">{label} 0/0</span>'
        )
    return "，".join(parts)


class TypeRangeDialog(QDialog):
    def __init__(
        self,
        parent,
        shape_options: list[tuple[str, int]],
        set_options: list[str],
        selected_shape_ids: list[str],
        selected_set_names: list[str],
        *,
        type_scope: str = "all",
        text_catalog=None,
    ):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.self_4'))
        self.setMinimumSize(760, 560)
        self.shape_options = shape_options
        self.set_options = set_options
        self.type_scope = type_scope
        self.shape_buttons: list[tuple[QPushButton, str]] = []
        self.set_checks: list[tuple[QCheckBox, str]] = []

        root = QVBoxLayout(self)
        root.setSpacing(12)
        self.range_summary = plain_label()
        self.range_summary.setWordWrap(False)
        root.addWidget(self.range_summary)
        root.addWidget(self._build_shape_section(set(selected_shape_ids)))
        root.addWidget(self._build_set_section(set(selected_set_names)), 1)
        # 处理类别只控制本次生效口径；非当前类别仍可编辑，单项与全选保存同一草稿。
        for button, _shape_id in self.shape_buttons:
            button.toggled.connect(self._update_summary)
        for checkbox, _set_name in self.set_checks:
            checkbox.toggled.connect(self._update_summary)
        self._update_summary()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _update_summary(self, _checked: bool = False) -> None:
        shapes, sets = self.selected_values()
        self.range_summary.setText(
            type_range_summary(
                self.type_scope,
                len(shapes),
                len(self.shape_options),
                len(sets),
                len(self.set_options),
            )
        )

    def _build_shape_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('identification.page.build_identify_page.label_3')))
        select_all = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('selector.select_all'))
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
            title = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.build_shape_section.title', area=f'{area}'))
            title.setFixedWidth(36)
            row.addWidget(title)
            for shape_id in shape_ids:
                button = QPushButton(shape_id)
                button.setCheckable(True)
                button.setChecked(shape_id in selected)
                button.setToolTip(shape_id)
                button.setMinimumSize(84, 54)
                pixmap = warehouse_shape_pixmap(shape_id, "Gold", text_catalog=getattr(self, 'text_catalog', None))
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
        header.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('identification.page.build_identify_page.label_4')))
        select_all = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('selector.select_all'))
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
        role_options: list[tuple[int, str, str, bool]],
        selected_character_ids: list[int],
        *,
        scored_roles=(),
        text_catalog=None,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.self_5'))
        self.setMinimumSize(620, 500)
        self.resize(700, 620)
        self._role_options = sorted(
            role_options,
            key=lambda item: (role_name_sort_key(item[1]), item[0]),
        )
        self._scored_roles = {role.character_id: role for role in scored_roles}
        selected_ids = {int(value) for value in selected_character_ids}

        root = QVBoxLayout(self)
        root.setSpacing(10)
        description = plain_label(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.description'))
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(description)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(resolve_text_catalog(text_catalog).render('matching.search'))
        self.search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.search_edit)

        toolbar = QHBoxLayout()
        select_all = TextActionButton(resolve_text_catalog(text_catalog).render('selector.select_all'))
        clear_all = TextActionButton(resolve_text_catalog(text_catalog).render('selector.clear'))
        select_all.clicked.connect(lambda: self._set_visible_items_checked(True))
        clear_all.clicked.connect(lambda: self._set_visible_items_checked(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(clear_all)
        toolbar.addStretch()
        self.count_label = plain_label()
        self.count_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
        toolbar.addWidget(self.count_label)
        root.addLayout(toolbar)

        self.role_scroll = QScrollArea()
        self.role_scroll.setWidgetResizable(True)
        self.role_scroll.setFrameShape(QFrame.NoFrame)
        self.role_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.role_scroll.setMinimumHeight(300)
        self.role_grid_widget = QWidget()
        self.role_grid = QGridLayout(self.role_grid_widget)
        self.role_grid.setContentsMargins(4, 4, 4, 4)
        self.role_grid.setHorizontalSpacing(8)
        self.role_grid.setVerticalSpacing(8)
        self.role_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.role_cards: list[tuple[QToolButton, int, str]] = []
        self._role_wrappers = {}
        self._scored_refs = {}
        for character_id, role_name, avatar_path, is_custom in self._role_options:
            # Bind the parent before the card is ever shown.  A parentless
            # widget briefly becomes a top-level window on Windows, which
            # previously caused a rapid flash while opening this dialog.
            role = self._scored_roles.get(character_id) or RewindTargetRole(
                character_id=character_id,
                name=role_name,
                default_suit_id=None,
                is_custom=is_custom,
                slots=(),
            )
            dpr = max(1.0, float(self.devicePixelRatioF()))
            portrait = (
                custom_role_portrait(64, dpr)
                if is_custom
                else temporary_role_portrait(avatar_path, 64, dpr)
                if avatar_path
                else QPixmap()
            )
            wrapper = RewindRoleCard(
                self.role_grid_widget, role, default_slot(role.slots, None), portrait, catalog=None
            )
            self._scored_refs[character_id] = default_slot(role.slots, None)
            wrapper.selection_button.setChecked(character_id in selected_ids)
            wrapper.slot_button.clicked.connect(
                lambda _=False, identifier=character_id: self._choose_scored_slot(identifier)
            )
            wrapper.selection_button.toggled.connect(self._update_count)
            self._role_wrappers[character_id] = wrapper
            self.role_cards.append((wrapper.selection_button, character_id, role_name))
        self._reflow_cards()
        self.role_scroll.setWidget(self.role_grid_widget)
        self.role_scroll.viewport().installEventFilter(self)
        root.addWidget(self.role_scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_count()

    def _apply_filter(self, text: str) -> None:
        self._reflow_cards(str(text or "").strip())

    def eventFilter(self, watched, event):
        if watched is self.role_scroll.viewport() and event.type() == QEvent.Resize:
            self._reflow_cards()
        return super().eventFilter(watched, event)

    def _reflow_cards(self, keyword: str = "") -> None:
        while self.role_grid.count():
            self.role_grid.takeAt(0)
        visible_cards = [card for card in self.role_cards if not keyword or match_pinyin(card[2], keyword)]
        scored = self._scored_roles
        visible_cards.sort(
            key=lambda row: (
                -(
                    max((slot.score for slot in scored[row[1]].slots if slot.score is not None), default=-1.0)
                    if row[1] in scored
                    else -1.0
                ),
                role_name_sort_key(row[2]),
                row[1],
            )
        )
        for card, _character_id, _role_name in self.role_cards:
            (card.parentWidget() if isinstance(card.parentWidget(), RewindRoleCard) else card).setVisible(False)
            card.setVisible(False)
        columns = max(1, min(5, (self.role_scroll.viewport().width() + 8) // 162))
        for index, (card, _character_id, _role_name) in enumerate(visible_cards):
            container = card.parentWidget() if isinstance(card.parentWidget(), RewindRoleCard) else card
            self.role_grid.addWidget(container, index // columns, index % columns)
            container.setVisible(True)
            card.setVisible(True)

    def _choose_scored_slot(self, identifier):
        role = self._scored_roles[identifier]
        from src.features.toolbox.rewind.slot_picker import RewindSlotPicker

        wrapper = self._role_wrappers[identifier]
        picker = RewindSlotPicker(self, role, self._scored_refs.get(identifier))
        if picker.exec() == QDialog.Accepted:
            reference = picker.selected_reference()
            self._scored_refs[identifier] = reference
            wrapper.set_reference(role, reference)

    def _set_visible_items_checked(self, checked: bool) -> None:
        for card, _character_id, _role_name in self.role_cards:
            if not card.isHidden():
                card.setChecked(checked)
        self._update_count()

    def _update_count(self, _checked: bool | None = None) -> None:
        self.count_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.update_count.count_label', value=f'{len(self.selected_character_ids())}'))

    def selected_character_ids(self) -> list[int]:
        return [character_id for card, character_id, _role_name in self.role_cards if card.isChecked()]
