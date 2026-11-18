# 展示静态规则推算的 Buff 区间及其逐击覆盖，不冒充运行时实测。
"""Presentation-only summary for inferred battle Buff intervals."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from collections import defaultdict

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.features.battle_report.analysis.components import analysis_table
from src.features.battle_report.analysis.quantification import damage_coverage_text
from src.services.battle_report.buffs.attribute_projection import (
    normalize_battle_buff_property_id,
)
from src.services.battle_report.buffs.counterfactual_plan import (
    battle_buff_counterfactual_key,
)
from src.services.battle_report.buffs.hit_explanation import (
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


def _trigger_label(value: str, *, text_catalog=None) -> str:
    raw = str(value or "")
    normalized = raw.casefold()
    # 未登记的新标签走 localized_text；QTE 与已登记的“普通攻击”按原文交给 ui_enum_text。
    rules = (
        (("whole_battle", "battle_condition", "equipped"),
         "battle_report.buff_evidence.trigger.whole_battle", "整场常驻"),
        (("change_role_in", "role_in", "appear"),
         "battle_report.buff_evidence.trigger.role_in", "切入/进场"),
        (("change_role_out", "role_out"),
         "battle_report.buff_evidence.trigger.role_out", "切出/离场"),
        (("qte",), None, "QTE"),
        (("ultra", "q_begin", "q_action"),
         "battle_report.buff_evidence.trigger.q_start", "Q 开始"),
        (("skill", "e_begin", "e_action"),
         "battle_report.buff_evidence.trigger.e_start", "E 开始"),
        (("treatment", "cure", "heal"),
         "battle_report.buff_evidence.trigger.heal", "治疗触发"),
        (("target_toppled", "unbalance"),
         "battle_report.buff_evidence.trigger.target_toppled", "目标倾陷"),
        (("dark_star", "nova"),
         "battle_report.buff_evidence.trigger.dark_star", "黯星结算"),
        (("damage_after_hit", "after_hit"),
         "battle_report.buff_evidence.trigger.after_hit", "造成伤害后"),
        (("normalattack", "melee"), None, "普通攻击"),
    )
    for needles, key, fallback in rules:
        if any(item in normalized for item in needles):
            return (localized_text(text_catalog, key, fallback)
                    if key is not None else ui_enum_text(text_catalog, fallback))
    if raw:
        return raw
    return localized_text(
        text_catalog, "battle_report.buff_evidence.trigger.unresolved", "触发待确认")


def _modifier_cells(intervals: list, *, text_catalog=None) -> tuple[str, str]:
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
                values.append(localized_text(
                    text_catalog,
                    "battle_report.buff_evidence.modifier.per_stack",
                    "每层 {value} × {stacks} 层",
                    value=format_battle_buff_value(property_id, per_stack),
                    stacks=max_stacks,
                ))
            else:
                values.append(format_battle_buff_value(property_id, per_stack))
        else:
            values.append(localized_text(
                text_catalog,
                "battle_report.buff_evidence.modifier.formula_unresolved",
                "公式尚未解析",
            ))
    if not labels:
        return (localized_text(
            text_catalog,
            "battle_report.buff_evidence.modifier.property_unresolved",
            "属性尚未解析",
        ), "—")
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
        return "—" if value is None else f"{value:+.2f}%"
    return "—"


def _optional_number(value: float | None, format_spec: str) -> str:
    return "—" if value is None else format(value, format_spec)


def _counterfactual_detail(counterfactual, *, text_catalog=None) -> str:
    if counterfactual is None:
        return localized_text(
            text_catalog,
            "battle_report.buff_evidence.counterfactual.missing",
            "当前分析结果尚未生成逐 Buff 移除反事实。",
        )
    gaps = "\n".join(
        f"- {gap.explanation}" for gap in counterfactual.quantification.gaps
    )
    partial_note = (
        "\n" + localized_text(
            text_catalog,
            "battle_report.buff_evidence.counterfactual.partial_note",
            "部分数值不代表完整 Buff 收益或收益下限。",
        )
        if counterfactual.quantification.status == "partial" else ""
    )
    lines = [
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.baseline_damage",
            "当前时段有效伤害：{damage}", damage=f"{counterfactual.baseline_damage:,.2f}"),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.damage_coverage",
            "伤害覆盖率：{coverage}", coverage=damage_coverage_text(counterfactual.damage_coverage, text_catalog=text_catalog)),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.affected_hits",
            "覆盖逐击：{hits}", hits=f"{counterfactual.affected_hits:,}"),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.quantified_hits",
            "可计算逐击：{hits}", hits=f"{counterfactual.quantified_hits:,}"),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.status",
            "量化状态：{status}", status=counterfactual.quantification.status),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.without_buff_damage",
            "移除后伤害：{damage}", damage=_optional_number(counterfactual.without_buff_damage, ',.2f')),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.damage_gain",
            "完整伤害增量：{damage}", damage=_optional_number(counterfactual.damage_gain, '+,.2f')),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.quantified_damage_gain",
            "部分伤害增量：{damage}", damage=_optional_number(counterfactual.quantified_damage_gain, '+,.2f')),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.method",
            "方法：{method}", method=counterfactual.method),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.confidence",
            "置信度：{confidence}", confidence=counterfactual.confidence),
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.explanation",
            "说明：{explanation}", explanation=counterfactual.explanation),
    ]
    if gaps:
        return "\n".join(lines) + partial_note + "\n" + localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.gaps",
            "未量化缺口：\n{gaps}", gaps=gaps)
    return "\n".join(lines) + partial_note


def _interval_detail(rows: list, counterfactual, *, text_catalog=None) -> str:
    first = rows[0]
    unknown_label = ui_enum_text(text_catalog, "未知")
    none_label = ui_enum_text(text_catalog, "无")
    interval_lines = "\n".join(
        localized_text(
            text_catalog,
            "battle_report.buff_evidence.interval.line",
            "- {start}—{end}；{stacks} 层；状态 {state} / 数值 {value}",
            start=f"{row.start_us / 1_000_000:.3f}s",
            end=f"{row.end_us / 1_000_000:.3f}s",
            stacks=getattr(row, 'stacks', 1),
            state=row.state_confidence,
            value=row.value_confidence,
        )
        for row in rows
    )
    modifier_lines = "\n".join(
        localized_text(
            text_catalog,
            "battle_report.buff_evidence.evidence.modifier_line",
            "- {property_id}；值 {value}；Calculation {asset}",
            property_id=modifier.property_id,
            value=repr(modifier.magnitude_value),
            asset=modifier.calculation_asset_path or none_label,
        )
        for modifier in getattr(first, "modifiers", ())
    ) or localized_text(
        text_catalog,
        "battle_report.buff_evidence.evidence.no_modifier",
        "- 未提取到属性修正",
    )
    return "\n".join((
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.buff_package",
            "Buff 包：{name}", name=first.buff_name),
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.source_definition",
            "来源定义：{value}", value=getattr(first, 'source_effect_definition_id', unknown_label)),
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.asset",
            "资产：{value}", value=getattr(first, 'buff_asset_path', unknown_label)),
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.source_kind",
            "来源类型：{value}", value=getattr(first, 'source_kind', unknown_label)),
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.raw_trigger",
            "原始触发：{value}", value=first.trigger_event_type),
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.duration_policy",
            "持续策略：{value}", value=getattr(first, 'duration_policy', unknown_label)),
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.inference_basis",
            "推断依据：{value}", value=first.inference_basis),
        "",
        localized_text(
            text_catalog, "battle_report.buff_evidence.interval.heading", "区间："),
        interval_lines,
        "",
        localized_text(
            text_catalog, "battle_report.buff_evidence.evidence.heading", "属性证据："),
        modifier_lines,
        "",
        localized_text(
            text_catalog, "battle_report.buff_evidence.counterfactual.heading", "反事实："),
        _counterfactual_detail(counterfactual, text_catalog=text_catalog),
    ))


class _BuffDetailDialog(QDialog):
    def __init__(self, parent: QWidget, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.battle_report.init.self'))
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

    def __init__(self, parent: QWidget | None = None, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary_label = plain_label(self)
        self.summary_label.hide()
        self.table = analysis_table(
            (
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table'), resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_2'), "Buff", resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_3'), resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_4'), resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_5'),
                resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_6'), resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_7'), resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_8'), resolve_text_catalog(text_catalog).render('ui.battle_report.init.table_9'),
            ),
            260,
            default_widths=(130, 105, 190, 190, 135, 190, 90, 145, 115, 70),
        )
        self.table.cellClicked.connect(self._cell_clicked)
        layout.addWidget(self.table)
        self._detail_dialog: _BuffDetailDialog | None = None

    def clear(self) -> None:
        self.summary_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.clear.summary_label'))
        self.table.setRowCount(0)

    def render(self, analysis: BattleAnalysisSnapshot) -> None:
        text_catalog = getattr(self, 'text_catalog', None)
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
            property_text, value_text = _modifier_cells(rows, text_catalog=text_catalog)
            coverage_percent = duration / total_duration * 100.0 if total_duration else 0.0
            detail = _interval_detail(rows, counterfactual, text_catalog=text_catalog)
            values = (
                first.source_character_name,
                ui_enum_text(text_catalog, _SCOPE_LABELS).get(first.target_scope, first.target_scope),
                property_text,
                value_text,
                " / ".join(dict.fromkeys(
                    _trigger_label(row.trigger_event_type, text_catalog=text_catalog)
                    for row in rows
                )),
                f"{duration:.3f}s / {total_duration:.3f}s = {coverage_percent:.1f}%",
                "—" if counterfactual is None else f"{counterfactual.affected_hits:,}",
                "—" if counterfactual is None else damage_coverage_text(counterfactual.damage_coverage, text_catalog=getattr(self, 'text_catalog', None)),
                _gain_percent(counterfactual),
                ui_enum_text(text_catalog, "查看"),
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
            self._detail_dialog = _BuffDetailDialog(self, text_catalog=getattr(self, 'text_catalog', None))
        self._detail_dialog.show_detail(detail)
