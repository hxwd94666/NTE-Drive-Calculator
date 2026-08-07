# 使用 Qt 绘制紧凑的实时战报悬浮窗。
"""Compact partner-inspired live battle HUD implemented with Qt painting."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from src.domain.battle_report import (
    BattleCharacterSummary,
    BattleSummary,
    active_abyss_half,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog


_ROLE_COLORS = ("#45d0ff", "#9b83ff", "#59d49a", "#e7b75f")


def _format_number(value: float) -> str:
    return f"{value:,.0f}"


class BattleReportOverlay(QWidget):
    """A 380x238 frameless overlay matching the partner HUD information density."""

    def __init__(self, *, game_ui_asset_root) -> None:
        super().__init__(None)
        self.setWindowTitle("NTE 实时战报")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowTransparentForInput, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.resize(380, 238)
        self.setMinimumSize(300, 200)
        self._asset_catalog = GameUiAssetCatalog(game_ui_asset_root)
        self._summary: BattleSummary | None = None
        self._history: deque[float] = deque(maxlen=72)
        self._avatar_cache: dict[int, QPixmap | None] = {}
        self._drag_origin: QPoint | None = None
        self._positioned = False

    def set_passthrough(self, enabled: bool) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowTransparentForInput, enabled)
        if was_visible:
            self.show_overlay()

    def show_overlay(self) -> None:
        if not self._positioned:
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(
                    area.x() + area.width() - self.width() - 28,
                    area.y() + 58,
                )
            self._positioned = True
        self.show()
        self.raise_()

    def update_summary(self, summary: BattleSummary | None) -> None:
        self._summary = summary
        if summary is not None:
            half = active_abyss_half(summary)
            self._history.append(half.total_dps if half is not None else summary.total_dps)
        self.update()

    def clear_summary(self) -> None:
        self._summary = None
        self._history.clear()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        summary = self._summary
        if summary is None:
            self._halo_text(
                painter,
                QRectF(16, 0, self.width() - 32, self.height()),
                "等待战斗数据…",
                15,
                Qt.AlignCenter,
                QColor("#f0f6fc"),
                bold=True,
            )
            return

        half = active_abyss_half(summary)
        duration = half.duration_seconds if half is not None else summary.duration_seconds
        total_damage = half.total_damage if half is not None else summary.total_damage
        total_dps = half.total_dps if half is not None else summary.total_dps
        characters = half.characters if half is not None else summary.characters
        characters = tuple(sorted(characters, key=lambda item: item.damage, reverse=True)[:4])

        self._halo_text(
            painter,
            QRectF(14, 8, 205, 18),
            f"队伍 DPS · {duration:.1f}s",
            10,
            Qt.AlignLeft | Qt.AlignVCenter,
            QColor("#d8dee9"),
        )
        self._halo_text(
            painter,
            QRectF(self.width() - 152, 8, 138, 18),
            "总伤害 / 受击",
            9,
            Qt.AlignRight | Qt.AlignVCenter,
            QColor("#d8dee9"),
        )
        self._halo_text(
            painter,
            QRectF(14, 25, 206, 34),
            _format_number(total_dps),
            24,
            Qt.AlignLeft | Qt.AlignVCenter,
            QColor("#6ee7a1"),
            bold=True,
        )
        self._halo_text(
            painter,
            QRectF(self.width() - 194, 27, 180, 28),
            f"{_format_number(total_damage)} / {_format_number(summary.total_damage_taken)}",
            13,
            Qt.AlignRight | Qt.AlignVCenter,
            QColor("#f0f6fc"),
        )

        bar_rect = QRectF(14, 62, self.width() - 28, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 34))
        painter.drawRoundedRect(bar_rect, 2, 2)
        x = bar_rect.x()
        for index, character in enumerate(characters):
            share = max(0.0, min(100.0, character.damage_share_percent)) / 100.0
            width = bar_rect.width() * share
            painter.setBrush(QColor(_ROLE_COLORS[index]))
            painter.drawRect(QRectF(x, bar_rect.y(), width, bar_rect.height()))
            x += width

        half_label = summary.abyss.active_half if summary.abyss.detected else "全局战斗"
        self._halo_text(
            painter,
            QRectF(14, 69, self.width() - 28, 16),
            half_label or "深渊战斗",
            9,
            Qt.AlignLeft | Qt.AlignVCenter,
            QColor("#b8c0cc"),
        )

        row_top = 86.0
        row_height = 30.0
        for index in range(4):
            character = characters[index] if index < len(characters) else None
            self._draw_character_row(painter, row_top + index * row_height, index, character)

        self._draw_history(painter, QRectF(14, self.height() - 24, self.width() - 28, 15))

    def _draw_character_row(
        self,
        painter: QPainter,
        y: float,
        index: int,
        character: BattleCharacterSummary | None,
    ) -> None:
        if character is None:
            return
        pixmap = self._avatar(character.character_id)
        if pixmap is not None:
            painter.drawPixmap(QRectF(14, y + 2, 25, 25), pixmap, QRectF(pixmap.rect()))
        color = QColor(_ROLE_COLORS[index])
        self._halo_text(
            painter,
            QRectF(45, y, 96, 15),
            character.name,
            9,
            Qt.AlignLeft | Qt.AlignVCenter,
            QColor("#f0f6fc"),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 32))
        track = QRectF(45, y + 18, self.width() - 188, 5)
        painter.drawRoundedRect(track, 2.5, 2.5)
        share = max(0.0, min(100.0, character.damage_share_percent)) / 100.0
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(track.x(), track.y(), track.width() * share, track.height()), 2.5, 2.5
        )
        self._halo_text(
            painter,
            QRectF(self.width() - 135, y, 76, 25),
            _format_number(character.dps),
            11,
            Qt.AlignRight | Qt.AlignVCenter,
            color,
        )
        self._halo_text(
            painter,
            QRectF(self.width() - 56, y, 42, 25),
            f"{character.damage_share_percent:.1f}%",
            9,
            Qt.AlignRight | Qt.AlignVCenter,
            QColor("#d8dee9"),
        )

    def _draw_history(self, painter: QPainter, rect: QRectF) -> None:
        values = tuple(self._history)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if len(values) < 2:
            return
        high = max(max(values), 1.0)
        path = QPainterPath()
        for index, value in enumerate(values):
            x = rect.left() + rect.width() * index / (len(values) - 1)
            y = rect.bottom() - rect.height() * value / high
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(QPen(QColor("#6ee7a1"), 1.4))
        painter.drawPath(path)

    def _avatar(self, character_id: int) -> QPixmap | None:
        if character_id not in self._avatar_cache:
            path = self._asset_catalog.character_icon(character_id)
            self._avatar_cache[character_id] = QPixmap(str(path)) if path is not None else None
        return self._avatar_cache[character_id]

    def _halo_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: int,
        alignment: Qt.AlignmentFlag,
        color: QColor,
        *,
        bold: bool = False,
    ) -> None:
        font = QFont("Microsoft YaHei UI", size)
        if bold:
            font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text)
        x = rect.left()
        if alignment & Qt.AlignRight:
            x = rect.right() - width
        elif alignment & Qt.AlignHCenter:
            x = rect.left() + (rect.width() - width) / 2
        y = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) / 2
        painter.setFont(font)
        for dx, dy, alpha in (
            (-1.0, 0.0, 230),
            (1.0, 0.0, 230),
            (0.0, -1.0, 230),
            (0.0, 1.0, 230),
            (0.0, 2.0, 175),
        ):
            painter.setPen(QColor(0, 0, 0, alpha))
            painter.drawText(QPointF(x + dx, y + dy), text)
        painter.setPen(color)
        painter.drawText(QPointF(x, y), text)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and bool(event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = None
        super().mouseReleaseEvent(event)
