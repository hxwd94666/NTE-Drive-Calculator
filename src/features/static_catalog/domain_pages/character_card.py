# 角色图鉴卡片墙的可交互人物卡。
"""Game-styled character card used by the catalog gallery."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.services.static_catalog_character_models import CharacterSummary


_CLASSIFICATION_LABELS = {
    "available_character": "已实装",
    "available_avatar_variant": "可用形态",
    "scheduled_character": "待登场",
    "combat_transformation": "战斗形态",
}


class CharacterGalleryCard(QFrame):
    activated = Signal(int)

    def __init__(
        self,
        summary: CharacterSummary,
        *,
        art_path: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.summary = summary
        self.setObjectName(f"characterGalleryCard_{summary.character_id}")
        self.setProperty("characterCard", True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(188, 258)
        self.setMaximumHeight(290)
        self.setStyleSheet(themed_style(
            "QFrame[characterCard='true']{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #10243f,stop:0.58 #161b22,stop:1 #0d1117);"
            "border:1px solid #30363d;border-radius:16px;}"
            "QFrame[characterCard='true']:hover,QFrame[characterCard='true']:focus{"
            "border:2px solid #58a6ff;background:#1c2128;}"
        ))
        self._build(art_path)

    def _build(self, art_path: Path | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(6)

        top = QHBoxLayout()
        element = QLabel(self.summary.element_label, self)
        element.setObjectName("characterElementBadge")
        element.setStyleSheet(themed_style(
            "background:#1f6feb33;color:#58a6ff;border:1px solid #58a6ff;"
            "border-radius:9px;padding:2px 8px;font-size:10px;font-weight:800"
        ))
        state = QLabel(
            _CLASSIFICATION_LABELS.get(
                self.summary.classification or "", "正式资料",
            ),
            self,
        )
        state.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:700"
        ))
        top.addWidget(element, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        top.addWidget(state, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(top)

        art = QLabel(self)
        art.setFixedHeight(164)
        art.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        if art_path is not None:
            pixmap = QPixmap(str(art_path))
            if not pixmap.isNull():
                art.setPixmap(pixmap.scaled(
                    174,
                    174,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        if art.pixmap().isNull():
            art.setText("立绘\n暂不可用")
            art.setStyleSheet(themed_style(
                "color:#6e7681;background:#0d1117;border:1px dashed #30363d;"
                "border-radius:12px;font-size:11px"
            ))
        root.addWidget(art)

        name = QLabel(self.summary.name_zh, self)
        name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:18px;font-weight:900"
        ))
        identity = QLabel(f"CHARACTER  {self.summary.character_id}", self)
        identity.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:700;letter-spacing:1px"
        ))
        root.addWidget(name)
        root.addWidget(identity)
        for widget in (element, state, art, name, identity):
            widget.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True,
            )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.summary.character_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.activated.emit(self.summary.character_id)
            event.accept()
            return
        super().keyPressEvent(event)
