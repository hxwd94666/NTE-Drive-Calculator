# 绘制结果页与替换对话框复用的紧凑仓库装备卡片。
"""Reusable QWidget card aligned with the virtual warehouse delegate."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
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
from src.features.inventory.warehouse.display.cards import (
    WarehouseCardDelegate,
    _equipped_owner_portrait,
    _equipment_item_pixmap,
    warehouse_core_pixmap,
    warehouse_shape_pixmap,
)


class WarehouseResultCard(QWidget):
    """Paint one result with the same compact visual rules as the warehouse."""

    CARD_SIZE = WarehouseCardDelegate.CARD_SIZE
    REVIEW_CARD_SIZE = QSize(CARD_SIZE.width(), 222)

    def __init__(
        self,
        item_view: Mapping[str, Any],
        *,
        score: float | None,
        grade: str | None,
        direct_damage_score: float | None,
        split_metrics: bool = False,
        ratio_only: bool = False,
        score_ratio: float | None = None,
        replacement_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        text_catalog=None,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.item_view = dict(item_view)
        self.score = None if score is None else float(score)
        self.grade = str(grade or "")
        self.direct_damage_score = (
            None
            if direct_damage_score is None
            else float(direct_damage_score)
        )
        self.split_metrics = bool(split_metrics)
        self.ratio_only, self.score_ratio = ratio_only, score_ratio
        self._replacement_callback = replacement_callback
        self._selected = False
        self.setFixedSize(self.REVIEW_CARD_SIZE if ratio_only else self.CARD_SIZE)
        self.setFocusPolicy(Qt.StrongFocus if replacement_callback else Qt.NoFocus)
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
                + localized_text(text_catalog, 'auto.features.inventory.warehouse.display.result_card.init.f60a509c4', '当前装配方案持有者：{owner_name}', owner_name=f'{owner_name}')
            )
        weighted = [f"{s['label']}：{s['weight']:g}" for s in [*(self.item_view.get('main_stats') or ()),
                    *(self.item_view.get('sub_stats') or ())] if 'weight' in s]
        if weighted:
            tooltip += '\n冻结词条权重（灰、绿、蓝、金、橙递增）：\n' + '\n'.join(weighted)
        if self.item_view.get('main_stats_at_max_level'):
            tooltip += localized_text(text_catalog, 'auto.features.inventory.warehouse.display.result_card.init.f8e7032d2', '\n实际等级 {value}；主属性按满级预览', value=f"{self.item_view.get('level', 0)}")
        if tooltip:
            self.setToolTip(plain_tooltip(ui_enum_text(getattr(self, 'text_catalog', None), tooltip)))

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
        painter = QPainter(self)
        paint_warehouse_result_card(painter, self.rect(), self.item_view, score=self.score, grade=self.grade,
                                    direct_damage_score=self.direct_damage_score, split_metrics=self.split_metrics,
                                    selected=self._selected, device_pixel_ratio=self.devicePixelRatioF(),
                                    ratio_only=self.ratio_only, score_ratio=self.score_ratio, text_catalog=getattr(self, 'text_catalog', None))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.LeftButton
            and self._replacement_callback is not None
        ):
            self._replacement_callback()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self._replacement_callback:
            self._replacement_callback()
            event.accept()
            return
        super().keyPressEvent(event)


def paint_warehouse_result_card(painter, bounds, item, *, score=None, grade="", direct_damage_score=None,
                                split_metrics=False, selected=False, device_pixel_ratio=1.0,
                                ratio_only=False, score_ratio=None, footer_note="", text_catalog=None):
    """仓库风格卡片的公共绘制入口，供 QWidget 和虚拟列表共用。"""
    painter.setRenderHint(QPainter.Antialiasing)
    rect = bounds.adjusted(4, 4, -4, -4)
    painter.setBrush(QColor(theme_color("#161b22")))
    painter.setPen(
        QPen(
            QColor(
                theme_color(
                    "#58a6ff" if selected else "#30363d"
                )
            ),
            2 if selected else 1,
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
    if pixmap.isNull() and not item.get("frozen_projection") and item.get("kind") == "core":
        pixmap = warehouse_core_pixmap(
            item.get("suit_id"), str(item.get("quality") or "gold")
        )
    if pixmap.isNull() and not item.get("frozen_projection") and item.get("kind") != "core":
        pixmap = warehouse_shape_pixmap(
            str(item.get("shape") or "H_3"),
            str(item.get("quality") or "gold"),
            text_catalog=text_catalog,
        )
    if not pixmap.isNull():
        painter.drawPixmap(icon_rect, pixmap)
    else:
        painter.setBrush(QColor(quality_color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(icon_rect.adjusted(8, 8, -8, -8))

    avatar_rect = QRect(rect.right() - 48, top + 1, 36, 36)
    if item.get("recommended_character_name") or item.get("equipped"):
        avatar_item = item
        if item.get("recommended_character_name"):
            avatar_item = {**item, "equipped_character_name": item["recommended_character_name"],
                           "equipped_character_icon_path": item.get("recommended_character_icon_path")}
        avatar = _equipped_owner_portrait(avatar_item, device_pixel_ratio)
        if not avatar.isNull():
            painter.drawPixmap(avatar_rect, avatar)

    if item.get("recommended_character_name"):
        WarehouseCardDelegate._text(painter, QRect(avatar_rect.left(), avatar_rect.bottom(), 40, 16),
                                    resolve_text_catalog(text_catalog).render('ui.inventory.paint_warehouse_result_card.warehousecarddelegate'), theme_color("#58a6ff"), 8)

    name_size = 10 if item.get("kind") == "core" else 11
    header_reserved = 42 if item.get("recommended_character_name") or item.get("equipped") else 0
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
        level = resolve_text_catalog(text_catalog).render('frontend.inventory.warehouse.display.result_card.paint_warehouse_result_card.text')
    if item.get('main_stats_at_max_level'):
        level = ''  # 统一口径由外层说明，卡片保留真实等级的悬停信息。
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
    if ratio_only:
        ratio = "未评估" if score_ratio is None else f"{score_ratio:.1%}"
        WarehouseResultCard._score_badge(painter, QRect(left, top + 54, width, 20),
                                         f"{grade or '未评估'} · {ratio}", GRADE_COLORS.get(grade, "#58a6ff"))
    content_top = top + (83 if ratio_only else 62)
    if not stats:
        WarehouseCardDelegate._text(
            painter,
            QRect(left, content_top + 7, width, 18),
            resolve_text_catalog(text_catalog).render('ui.inventory.paint_warehouse_result_card.warehousecarddelegate_2'),
            theme_color("#6e7681"),
            10,
        )
    else:
        for number, stat in enumerate(stats[:6]):
            WarehouseCardDelegate._stat_row(
                painter,
                QRect(left, content_top + number * (18 if ratio_only else 20), width, 18),
                stat,
                main=bool(stat.get("main")),
            )

    footer_top = rect.bottom() - 29
    if ratio_only:
        WarehouseCardDelegate._text(painter, QRect(left, footer_top, width, 22),
                                    footer_note, theme_color("#c9d1d9"), 9)
        return
    score_text = (
        "--"
        if score is None
        else f"{score:.1f}".rstrip("0").rstrip(".")
    )
    grade_text = grade or "--"
    direct_text = (
        f"{direct_damage_score:.1f}%"
        if direct_damage_score is not None
        else "--"
    )
    grade_color = GRADE_COLORS.get(grade, "#58a6ff")
    if split_metrics:
        gap = 5
        metric_width = max(1, (width - gap * 2) // 3)
        badges = (
            (score_text, grade_color),
            (grade_text, grade_color),
            (direct_text, "#ffaa00"),
        )
        for index, (text, color) in enumerate(badges):
            WarehouseResultCard._score_badge(
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
            f"{score_text}·{grade_text}" if grade else score_text
        )
        WarehouseResultCard._score_badge(
            painter,
            QRect(left, footer_top, 80, 20),
            compact_score,
            grade_color,
        )
        WarehouseResultCard._score_badge(
            painter,
            QRect(left + 85, footer_top, 62, 20),
            direct_text,
            "#ffaa00",
        )
