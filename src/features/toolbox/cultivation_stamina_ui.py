# 提供养成计算器的猎人等级输入与体力结果展示。
"""Stamina controls and compact result formatting for cultivation planning."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.progression_stamina import (
    ProgressionStaminaResult,
    project_identification_level,
)
from src.features.toolbox.cultivation_controls import disable_numeric_input_method


class CultivationStaminaControls(QFrame):
    """Collect the account-independent level boundary used for farming stages."""

    values_changed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cultivationStaminaControls")
        self.setStyleSheet(themed_style(
            "QFrame#cultivationStaminaControls{background:#161b22;"
            "border:1px solid #30363d;border-radius:9px;}"
        ))
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(9)
        title = QLabel("体力计算", self)
        title.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:14px;font-weight:800"
        ))
        row.addWidget(title)
        row.addWidget(QLabel("猎人等级", self))
        self.hunter_level = QSpinBox(self)
        disable_numeric_input_method(self.hunter_level)
        self.hunter_level.setObjectName("cultivationHunterLevel")
        self.hunter_level.setRange(1, 60)
        self.hunter_level.setValue(60)
        row.addWidget(self.hunter_level)
        row.addWidget(QLabel("生效鉴别等级", self))
        self.identification_level = QComboBox(self)
        self.identification_level.setObjectName("cultivationIdentificationLevel")
        row.addWidget(self.identification_level)
        hint = QLabel("按正式副本确定产出计算；刷怪/Boss 掉落不计副本体力", self)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        row.addWidget(hint, 1)
        self.hunter_level.valueChanged.connect(self._refresh_identification)
        self.identification_level.currentIndexChanged.connect(
            lambda _index: self.values_changed.emit()
        )
        self._refresh_identification()

    def values(self) -> tuple[int, int]:
        return self.hunter_level.value(), int(self.identification_level.currentData())

    def _refresh_identification(self) -> None:
        previous = self.identification_level.currentData()
        projection = project_identification_level(self.hunter_level.value())
        levels = [projection.native_level]
        if projection.native_level >= 3:
            levels.append(projection.native_level - 1)
        self.identification_level.blockSignals(True)
        self.identification_level.clear()
        for level in levels:
            suffix = "（当前）" if level == projection.native_level else "（下调）"
            self.identification_level.addItem(f"鉴别 {level} {suffix}", level)
        selected = previous if previous in levels else projection.native_level
        self.identification_level.setCurrentIndex(levels.index(selected))
        self.identification_level.blockSignals(False)
        self.values_changed.emit()


def stamina_summary_text(result: ProgressionStaminaResult | None) -> str:
    if result is None:
        return "体力数据暂不可用"
    if result.total_stamina is not None:
        return f"最低体力 {result.total_stamina:,}"
    if result.known_stamina:
        return f"已知体力 {result.known_stamina:,} · 部分材料不可计算"
    return "体力暂不可计算"


def stamina_runs_text(result: ProgressionStaminaResult | None) -> str:
    if result is None:
        return ""
    if result.runs:
        return "副本建议：" + "；".join(
            f"{run.label} × {run.runs} 次（{run.total_stamina:,} 体力）"
            for run in result.runs
        )
    if result.total_stamina == 0:
        return "该项无需统计材料副本体力"
    if result.unresolved_item_ids:
        return "缺少确定产出的材料：" + "、".join(result.unresolved_item_ids)
    return ""


def style_stamina_badge(label: QLabel) -> None:
    label.setObjectName("cultivationStaminaBadge")
    label.setStyleSheet(themed_style(
        "QLabel#cultivationStaminaBadge{color:#3fb950;background:#0d1117;"
        "border:1px solid #238636;border-radius:6px;padding:4px 8px;font-weight:800;}"
    ))


__all__ = [
    "CultivationStaminaControls",
    "stamina_runs_text",
    "stamina_summary_text",
    "style_stamina_badge",
]
