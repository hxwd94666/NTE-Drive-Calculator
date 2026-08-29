# 渲染按来源筛选的 Buff 与被动边际结果。
"""Small UI mixin for source-filtered Buff and passive marginal rows."""

from __future__ import annotations

from PySide6.QtWidgets import QTableWidget

from src.features.battle_report.marginal_result_table_view import (
    render_buff_benefit_results,
)


class BattleMarginalBuffRenderMixin:
    buff_benefit_table: QTableWidget

    def selected_character_id(self) -> int | None:
        raise NotImplementedError

    def _render_buff_benefits(self, results, *, passive_results=()) -> None:
        render_buff_benefit_results(
            self.buff_benefit_table,
            results,
            source_character_id=self.selected_character_id(),
            passive_results=passive_results,
        )


__all__ = ["BattleMarginalBuffRenderMixin"]
