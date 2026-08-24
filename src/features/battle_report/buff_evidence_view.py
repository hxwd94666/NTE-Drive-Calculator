# 展示静态规则推算的 Buff 区间及其逐击覆盖，不冒充运行时实测。
"""Presentation-only summary for inferred battle Buff intervals."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtWidgets import QLabel, QTableWidgetItem, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.domain.battle_report import BattleAnalysisSnapshot
from src.features.battle_report.analysis_components import analysis_table
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.battle_buff_counterfactual_service import (
    battle_buff_counterfactual_key,
)


_SCOPE_LABELS = {
    "self": "自身",
    "team": "全队",
    "target": "目标",
    "unknown": "作用对象待确认",
}


def _modifier_text(interval) -> str:
    values = []
    for row in interval.modifiers:
        value = "公式待解释"
        if row.magnitude_value is not None:
            value = f"{row.magnitude_value:g}"
            if row.calculation_asset_path:
                calculation = row.calculation_asset_path.rsplit("/", 1)[-1]
                value = f"{value}（{calculation} 已解析）"
        elif row.calculation_asset_path:
            value = row.calculation_asset_path.rsplit("/", 1)[-1]
        values.append(f"{row.property_id} {value}")
    return "、".join(values) or "未提取数值属性"


class BattleBuffEvidencePanel(QWidget):
    """Aggregate inferred intervals while hit rows expose per-hit coverage."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.summary_label = QLabel("当前没有可用的 Buff 推算。")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            themed_style("color:#d29922;font-size:12px;font-weight:600")
        )
        layout.addWidget(self.summary_label)
        self.table = analysis_table(
            (
                "来源角色",
                "Buff / GE",
                "作用对象",
                "静态触发",
                "区间数",
                "覆盖时长",
                "覆盖逐击",
                "无此 Buff 伤害",
                "伤害增量",
                "时段收益率",
                "量化覆盖",
                "属性证据",
                "置信度",
            ),
            260,
            default_widths=(
                125, 250, 110, 220, 76, 95, 90, 145, 120, 120, 115, 300, 145,
            ),
        )
        layout.addWidget(self.table)

    def clear(self) -> None:
        self.summary_label.setText("当前没有可用的 Buff 推算。")
        self.table.setRowCount(0)

    def render(self, analysis: BattleAnalysisSnapshot) -> None:
        intervals = analysis.buff_intervals
        if not intervals:
            self.clear()
            self.summary_label.setText(
                "当前冻结配装没有生成可定界的静态 Buff 区间；"
                "未解析不等于本场没有 Buff。"
            )
            return
        groups: dict[str, list] = defaultdict(list)
        for interval in intervals:
            key = battle_buff_counterfactual_key(interval)
            groups[key].append(interval)
        counterfactuals = {
            row.buff_key: row
            for row in getattr(analysis, "buff_counterfactuals", ())
        }
        ordered = sorted(
            groups.items(),
            key=lambda item: (
                item[1][0].source_character_name,
                item[1][0].buff_name,
                item[0],
            ),
        )
        self.table.setRowCount(len(ordered))
        for row_index, (key, rows) in enumerate(ordered):
            first = rows[0]
            counterfactual = counterfactuals.get(key)
            fallback_duration = sum(
                max(
                    0,
                    min(item.end_us, analysis.range_end_us)
                    - max(item.start_us, analysis.range_start_us),
                )
                for item in rows
            ) / 1_000_000.0
            duration = (
                counterfactual.coverage_seconds
                if counterfactual is not None
                else fallback_duration
            )
            triggers = " / ".join(dict.fromkeys(
                row.trigger_event_type for row in rows
            ))
            if counterfactual is None:
                affected_hits = "—"
                without_damage = "—"
                damage_gain = "—"
                gain_percent = "—"
                quantified = "—"
                result_tooltip = "当前分析结果尚未生成逐 Buff 移除反事实。"
            else:
                affected_hits = f"{counterfactual.affected_hits:,}"
                without_damage = f"{counterfactual.without_buff_damage:,.2f}"
                damage_gain = f"{counterfactual.damage_gain:+,.2f}"
                gain_percent = f"{counterfactual.gain_percent:+.2f}%"
                quantified = (
                    f"{counterfactual.quantified_hits:,} 击 / "
                    f"{counterfactual.quantified_percent:.1f}%伤害"
                )
                if counterfactual.method == "unquantified_zero_estimate":
                    gain_percent = "暂估 +0.00%"
                result_tooltip = (
                    f"当前时段伤害：{counterfactual.baseline_damage:,.2f}\n"
                    f"移除后伤害：{counterfactual.without_buff_damage:,.2f}\n"
                    f"伤害增量：{counterfactual.damage_gain:+,.2f}\n"
                    "收益率 = (当前伤害 - 移除后伤害) / 移除后伤害\n"
                    f"= {counterfactual.gain_percent:+.4f}%\n"
                    f"量化逐击：{counterfactual.quantified_hits:,} / "
                    f"覆盖逐击：{counterfactual.affected_hits:,}\n"
                    f"{counterfactual.explanation}"
                )
            values = (
                first.source_character_name,
                first.buff_name,
                _SCOPE_LABELS.get(first.target_scope, first.target_scope),
                triggers,
                f"{len(rows):,}",
                f"{duration:.3f}s",
                affected_hits,
                without_damage,
                damage_gain,
                gain_percent,
                quantified,
                _modifier_text(first),
                (
                    f"反事实 {counterfactual.confidence} / "
                    f"状态 {first.state_confidence} / 数值 {first.value_confidence}"
                    if counterfactual is not None
                    else f"状态 {first.state_confidence} / 数值 {first.value_confidence}"
                ),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(
                    result_tooltip if 6 <= column <= 10 else first.inference_basis
                )
                self.table.setItem(row_index, column, item)
        affected_hits = sum(
            bool(BattleBuffInferenceService.active_for_hit(intervals, hit))
            for hit in analysis.hits
            if hit.direction == "outgoing"
        )
        numeric = sum(
            any(
                modifier.magnitude_value is not None
                and not modifier.calculation_asset_path
                for modifier in row.modifiers
            )
            for row in intervals
        )
        self.summary_label.setText(
            f"{analysis.buff_inference_version} · 当前时段推算 {len(intervals):,} 个区间 / "
            f"{len(groups):,} 类效果 · {numeric:,} 个区间含可直接读取的数值修正 · "
            f"覆盖 {affected_hits:,} 个出伤事件。收益逐项按移除单个 Buff 独立计算，"
            "Buff 之间存在重叠和乘区交互，各行收益不可直接相加。所有结果均为静态推算，"
            "未来 nte-core 实测将按 Buff、对象、字段和时段逐项覆盖。"
        )
