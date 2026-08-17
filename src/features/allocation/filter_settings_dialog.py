# 编辑账号级分配候选过滤设置。
"""Multi-select allocation filter dialog."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.dialogs import show_help
from src.app.theme import themed_style
from src.services.allocation_filter_settings import (
    AllocationFilterSettings,
    AllocationFilterValidationError,
)


_TILE_STYLE = (
    "QPushButton#allocationFilterTile{background:#21262d;color:#8b949e;"
    "border:1px solid #30363d;border-radius:7px;padding:7px 12px;font-weight:600;}"
    "QPushButton#allocationFilterTile:hover{background:#30363d;color:#c9d1d9;}"
    "QPushButton#allocationFilterTile:checked{background:#1f6feb33;color:#58a6ff;"
    "border-color:#58a6ff;}"
)


class AllocationFilterSettingsDialog(QDialog):
    """Edit a draft and publish it only when the user confirms a valid value."""

    def __init__(
        self,
        initial: AllocationFilterSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("分配设置")
        self.resize(QSize(340, 205))
        self.setMinimumWidth(320)
        current = initial or AllocationFilterSettings()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        module = QGroupBox("筛选设置")
        module.setObjectName("allocationFilterModule")
        module_layout = QVBoxLayout(module)
        module_layout.setContentsMargins(8, 8, 8, 8)
        module_layout.setSpacing(5)

        module_help = QPushButton("?", module)
        module_help.setObjectName("allocationFilterHelp")
        module_help.setFixedSize(20, 20)
        module_help.setToolTip("查看筛选设置说明")
        module_help.clicked.connect(
            lambda _checked=False, parent=module_help: show_help(
                parent,
                "筛选设置说明",
                "对已选类型：仅已选品质会进入角色管理筛选，其他品质会被过滤；未选类型按默认规则处理。",
            )
        )
        module_help.move(
            36 + module.fontMetrics().horizontalAdvance(module.title()),
            0,
        )
        module_help.raise_()

        self.type_buttons = self._selection_row(
            module_layout,
            "分配类型",
            (("卡带", "tape"), ("驱动", "drive")),
            current.item_types,
        )
        self.quality_buttons = self._selection_row(
            module_layout,
            "分配品质",
            (("蓝色", "Blue"), ("紫色", "Purple"), ("金色", "Gold")),
            current.qualities,
        )
        root.addWidget(module)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_valid_settings)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _selection_row(
        parent_layout: QVBoxLayout,
        title: str,
        options: tuple[tuple[str, str], ...],
        selected: frozenset[str],
    ) -> dict[str, QPushButton]:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(title)
        label.setMinimumWidth(72)
        row.addWidget(label)
        buttons: dict[str, QPushButton] = {}
        for text, value in options:
            button = QPushButton(text)
            button.setObjectName("allocationFilterTile")
            button.setStyleSheet(themed_style(_TILE_STYLE))
            button.setCheckable(True)
            button.setChecked(value in selected)
            button.setProperty("filterValue", value)
            buttons[value] = button
            row.addWidget(button)
        row.addStretch(1)
        parent_layout.addLayout(row)
        return buttons

    def settings(self) -> AllocationFilterSettings:
        settings = AllocationFilterSettings(
            qualities=frozenset(
                value for value, button in self.quality_buttons.items() if button.isChecked()
            ),
            item_types=frozenset(
                value for value, button in self.type_buttons.items() if button.isChecked()
            ),
        )
        settings.validate()
        return settings

    def _accept_valid_settings(self) -> None:
        try:
            self.settings()
        except AllocationFilterValidationError as exc:
            QMessageBox.warning(self, "分配设置无效", str(exc))
            return
        self.accept()
