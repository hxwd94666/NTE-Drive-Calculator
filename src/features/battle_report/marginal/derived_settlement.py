# 在边际页显式列出候选配置新增、但不改写原逐击的派生结算。
"""Audit table for candidate-only fixed-axis settlements."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from collections import defaultdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QTableWidgetItem, QVBoxLayout

from src.app.theme import themed_style
from src.domain.battle_report.counterfactual.results import BattleBuildCounterfactual
from src.features.battle_report.analysis.components import analysis_table
from src.services.battle_report.characters.daffodill_marginal import (
    DAFFODILL_EFFECT_FIVE_METHOD,
)


class BattleMarginalDerivedSettlementView(QFrame):
    """Make baseline-zero mechanism gains visible and source-addressable."""

    hit_activated = Signal(str)

    def __init__(self, parent=None, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._event_ids: list[str] = []
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        heading = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.init.heading'))
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        self.summary = plain_label()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(themed_style("color:#d29922;font-weight:700"))
        layout.addWidget(self.summary)
        note = plain_label(
            resolve_text_catalog(text_catalog).render('ui.battle_report.init.note_4')
        )
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(note)
        self.table = analysis_table(
            (
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_10'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_11'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_12'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_13'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_14'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_15'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_16'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_17'),
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
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.render.summary', value=f'{len(rows)}', total_gain=f'{total_gain:+,.2f}', team_gain=f'{team_gain:+.2f}')
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
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.battle_report.marginal.derived_settlement.render.f7bf7bf19', '总 {value} 次（基础 1 + 新增 {count}）', value=f'{1 + len(group)}', count=f'{len(group)}'),
                f"{per_settlement:,.2f}",
                f"{gain:+,.2f}",
                f"{role_gain:+.2f}%",
                f"{group_team_gain:+.2f}%",
                formula,
            )
            tooltip = (
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.battle_report.marginal.derived_settlement.render.text', value=f'{len(group)}', value_2=f'{len(group)}', per_settlement=f'{per_settlement:,.2f}', gain=f'{gain:,.2f}', explanation=f'{first.quantification.explanation}')
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
