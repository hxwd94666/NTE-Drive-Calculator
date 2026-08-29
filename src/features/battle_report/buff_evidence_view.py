# 展示静态规则推算的 Buff 区间及其逐击覆盖，不冒充运行时实测。
"""Presentation-only summary for inferred battle Buff intervals."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtWidgets import QLabel, QTableWidgetItem, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.domain.battle_report import BattleAnalysisSnapshot
from src.features.battle_report.analysis_components import analysis_table
from src.features.battle_report.marginal_quantification_view import (
    damage_coverage_text,
)
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


def _coverage_text(counterfactual) -> str:
    quantification = counterfactual.quantification
    basis = quantification.basis_damage
    if quantification.status == "not_applicable":
        return "不适用"
    if basis <= 0.0:
        return "无伤害基数"
    complete = quantification.fully_quantified_damage / basis * 100.0
    partial = quantification.partially_quantified_damage / basis * 100.0
    unavailable = quantification.unavailable_damage / basis * 100.0
    if quantification.status == "complete":
        return f"完整 {complete:.1f}%"
    if quantification.status == "unavailable":
        return f"未量化 {unavailable:.1f}%"
    return (
        f"完整 {complete:.1f}% / 部分 {partial:.1f}% / "
        f"未量化 {unavailable:.1f}%"
    )


def _counterfactual_cells(counterfactual) -> tuple[str, str, str, str]:
    status = counterfactual.quantification.status
    if status == "not_applicable":
        return "不适用", "不适用", "不适用", _coverage_text(counterfactual)
    if status == "unavailable":
        return "—", "未量化", "—", _coverage_text(counterfactual)
    if status == "partial":
        without = counterfactual.without_quantified_effect_damage
        gain = counterfactual.quantified_damage_gain
        percent = counterfactual.quantified_gain_percent
        return (
            "—" if without is None else f"已量化 {without:,.2f}",
            "—" if gain is None else f"已量化 {gain:+,.2f}",
            "—" if percent is None else f"已量化 {percent:+.2f}%",
            _coverage_text(counterfactual),
        )
    without = counterfactual.without_buff_damage
    gain = counterfactual.damage_gain
    percent = counterfactual.gain_percent
    return (
        "—" if without is None else f"{without:,.2f}",
        "—" if gain is None else f"{gain:+,.2f}",
        "—" if percent is None else f"{percent:+.2f}%",
        _coverage_text(counterfactual),
    )


def _optional_number(value: float | None, format_spec: str) -> str:
    return "—" if value is None else format(value, format_spec)


def _counterfactual_tooltip(counterfactual) -> str:
    status = counterfactual.quantification.status
    gaps = "\n".join(
        f"- {gap.explanation}" for gap in counterfactual.quantification.gaps
    )
    if status == "not_applicable":
        detail = "当前时段没有可归给该 Buff 的伤害变化。"
    elif status == "unavailable":
        detail = "完整 Buff 收益暂不可量化；未知不记为 0。"
    elif status == "partial":
        detail = (
            f"已量化改动下伤害："
            f"{_optional_number(counterfactual.without_quantified_effect_damage, ',.2f')}\n"
            "已量化分量："
            f"{_optional_number(counterfactual.quantified_damage_gain, '+,.2f')}\n"
            "该数值不代表完整 Buff 收益或收益下限。"
        )
    else:
        detail = (
            "移除后伤害："
            f"{_optional_number(counterfactual.without_buff_damage, ',.2f')}\n"
            "完整伤害增量："
            f"{_optional_number(counterfactual.damage_gain, '+,.2f')}\n"
            "完整收益率："
            f"{_optional_number(counterfactual.gain_percent, '+.4f')}%"
        )
    return (
        f"当前时段伤害：{counterfactual.baseline_damage:,.2f}\n"
        "原始伤害覆盖率："
        f"{damage_coverage_text(getattr(counterfactual, 'damage_coverage', None))}\n"
        f"量化状态：{status}\n{detail}\n"
        f"量化逐击：{counterfactual.quantified_hits:,} / "
        f"覆盖逐击：{counterfactual.affected_hits:,}\n"
        f"{counterfactual.explanation}"
        + (f"\n未量化缺口：\n{gaps}" if gaps else "")
    )


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
                "伤害覆盖率",
                "移除后 / 已量化伤害",
                "Buff 收益",
                "收益率",
                "量化状态",
                "属性证据",
                "置信度",
            ),
            260,
            default_widths=(
                125, 250, 110, 220, 76, 95, 90, 150, 145, 120, 120, 115, 300,
                145,
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
                damage_coverage = "—"
                without_damage = "—"
                damage_gain = "—"
                gain_percent = "—"
                quantified = "—"
                result_tooltip = "当前分析结果尚未生成逐 Buff 移除反事实。"
            else:
                affected_hits = f"{counterfactual.affected_hits:,}"
                damage_coverage = damage_coverage_text(
                    getattr(counterfactual, "damage_coverage", None)
                )
                without_damage, damage_gain, gain_percent, quantified = (
                    _counterfactual_cells(counterfactual)
                )
                result_tooltip = _counterfactual_tooltip(counterfactual)
            values = (
                first.source_character_name,
                first.buff_name,
                _SCOPE_LABELS.get(first.target_scope, first.target_scope),
                triggers,
                f"{len(rows):,}",
                f"{duration:.3f}s",
                affected_hits,
                damage_coverage,
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
                    result_tooltip if 6 <= column <= 11 else first.inference_basis
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
