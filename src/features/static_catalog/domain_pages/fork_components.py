# 提供弧盘图鉴内部复用的文本与角色卡组件。
"""Small UI helpers owned exclusively by the fork catalog page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QComboBox,
    QGridLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.progression_stamina import ProgressionStaminaResult, StaminaPlanStatus
from src.services.advancement_stage_service import (
    fork_breakthrough_choices,
    select_fork_breakthrough,
)
from src.services.static_catalog_character_models import CharacterSummary
from src.services.static_catalog_fork_release_metadata import (
    ForkItemDisplayNameService,
    ForkProgressionState,
)
from src.services.static_catalog_fork_service import (
    ForkBreakthrough,
    ForkBuffDefinition,
    ForkCatalogDetail,
    ForkCost,
    ForkRefinementLevel,
)


_MARKUP = re.compile(r"<[^>]+>")
_EVENT_LABELS = {
    "BUFF_EVENT_BEGIN": "效果开始",
    "BUFF_EVENT_BESKILL_HIT_BEFORE_CALC": "受到技能命中前",
    "BUFF_EVENT_CHANGE_ROLE_INT": "切换入场",
    "BUFF_EVENT_CHANGE_ROLE_OUT": "切换离场",
    "BUFF_EVENT_CHANGE_ROLE_OUT_BEGIN": "开始离场",
    "BUFF_EVENT_CRIT": "造成暴击",
    "BUFF_EVENT_E_SKILL_BEGIN": "释放战技",
    "BUFF_EVENT_FINISH": "效果结束",
    "BUFF_EVENT_INCREASE_UNBAL": "造成倾陷积累",
    "BUFF_EVENT_KILL": "击败目标",
    "BUFF_EVENT_KILL_ALL_PLAYER": "队伍击败目标",
    "BUFF_EVENT_PERFECT_EVADE": "完美闪避",
    "BUFF_EVENT_QTE_BEGIN": "发动支援技",
    "BUFF_EVENT_QTE_BEGIN_ALL_PLAYER": "队伍发动支援技",
    "BUFF_EVENT_Q_SKILL_BEGIN": "释放终结技",
    "BUFF_EVENT_SHIELDHEALTHSTART": "获得护盾",
    "BUFF_EVENT_SKILL_AFTER_HIT": "技能命中后",
    "BUFF_EVENT_SKILL_HIT_BEFORE_CALC": "技能命中前",
    "BUFF_EVENT_SKILL_REALFINISH": "技能释放结束",
    "BUFF_EVENT_SKILL_REALFINISH_ALL_PLAYER": "队伍技能释放结束",
    "BUFF_EVENT_TREATMENT": "进行治疗",
}


@dataclass(frozen=True, slots=True)
class ForkEffectPresentation:
    timing: str
    result: str
    stacking: str | None
    condition: str | None


def plain_text(value: str | None) -> str:
    return " ".join(_MARKUP.sub("", value or "").replace("</>", "").split())


def refinement_skill_text(refinement: ForkRefinementLevel) -> str:
    """Substitute formal refinement values without exposing parameter keys."""

    text = plain_text(refinement.description_zh)
    for index, parameter in enumerate(refinement.parameters):
        text = text.replace(f"{{{index}}}", parameter.display_value)
    return text or "暂无技能说明"


def present_effects(
    buffs: tuple[ForkBuffDefinition, ...],
) -> ForkEffectPresentation | None:
    """Project imported effect structure into player-facing Chinese text."""

    if not buffs:
        return None
    applied: list[str] = []
    removed: list[str] = []
    properties: list[str] = []
    stack_limits: list[int] = []
    has_condition = False
    for buff in buffs:
        if buff.stack_limit_count is not None and buff.stack_limit_count > 0:
            stack_limits.append(buff.stack_limit_count)
        for modifier in buff.modifiers:
            name = modifier.property_name_zh or modifier.property_id
            if name and name not in properties:
                properties.append(name)
            has_condition = has_condition or bool(
                modifier.gameplay_tags
                or modifier.application_requirement_asset_path
            )
        for trigger in buff.triggers:
            event_key = str(trigger.event_type or "").rsplit("::", 1)[-1]
            event = _EVENT_LABELS.get(event_key)
            if not event:
                continue
            effect_key = str(trigger.effect_type or "").rsplit("::", 1)[-1]
            target = removed if effect_key == "BUFF_REMOVE" else applied
            if event not in target:
                target.append(event)
            has_condition = has_condition or bool(
                trigger.application_requirement_asset_path
            )
    timing_parts = []
    if applied:
        timing_parts.append(f"{'、'.join(applied)}时生效")
    if removed:
        timing_parts.append(f"{'、'.join(removed)}时结束")
    result = (
        f"影响{'、'.join(properties)}，具体数值以当前混频技能说明为准"
        if properties else "效果内容以当前混频技能说明为准"
    )
    stacking = f"最多叠加 {max(stack_limits)} 层" if stack_limits else None
    condition = "仅在技能说明所述的攻击或状态下生效" if has_condition else None
    return ForkEffectPresentation(
        timing="；".join(timing_parts) or "随技能说明所述条件生效",
        result=result,
        stacking=stacking,
        condition=condition,
    )


def effect_tile(title: str, text: str) -> QFrame:
    card = QFrame()
    card.setStyleSheet(themed_style(
        "background:#10243f;border:1px solid #30363d;border-radius:10px"
    ))
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 9, 12, 9)
    caption = QLabel(title, card)
    caption.setStyleSheet(themed_style(
        "color:#58a6ff;background:transparent;border:none;"
        "font-size:10px;font-weight:900"
    ))
    content = QLabel(text, card)
    content.setWordWrap(True)
    content.setStyleSheet(themed_style(
        "color:#c9d1d9;background:transparent;border:none;font-size:12px"
    ))
    layout.addWidget(caption)
    layout.addWidget(content)
    return card


def add_effect_tiles(
    layout: QLayout,
    presentation: ForkEffectPresentation,
) -> None:
    rows = [
        ("生效时机", presentation.timing),
        ("效果内容", presentation.result),
    ]
    if presentation.stacking:
        rows.append(("叠加规则", presentation.stacking))
    if presentation.condition:
        rows.append(("生效条件", presentation.condition))
    for title, text in rows:
        layout.addWidget(effect_tile(title, text))


def display_number(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            clear_layout(child)


def breakthrough_cost_text(
    stage: ForkBreakthrough | None,
    item_names: ForkItemDisplayNameService,
) -> str:
    if stage is None:
        return "突破消耗 · 当前正式数据未提供"
    costs = (
        item_names.present_costs(stage.item_costs),
        item_names.present_costs(stage.gold_costs),
    )
    text = item_names.player_text((*costs[0], *costs[1]))
    return f"消耗：{text or '暂未提供'}"


@dataclass(frozen=True, slots=True)
class ForkProgressionResultProjection:
    text: str
    available: bool


def project_fork_progression_result(
    result: ProgressionStaminaResult,
    item_names: ForkItemDisplayNameService,
) -> ForkProgressionResultProjection:
    """Format the shared result without running or reproducing its algorithm."""

    status_labels = {
        StaminaPlanStatus.COMPLETE: "规划完成",
        StaminaPlanStatus.PARTIAL: "部分可用",
        StaminaPlanStatus.UNAVAILABLE: "暂不可用",
    }
    lines = [f"规划状态：{status_labels[result.status]}"]
    if result.total_stamina is not None:
        lines.append(f"总活力：{result.total_stamina}")
    elif result.known_stamina:
        lines.append(f"已知活力：{result.known_stamina}；完整总活力暂不可用")
    else:
        lines.append("总活力暂不可用")
    deficits = tuple(item for item in result.deficits if item.deficit_quantity > 0)
    if deficits:
        costs = tuple(ForkCost(
            item_id=item.item_id,
            amount=item.deficit_quantity,
            raw_value=str(item.deficit_quantity),
        ) for item in deficits)
        presented = item_names.present_costs(costs)
        lines.append("材料缺口：" + item_names.player_text(presented))
    if result.unresolved_item_ids:
        unresolved = item_names.present_costs(tuple(ForkCost(
            item_id=item_id,
            amount=None,
            raw_value="数量暂未提供",
        ) for item_id in result.unresolved_item_ids))
        lines.append(
            "缺少正式产出：" + "、".join(
                item.display_name for item in unresolved
            )
        )
    if result.status != StaminaPlanStatus.COMPLETE:
        lines.append("未知产出或数量保持为缺口，不按 0 处理。")
    return ForkProgressionResultProjection(
        text="\n".join(lines),
        available=result.status == StaminaPlanStatus.COMPLETE,
    )


class ForkProgressionControls(QFrame):
    """Compact current-to-target input; owns no farming or stamina algorithm."""

    target_changed = Signal(int, object, int)
    request_clicked = Signal()

    def __init__(
        self,
        *,
        item_names: ForkItemDisplayNameService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("forkProgressionControls")
        self._detail: ForkCatalogDetail | None = None
        self._item_names = item_names
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for column, text in enumerate(("", "等级", "突破状态", "混频")):
            label = QLabel(text, self)
            label.setStyleSheet(themed_style(
                "color:#8b949e;font-size:10px;font-weight:700"
            ))
            grid.addWidget(label, 0, column)
        self.current_level = self._level_input("forkCurrentLevel")
        self.current_stage = self._stage_input("forkCurrentStage")
        self.current_mixing = self._mixing_input("forkCurrentMixing")
        self.target_level = self._level_input("forkTargetLevel")
        self.target_stage = self._stage_input("forkTargetStage")
        self.target_mixing = self._mixing_input("forkTargetMixing")
        for row, (title, level, stage, mixing) in enumerate((
            ("当前", self.current_level, self.current_stage, self.current_mixing),
            ("目标", self.target_level, self.target_stage, self.target_mixing),
        ), start=1):
            title_label = QLabel(title, self)
            title_label.setStyleSheet(themed_style(
                "color:#f0f6fc;font-size:11px;font-weight:900"
            ))
            grid.addWidget(title_label, row, 0)
            grid.addWidget(level, row, 1)
            grid.addWidget(stage, row, 2)
            grid.addWidget(mixing, row, 3)
        grid.setColumnStretch(2, 1)
        root.addLayout(grid)
        self.validation = QLabel("", self)
        self.validation.setObjectName("forkProgressionValidation")
        self.validation.setWordWrap(True)
        root.addWidget(self.validation)
        self.request = QPushButton("计算材料缺口与活力", self)
        self.request.setObjectName("btnAction")
        self.request.clicked.connect(
            lambda _checked=False: self.request_clicked.emit(),
        )
        root.addWidget(self.request, 0, Qt.AlignmentFlag.AlignLeft)
        self.result = QLabel("", self)
        self.result.setObjectName("forkProgressionResult")
        self.result.setWordWrap(True)
        self.result.setVisible(False)
        root.addWidget(self.result)

        self.current_level.valueChanged.connect(
            lambda _value: self._current_changed(),
        )
        self.current_stage.currentIndexChanged.connect(
            lambda _index: self._selection_changed(),
        )
        self.current_mixing.valueChanged.connect(
            lambda _value: self._selection_changed(),
        )
        self.target_level.valueChanged.connect(
            lambda _value: self._target_level_changed(),
        )
        self.target_stage.currentIndexChanged.connect(
            lambda _index: self._target_changed(),
        )
        self.target_mixing.valueChanged.connect(
            lambda _value: self._target_changed(),
        )

    def set_detail(self, detail: ForkCatalogDetail) -> None:
        self._detail = detail
        self._set_spin(self.current_level, 1)
        self._set_spin(self.current_mixing, 1)
        self._set_spin(self.target_level, 80)
        self._set_spin(self.target_mixing, 1)
        self._refresh_stage(self.current_stage, 1, 0)
        self._refresh_stage(self.target_stage, 80, 6)
        self._validate()
        self._clear_result()

    def set_target_state(
        self,
        level: int,
        breakthrough_stage: int | None,
        mixing_level: int,
    ) -> None:
        self._set_spin(self.target_level, level)
        self._set_spin(self.target_mixing, mixing_level)
        self._refresh_stage(self.target_stage, level, breakthrough_stage)
        self._validate()

    def states(self) -> tuple[ForkProgressionState, ForkProgressionState]:
        return (
            ForkProgressionState(
                level=self.current_level.value(),
                breakthrough_stage=self.current_stage.currentData(),
                mixing_level=self.current_mixing.value(),
            ),
            ForkProgressionState(
                level=self.target_level.value(),
                breakthrough_stage=self.target_stage.currentData(),
                mixing_level=self.target_mixing.value(),
            ),
        )

    def set_progression_result(self, result: ProgressionStaminaResult) -> None:
        projection = project_fork_progression_result(result, self._item_names)
        color = "#3fb950" if projection.available else "#f2cc60"
        self.result.setText(projection.text)
        self.result.setStyleSheet(themed_style(
            f"color:{color};background:#0d1117;border:1px solid {color};"
            "border-radius:8px;padding:9px;font-weight:700"
        ))
        self.result.setVisible(True)

    def _current_changed(self) -> None:
        self._refresh_stage(
            self.current_stage,
            self.current_level.value(),
            self.current_stage.currentData(),
        )
        self._selection_changed()

    def _target_level_changed(self) -> None:
        self._refresh_stage(
            self.target_stage,
            self.target_level.value(),
            self.target_stage.currentData(),
        )
        self._target_changed()

    def _selection_changed(self) -> None:
        self._validate()
        self._clear_result()

    def _target_changed(self) -> None:
        self._selection_changed()
        target = self.states()[1]
        self.target_changed.emit(
            target.level,
            target.breakthrough_stage,
            target.mixing_level,
        )

    def _refresh_stage(
        self,
        combo: QComboBox,
        level: int,
        preferred_stage: int | None,
    ) -> None:
        detail = self._detail
        if detail is None:
            return
        rows = [{
            "stage": row.stage,
            "max_fork_level": row.max_fork_level,
        } for row in detail.breakthroughs]
        choices = fork_breakthrough_choices(rows, int(level))
        selected = select_fork_breakthrough(
            rows,
            int(level),
            preferred_stage=preferred_stage,
        )
        combo.blockSignals(True)
        combo.clear()
        for index, row in enumerate(choices):
            stage = int(row["stage"])
            if len(choices) == 2:
                label = "突破前" if index == 0 else "突破后"
            else:
                label = f"阶段 {stage}"
            combo.addItem(label, stage)
        if selected is not None:
            selected_stage = int(selected["stage"])
            combo.setCurrentIndex(max(0, combo.findData(selected_stage)))
        combo.blockSignals(False)

    def _validate(self) -> None:
        current, target = self.states()
        valid = (
            (target.level, target.breakthrough_stage or 0)
            >= (current.level, current.breakthrough_stage or 0)
            and target.mixing_level >= current.mixing_level
        )
        self.request.setEnabled(valid)
        self.validation.setText(
            "正式材料按所跨越的突破与混频节点汇总；未知数量保留为缺口。"
            if valid else "目标状态不能低于当前状态。"
        )
        self.validation.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px" if valid
            else "color:#f85149;font-size:10px;font-weight:700"
        ))

    def _clear_result(self) -> None:
        self.result.setVisible(False)

    @staticmethod
    def _set_spin(widget: QSpinBox, value: int) -> None:
        widget.blockSignals(True)
        widget.setValue(int(value))
        widget.blockSignals(False)

    @staticmethod
    def _level_input(name: str) -> QSpinBox:
        control = QSpinBox()
        control.setObjectName(name)
        control.setRange(1, 80)
        control.setPrefix("Lv.")
        return control

    @staticmethod
    def _mixing_input(name: str) -> QSpinBox:
        control = QSpinBox()
        control.setObjectName(name)
        control.setRange(1, 5)
        control.setPrefix("混频 ")
        return control

    @staticmethod
    def _stage_input(name: str) -> QComboBox:
        control = QComboBox()
        control.setObjectName(name)
        control.setMinimumWidth(104)
        return control


class ForkCharacterCard(QFrame):
    def __init__(
        self,
        character: CharacterSummary,
        *,
        relation_label: str,
        art_path: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.character_id = character.character_id
        self.setObjectName(f"forkCompatibleCharacter_{character.character_id}")
        self.setMinimumSize(168, 68)
        self.setStyleSheet(themed_style(
            "QFrame{background:#161b22;border:1px solid #30363d;border-radius:10px;}"
            "QLabel{border:none;background:transparent;}"
        ))
        layout = QGridLayout(self)
        layout.setContentsMargins(7, 6, 9, 6)
        layout.setHorizontalSpacing(8)
        image = QLabel(self)
        image.setFixedSize(48, 48)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if art_path is not None:
            pixmap = QPixmap(str(art_path))
            if not pixmap.isNull():
                image.setPixmap(pixmap.scaled(
                    46, 46,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        layout.addWidget(image, 0, 0, 2, 1)
        name = QLabel(character.name_zh, self)
        name.setStyleSheet(themed_style("color:#f0f6fc;font-weight:800;"))
        layout.addWidget(name, 0, 1)
        relation = QLabel(relation_label, self)
        relation.setStyleSheet(themed_style("color:#8b949e;font-size:10px;"))
        layout.addWidget(relation, 1, 1)


__all__ = [
    "ForkEffectPresentation",
    "ForkCharacterCard",
    "ForkProgressionControls",
    "ForkProgressionResultProjection",
    "add_effect_tiles",
    "breakthrough_cost_text",
    "clear_layout",
    "display_number",
    "effect_tile",
    "plain_text",
    "present_effects",
    "project_fork_progression_result",
    "refinement_skill_text",
]
