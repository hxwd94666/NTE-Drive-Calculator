# 展示选定时段的角色伤害占比条和环形图。
"""Compact role contribution visuals for the battle report long page."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget

from src.app.theme import theme_color
from src.domain.battle_counterfactual import BattleBuildRoleCounterfactual
from src.domain.battle_report import BattleRangeRoleSummary


_ROLE_COLOR_VALUES = (
    "#58a6ff",
    "#d29922",
    "#db61a2",
    "#3fb950",
    "#a371f7",
    "#f0883e",
)


def role_contribution_color(index: int) -> QColor:
    value = _ROLE_COLOR_VALUES[index % len(_ROLE_COLOR_VALUES)]
    return QColor(theme_color(value))


def _number(value: float) -> str:
    return f"{value:,.0f}"


def _donut_center_text_rects(hole_rect: QRectF) -> tuple[QRectF, QRectF]:
    """Split the donut hole into non-overlapping title and value bands."""

    inner = hole_rect.adjusted(5.0, 4.0, -5.0, -4.0)
    title_height = max(13.0, inner.height() * 0.27)
    value_height = max(16.0, inner.height() * 0.32)
    gap = max(2.0, inner.height() * 0.04)
    content_height = title_height + gap + value_height
    top = inner.center().y() - content_height / 2.0
    title_rect = QRectF(inner.left(), top, inner.width(), title_height)
    value_rect = QRectF(
        inner.left(),
        title_rect.bottom() + gap,
        inner.width(),
        value_height,
    )
    return title_rect, value_rect


def _fitted_font(
    base_font: QFont,
    text: str,
    maximum_width: float,
    *,
    preferred_size: float,
    minimum_size: float,
    bold: bool = False,
) -> QFont:
    """Fit one unwrapped center label without leaking into the donut ring."""

    font = QFont(base_font)
    font.setBold(bold)
    point_size = max(minimum_size, preferred_size)
    while point_size > minimum_size:
        font.setPointSizeF(point_size)
        if QFontMetricsF(font).horizontalAdvance(text) <= maximum_width:
            return font
        point_size = max(minimum_size, point_size - 0.5)
    font.setPointSizeF(minimum_size)
    return font


class BattleRoleShareBar(QWidget):
    """Keep each role's percentage visible even when the table is narrow."""

    def __init__(
        self,
        *,
        share_percent: float,
        color: QColor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._share_percent = max(0.0, min(100.0, float(share_percent)))
        self._color = QColor(color)
        self.setMinimumWidth(104)
        self.setFixedHeight(28)
        self.setToolTip(f"该角色占选定时段总伤害的 {self._share_percent:.2f}%")

    def sizeHint(self) -> QSize:
        return QSize(126, 28)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(4, 7, max(1, self.width() - 8), 14)
        track_color = QColor(theme_color("#30363d"))
        track_color.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 7, 7)
        if self._share_percent > 0:
            fill = QRectF(
                track.left(),
                track.top(),
                max(3.0, track.width() * self._share_percent / 100.0),
                track.height(),
            )
            fill_color = QColor(self._color)
            fill_color.setAlpha(190)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(fill, 7, 7)
        painter.setPen(QColor(theme_color("#c9d1d9")))
        painter.drawText(
            track.adjusted(7, -1, -7, 1),
            Qt.AlignCenter,
            f"{self._share_percent:.2f}%",
        )


class BattleRoleDamagePieWidget(QWidget):
    """Draw a donut and matching legend from the selected-range role totals."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._roles: tuple[BattleRangeRoleSummary, ...] = ()
        self.setMinimumSize(290, 220)

    def sizeHint(self) -> QSize:
        return QSize(360, 240)

    def set_roles(self, roles: tuple[BattleRangeRoleSummary, ...]) -> None:
        self._roles = tuple(sorted(roles, key=lambda row: row.damage, reverse=True))
        self.setToolTip(
            "\n".join(
                f"{row.character_name}：{_number(row.damage)}（{row.share_percent:.2f}%）"
                for row in self._roles
            )
        )
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._roles or sum(max(0.0, row.damage) for row in self._roles) <= 0:
            painter.setPen(QColor(theme_color("#8b949e")))
            painter.drawText(self.rect(), Qt.AlignCenter, "当前时段没有角色伤害")
            return

        width = self.width()
        height = self.height()
        diameter = min(172.0, max(118.0, min(width * 0.46, height - 30.0)))
        pie_rect = QRectF(14.0, (height - diameter) / 2.0, diameter, diameter)
        total_damage = sum(max(0.0, row.damage) for row in self._roles)
        start_angle = 90 * 16
        card_background = QColor(theme_color("#161b22"))
        painter.setPen(QPen(card_background, 2))
        remainder_color = QColor(theme_color("#30363d"))
        remainder_color.setAlpha(135)
        painter.setBrush(remainder_color)
        painter.drawEllipse(pie_rect)
        for index, role in enumerate(self._roles):
            span = -round(
                360.0
                * 16.0
                * max(0.0, min(100.0, role.share_percent))
                / 100.0
            )
            painter.setBrush(role_contribution_color(index))
            painter.drawPie(pie_rect, start_angle, span)
            start_angle += span

        hole_size = diameter * 0.56
        hole_rect = QRectF(
            pie_rect.center().x() - hole_size / 2.0,
            pie_rect.center().y() - hole_size / 2.0,
            hole_size,
            hole_size,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(card_background)
        painter.drawEllipse(hole_rect)
        title_text = "角色有效伤害"
        value_text = _number(total_damage)
        title_rect, value_rect = _donut_center_text_rects(hole_rect)
        base_font = painter.font()
        title_font = _fitted_font(
            base_font,
            title_text,
            title_rect.width(),
            preferred_size=8.0,
            minimum_size=7.0,
        )
        value_font = _fitted_font(
            base_font,
            value_text,
            value_rect.width(),
            preferred_size=10.0,
            minimum_size=7.5,
            bold=True,
        )
        painter.setFont(title_font)
        painter.setPen(QColor(theme_color("#8b949e")))
        painter.drawText(
            title_rect,
            Qt.AlignCenter,
            title_text,
        )
        painter.setFont(value_font)
        painter.setPen(QColor(theme_color("#c9d1d9")))
        painter.drawText(
            value_rect,
            Qt.AlignCenter,
            value_text,
        )

        legend_left = pie_rect.right() + 22.0
        legend_width = max(90.0, width - legend_left - 8.0)
        row_height = min(39.0, max(27.0, (height - 20.0) / max(1, len(self._roles))))
        legend_top = max(10.0, (height - row_height * len(self._roles)) / 2.0)
        normal_font = painter.font()
        normal_font.setBold(False)
        normal_font.setPointSizeF(max(8.0, normal_font.pointSizeF() - 1.0))
        painter.setFont(normal_font)
        for index, role in enumerate(self._roles):
            y = legend_top + index * row_height
            color = role_contribution_color(index)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(legend_left, y + 6, 9, 9), 3, 3)
            painter.setPen(QColor(theme_color("#c9d1d9")))
            painter.drawText(
                QRectF(legend_left + 16, y, legend_width - 16, 20),
                Qt.AlignLeft | Qt.AlignVCenter,
                role.character_name,
            )
            painter.setPen(QColor(theme_color("#8b949e")))
            painter.drawText(
                QRectF(legend_left + 16, y + 17, legend_width - 16, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{role.share_percent:.2f}% · {_number(role.damage)}",
            )


def render_counterfactual_roles(
    table: QTableWidget,
    pie: BattleRoleDamagePieWidget,
    roles: Sequence[BattleBuildRoleCounterfactual],
    predicted_total: float | None,
) -> None:
    """Render complete/known role projections without promoting heuristics."""

    table.setRowCount(len(roles))
    pie_rows = []
    displayed_total = sum(
        role.candidate_damage
        if role.candidate_damage is not None
        else role.known_projection_damage
        if role.known_projection_damage is not None
        else role.baseline_damage
        for role in roles
    ) or predicted_total
    for row_index, role in enumerate(roles):
        damage = (
            role.candidate_damage
            if role.candidate_damage is not None
            else role.known_projection_damage
            if role.known_projection_damage is not None
            else role.baseline_damage
        )
        share = damage / displayed_total * 100.0 if displayed_total else 0.0
        damage_label = (
            _number(damage)
            if role.candidate_damage is not None
            else f"已量化 {_number(damage)}"
            if role.known_projection_damage is not None
            else f"原轴 {_number(damage)}"
        )
        gain = (
            role.gain_percent
            if role.gain_percent is not None
            else role.known_gain_percent
        )
        table.setItem(row_index, 0, QTableWidgetItem(role.character_name))
        table.setItem(row_index, 1, QTableWidgetItem(damage_label))
        table.setCellWidget(
            row_index,
            2,
            BattleRoleShareBar(
                share_percent=share,
                color=role_contribution_color(row_index),
            ),
        )
        table.setItem(
            row_index,
            3,
            QTableWidgetItem("—" if gain is None else f"{gain:+.2f}%"),
        )
        pie_rows.append(BattleRangeRoleSummary(
            character_id=role.character_id,
            character_name=role.character_name,
            hits=0,
            damage=damage,
            dps=0.0,
            share_percent=share,
        ))
    pie.set_roles(tuple(pie_rows))
