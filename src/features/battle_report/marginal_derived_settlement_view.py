# 在边际页显式列出候选配置新增、但不改写原逐击的派生结算。
"""Audit table for candidate-only fixed-axis settlements."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QTableWidgetItem, QVBoxLayout

from src.app.theme import themed_style
from src.domain.battle_counterfactual import BattleBuildCounterfactual
from src.features.battle_report.analysis_components import analysis_table
from src.services.battle_daffodill_marginal_service import (
    DAFFODILL_EFFECT_FIVE_METHOD,
)


class BattleMarginalDerivedSettlementView(QFrame):
    """Make baseline-zero mechanism gains visible and source-addressable."""

    hit_activated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._event_ids: list[str] = []
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        heading = QLabel("候选新增机制结算")
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(themed_style("color:#d29922;font-weight:700"))
        layout.addWidget(self.summary)
        note = QLabel(
            "零觉洞察原本会额外结算一次；五觉再按每层洞察各追加一次。这里"
            "只单列相对零觉新增的伤害事件，它们计入新总伤害和角色收益，"
            "但不会改写作为触发条件的原始逐击；双击一行可查看完整公式。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(note)
        self.table = analysis_table(
            (
                "新增机制",
                "原轴触发逐击",
                "结算次数",
                "单次伤害",
                "新增伤害",
                "角色收益",
                "折合全队收益",
                "公式锚点",
            ),
            150,
            default_widths=(220, 150, 100, 130, 140, 120, 140, 360),
        )
        self.table.cellDoubleClicked.connect(self._activate_row)
        layout.addWidget(self.table)
        self.hide()

    def render(self, comparison: BattleBuildCounterfactual | None) -> None:
        rows = () if comparison is None else tuple(
            row
            for row in comparison.hits
            if row.quantification.method == DAFFODILL_EFFECT_FIVE_METHOD
            and row.candidate_damage is not None
        )
        grouped = defaultdict(list)
        for row in rows:
            grouped[(row.quantification.method, row.source_event_id)].append(row)
        groups = tuple(grouped.values())
        self._event_ids = [group[0].event_id for group in groups]
        self.table.setRowCount(len(groups))
        total_gain = sum(
            row.candidate_damage - row.baseline_damage
            for row in rows
            if row.candidate_damage is not None
        )
        team_gain = (
            total_gain / comparison.baseline_damage * 100.0
            if comparison is not None and comparison.baseline_damage > 0.0
            else 0.0
        )
        self.summary.setText(
            f"五觉相对零觉新增：{len(rows)} 次结算，共 {total_gain:+,.2f} 伤害；"
            f"折合全队 {team_gain:+.2f}%。"
        )
        role_baselines = {
            role.character_id: role.baseline_damage
            for role in (() if comparison is None else comparison.roles)
        }
        for row_index, group in enumerate(groups):
            first = group[0]
            gain = sum(
                row.candidate_damage - row.baseline_damage
                for row in group
                if row.candidate_damage is not None
            )
            per_settlement = gain / len(group)
            role_baseline = role_baselines.get(first.character_id, 0.0)
            role_gain = gain / role_baseline * 100.0 if role_baseline > 0.0 else 0.0
            group_team_gain = (
                gain / comparison.baseline_damage * 100.0
                if comparison is not None and comparison.baseline_damage > 0.0
                else 0.0
            )
            formula = (
                f"{len(group)} × {per_settlement:,.2f} = {gain:,.2f}；"
                f"{first.quantification.explanation}"
            )
            values = (
                "达芙蒂尔五觉·额外倾陷",
                first.source_event_id,
                f"总 {1 + len(group)} 次（基础 1 + 新增 {len(group)}）",
                f"{per_settlement:,.2f}",
                f"{gain:+,.2f}",
                f"{role_gain:+.2f}%",
                f"{group_team_gain:+.2f}%",
                formula,
            )
            tooltip = (
                f"总次数 = 零觉基础 1 次 + 五觉追加 {len(group)} 次；\n"
                f"新增收益 = 洞察层数 {len(group)} × 单次达芙蒂尔个人倾陷"
                f" {per_settlement:,.2f} = {gain:,.2f}\n"
                f"{first.quantification.explanation}\n"
                "双击查看候选伤害公式；原轴触发逐击本身保持独立。"
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                self.table.setItem(row_index, column, item)
        self.setVisible(bool(groups))

    def _activate_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._event_ids):
            self.hit_activated.emit(self._event_ids[row])


__all__ = ["BattleMarginalDerivedSettlementView"]
