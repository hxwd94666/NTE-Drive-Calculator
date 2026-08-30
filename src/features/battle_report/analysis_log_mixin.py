# 战报长页的逐击筛选、渲染和分页交互。
"""Hit-log presentation kept separate from the long analysis page layout."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QTableWidgetItem

from src.domain.battle_report import BattleAnalysisHit
from src.features.battle_report.analysis_components import (
    ancestor_vertical_scroll_positions,
    restore_vertical_scroll_positions,
)
from src.features.battle_report.hit_buff_dialog import BattleHitBuffDialog
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.skill_name_rendering_service import (
    preferred_battle_damage_name,
    render_battle_event_type,
)


def _number(value: float) -> str:
    return f"{value:,.0f}"


def _time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def _hit_character_label(hit: BattleAnalysisHit) -> str:
    return (
        hit.character_name
        if hit.direction == "outgoing"
        else f"承伤：{hit.character_name}"
    )


class BattleAnalysisLogMixin:
    """Render immutable hit evidence; the concrete QWidget owns all controls."""

    _analysis: Any
    _log_page: int
    _log_page_size: int

    def _active_buffs_for_hit(self, hit: BattleAnalysisHit) -> tuple:
        intervals = (
            getattr(self._analysis, "buff_intervals", ())
            if self._analysis is not None
            else ()
        )
        return BattleBuffInferenceService.active_for_hit(intervals, hit)

    def _filtered_hits(self) -> tuple[BattleAnalysisHit, ...]:
        analysis = self._analysis
        if analysis is None:
            return ()
        needle = self.log_filter.text().strip().casefold()
        if not needle:
            return analysis.hits
        return tuple(
            hit
            for hit in analysis.hits
            if needle
            in " ".join((
                hit.character_name,
                hit.skill_name,
                hit.damage_name,
                hit.target_name,
                hit.classification,
                render_battle_event_type(
                    hit.classification,
                    hit.attack_type,
                    hit.damage_attribute,
                ),
            )).casefold()
        )

    def _render_log(self) -> None:
        hits = self._filtered_hits()
        page_count = max(1, math.ceil(len(hits) / self._log_page_size))
        self._log_page = min(self._log_page, page_count - 1)
        start = self._log_page * self._log_page_size
        page = hits[start : start + self._log_page_size]
        self.log_table.setRowCount(len(page))
        replay_by_event = {
            item.event_id: item
            for item in (
                getattr(self._analysis, "hit_replays", ())
                if self._analysis else ()
            )
        }
        crit_labels = {
            "critical": "暴击",
            "non_critical": "未暴击",
            "not_applicable": "不适用",
            "ambiguous": "无法确定",
            "unreplayable": "未重放",
        }
        for row, hit in enumerate(page):
            damage_name = preferred_battle_damage_name(
                hit.damage_name,
                hit.skill_name,
                hit.ability_id,
            )
            damage_source_name = damage_name
            if hit.skill_name not in {
                "",
                damage_name,
                "未知技能",
                "未识别技能",
            }:
                damage_source_name = f"{damage_name} / {hit.skill_name}"
            replay = replay_by_event.get(hit.event_id)
            replay_text = "—"
            crit_text = "未重放"
            replay_tooltip = "当前逐击尚无公式重放结果。"
            if replay is not None:
                if replay.selected_damage is not None:
                    signed_error = replay.signed_error_percent
                    if signed_error is None and replay.observed_damage > 0:
                        signed_error = (
                            (replay.selected_damage - replay.observed_damage)
                            / replay.observed_damage
                            * 100.0
                        )
                    error_text = (
                        "—" if signed_error is None else f"{signed_error:+.2f}%"
                    )
                    replay_text = f"{_number(replay.selected_damage)} / {error_text}"
                crit_text = crit_labels[replay.critical_state]
                details = "\n".join(
                    f"{factor.label}: {factor.value:g}（{factor.evidence_basis}）"
                    for factor in replay.factors
                )
                gaps = "\n".join(replay.missing_evidence)
                replay_tooltip = "\n".join(
                    value
                    for value in (
                        f"观测：{replay.observed_damage:,.0f}",
                        (
                            "非暴击候选：—"
                            if replay.non_critical_damage is None
                            else f"非暴击候选：{replay.non_critical_damage:,.2f}"
                        ),
                        (
                            "暴击候选：—"
                            if replay.critical_damage is None
                            else f"暴击候选：{replay.critical_damage:,.2f}"
                        ),
                        details,
                        gaps,
                    )
                    if value
                )
            values = (
                _time(self._display_time_us(hit.relative_time_us)),
                str(hit.sequence),
                _hit_character_label(hit),
                damage_source_name,
                render_battle_event_type(
                    hit.classification,
                    hit.attack_type,
                    hit.damage_attribute,
                ),
                hit.target_name,
                _number(hit.damage),
                replay_text,
                crit_text,
                "查看",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {7, 8}:
                    item.setToolTip(replay_tooltip)
                if column == 9:
                    item.setData(Qt.ItemDataRole.UserRole, hit.event_id)
                    item.setToolTip(
                        "点击查看本击原始字段、HP、公式因子、置信度和推算 Buff。"
                    )
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                self.log_table.setItem(row, column, item)
        self.log_page_label.setText(
            f"{self._log_page + 1} / {page_count} · {len(hits):,} 条"
        )
        self.prev_button.setEnabled(self._log_page > 0)
        self.next_button.setEnabled(self._log_page + 1 < page_count)

    def _change_log_page(self, delta: int) -> None:
        hits = self._filtered_hits()
        page_count = max(1, math.ceil(len(hits) / self._log_page_size))
        target_page = min(page_count - 1, max(0, self._log_page + delta))
        if target_page == self._log_page:
            return
        scroll_positions = ancestor_vertical_scroll_positions(self)
        self._log_page = target_page
        self._render_log()
        self.log_table.scrollToTop()
        restore_vertical_scroll_positions(scroll_positions)
        QTimer.singleShot(
            0,
            lambda: restore_vertical_scroll_positions(scroll_positions),
        )

    def _previous_log_page(self) -> None:
        self._change_log_page(-1)

    def _next_log_page(self) -> None:
        self._change_log_page(1)

    def _reset_log_page(self) -> None:
        self._log_page = 0
        self._render_log()

    def _log_cell_clicked(self, row: int, column: int) -> None:
        if column != 9 or self._analysis is None:
            return
        item = self.log_table.item(row, column)
        event_id = "" if item is None else str(
            item.data(Qt.ItemDataRole.UserRole) or ""
        )
        hit = next(
            (row for row in self._analysis.hits if row.event_id == event_id),
            None,
        )
        if hit is None:
            return
        dialog = getattr(self, "_hit_buff_dialog", None)
        if dialog is None:
            dialog = BattleHitBuffDialog(getattr(self, "log_dialog", self))
            self._hit_buff_dialog = dialog
        replay = next(
            (
                row for row in getattr(self._analysis, "hit_replays", ())
                if row.event_id == hit.event_id
            ),
            None,
        )
        dialog.show_for_hit(hit, self._active_buffs_for_hit(hit), replay=replay)

    def _hide_hit_buff_dialog(self) -> None:
        dialog = getattr(self, "_hit_buff_dialog", None)
        if dialog is not None:
            dialog.hide()
