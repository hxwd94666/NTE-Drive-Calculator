# 角色图鉴的等级面板、突破里程碑与公共养成计算接线位。
"""Game-styled level progression view without local stamina algorithms."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.services.static_catalog_character_models import (
    BreakthroughStage,
    CharacterDetail,
    GrowthPage,
    GrowthPoint,
)


def _number(value: float) -> str:
    return f"{value:,.1f}".rstrip("0").rstrip(".")


class CharacterGrowthView(QWidget):
    progression_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._detail: CharacterDetail | None = None
        self._points: tuple[GrowthPoint, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        calculator = QFrame(self)
        calculator.setObjectName("characterLevelCalculator")
        calculator.setStyleSheet(themed_style(
            "QFrame#characterLevelCalculator{background:#161b22;"
            "border:1px solid #30363d;border-radius:14px;}"
        ))
        layout = QVBoxLayout(calculator)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("等级规划", calculator)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:17px;font-weight:900"
        ))
        hint = QLabel(
            "选择等级与突破状态。面板由正式曲线即时预览；材料、缺口、副本次数和活力交给公共养成服务。",
            calculator,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        layout.addWidget(title)
        layout.addWidget(hint)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("角色等级", calculator))
        self.start_level = QComboBox(calculator)
        self.end_level = QComboBox(calculator)
        for level in range(1, 71):
            self.start_level.addItem(f"Lv.{level}", level)
            self.end_level.addItem(f"Lv.{level}", level)
        self.start_level.setCurrentIndex(4)
        self.end_level.setCurrentIndex(69)
        self.include_breakthroughs = QCheckBox("包含沿途突破", calculator)
        self.include_breakthroughs.setChecked(False)
        controls.addWidget(self.start_level)
        controls.addWidget(QLabel("→", calculator))
        controls.addWidget(self.end_level)
        controls.addWidget(self.include_breakthroughs)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.panel_preview = QFrame(calculator)
        self.panel_preview.setObjectName("characterPanelPreview")
        self.panel_preview.setStyleSheet(themed_style(
            "QFrame#characterPanelPreview{background:#10243f;"
            "border:1px solid #1f6feb;border-radius:12px;}"
        ))
        preview = QHBoxLayout(self.panel_preview)
        preview.setContentsMargins(14, 10, 14, 10)
        self.preview_level = self._metric("目标面板", "—")
        self.preview_hp = self._metric("生命", "—")
        self.preview_atk = self._metric("攻击", "—")
        self.preview_def = self._metric("防御", "—")
        for widget in (
            self.preview_level, self.preview_hp, self.preview_atk, self.preview_def,
        ):
            preview.addWidget(widget, 1)
        layout.addWidget(self.panel_preview)

        action_row = QHBoxLayout()
        request = QPushButton("计算材料缺口与活力", calculator)
        request.setObjectName("btnAction")
        request.clicked.connect(self._request_progression)
        action_row.addWidget(request)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self.progression_result = QLabel(
            "当前正式数据未提供角色升级/突破消耗；公共 ProgressionStaminaService 尚未接入。",
            calculator,
        )
        self.progression_result.setObjectName("characterProgressionResult")
        self.progression_result.setWordWrap(True)
        self.progression_result.setStyleSheet(themed_style(
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:9px"
        ))
        layout.addWidget(self.progression_result)
        root.addWidget(calculator)

        section_title = QLabel("突破里程碑", self)
        section_title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        root.addWidget(section_title)
        self.milestone_grid = QGridLayout()
        self.milestone_grid.setHorizontalSpacing(10)
        self.milestone_grid.setVerticalSpacing(10)
        root.addLayout(self.milestone_grid)
        root.addStretch(1)
        self.start_level.currentIndexChanged.connect(self._refresh_preview)
        self.end_level.currentIndexChanged.connect(self._refresh_preview)
        self.include_breakthroughs.toggled.connect(self._refresh_preview)

    @staticmethod
    def _metric(title: str, value: str) -> QFrame:
        card = QFrame()
        content = QVBoxLayout(card)
        content.setContentsMargins(6, 4, 6, 4)
        caption = QLabel(title, card)
        caption.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        metric = QLabel(value, card)
        metric.setObjectName("metricValue")
        metric.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        content.addWidget(caption)
        content.addWidget(metric)
        return card

    def set_data(self, detail: CharacterDetail, growth: GrowthPage) -> None:
        if detail.character.character_id != growth.character_id:
            return
        self._detail = detail
        self._points = growth.items
        self._clear_grid()
        for index, stage in enumerate(detail.breakthroughs):
            self.milestone_grid.addWidget(
                self._milestone(stage), index // 3, index % 3,
            )
        for column in range(3):
            self.milestone_grid.setColumnStretch(column, 1)
        self._refresh_preview()

    def _milestone(self, stage: BreakthroughStage) -> QFrame:
        card = QFrame(self)
        card.setProperty("milestoneCard", True)
        card.setStyleSheet(themed_style(
            "QFrame[milestoneCard='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:12px;}"
        ))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel(f"Lv.{stage.level} · 突破 {stage.stage}", card)
        title.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:13px;font-weight:900"
        ))
        delta = QLabel(
            f"生命 +{_number(stage.after.hp_base - stage.before.hp_base)}   "
            f"攻击 +{_number(stage.after.atk_base - stage.before.atk_base)}   "
            f"防御 +{_number(stage.after.def_base - stage.before.def_base)}",
            card,
        )
        delta.setWordWrap(True)
        delta.setStyleSheet(themed_style("color:#c9d1d9;font-size:11px"))
        unavailable = QLabel("材料 · 当前正式数据未提供", card)
        unavailable.setStyleSheet(themed_style("color:#d29922;font-size:10px"))
        layout.addWidget(title)
        layout.addWidget(delta)
        layout.addWidget(unavailable)
        return card

    def _refresh_preview(self) -> None:
        if not self._points:
            self._set_metric(self.preview_level, "当前正式数据未提供")
            for widget in (self.preview_hp, self.preview_atk, self.preview_def):
                self._set_metric(widget, "—")
            return
        target = int(self.end_level.currentData())
        candidates = tuple(point for point in self._points if point.level == target)
        point = self._select_target(candidates)
        if point is None:
            self._set_metric(self.preview_level, f"Lv.{target} · 无正式面板")
            return
        state = "突破后" if point.state == "breakthrough_after" else "突破前"
        if point.state not in {"breakthrough_before", "breakthrough_after"}:
            state = "等级面板"
        self._set_metric(self.preview_level, f"Lv.{target} · {state}")
        self._set_metric(self.preview_hp, _number(point.hp_base))
        self._set_metric(self.preview_atk, _number(point.atk_base))
        self._set_metric(self.preview_def, _number(point.def_base))

    def _select_target(self, points: tuple[GrowthPoint, ...]) -> GrowthPoint | None:
        if not points:
            return None
        preferred = (
            "breakthrough_after" if self.include_breakthroughs.isChecked()
            else "breakthrough_before"
        )
        return next((point for point in points if point.state == preferred), points[0])

    def _request_progression(self) -> None:
        detail = self._detail
        if detail is None:
            return
        self.progression_requested.emit({
            "kind": "character_level",
            "character_id": detail.character.character_id,
            "from_level": int(self.start_level.currentData()),
            "to_level": int(self.end_level.currentData()),
            "include_breakthroughs": self.include_breakthroughs.isChecked(),
        })

    def set_progression_result(self, text: str, *, available: bool) -> None:
        self.progression_result.setText(text)
        self.progression_result.setStyleSheet(themed_style(
            "color:#3fb950;background:#0d1117;border:1px solid #3fb950;"
            "border-radius:8px;padding:9px"
            if available else
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:9px"
        ))

    @staticmethod
    def _set_metric(card: QFrame, value: str) -> None:
        label = card.findChild(QLabel, "metricValue")
        if label is not None:
            label.setText(value)

    def _clear_grid(self) -> None:
        while self.milestone_grid.count():
            item = self.milestone_grid.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
