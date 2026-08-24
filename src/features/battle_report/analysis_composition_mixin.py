# 投影选定时段的角色贡献、粗细伤害分类和倾陷归属状态。
"""Damage-composition presentation kept outside the long analysis view shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from src.domain.battle_report import BattleRangeRoleSummary
from src.features.battle_report.role_contribution_view import (
    BattleRoleShareBar,
    role_contribution_color,
)
from src.services.battle_damage_composition_service import (
    BattleDamageCompositionService,
)


class BattleAnalysisCompositionMixin:
    """Render role totals from the same attributed composition used by cards."""

    def _render_roles(self) -> None:
        analysis = self._analysis
        composition = self._current_composition
        original_by_id = {
            row.character_id: row for row in (analysis.roles if analysis else ())
        }
        denominator = (
            float(
                getattr(
                    analysis,
                    "effective_damage",
                    getattr(analysis, "total_damage", 0.0),
                )
            )
            if analysis is not None
            else 0.0
        )
        rows = tuple(
            BattleRangeRoleSummary(
                character_id=row.character_id,
                character_name=row.character_name,
                hits=(
                    original_by_id[row.character_id].hits
                    if row.character_id in original_by_id
                    else 0
                ),
                damage=row.total_damage,
                dps=0.0,
                share_percent=(
                    row.total_damage / denominator * 100.0
                    if denominator > 0
                    else 0.0
                ),
                max_hp_reduction_events=(
                    original_by_id[row.character_id].max_hp_reduction_events
                    if row.character_id in original_by_id
                    else 0
                ),
            )
            for row in (composition.roles if composition is not None else ())
        )
        rows = tuple(sorted(rows, key=lambda item: item.damage, reverse=True))
        duration_seconds = self._selected_display_duration_seconds()
        self.roles_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            dps = item.damage / duration_seconds if duration_seconds > 0 else 0.0
            count_text = f"{item.hits:,} / {item.max_hp_reduction_events:,}"
            values = (
                item.character_name,
                count_text,
                f"{item.damage:,.0f}",
                f"{dps:,.0f}",
            )
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                if column > 0:
                    table_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.roles_table.setItem(row, column, table_item)
            self.roles_table.setCellWidget(
                row,
                4,
                BattleRoleShareBar(
                    share_percent=item.share_percent,
                    color=role_contribution_color(row),
                ),
            )
            self.roles_table.setRowHeight(row, 34)
        self.roles_pie.set_roles(rows)

    def _render_damage_composition(self) -> None:
        analysis = self._analysis
        if analysis is None:
            self._current_composition = None
            self.damage_composition_panel.clear()
            return
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=getattr(analysis, "roles", ()),
            hits=getattr(analysis, "hits", ()),
            max_hp_events=getattr(analysis, "max_hp_events", ()),
            hit_replays=getattr(analysis, "hit_replays", ()),
            role_identities=tuple(
                (row.character_id, row.character_name)
                for row in getattr(analysis, "baselines", ())
            ),
            grouping=self._composition_grouping,
            segment_total_damage=getattr(
                analysis,
                "effective_damage",
                getattr(analysis, "total_damage", 0.0),
            ),
        )
        self._current_composition = composition
        self.damage_composition_panel.render(composition)
        needs_message = (
            composition.pending_topple_attribution
            or composition.unresolved_topple_attribution
        )
        self.composition_status_label.setVisible(needs_message)
        self.composition_topple_button.setVisible(
            composition.pending_topple_attribution
        )
        if composition.pending_topple_attribution:
            self.composition_status_label.setText(
                "当前时段含团队倾陷，尚未加载逐角色公式。"
            )
        elif composition.unresolved_topple_attribution:
            self.composition_status_label.setText(
                "倾陷缺少明确目标或公式证据，暂列未归因。"
            )

    def _set_composition_grouping(self, grouping: str) -> None:
        if grouping not in {"coarse", "fine"}:
            return
        self._composition_grouping = grouping
        self.composition_buttons[grouping].setChecked(True)
        self._render_damage_composition()
