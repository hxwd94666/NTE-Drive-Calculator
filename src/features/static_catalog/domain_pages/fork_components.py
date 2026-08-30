# 提供弧盘图鉴内部复用的文本与角色卡组件。
"""Small UI helpers owned exclusively by the fork catalog page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QLayout,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.services.static_catalog_character_models import CharacterSummary
from src.services.static_catalog_fork_release_metadata import ForkItemDisplayNameService
from src.services.static_catalog_fork_service import (
    ForkBreakthrough,
    ForkBuffDefinition,
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
    "add_effect_tiles",
    "breakthrough_cost_text",
    "clear_layout",
    "display_number",
    "effect_tile",
    "plain_text",
    "present_effects",
    "refinement_skill_text",
]
