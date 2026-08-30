# 战斗机制图鉴私有的卡片、徽记与折叠原语。
"""Private, player-facing widgets for the combat-mechanics catalog."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.services.static_catalog_mechanics_service import (
    MechanicsCard,
    PlayerField,
)


STATUS_LABELS = {
    "complete": "完整",
    "partial": "部分覆盖",
    "unavailable": "不可用",
    "not_applicable": "不适用",
}
STATUS_COLORS = {
    "complete": "#3fb950",
    "partial": "#d29922",
    "unavailable": "#f85149",
    "not_applicable": "#8b949e",
}
TONE_COLORS = {
    "neutral": "#c9d1d9",
    "accent": "#58a6ff",
    "success": "#3fb950",
    "warning": "#e3b341",
    "formula": "#d2a8ff",
}


def pill(text: str, *, color: str = "#58a6ff", parent=None) -> QLabel:
    label = QLabel(str(text), parent)
    label.setObjectName("mechanicsPill")
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(themed_style(
        f"QLabel#mechanicsPill{{color:{color};background:#0d1117;"
        f"border:1px solid {color};border-radius:9px;padding:2px 7px;"
        "font-size:9px;font-weight:800;}"
    ))
    return label


def status_pill(status: str, parent=None) -> QLabel:
    return pill(
        STATUS_LABELS.get(status, status),
        color=STATUS_COLORS.get(status, "#8b949e"),
        parent=parent,
    )


class MechanicsGalleryCard(QFrame):
    activated = Signal(str)

    def __init__(self, model: MechanicsCard, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.setObjectName("mechanicsGalleryCard")
        self.setProperty("recordId", model.record_id)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(210)
        self.setMinimumHeight(112)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(themed_style(
            "QFrame#mechanicsGalleryCard{background:#161b22;"
            "border:1px solid #30363d;border-radius:9px;}"
            "QFrame#mechanicsGalleryCard:hover{background:#182434;"
            "border-color:#58a6ff;}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 9)
        root.setSpacing(4)

        top = QHBoxLayout()
        eyebrow = QLabel(model.eyebrow, self)
        eyebrow.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:9px;font-weight:900;letter-spacing:1px;"
        ))
        top.addWidget(eyebrow)
        top.addStretch(1)
        root.addLayout(top)

        title = QLabel(model.title, self)
        title.setObjectName("mechanicsCardTitle")
        title.setWordWrap(True)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:14px;font-weight:900;"
        ))
        root.addWidget(title)
        subtitle = QLabel(model.subtitle, self)
        subtitle.setObjectName("mechanicsCardSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setMaximumHeight(40)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:10px;"))
        root.addWidget(subtitle, 1)


    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.model.record_id)
        super().mouseReleaseEvent(event)


class FieldCard(QFrame):
    def __init__(
        self,
        title: str,
        fields: Iterable[PlayerField],
        *,
        accent: str = "#30363d",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mechanicsFieldCard")
        self.setStyleSheet(themed_style(
            f"QFrame#mechanicsFieldCard{{background:transparent;border:0;"
            f"border-left:2px solid {accent};}}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 6, 8, 6)
        root.setSpacing(5)
        heading = QLabel(title, self)
        heading.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:13px;font-weight:900;"
        ))
        root.addWidget(heading)
        for field in fields:
            row = QWidget(self)
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(1)
            label = QLabel(field.label, row)
            label.setStyleSheet(themed_style(
                "color:#8b949e;font-size:9px;font-weight:800;"
            ))
            value = QLabel(field.value, row)
            value.setObjectName("mechanicsFieldValue")
            value.setWordWrap(True)
            color = TONE_COLORS.get(field.tone, TONE_COLORS["neutral"])
            size = "13px" if field.tone == "formula" else "10px"
            value.setStyleSheet(themed_style(
                f"color:{color};font-size:{size};font-weight:700;"
            ))
            row_layout.addWidget(label)
            row_layout.addWidget(value)
            root.addWidget(row)


class LinkButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(f"{text}  ›", parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(themed_style(
            "QPushButton{color:#58a6ff;background:#0d1117;border:1px solid #30363d;"
            "border-radius:9px;padding:7px 10px;text-align:left;font-size:10px;"
            "font-weight:800;}QPushButton:hover{border-color:#58a6ff;"
            "background:#182434;}"
        ))


class CollapsiblePanel(QFrame):
    """A compact disclosure panel, collapsed by default."""

    def __init__(
        self,
        title: str,
        summary: str,
        *,
        expanded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mechanicsDisclosure")
        self.setStyleSheet(themed_style(
            "QFrame#mechanicsDisclosure{background:#0d1117;"
            "border:1px solid #30363d;border-radius:12px;}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)
        self.toggle = QToolButton(self)
        self.toggle.setObjectName("mechanicsDisclosureToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setText(f"{title} · {summary}")
        self.toggle.setStyleSheet(themed_style(
            "QToolButton{color:#c9d1d9;background:transparent;border:0;"
            "font-size:11px;font-weight:900;text-align:left;padding:3px;}"
        ))
        root.addWidget(self.toggle)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 2, 4, 4)
        self.body_layout.setSpacing(7)
        self.body.setVisible(expanded)
        root.addWidget(self.body)
        self.toggle.toggled.connect(self._set_expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.body.setVisible(expanded)
