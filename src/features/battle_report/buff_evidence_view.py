# 展示静态规则推算的 Buff 区间及其逐击覆盖，不冒充运行时实测。
"""Presentation-only summary for inferred battle Buff intervals."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisSnapshot
from src.features.battle_report.analysis_components import analysis_table
from src.features.battle_report.marginal_quantification_view import damage_coverage_text
from src.services.battle_buff_attribute_projection_service import (
    normalize_battle_buff_property_id,
)
from src.services.battle_buff_counterfactual_plan_service import (
    battle_buff_counterfactual_key,
)
from src.services.battle_hit_buff_explanation_service import (
    battle_buff_property_label,
    format_battle_buff_value,
)


_SCOPE_LABELS = {
    "self": "自身",
    "team": "全队",
    "team_others": "其他队友",
    "target": "敌方目标",
    "unknown": "作用对象待确认",
}


def _trigger_label(value: str) -> str:
    raw = str(value or "")
    normalized = raw.casefold()
    rules = (
        (("whole_battle", "battle_condition", "equipped"), "整场常驻"),
        (("change_role_in", "role_in", "appear"), "切入/进场"),
        (("change_role_out", "role_out"), "切出/离场"),
        (("qte",), "QTE"),
        (("ultra", "q_begin", "q_action"), "Q 开始"),
        (("skill", "e_begin", "e_action"), "E 开始"),
        (("treatment", "cure", "heal"), "治疗触发"),
        (("target_toppled", "unbalance"), "目标倾陷"),
        (("dark_star", "nova"), "黯星结算"),
        (("damage_after_hit", "after_hit"), "造成伤害后"),
        (("normalattack", "melee"), "普通攻击"),
    )
    return next(
        (label for needles, label in rules if any(item in normalized for item in needles)),
        raw or "触发待确认",
    )


def _modifier_cells(intervals: list) -> tuple[str, str]:
    first = intervals[0]
    max_stacks = max(
        (max(1, int(getattr(row, "stacks", 1))) for row in intervals),
        default=1,
    )
    labels: list[str] = []
    values: list[str] = []
    for modifier in getattr(first, "modifiers", ()):
        property_id = normalize_battle_buff_property_id(modifier.property_id)
        labels.append(battle_buff_property_label(property_id))
        if modifier.magnitude_value is not None:
            per_stack = float(modifier.magnitude_value)
            if max_stacks > 1 or int(getattr(first, "stack_limit_count", 1)) > 1:
                values.append(
                    f"每层 {format_battle_buff_value(property_id, per_stack)}"
                    f" × {max_stacks} 层"
                )
            else:
                values.append(format_battle_buff_value(property_id, per_stack))
        else:
            values.append("公式尚未解析")
    if not labels:
        return "属性尚未解析", "—"
    return "\n".join(labels), "\n".join(values)


def _gain_percent(counterfactual) -> str:
    if counterfactual is None:
        return "—"
    status = counterfactual.quantification.status
    if status == "not_applicable":
        return "+0.00%"
    if status == "complete":
        value = counterfactual.gain_percent
        return "—" if value is None else f"{value:+.2f}%"
    if status == "partial":
        value = counterfactual.quantified_gain_percent
        return "—" if value is None else f"{value:+.2f}%（部分）"
    return "—"


def _optional_number(value: float | None, format_spec: str) -> str:
    return "—" if value is None else format(value, format_spec)


def _counterfactual_detail(counterfactual) -> str:
    if counterfactual is None:
        return "当前分析结果尚未生成逐 Buff 移除反事实。"
    gaps = "\n".join(
        f"- {gap.explanation}" for gap in counterfactual.quantification.gaps
    )
    partial_note = (
        "\n部分数值不代表完整 Buff 收益或收益下限。"
        if counterfactual.quantification.status == "partial" else ""
    )
    return (
        f"当前时段有效伤害：{counterfactual.baseline_damage:,.2f}\n"
        f"伤害覆盖率：{damage_coverage_text(counterfactual.damage_coverage)}\n"
        f"覆盖逐击：{counterfactual.affected_hits:,}\n"
        f"可计算逐击：{counterfactual.quantified_hits:,}\n"
        f"量化状态：{counterfactual.quantification.status}\n"
        f"移除后伤害：{_optional_number(counterfactual.without_buff_damage, ',.2f')}\n"
        f"完整伤害增量：{_optional_number(counterfactual.damage_gain, '+,.2f')}\n"
        f"部分伤害增量：{_optional_number(counterfactual.quantified_damage_gain, '+,.2f')}\n"
        f"方法：{counterfactual.method}\n"
        f"置信度：{counterfactual.confidence}\n"
        f"说明：{counterfactual.explanation}"
        + partial_note
        + (f"\n未量化缺口：\n{gaps}" if gaps else "")
    )


def _interval_detail(rows: list, counterfactual) -> str:
    first = rows[0]
    interval_lines = "\n".join(
        f"- {row.start_us / 1_000_000:.3f}s—{row.end_us / 1_000_000:.3f}s；"
        f"{getattr(row, 'stacks', 1)} 层；状态 {row.state_confidence} / 数值 {row.value_confidence}"
        for row in rows
    )
    modifier_lines = "\n".join(
        f"- {modifier.property_id}；值 {modifier.magnitude_value!r}；"
        f"Calculation {modifier.calculation_asset_path or '无'}"
        for modifier in getattr(first, "modifiers", ())
    ) or "- 未提取到属性修正"
    return (
        f"Buff 包：{first.buff_name}\n"
        f"来源定义：{getattr(first, 'source_effect_definition_id', '未知')}\n"
        f"资产：{getattr(first, 'buff_asset_path', '未知')}\n"
        f"来源类型：{getattr(first, 'source_kind', '未知')}\n"
        f"原始触发：{first.trigger_event_type}\n"
        f"持续策略：{getattr(first, 'duration_policy', '未知')}\n"
        f"推断依据：{first.inference_basis}\n\n"
        f"区间：\n{interval_lines}\n\n属性证据：\n{modifier_lines}\n\n"
        f"反事实：\n{_counterfactual_detail(counterfactual)}"
    )


class _BuffDetailDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Buff 详情")
        root = QVBoxLayout(self)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        root.addWidget(self.detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(920, 720))

    def show_detail(self, text: str) -> None:
        self.detail.setPlainText(text)
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)
        self.show()
        self.raise_()


class BattleBuffEvidencePanel(QWidget):
    """Render one decision-oriented row per stable Buff package."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary_label = QLabel()
        self.summary_label.hide()
        self.table = analysis_table(
            (
                "来源", "作用对象", "Buff", "值", "触发", "时间覆盖",
                "覆盖逐击", "伤害覆盖率", "收益率", "详情",
            ),
            260,
            default_widths=(130, 105, 190, 190, 135, 190, 90, 145, 115, 70),
        )
        self.table.cellClicked.connect(self._cell_clicked)
        layout.addWidget(self.table)
        self._detail_dialog: _BuffDetailDialog | None = None

    def clear(self) -> None:
        self.summary_label.setText("当前没有可用的 Buff 推算。")
        self.table.setRowCount(0)

    def render(self, analysis: BattleAnalysisSnapshot) -> None:
        intervals = analysis.buff_intervals
        if not intervals:
            self.clear()
            return
        groups: dict[str, list] = defaultdict(list)
        for interval in intervals:
            groups[battle_buff_counterfactual_key(interval)].append(interval)
        counterfactuals = {
            row.buff_key: row
            for row in getattr(analysis, "buff_counterfactuals", ())
        }
        ordered = sorted(
            groups.items(),
            key=lambda item: (item[1][0].source_character_name, item[1][0].buff_name),
        )
        self.table.setRowCount(len(ordered))
        total_duration = max(
            0.0,
            (analysis.range_end_us - analysis.range_start_us) / 1_000_000,
        )
        for row_index, (key, rows) in enumerate(ordered):
            first = rows[0]
            counterfactual = counterfactuals.get(key)
            duration = (
                counterfactual.coverage_seconds
                if counterfactual is not None
                else sum(
                    max(
                        0,
                        min(row.end_us, analysis.range_end_us)
                        - max(row.start_us, analysis.range_start_us),
                    )
                    for row in rows
                ) / 1_000_000
            )
            property_text, value_text = _modifier_cells(rows)
            coverage_percent = duration / total_duration * 100.0 if total_duration else 0.0
            detail = _interval_detail(rows, counterfactual)
            values = (
                first.source_character_name,
                _SCOPE_LABELS.get(first.target_scope, first.target_scope),
                property_text,
                value_text,
                " / ".join(dict.fromkeys(_trigger_label(row.trigger_event_type) for row in rows)),
                f"{duration:.3f}s / {total_duration:.3f}s = {coverage_percent:.1f}%",
                "—" if counterfactual is None else f"{counterfactual.affected_hits:,}",
                "—" if counterfactual is None else damage_coverage_text(counterfactual.damage_coverage),
                _gain_percent(counterfactual),
                "查看",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(detail)
                if column == 9:
                    item.setData(Qt.ItemDataRole.UserRole, detail)
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                self.table.setItem(row_index, column, item)
        self.table.resizeRowsToContents()

    def _cell_clicked(self, row: int, column: int) -> None:
        if column != 9:
            return
        item = self.table.item(row, column)
        detail = "" if item is None else str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not detail:
            return
        if self._detail_dialog is None:
            self._detail_dialog = _BuffDetailDialog(self)
        self._detail_dialog.show_detail(detail)
