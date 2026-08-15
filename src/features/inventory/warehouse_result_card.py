# 绘制结果页与替换对话框复用的紧凑仓库装备卡片。
"""Reusable QWidget card aligned with the virtual warehouse delegate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from src.app.theme import GRADE_COLORS, theme_color
from src.features.inventory.warehouse import (
    WarehouseCardDelegate,
    _equipment_item_pixmap,
    _legacy_character_avatar,
    warehouse_core_pixmap,
    warehouse_shape_pixmap,
)


class WarehouseResultCard(QWidget):
    """Paint one result with the same compact visual rules as the warehouse."""

    CARD_SIZE = WarehouseCardDelegate.CARD_SIZE

    def __init__(
        self,
        item_view: Mapping[str, Any],
        *,
        score: float | None,
        grade: str | None,
        direct_damage_score: float | None,
        split_metrics: bool = False,
        replacement_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item_view = dict(item_view)
        self.score = None if score is None else float(score)
        self.grade = str(grade or "")
        self.direct_damage_score = (
            None
            if direct_damage_score is None
            else float(direct_damage_score)
        )
        self.split_metrics = bool(split_metrics)
        self._replacement_callback = replacement_callback
        self._selected = False
        self.setFixedSize(self.CARD_SIZE)
        owner_name = str(
            self.item_view.get("equipped_character_name") or ""
        ).strip()
        if replacement_callback is not None:
            self.setCursor(QCursor(Qt.PointingHandCursor))
        tooltip = (
            "点击卡片替换装备" if replacement_callback is not None else ""
        )
        if owner_name:
            tooltip += (
                ("\n" if tooltip else "")
                + f"当前装配方案持有者：{owner_name}"
            )
        if tooltip:
            self.setToolTip(tooltip)

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if self._selected == selected:
            return
        self._selected = selected
        self.update()

    @staticmethod
    def _score_badge(
        painter: QPainter,
        rect: QRect,
        text: str,
        color: str,
    ) -> None:
        background = QColor(theme_color(color))
        background.setAlpha(64)
        painter.setBrush(background)
        painter.setPen(QPen(QColor(theme_color(color)), 1))
        painter.drawRoundedRect(rect, 4, 4)
        font = QFont(painter.font())
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme_color("#f0f6fc")))
        painter.drawText(
            rect.adjusted(3, 0, -3, 0),
            Qt.AlignCenter | Qt.TextSingleLine,
            text,
        )

    def paintEvent(self, _event) -> None:
        item = self.item_view
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)
        painter.setBrush(QColor(theme_color("#161b22")))
        painter.setPen(
            QPen(
                QColor(
                    theme_color(
                        "#58a6ff" if self._selected else "#30363d"
                    )
                ),
                2 if self._selected else 1,
            )
        )
        painter.drawRoundedRect(rect, 9, 9)

        left = rect.left() + 12
        top = rect.top() + 10
        width = rect.width() - 24
        quality_color = str(item.get("quality_color") or "#8b949e")
        icon_rect = QRect(left, top, 44, 44)
        pixmap = _equipment_item_pixmap(
            str(item.get("item_icon_path") or "")
        )
        if pixmap.isNull() and item.get("kind") == "core":
            pixmap = warehouse_core_pixmap(
                item.get("suit_id"), str(item.get("quality") or "gold")
            )
        if pixmap.isNull() and item.get("kind") != "core":
            pixmap = warehouse_shape_pixmap(
                str(item.get("shape") or "H_3"),
                str(item.get("quality") or "gold"),
            )
        if not pixmap.isNull():
            painter.drawPixmap(icon_rect, pixmap)
        else:
            painter.setBrush(QColor(quality_color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(icon_rect.adjusted(8, 8, -8, -8))

        avatar_rect = QRect(rect.right() - 48, top + 1, 36, 36)
        if item.get("equipped"):
            avatar = _equipment_item_pixmap(
                str(item.get("equipped_character_icon_path") or "")
            )
            if avatar.isNull():
                avatar = _legacy_character_avatar(
                    str(item.get("equipped_character_name") or "")
                )
            if not avatar.isNull():
                painter.drawPixmap(avatar_rect, avatar)

        name_size = 10 if item.get("kind") == "core" else 11
        header_reserved = 42 if item.get("equipped") else 0
        WarehouseCardDelegate._text(
            painter,
            QRect(left + 52, top + 2, width - 52 - header_reserved, 20),
            str(item.get("display_name") or item.get("item_name") or ""),
            theme_color("#f0f6fc"),
            name_size,
            bold=True,
        )
        if item.get("level_known", True):
            level = f"Lv.{item.get('level', 0)}"
            max_level = int(item.get("max_level", 0) or 0)
            if max_level:
                level += f" / {max_level}"
        else:
            level = "等级未知"
        WarehouseCardDelegate._text(
            painter,
            QRect(left + 52, top + 23, width - 52 - header_reserved, 16),
            level,
            quality_color,
            9,
        )

        stats = [
            *(item.get("main_stats") or ()),
            *(item.get("sub_stats") or ()),
        ]
        content_top = top + 62
        if not stats:
            WarehouseCardDelegate._text(
                painter,
                QRect(left, content_top + 7, width, 18),
                "暂无词条数据",
                theme_color("#6e7681"),
                10,
            )
        else:
            for number, stat in enumerate(stats[:6]):
                WarehouseCardDelegate._stat_row(
                    painter,
                    QRect(left, content_top + number * 20, width, 18),
                    stat,
                    main=bool(stat.get("main")),
                )

        footer_top = rect.bottom() - 29
        score_text = (
            "--"
            if self.score is None
            else f"{self.score:.1f}".rstrip("0").rstrip(".")
        )
        grade_text = self.grade or "--"
        direct_text = (
            f"{self.direct_damage_score:.1f}%"
            if self.direct_damage_score is not None
            else "--"
        )
        grade_color = GRADE_COLORS.get(self.grade, "#58a6ff")
        if self.split_metrics:
            gap = 5
            metric_width = max(1, (width - gap * 2) // 3)
            badges = (
                (score_text, grade_color),
                (grade_text, grade_color),
                (direct_text, "#ffaa00"),
            )
            for index, (text, color) in enumerate(badges):
                self._score_badge(
                    painter,
                    QRect(
                        left + (metric_width + gap) * index,
                        footer_top,
                        metric_width,
                        20,
                    ),
                    text,
                    color,
                )
        else:
            compact_score = (
                f"{score_text}·{grade_text}" if self.grade else score_text
            )
            self._score_badge(
                painter,
                QRect(left, footer_top, 80, 20),
                compact_score,
                grade_color,
            )
            self._score_badge(
                painter,
                QRect(left + 85, footer_top, 62, 20),
                direct_text,
                "#ffaa00",
            )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.LeftButton
            and self._replacement_callback is not None
        ):
            self._replacement_callback()
            event.accept()
            return
        super().mouseReleaseEvent(event)
