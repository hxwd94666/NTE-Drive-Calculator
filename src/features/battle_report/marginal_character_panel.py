# 展示战报冻结静态面板与按公式关联伤害加权的逐击动态面板。
"""Wide character-panel comparison for fixed-axis marginal analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.app.theme import themed_style
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
)
from src.features.battle_report.marginal_result_table_view import (
    render_attribute_results,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from src.services.battle_marginal_calculation_support import (
    drive_substat_marginal_units,
)


@dataclass(frozen=True, slots=True)
class _PanelProperty:
    property_id: str
    label: str
    is_percent: bool = False


_ELEMENT_PROPERTIES = (
    _PanelProperty("DamageUpChaosBase", "暗属性伤害", True),
    _PanelProperty("DamageUpCosmosBase", "光属性伤害", True),
    _PanelProperty("DamageUpIncantationBase", "咒属性伤害", True),
    _PanelProperty("DamageUpLakshanaBase", "相属性伤害", True),
    _PanelProperty("DamageUpNatureBase", "灵属性伤害", True),
    _PanelProperty("DamageUpPsycheBase", "魂属性伤害", True),
    _PanelProperty("DamageUpPsychicallyBase", "心灵伤害", True),
)
_ELEMENT_PROPERTY_BY_ATTRIBUTE = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
_PENETRATION_PROPERTIES = (
    _PanelProperty("DamagePenetrateChaos", "暗属性穿透", True),
    _PanelProperty("DamagePenetrateCosmos", "光属性穿透", True),
    _PanelProperty("DamagePenetrateIncantation", "咒属性穿透", True),
    _PanelProperty("DamagePenetrateLakshana", "相属性穿透", True),
    _PanelProperty("DamagePenetrateNature", "灵属性穿透", True),
    _PanelProperty("DamagePenetratePsyche", "魂属性穿透", True),
    _PanelProperty("DamagePenetratePsychically", "心灵穿透", True),
)
_PANEL_PROPERTIES = (
    _PanelProperty("CritBase", "暴击率", True),
    _PanelProperty("CritDamageBase", "暴击伤害", True),
    _PanelProperty("DamageUpGeneralBase", "通用伤害增强", True),
    *_ELEMENT_PROPERTIES,
    _PanelProperty("MagBase", "环合强度"),
    _PanelProperty("AtkBase", "基础攻击力"),
    _PanelProperty("AtkAdd", "额外攻击力"),
    _PanelProperty("AtkUp", "攻击力%", True),
    _PanelProperty("PanelAtk", "总攻击力"),
    _PanelProperty("HPMaxBase", "基础生命值"),
    _PanelProperty("HPMaxAdd", "额外生命值"),
    _PanelProperty("HPMaxUp", "生命值%", True),
    _PanelProperty("PanelHP", "总生命值"),
    _PanelProperty("DefBase", "基础防御力"),
    _PanelProperty("DefAdd", "额外防御力"),
    _PanelProperty("DefUp", "防御力%", True),
    _PanelProperty("PanelDef", "总防御力"),
    _PanelProperty("DefIgnore", "防御忽略", True),
    *_PENETRATION_PROPERTIES,
    _PanelProperty("UnbalIntensityBase", "倾陷强度"),
    _PanelProperty("UnbalIntensityUp", "倾陷强度%", True),
    _PanelProperty("UnbalIntensityAdd", "额外倾陷强度"),
    _PanelProperty("UnbalDamageUp", "倾陷伤害增强", True),
    _PanelProperty("HealUp", "治疗加成", True),
    _PanelProperty("HealBeUp", "受治疗加成", True),
    _PanelProperty("ShieldUp", "护盾加成", True),
    _PanelProperty("ChargeGetEfficiencyBase", "充能效率", True),
    _PanelProperty("UltraEnergyAdd", "额外终结技能量"),
)
_DERIVED_PROPERTIES = frozenset({"PanelAtk", "PanelHP", "PanelDef"})
_DYNAMIC_PROPERTIES = frozenset({
    "CritBase", "CritDamageBase", "DamageUpGeneralBase", "MagBase",
    "AtkUp", "AtkAdd", "HPMaxUp", "HPMaxAdd", "DefUp", "DefAdd",
    "DefIgnore", "UnbalIntensityBase",
    *(row.property_id for row in _ELEMENT_PROPERTIES),
    *(row.property_id for row in _PENETRATION_PROPERTIES),
})


def character_panel_marginal_units(
    drive_units: Mapping[str, float],
) -> dict[str, float]:
    """Add zero-delta rows needed only to project the current dynamic panel."""

    return {
        **{str(key): float(value) for key, value in drive_units.items()},
        **{
            property_id: 0.0
            for property_id in _DYNAMIC_PROPERTIES
            if property_id not in drive_units
        },
    }


def render_character_panel_and_margins(
    panel: "BattleMarginalCharacterPanel",
    attribute_table: QTableWidget,
    *,
    analysis: BattleAnalysisSnapshot | None,
    baseline: BattleCharacterBaseline | None,
    scoring_engine: object | None,
) -> None:
    """Render both views from one shared fixed-axis marginal calculation."""

    if baseline is None or analysis is None:
        panel.clear()
        attribute_table.setRowCount(0)
        return
    stat_catalog = getattr(scoring_engine, "stat_catalog", None)
    drive_units = drive_substat_marginal_units(
        getattr(stat_catalog, "gold_base_values", None),
    )
    results = BattleMarginalCalculationService.calculate(
        analysis=analysis,
        character_id=baseline.character_id,
        edited_values={},
        units=character_panel_marginal_units(drive_units),
    )
    panel.render(
        baseline,
        results,
        current_element_property=_current_element_property(analysis, baseline),
    )
    render_attribute_results(
        attribute_table,
        tuple(row for row in results if row.property_id in drive_units),
    )


def _current_element_property(
    analysis: BattleAnalysisSnapshot,
    baseline: BattleCharacterBaseline,
) -> str | None:
    """Infer the role element from its own non-reaction, non-weave damage."""

    replays = {row.event_id: row for row in analysis.hit_replays}
    damage_by_property: dict[str, float] = {}
    for hit in analysis.hits:
        classification = str(hit.classification or "").casefold()
        if (
            hit.direction != "outgoing"
            or hit.character_id != baseline.character_id
            or classification == "weave"
            or classification.startswith("reaction")
        ):
            continue
        replay = replays.get(hit.event_id)
        attribute = str(
            getattr(replay, "formula_damage_attribute", "")
            or hit.damage_attribute
            or ""
        ).casefold()
        property_id = _ELEMENT_PROPERTY_BY_ATTRIBUTE.get(attribute)
        if property_id is not None:
            damage_by_property[property_id] = (
                damage_by_property.get(property_id, 0.0)
                + max(0.0, float(hit.damage))
            )
    if not damage_by_property:
        return None
    return max(damage_by_property, key=damage_by_property.__getitem__)


def _format_value(value: float | None, *, percent: bool) -> str:
    if value is None:
        return "—"
    if percent:
        return f"{float(value) * 100:.2f}%"
    return f"{float(value):,.2f}"


def _derived_value(
    values: Mapping[str, float | None],
    base_id: str,
    up_id: str,
    add_id: str,
) -> float | None:
    base = values.get(base_id)
    up = values.get(up_id)
    addition = values.get(add_id)
    if base is None or up is None or addition is None:
        return None
    return float(base) * (1.0 + float(up)) + float(addition)


class BattleMarginalCharacterPanel(QFrame):
    """Render two wide rows without inventing unsupported dynamic values."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        title = QLabel("人物面板")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        note = QLabel(
            "静态面板来自本场冻结角色快照；动态面板按该属性实际关联的公式面板伤害，"
            "使用伤害发生时的 Buff 后属性加权。无公式关联或动态证据不足时显示“—”。"
        )
        note.setObjectName("battleMarginalCharacterPanelNote")
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 0)
        self.table.setObjectName("battleMarginalCharacterPanelTable")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.setMinimumHeight(132)
        self.table.setMaximumHeight(132)
        layout.addWidget(self.table)

    def clear(self) -> None:
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)

    def render(
        self,
        baseline: BattleCharacterBaseline,
        results: Sequence[object],
        *,
        current_element_property: str | None = None,
    ) -> None:
        static = {row.property_id: float(row.value) for row in baseline.stats}
        result_by_property = {
            str(row.property_id): row for row in results
            if str(getattr(row, "property_id", ""))
        }
        dynamic: dict[str, float | None] = {
            property_id: getattr(
                result_by_property.get(property_id),
                "weighted_effective_value",
                None,
            )
            for property_id in _DYNAMIC_PROPERTIES
        }
        for base_id, up_id, add_id, total_id in (
            ("AtkBase", "AtkUp", "AtkAdd", "PanelAtk"),
            ("HPMaxBase", "HPMaxUp", "HPMaxAdd", "PanelHP"),
            ("DefBase", "DefUp", "DefAdd", "PanelDef"),
        ):
            if dynamic.get(up_id) is not None or dynamic.get(add_id) is not None:
                dynamic[base_id] = static.get(base_id, 0.0)
            dynamic[total_id] = _derived_value(dynamic, base_id, up_id, add_id)
        properties = self._properties(baseline)
        self.table.clear()
        self.table.setRowCount(2)
        self.table.setColumnCount(len(properties) + 1)
        self.table.setHorizontalHeaderLabels(("属性", *tuple(
            f"{row.label}（本系）"
            if row.property_id == current_element_property else row.label
            for row in properties
        )))
        self.table.setColumnWidth(0, 100)
        for row_index, label in enumerate(("静态面板", "动态面板")):
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_index, 0, item)
        for column, row in enumerate(properties, start=1):
            static_value = static.get(row.property_id, 0.0)
            if row.property_id in _DERIVED_PROPERTIES and row.property_id not in static:
                prefixes = {
                    "PanelAtk": ("AtkBase", "AtkUp", "AtkAdd"),
                    "PanelHP": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
                    "PanelDef": ("DefBase", "DefUp", "DefAdd"),
                }[row.property_id]
                static_value = _derived_value(static, *prefixes) or 0.0
            for row_index, value in enumerate((
                static_value,
                dynamic.get(row.property_id),
            )):
                item = QTableWidgetItem(
                    _format_value(value, percent=row.is_percent)
                )
                item.setTextAlignment(Qt.AlignCenter)
                if row_index == 1 and value is None:
                    item.setToolTip("该属性没有可安全归属的公式面板伤害或动态投影。")
                self.table.setItem(row_index, column, item)
            self.table.setColumnWidth(column, max(112, len(row.label) * 18 + 24))

    @staticmethod
    def _properties(baseline: BattleCharacterBaseline) -> tuple[_PanelProperty, ...]:
        known = {row.property_id for row in _PANEL_PROPERTIES}
        extras = tuple(
            _PanelProperty(row.property_id, row.label, bool(row.is_percent))
            for row in baseline.stats
            if row.property_id not in known
        )
        return tuple((*_PANEL_PROPERTIES, *extras))


__all__ = [
    "BattleMarginalCharacterPanel",
    "character_panel_marginal_units",
    "render_character_panel_and_margins",
]
