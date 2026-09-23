# 提供资料库与养成计算器共用的带图片材料卡片。
"""Reusable progression material presentation widget."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style


class ProgressionMaterialCard(QFrame):
    """Render one material identity, image and amount without owning formulas."""

    def __init__(
        self,
        *,
        name: str,
        amount_text: str,
        icon_path: str | Path | None = None,
        detail_text: str | None = None,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("progressionMaterialImageCard")
        self.setProperty("compact", compact)
        if compact:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setMinimumSize(118, 116)
            self.setFixedHeight(126)
            self.setMaximumWidth(220)
            root = QVBoxLayout(self)
            root.setContentsMargins(8, 8, 8, 8)
            root.setSpacing(4)
        else:
            root = QHBoxLayout(self)
            root.setContentsMargins(9, 7, 9, 7)
            root.setSpacing(9)

        icon = QLabel(self)
        icon.setObjectName("progressionMaterialIcon")
        icon.setFixedSize(54 if compact else 46, 54 if compact else 46)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(icon_path or ""))
        if pixmap.isNull():
            icon.setText((str(name).strip() or "材")[:1])
        else:
            icon.setPixmap(pixmap.scaled(
                50 if compact else 42,
                50 if compact else 42,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        root.addWidget(
            icon,
            0,
            Qt.AlignmentFlag.AlignHCenter if compact else Qt.AlignmentFlag.AlignVCenter,
        )

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        title = QLabel(name, self)
        title.setObjectName("progressionMaterialCardName")
        title.setToolTip(name)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter if compact else Qt.AlignmentFlag.AlignLeft)
        title.setWordWrap(compact)
        if compact:
            title.setFixedHeight(32)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_layout.addWidget(title)
        if detail_text:
            detail = QLabel(detail_text, self)
            detail.setObjectName("progressionMaterialCardDetail")
            detail.setWordWrap(True)
            text_layout.addWidget(detail)
        root.addLayout(text_layout, 1 if not compact else 0)

        amount = QLabel(amount_text, self)
        amount.setObjectName("progressionMaterialCardAmount")
        amount.setAlignment(Qt.AlignmentFlag.AlignCenter if compact else Qt.AlignmentFlag.AlignRight)
        amount.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(amount)
        self.setStyleSheet(themed_style(
            "QFrame#progressionMaterialImageCard{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;}"
            "QLabel#progressionMaterialIcon{background:#161b22;border:1px solid #30363d;"
            "border-radius:7px;color:#8b949e;font-weight:900;}"
            "QLabel#progressionMaterialCardName{color:#f0f6fc;font-weight:800;}"
            "QLabel#progressionMaterialCardDetail{color:#8b949e;font-size:10px;}"
            "QLabel#progressionMaterialCardAmount{color:#58a6ff;font-weight:900;}"
            "QFrame#progressionMaterialImageCard[compact=true]{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;}"
        ))


__all__ = ["ProgressionMaterialCard"]
