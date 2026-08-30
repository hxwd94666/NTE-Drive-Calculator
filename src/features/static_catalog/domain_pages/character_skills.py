# 以官方技能顺序展示整行技能、可编辑等级与当前等级倍率。
"""Dense character skill rows and a separate skill-cultivation view."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.character_progression import (
    MaterialSummaryStatus,
    project_skill_level_requirements,
)
from src.features.static_catalog.domain_pages.character_terminology import (
    project_character_term,
)
from src.services.static_catalog_character_models import (
    CharacterDetail,
    CharacterPassive,
    CharacterSkill,
    CombatLink,
    SkillDamageItem,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_MARKUP = re.compile(r"<[^>]+>")
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_HAN_CHARACTER = re.compile(r"[\u3400-\u9fff]")
_PRIMARY_INPUTS = {
    "Ability.Melee": "A",
    "Ability.Skill": "E",
    "Ability.UltraSkill": "Q",
}
_SLOT_LABELS = {
    "A": "普通攻击",
    "E": "E 技能",
    "Q": "Q 技能",
    "QTE": "QTE 技能",
    "G": "G 技能",
    "PASSIVE": "被动技能",
}


def _plain(value: str | None) -> str:
    text = _MARKUP.sub("", value or "")
    return " ".join(text.replace("</>", "").split())


def _number(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


@dataclass(frozen=True, slots=True)
class CharacterActionCard:
    character_id: int
    slot: str
    title: str
    skill: CharacterSkill | None
    ability_id: str | None
    resource_path: str | None
    available: bool
    reason: str | None = None
    passive: CharacterPassive | None = None


def build_action_cards(
    detail: CharacterDetail,
    combat_links: tuple[CombatLink, ...],
    *,
    terminology: StaticCatalogTerminologyService | None = None,
) -> tuple[CharacterActionCard, ...]:
    """Project only the official A/E/Q/QTE order, plus a formal G action."""

    by_slot = {
        slot: skill
        for skill in detail.skills
        if (slot := _PRIMARY_INPUTS.get(skill.gameplay_tag or "")) is not None
    }
    cards = [
        _skill_card(detail.character.character_id, slot, by_slot[slot], terminology)
        for slot in ("A", "E", "Q")
        if slot in by_slot
    ]
    qte = next(
        (skill for skill in detail.skills if skill.skill_id.endswith("_QTE")),
        None,
    )
    if qte is not None:
        cards.append(_skill_card(
            detail.character.character_id,
            "QTE",
            qte,
            terminology,
        ))
    active_links = tuple(
        link for link in combat_links if link.binding_kind == "active"
    )
    g_link = next((
        link for link in active_links
        if (link.input_id or "").endswith("InputID_GSkill")
    ), None)
    if g_link is not None:
        skill_names = {
            skill.skill_id: skill.name_zh
            for skill in detail.skills if skill.name_zh
        }
        name = _extra_action_name(g_link, skill_names, terminology)
        if name is not None:
            cards.append(CharacterActionCard(
                character_id=detail.character.character_id,
                slot="G",
                title=name,
                skill=None,
                ability_id=g_link.ability_id,
                resource_path=g_link.ability_asset_path,
                available=True,
            ))
    for passive in detail.passives:
        cards.append(CharacterActionCard(
            character_id=detail.character.character_id,
            slot="PASSIVE",
            title=passive.name_zh or project_character_term(
                terminology,
                entity_kind="gameplay_ability",
                stable_id=passive.ability_id,
                identity_label="被动技能",
            ).display_name,
            skill=None,
            ability_id=passive.ability_id,
            resource_path=None,
            available=True,
            passive=passive,
        ))
    return tuple(cards)


def _skill_card(
    character_id: int,
    slot: str,
    skill: CharacterSkill,
    terminology: StaticCatalogTerminologyService | None,
) -> CharacterActionCard:
    return CharacterActionCard(
        character_id=character_id,
        slot=slot,
        title=skill.name_zh or project_character_term(
            terminology,
            entity_kind="gameplay_ability",
            stable_id=skill.skill_id,
            identity_label="技能",
        ).display_name,
        skill=skill,
        ability_id=skill.skill_id,
        resource_path=skill.gameplay_ability_path or skill.icon_path,
        available=True,
    )


def _extra_action_name(
    link: CombatLink,
    skill_names: dict[str, str | None],
    terminology: StaticCatalogTerminologyService | None,
) -> str | None:
    name = link.ability_name_zh or skill_names.get(
        link.ability_id or ""
    ) or project_character_term(
        terminology,
        entity_kind="gameplay_ability",
        stable_id=link.ability_id or "",
        identity_label="技能",
    ).display_name
    return (
        name
        if name != "名称暂未提供" and _HAN_CHARACTER.search(name)
        else None
    )


def _skill_max_level(skill: CharacterSkill) -> int:
    """Nine formal upgrade rows describe the ten base skill levels."""

    if skill.levels:
        return min(10, max(level.level for level in skill.levels) + 1)
    longest_curve = max((
        len(curve)
        for item in skill.damage_items
        for curve in (item.atk_rates, item.def_rates, item.hp_rates)
    ), default=1)
    return min(10, max(1, longest_curve))


def _rate_at(item: SkillDamageItem, level: int) -> tuple[str, float] | None:
    for basis, values in (
        ("攻击", item.atk_rates),
        ("防御", item.def_rates),
        ("生命", item.hp_rates),
    ):
        if values:
            index = min(max(0, level - 1), len(values) - 1)
            return basis, values[index]
    return None


def _fill_template(template: str, values: tuple[float, ...]) -> str:
    formatted = tuple(_number(value * 100) for value in values)

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return formatted[index] if index < len(formatted) else "—"

    return _PLACEHOLDER.sub(replace, template)


def _multiplier_lines(skill: CharacterSkill, level: int) -> tuple[tuple[str, str], ...]:
    items = {item.damage_id: item for item in skill.damage_items}
    lines: list[tuple[str, str]] = []
    used: set[str] = set()
    for hint in skill.level_hints:
        effect_ids = (
            *hint.damage_effect_ids,
            *hint.defense_effect_ids,
            *hint.health_effect_ids,
        )
        projected = tuple(
            (effect_id, result)
            for effect_id in effect_ids
            if (item := items.get(effect_id)) is not None
            and (result := _rate_at(item, level)) is not None
        )
        if not projected:
            continue
        values = tuple(result[1] for _effect_id, result in projected)
        bases = tuple(dict.fromkeys(result[0] for _effect_id, result in projected))
        template = _plain(hint.value_description_zh) or "{0}%"
        label = _plain(hint.description_zh) or f"倍率段 {len(lines) + 1}"
        if bases != ("攻击",):
            label += " · " + "/".join(bases)
        lines.append((label, _fill_template(template, values)))
        used.update(effect_id for effect_id, _result in projected)
    for item in skill.damage_items:
        if item.damage_id in used:
            continue
        result = _rate_at(item, level)
        if result is None:
            continue
        basis, value = result
        lines.append((
            f"附加伤害倍率 {len(lines) + 1} · {basis}",
            f"{_number(value * 100)}%",
        ))
    return tuple(lines)


class _SkillRowHeader(QFrame):
    """Clickable skill-row header that leaves embedded controls interactive."""

    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("characterSkillHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("展开或收起技能详情")
        self.setToolTip("点击整行展开或收起")
        self.setStyleSheet(themed_style(
            "QFrame#characterSkillHeader{background:transparent;border:none;"
            "border-radius:8px;}"
            "QFrame#characterSkillHeader:hover{background:#1f6feb22;}"
            "QFrame#characterSkillHeader:focus{border:1px solid #58a6ff;}"
        ))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SkillActionCard(QFrame):
    """One full-width official skill row with its own downward drawer."""

    expanded = Signal(object)

    def __init__(self, action: CharacterActionCard, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action = action
        self.setProperty("skillActionCard", True)
        self.setStyleSheet(themed_style(
            "QFrame[skillActionCard='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:10px;}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(7)
        self.heading = _SkillRowHeader(self)
        self.heading.activated.connect(self._toggle_from_row)
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(10)
        slot = QLabel(_SLOT_LABELS.get(action.slot, action.slot), self)
        slot.setObjectName("characterSkillSlot")
        slot.setMinimumWidth(78)
        slot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        slot.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:13px;font-weight:900"
        ))
        name = QLabel(action.title, self)
        name.setObjectName("characterSkillTitle")
        name.setWordWrap(True)
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:15px;font-weight:900"
        ))
        heading.addWidget(slot)
        heading.addWidget(name, 1)
        self.level = QComboBox(self)
        self.level.setObjectName("characterSkillLevel")
        if action.skill is not None:
            for level in range(1, _skill_max_level(action.skill) + 1):
                self.level.addItem(f"Lv.{level}", level)
            self.level.setCurrentIndex(self.level.count() - 1)
            heading.addWidget(self.level)
        else:
            self.level.hide()
            status = (
                f"突破 {action.passive.unlock_stage} 解锁"
                if action.passive is not None else "等级未提供"
            )
            unavailable = QLabel(status, self)
            unavailable.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
            heading.addWidget(unavailable)
        self.toggle = QLabel(self._toggle_text(False), self)
        self.toggle.setObjectName("characterSkillToggle")
        self.toggle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toggle.setStyleSheet(themed_style(
            "color:#8b949e;background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;padding:7px 12px;font-size:12px;font-weight:800"
        ))
        heading.addWidget(self.toggle)
        root.addWidget(self.heading)
        self.drawer = QFrame(self)
        self.drawer.setObjectName("characterSkillMultiplierDrawer")
        self.drawer.setStyleSheet(themed_style(
            "QFrame#characterSkillMultiplierDrawer{background:#0d1117;"
            "border:0;border-top:1px solid #30363d;}"
        ))
        self.drawer_layout = QVBoxLayout(self.drawer)
        self.drawer_layout.setContentsMargins(4, 9, 4, 2)
        self.drawer_layout.setSpacing(5)
        self.drawer.setVisible(False)
        root.addWidget(self.drawer)
        self._expanded = False
        self.level.currentIndexChanged.connect(self._render_drawer)

    def selected_level(self) -> int | None:
        return int(self.level.currentData()) if self.level.count() else None

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        if expanded:
            self.expanded.emit(self)
            self._render_drawer()
        self.drawer.setVisible(expanded)
        self.toggle.setText(self._toggle_text(expanded))
        self.toggle.setStyleSheet(themed_style(
            "color:#58a6ff;background:#1f6feb33;border:1px solid #58a6ff;"
            "border-radius:8px;padding:7px 12px;font-size:12px;font-weight:800"
            if expanded else
            "color:#8b949e;background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;padding:7px 12px;font-size:12px;font-weight:800"
        ))

    def _toggle_from_row(self) -> None:
        self.set_expanded(not self._expanded)

    def _toggle_text(self, expanded: bool) -> str:
        noun = "说明" if self.action.passive is not None else "倍率"
        return f"{'收起' if expanded else '查看'}{noun}  {'▴' if expanded else '▾'}"

    def _render_drawer(self) -> None:
        self._clear(self.drawer_layout)
        skill = self.action.skill
        level = self.selected_level()
        if self.action.passive is not None:
            self._render_passive(self.action.passive)
            return
        if skill is None or level is None:
            self.drawer_layout.addWidget(self._muted("当前正式数据未提供可分级倍率"))
            return
        title = QLabel(f"当前等级倍率 · Lv.{level}", self.drawer)
        title.setObjectName("characterSkillMultiplierTitle")
        title.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:14px;font-weight:900"
        ))
        self.drawer_layout.addWidget(title)
        lines = _multiplier_lines(skill, level)
        if not lines:
            self.drawer_layout.addWidget(self._muted("当前正式数据未提供倍率明细"))
            return
        for label_text, value_text in lines:
            row = QWidget(self.drawer)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 4, 0, 4)
            layout.setSpacing(14)
            label = QLabel(label_text, row)
            label.setObjectName("characterSkillMultiplierLabel")
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#c9d1d9;font-size:13px;line-height:1.45"
            ))
            value = QLabel(value_text, row)
            value.setObjectName("characterSkillMultiplierValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setStyleSheet(themed_style(
                "color:#f0f6fc;font-size:14px;font-weight:900"
            ))
            layout.addWidget(label, 1)
            layout.addWidget(value)
            self.drawer_layout.addWidget(row)

    def _render_passive(self, passive: CharacterPassive) -> None:
        title = QLabel(f"突破 {passive.unlock_stage} 解锁", self.drawer)
        title.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:14px;font-weight:900"
        ))
        self.drawer_layout.addWidget(title)
        descriptions = tuple(
            item for item in passive.descriptions
            if item.description_type == "ADT_DES"
        ) or passive.descriptions[:1]
        rendered = False
        for item in descriptions:
            text = _plain(item.description_zh or item.short_description_zh)
            if not text:
                continue
            label = QLabel(text, self.drawer)
            label.setObjectName("characterPassiveDescription")
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#c9d1d9;font-size:13px;line-height:1.45"
            ))
            self.drawer_layout.addWidget(label)
            rendered = True
        if not rendered:
            self.drawer_layout.addWidget(self._muted("当前正式数据未提供被动说明"))

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text, self.drawer)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        return label

    @staticmethod
    def _clear(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()


class CharacterSkillView(QWidget):
    """Official skill list; it owns only editable display levels."""

    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        del terminology
        self._cards: list[SkillActionCard] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget(scroll)
        self.rows = QVBoxLayout(host)
        self.rows.setContentsMargins(4, 4, 8, 14)
        self.rows.setSpacing(7)
        scroll.setWidget(host)
        root.addWidget(scroll)

    def set_actions(self, actions: tuple[CharacterActionCard, ...]) -> None:
        self._clear(self.rows)
        self._cards = []
        for action in actions:
            card = SkillActionCard(action, self)
            card.expanded.connect(self._keep_single_drawer)
            self._cards.append(card)
            self.rows.addWidget(card)
        self.rows.addStretch(1)

    def _keep_single_drawer(self, active: SkillActionCard) -> None:
        for card in self._cards:
            if card is not active:
                card.set_expanded(False)

    @staticmethod
    def _clear(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()


class CharacterSkillTrainingView(QWidget):
    """Separate skill-cultivation page with formal material totals."""

    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._terminology = terminology
        self._actions: tuple[CharacterActionCard, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 12)
        root.setSpacing(8)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("技能", self))
        self.skill = QComboBox(self)
        self.skill.setObjectName("characterTrainingSkill")
        self.start = QComboBox(self)
        self.end = QComboBox(self)
        controls.addWidget(self.skill, 1)
        controls.addWidget(QLabel("等级", self))
        controls.addWidget(self.start)
        controls.addWidget(QLabel("→", self))
        controls.addWidget(self.end)
        root.addLayout(controls)
        self.result = QLabel("选择技能和等级后显示正式材料合计。", self)
        self.result.setObjectName("skillProgressionResult")
        self.result.setWordWrap(True)
        self.result.setStyleSheet(themed_style(
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:8px"
        ))
        root.addWidget(self.result)
        self.cost_scroll = QScrollArea(self)
        self.cost_scroll.setWidgetResizable(True)
        self.cost_scroll.setFrameShape(QFrame.Shape.NoFrame)
        cost_host = QWidget(self.cost_scroll)
        self.costs = QVBoxLayout(cost_host)
        self.costs.setContentsMargins(0, 4, 4, 4)
        self.costs.setSpacing(5)
        self.cost_scroll.setWidget(cost_host)
        root.addWidget(self.cost_scroll, 1)
        self.skill.currentIndexChanged.connect(self._refresh_levels)
        self.start.currentIndexChanged.connect(self._refresh_costs)
        self.end.currentIndexChanged.connect(self._refresh_costs)

    def set_actions(self, actions: tuple[CharacterActionCard, ...]) -> None:
        self._actions = tuple(action for action in actions if action.skill is not None)
        self.skill.blockSignals(True)
        self.skill.clear()
        for action in self._actions:
            self.skill.addItem(
                f"{_SLOT_LABELS.get(action.slot, action.slot)} · {action.title}",
                action.ability_id,
            )
        self.skill.blockSignals(False)
        self._refresh_levels()

    def active_skill_id(self) -> str | None:
        return str(self.skill.currentData()) if self.skill.currentData() else None

    def _active_action(self) -> CharacterActionCard | None:
        index = self.skill.currentIndex()
        return self._actions[index] if 0 <= index < len(self._actions) else None

    def _refresh_levels(self) -> None:
        action = self._active_action()
        for combo in (self.start, self.end):
            combo.blockSignals(True)
            combo.clear()
        if action is not None and action.skill is not None:
            for level in range(1, _skill_max_level(action.skill) + 1):
                self.start.addItem(f"Lv.{level}", level)
                self.end.addItem(f"Lv.{level}", level)
            self.end.setCurrentIndex(self.end.count() - 1)
        for combo in (self.start, self.end):
            combo.blockSignals(False)
        self._refresh_costs()

    def _refresh_costs(self) -> None:
        self._clear(self.costs)
        action = self._active_action()
        if action is None or action.skill is None or not self.start.count():
            self.costs.addWidget(self._muted("当前正式数据未提供技能养成记录"))
            self._set_summary("当前正式数据未提供技能养成记录。", available=False)
            return
        start = int(self.start.currentData())
        end = int(self.end.currentData())
        projection = project_skill_level_requirements(
            action.skill,
            from_level=start,
            to_level=end,
            terminology=self._terminology,
        )
        if projection.requirements:
            terms = tuple(
                project_character_term(
                    self._terminology,
                    entity_kind="item",
                    stable_id=item.item_id,
                    identity_label=f"合计项 {index}",
                    context="progression_cost",
                )
                for index, item in enumerate(projection.requirements, start=1)
            )
            summary = "所选区间合计 · " + "、".join(
                f"{term.display_name} × {item.required_quantity:,}"
                for item, term in zip(projection.requirements, terms)
            )
            if projection.gaps:
                summary += "\n部分正式数量尚未提供，以上仅为已知合计。"
        elif start >= end:
            summary = "目标等级需要高于当前等级。"
        else:
            summary = "所选区间没有正式消耗。"
        self._set_summary(
            summary,
            available=projection.status == MaterialSummaryStatus.COMPLETE,
        )
        rows = {
            level.level: level for level in action.skill.levels
            if start <= level.level < end
        }
        if not rows:
            self.costs.addWidget(self._muted("所选区间没有正式消耗"))
            return
        for cost_level in range(start, end):
            level = rows.get(cost_level)
            if level is None:
                self.costs.addWidget(self._muted(
                    f"升至 Lv.{cost_level + 1} · 消耗暂未提供"
                ))
                continue
            projections = tuple(
                project_character_term(
                    self._terminology,
                    entity_kind="item",
                    stable_id=item.item_id,
                    identity_label=f"消耗项 {index}",
                    context="progression_cost",
                )
                for index, item in enumerate(level.costs, start=1)
            )
            text = "、".join(
                f"{projection.display_name} × " + (
                    _number(item.quantity) if not item.hidden_amount else "数量未提供"
                )
                for item, projection in zip(level.costs, projections)
            ) or "消耗暂未提供"
            label = QLabel(f"升至 Lv.{cost_level + 1}    {text}", self)
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#c9d1d9;border-bottom:1px solid #30363d;padding:6px 2px"
            ))
            self.costs.addWidget(label)
        self.costs.addStretch(1)

    def _set_summary(self, text: str, *, available: bool) -> None:
        self.result.setText(text)
        self.result.setStyleSheet(themed_style(
            "color:#3fb950;background:#0d1117;border:1px solid #3fb950;"
            "border-radius:8px;padding:8px"
            if available else
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:8px"
        ))

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        return label

    @staticmethod
    def _clear(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()


__all__ = [
    "CharacterActionCard",
    "CharacterSkillTrainingView",
    "CharacterSkillView",
    "SkillActionCard",
    "build_action_cards",
]
