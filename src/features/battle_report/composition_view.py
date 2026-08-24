# 展示角色和公共伤害构成的定高卡片。
"""Fixed-height cards for per-role and public battle damage composition."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.battle_report import (
    BattleDamageComposition,
    DamageCompositionEntry,
    RoleDamageComposition,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog


_CARD_HEIGHT = 244
_VISIBLE_DAMAGE_ROWS = 5
_DAMAGE_ROW_HEIGHT = 25
_DAMAGE_ROW_SPACING = 2
_CHANNEL_COLORS = {
    "direct": "#58a6ff",
    "direct_follow_up": "#a371f7",
    "dot": "#ff75b5",
    "attachment": "#7ee787",
    "topple": "#d29922",
    "shared_damage": "#39c5cf",
    "special": "#f0883e",
    "special_nightmare": "#ff75b5",
    "special_zankou_erosion": "#ff9b73",
    "special_zankou_venom": "#c77dff",
    "max_hp_reduction": "#ffd166",
    "max_hp_reduction_estimated": "#c9a227",
    "reaction_creation": "#a371f7",
    "reaction_hexed": "#bc8cff",
    "reaction_remora": "#7ee787",
    "reaction_nova": "#d2a8ff",
    "reaction_scorch": "#ff7b72",
    "reaction_stain": "#79c0ff",
    "reaction_charge": "#e3b341",
    "reaction_discord": "#db6dcd",
    "reaction_unknown": "#8b949e",
    "other": "#6e7681",
    "other_topple": "#d29922",
    "other_reflected_projectile": "#8b949e",
    "other_environment": "#8b949e",
    "other_shared": "#39c5cf",
    "other_unattributed": "#6e7681",
}


def _format_damage(value: float) -> str:
    return f"{value:,.0f}"


class BattleDamageCompositionPanel(QWidget):
    def __init__(
        self,
        *,
        game_ui_asset_root=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_catalog = (
            None
            if game_ui_asset_root is None
            else GameUiAssetCatalog(game_ui_asset_root)
        )
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)

    def render(self, composition: BattleDamageComposition) -> None:
        self._clear()
        for index, role in enumerate(composition.roles):
            self._grid.addWidget(
                self._role_card(role),
                index // 2,
                index % 2,
            )
        next_row = (len(composition.roles) + 1) // 2
        if composition.system_entries:
            self._grid.addWidget(
                self._system_card(composition),
                next_row,
                0,
                1,
                2,
            )
            next_row += 1
        self._grid.addWidget(
            self._unattributed_card(composition),
            next_row,
            0,
            1,
            2,
        )
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)

    def clear(self) -> None:
        self._clear()

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _role_card(self, role: RoleDamageComposition) -> QFrame:
        card, layout = self._card_shell()
        header = QHBoxLayout()
        icon_path = (
            None
            if self._asset_catalog is None
            else self._asset_catalog.character_icon(role.character_id)
        )
        if icon_path is not None:
            icon = QLabel()
            icon.setFixedSize(36, 36)
            icon.setPixmap(
                QPixmap(str(icon_path)).scaled(
                    36,
                    36,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            header.addWidget(icon)
        name = QLabel(role.character_name)
        name.setStyleSheet(themed_style("font-size:14px;font-weight:700;color:#f0f6fc"))
        header.addWidget(name)
        header.addStretch()
        total = QLabel(f"总伤害  {_format_damage(role.total_damage)}")
        total.setStyleSheet(themed_style("font-size:12px;color:#8b949e"))
        header.addWidget(total)
        layout.addLayout(header)
        layout.addWidget(self._composition_bar(role.entries))
        layout.addWidget(self._rows_scroll(role.entries))
        return card

    def _system_card(self, composition: BattleDamageComposition) -> QFrame:
        card, layout = self._card_shell()
        header = QHBoxLayout()
        badge = QLabel("系统 / 环境")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(84, 30)
        badge.setStyleSheet(
            themed_style(
                "background:#21262d;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:7px;font-size:12px;font-weight:700"
            )
        )
        header.addWidget(badge)
        description = QLabel("已识别机制，但没有角色伤害所有者")
        description.setStyleSheet(themed_style("font-size:12px;color:#8b949e"))
        header.addWidget(description)
        header.addStretch()
        total = QLabel(
            f"{_format_damage(composition.system_total_damage)}  ·  "
            f"{composition.system_share_percent:.1f}% 时段伤害"
        )
        total.setStyleSheet(themed_style("font-size:12px;color:#8b949e"))
        header.addWidget(total)
        layout.addLayout(header)
        layout.addWidget(self._composition_bar(composition.system_entries))
        layout.addWidget(
            self._rows_scroll(
                composition.system_entries,
                empty_text="当前范围内没有系统或环境伤害",
            )
        )
        return card

    def _unattributed_card(self, composition: BattleDamageComposition) -> QFrame:
        card, layout = self._card_shell()
        header = QHBoxLayout()
        badge = QLabel("未归因")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(62, 30)
        badge.setStyleSheet(
            themed_style(
                "background:#21262d;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:7px;font-size:12px;font-weight:700"
            )
        )
        header.addWidget(badge)
        description = QLabel(
            "倾陷逐角色公式尚未加载"
            if composition.pending_topple_attribution
            else "倾陷缺少明确目标或公式证据"
            if composition.unresolved_topple_attribution
            else "完整、正常归属的战报这里应为 0"
        )
        description.setStyleSheet(themed_style("font-size:12px;color:#8b949e"))
        header.addWidget(description)
        header.addStretch()
        total = QLabel(
            f"{_format_damage(composition.other_total_damage)}  ·  "
            f"{composition.other_share_percent:.1f}% 时段伤害"
        )
        total.setStyleSheet(themed_style("font-size:12px;color:#8b949e"))
        header.addWidget(total)
        layout.addLayout(header)
        layout.addWidget(self._composition_bar(composition.other_entries))
        layout.addWidget(
            self._rows_scroll(
                composition.other_entries,
                empty_text="当前范围内没有未归因伤害",
            )
        )
        return card

    @staticmethod
    def _card_shell() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("damageCompositionCard")
        card.setFixedHeight(_CARD_HEIGHT)
        card.setStyleSheet(
            themed_style(
                "QFrame#damageCompositionCard{background:#0d1117;border:1px solid #30363d;"
                "border-radius:9px;}"
            )
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        return card, layout

    @staticmethod
    def _composition_bar(entries: tuple[DamageCompositionEntry, ...]) -> QFrame:
        bar = QFrame()
        bar.setObjectName("damageCompositionBar")
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            themed_style(
                "QFrame#damageCompositionBar{background:#21262d;border:none;border-radius:4px;}"
            )
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for entry in entries:
            if entry.share_percent <= 0:
                continue
            segment = QFrame()
            color = _CHANNEL_COLORS.get(
                entry.key.split("|", 1)[0],
                "#6e7681",
            )
            segment.setStyleSheet(f"background:{color};border:none")
            layout.addWidget(segment, max(1, round(entry.share_percent * 10)))
        remaining = max(0.0, 100.0 - sum(row.share_percent for row in entries))
        if remaining > 0:
            spacer = QFrame()
            spacer.setStyleSheet("background:transparent;border:none")
            layout.addWidget(spacer, max(1, round(remaining * 10)))
        return bar

    def _rows_scroll(
        self,
        entries: tuple[DamageCompositionEntry, ...],
        *,
        empty_text: str = "",
    ) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        visible_height = (
            _VISIBLE_DAMAGE_ROWS * _DAMAGE_ROW_HEIGHT
            + (_VISIBLE_DAMAGE_ROWS - 1) * _DAMAGE_ROW_SPACING
        )
        scroll.setFixedHeight(visible_height)

        content = QWidget()
        content.setStyleSheet("background:transparent")
        row_count = max(1, len(entries))
        content.setMinimumHeight(
            row_count * _DAMAGE_ROW_HEIGHT
            + max(0, row_count - 1) * _DAMAGE_ROW_SPACING
        )
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 2, 0)
        rows.setSpacing(_DAMAGE_ROW_SPACING)
        if entries:
            for entry in entries:
                rows.addWidget(self._damage_row(entry))
        else:
            placeholder = QLabel(empty_text or "暂无伤害数据")
            placeholder.setFixedHeight(_DAMAGE_ROW_HEIGHT)
            placeholder.setStyleSheet(themed_style("color:#6e7681;font-size:11px"))
            rows.addWidget(placeholder)
        rows.addStretch()
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _damage_row(entry: DamageCompositionEntry) -> QWidget:
        row = QWidget()
        row.setFixedHeight(_DAMAGE_ROW_HEIGHT)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 0, 6, 0)
        layout.setSpacing(7)
        marker = QFrame()
        marker.setFixedSize(8, 8)
        marker.setStyleSheet(
            f"background:{_CHANNEL_COLORS.get(entry.key.split('|', 1)[0], '#6e7681')};"
            "border:none;border-radius:4px"
        )
        layout.addWidget(marker)
        label = QLabel(entry.label)
        label.setStyleSheet(themed_style("font-size:11px;color:#c9d1d9"))
        layout.addWidget(label)
        layout.addStretch()
        damage = QLabel(_format_damage(entry.damage))
        damage.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        damage.setMinimumWidth(92)
        damage.setStyleSheet(themed_style("font-size:11px;color:#c9d1d9"))
        layout.addWidget(damage)
        share = QLabel(f"{entry.share_percent:.1f}%")
        share.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        share.setFixedWidth(52)
        share.setStyleSheet(themed_style("font-size:11px;color:#8b949e"))
        layout.addWidget(share)
        return row
