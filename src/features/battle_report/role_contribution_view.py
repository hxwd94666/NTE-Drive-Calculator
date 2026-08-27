# 展示选定时段的角色伤害占比条和环形图。
"""Compact role contribution visuals for the battle report long page."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget

from src.app.theme import theme_color
from src.domain.battle_report import (
    BattleBuildRoleCounterfactual,
    BattleRangeRoleSummary,
)


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
        painter.setPen(QColor(theme_color("#8b949e")))
        painter.drawText(
            hole_rect.adjusted(0, 12, 0, -hole_size * 0.43),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "角色有效伤害",
        )
        value_font = painter.font()
        value_font.setBold(True)
        value_font.setPointSizeF(max(10.0, value_font.pointSizeF() + 2.0))
        painter.setFont(value_font)
        painter.setPen(QColor(theme_color("#c9d1d9")))
        painter.drawText(
            hole_rect.adjusted(0, hole_size * 0.34, 0, -8),
            Qt.AlignHCenter | Qt.AlignTop,
            _number(total_damage),
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
    predicted_total: float,
) -> None:
    """Render the marginal role table and donut from the same immutable rows."""

    table.setRowCount(len(roles))
    pie_rows = []
    for row_index, role in enumerate(roles):
        share = role.predicted_damage / predicted_total * 100.0 if predicted_total else 0.0
        table.setItem(row_index, 0, QTableWidgetItem(role.character_name))
        table.setItem(row_index, 1, QTableWidgetItem(_number(role.predicted_damage)))
        table.setCellWidget(
            row_index,
            2,
            BattleRoleShareBar(
                share_percent=share,
                color=role_contribution_color(row_index),
            ),
        )
        table.setItem(row_index, 3, QTableWidgetItem(f"{role.gain_percent:+.2f}%"))
        pie_rows.append(BattleRangeRoleSummary(
            character_id=role.character_id,
            character_name=role.character_name,
            hits=0,
            damage=role.predicted_damage,
            dps=0.0,
            share_percent=share,
        ))
    pie.set_roles(tuple(pie_rows))
