# 角色图鉴卡片墙的可交互人物卡。
"""Game-styled character card used by the catalog gallery."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadata,
)
from src.services.static_catalog_character_models import CharacterSummary


class CharacterGalleryCard(QFrame):
    activated = Signal(int)

    def __init__(
        self,
        summary: CharacterSummary,
        *,
        art_path: Path | None,
        release_metadata: CharacterReleaseMetadata | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.summary = summary
        self.release_metadata = release_metadata
        self.setObjectName(f"characterGalleryCard_{summary.character_id}")
        self.setProperty("characterCard", True)
        self.setProperty(
            "scheduledCharacter",
            summary.classification == "scheduled_character",
        )
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(180, 220)
        self.setMaximumHeight(238)
        self.setStyleSheet(themed_style(
            "QFrame[characterCard='true']{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #10243f,stop:0.58 #161b22,stop:1 #0d1117);"
            "border:1px solid #30363d;border-radius:16px;}"
            "QFrame[scheduledCharacter='true']{border-color:#d29922;"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #10243f,stop:0.48 #161b22,stop:1 #0d1117);}"
            "QFrame[characterCard='true']:hover,QFrame[characterCard='true']:focus{"
            "border:2px solid #58a6ff;background:#1c2128;}"
        ))
        self._build(art_path)

    def _build(self, art_path: Path | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 9, 11, 10)
        root.setSpacing(4)

        top = QHBoxLayout()
        element = QLabel(self.summary.element_label, self)
        element.setObjectName("characterElementBadge")
        element.setStyleSheet(themed_style(
            "background:#1f6feb33;color:#58a6ff;border:1px solid #58a6ff;"
            "border-radius:9px;padding:2px 8px;font-size:10px;font-weight:800"
        ))
        metadata = self.release_metadata
        acquisition = (
            metadata.acquisition_term.display_name
            if (
                metadata is not None
                and metadata.acquisition_term is not None
                and metadata.acquisition_term.display_name
            )
            else "名称暂未提供"
        )
        quality = (
            metadata.quality
            if metadata is not None and metadata.quality
            else "品质未提供"
        )
        availability = (
            " · 待上线"
            if self.summary.classification == "scheduled_character"
            else ""
        )
        state = QLabel(f"{quality} · {acquisition}{availability}", self)
        state.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:700"
        ))
        top.addWidget(element, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        top.addWidget(state, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(top)

        visual = QFrame(self)
        visual.setObjectName("characterAvatarFrame")
        visual.setFixedHeight(130)
        visual.setStyleSheet(themed_style(
            "QFrame#characterAvatarFrame{background:#0d1117;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        visual_layout = QVBoxLayout(visual)
        visual_layout.setContentsMargins(5, 4, 5, 3)
        visual_layout.setSpacing(1)
        art = QLabel(visual)
        art.setObjectName("characterAvatar")
        art.setFixedHeight(106)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if art_path is not None:
            pixmap = QPixmap(str(art_path))
            if not pixmap.isNull():
                art.setPixmap(pixmap.scaled(
                    112,
                    112,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        if art.pixmap().isNull():
            art.setText("正式头像\n当前未提供")
            art.setStyleSheet(themed_style(
                "color:#6e7681;background:#0d1117;border:1px dashed #30363d;"
                "border-radius:12px;font-size:11px"
            ))
        avatar_caption = QLabel("正式头像", visual)
        avatar_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_caption.setStyleSheet(themed_style(
            "color:#6e7681;background:transparent;border:none;"
            "font-size:8px;font-weight:700"
        ))
        visual_layout.addWidget(art)
        visual_layout.addWidget(avatar_caption)
        root.addWidget(visual)

        name = QLabel(self.summary.name_zh, self)
        name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:17px;font-weight:900"
        ))
        identity = QLabel(f"CHARACTER  {self.summary.character_id}", self)
        identity.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:700;letter-spacing:1px"
        ))
        root.addWidget(name)
        root.addWidget(identity)
        release_prefix = (
            "预计上线 "
            if self.summary.classification == "scheduled_character"
            else "上线 "
        )
        release = QLabel(
            release_prefix + (
                metadata.release_date
                if metadata is not None and metadata.release_date
                else "当前正式数据未提供"
            ),
            self,
        )
        release.setStyleSheet(themed_style(
            "color:#6e7681;font-size:9px;font-weight:700"
        ))
        root.addWidget(release)
        for widget in (
            element, state, visual, art, avatar_caption, name, identity, release,
        ):
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
