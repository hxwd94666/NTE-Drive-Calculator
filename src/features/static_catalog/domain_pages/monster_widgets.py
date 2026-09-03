# 怪物资料库独立页面的私有卡片原语。
"""Private card widgets for the monster archive page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.services.static_catalog_monster_service import UNAVAILABLE


class ArchiveCard(QFrame):
    activated = Signal()

    def __init__(self, model, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.setObjectName("monsterArchiveCard")
        self.setProperty("playModeLabel", model.title)
        self.setCursor(Qt.PointingHandCursor if model.action else Qt.ArrowCursor)
        self.setMinimumWidth(190)
        self.setMinimumHeight(206)
        self.setMaximumHeight(228)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(themed_style(
            "QFrame#monsterArchiveCard{background:#161b22;border:1px solid transparent;"
            "border-radius:15px;}QFrame#monsterArchiveCard:hover{border-color:#58a6ff;}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(6)
        art = QLabel(self)
        art.setAlignment(Qt.AlignCenter)
        art.setFixedHeight(96)
        set_art(art, model.icon, 92, unavailable=model.unavailable)
        layout.addWidget(art)
        badge = QLabel(model.badge, self)
        badge.setStyleSheet(themed_style(
            "color:#39d0d8;font-size:9px;font-weight:900;letter-spacing:1px"
        ))
        layout.addWidget(badge)
        title = QLabel(model.title, self)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:14px;font-weight:900"))
        layout.addWidget(title)
        subtitle = QLabel(model.subtitle, self)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        layout.addWidget(subtitle)
        if model.action:
            self.activated.connect(model.action)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton and self.model.action:
            self.activated.emit()
        super().mouseReleaseEvent(event)


class MetricCard(QFrame):
    def __init__(self, label: str, value: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("statLabel", label)
        self.setStyleSheet(themed_style(
            "QFrame{background:#0d1117;border:0;border-radius:11px;}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        name = QLabel(label, self)
        name.setStyleSheet(themed_style(f"color:{accent};font-size:10px;font-weight:900"))
        number = QLabel(value, self)
        number.setWordWrap(True)
        number.setStyleSheet(themed_style("color:#f0f6fc;font-size:13px;font-weight:800"))
        layout.addWidget(name)
        layout.addWidget(number)


class ResistanceCard(QFrame):
    def __init__(self, label: str, value: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(themed_style(
            "QFrame{background:#0d1117;border:0;border-radius:9px;}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        row = QHBoxLayout()
        name = QLabel(label.removeprefix("抗性 "), self)
        name.setStyleSheet(themed_style("color:#c9d1d9;font-size:10px;font-weight:800"))
        number = QLabel(value, self)
        number.setStyleSheet(themed_style("color:#39d0d8;font-size:10px"))
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(number)
        layout.addLayout(row)
        bar = QProgressBar(self)
        bar.setRange(0, 100)
        bar.setValue(_percentage(value))
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        bar.setStyleSheet(themed_style(
            "QProgressBar{border:0;background:#30363d;border-radius:2px;}"
            "QProgressBar::chunk{background:#39d0d8;border-radius:2px;}"
        ))
        layout.addWidget(bar)


class BuffCard(QFrame):
    def __init__(
        self,
        label: str,
        value: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sceneBuffCard")
        self.setStyleSheet(themed_style(
            "QFrame#sceneBuffCard{background:#161b22;border:0;"
            "border-radius:12px;}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        title = QLabel(f"✦ {label}", self)
        title.setStyleSheet(themed_style("color:#e3b341;font-size:10px;font-weight:900"))
        text = QLabel(value, self)
        text.setWordWrap(True)
        text.setStyleSheet(themed_style("color:#f0f6fc;font-size:10px"))
        layout.addWidget(title)
        layout.addWidget(text)


class DropCard(QFrame):
    def __init__(
        self,
        label: str,
        value: str,
        parent=None,
        *,
        warning: bool = False,
    ) -> None:
        super().__init__(parent)
        accent = "#ff7b72" if warning else "#39d0d8"
        self.setObjectName("formalDropCard")
        self.setStyleSheet(themed_style(
            "QFrame#formalDropCard{background:#161b22;border:0;"
            "border-radius:12px;}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        title = QLabel(label, self)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style(
            f"color:{accent};font-size:10px;font-weight:900"
        ))
        text = QLabel(value, self)
        text.setWordWrap(True)
        text.setStyleSheet(themed_style("color:#f0f6fc;font-size:11px"))
        layout.addWidget(title)
        layout.addWidget(text)


def section_title(title: str, note: str) -> QWidget:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 3, 0, 1)
    heading = QLabel(title, host)
    heading.setStyleSheet(themed_style("color:#f0f6fc;font-size:16px;font-weight:900"))
    copy = QLabel(note, host)
    copy.setWordWrap(True)
    copy.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
    layout.addWidget(heading)
    layout.addWidget(copy)
    return host


def set_art(label: QLabel, path: Path | None, size: int, *, unavailable: bool) -> None:
    if path and path.is_file():
        pixmap = QPixmap(str(path)).scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
    else:
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor("#111820"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#8b949e"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "暂无正式图片")
        painter.end()
    label.setPixmap(pixmap)
    label.setToolTip("正式图标不可用；未按名称猜测" if unavailable else "正式怪物图标")


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if child:
            clear_layout(child)
        if widget:
            widget.deleteLater()


def source_color(provenance: str) -> str:
    return "#ff7b72" if provenance == UNAVAILABLE else "#30363d"


def _percentage(value: str) -> int:
    try:
        number = float(value.split("/", 1)[0].strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0
    if "%" not in value and abs(number) <= 1:
        number *= 100
    return max(0, min(100, round(abs(number))))
