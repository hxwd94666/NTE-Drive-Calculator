# 以游戏技能卡而非表格呈现角色正式技能与养成消耗。
"""Game-styled skill cards and detail drawer for character archives."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.character_progression import (
    project_skill_level_requirements,
)
from src.features.static_catalog.domain_pages.character_terminology import (
    project_character_term,
)
from src.services.static_catalog_character_models import (
    CharacterDetail,
    CharacterSkill,
    CombatLink,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_MARKUP = re.compile(r"<[^>]+>")
_HAN_CHARACTER = re.compile(r"[\u3400-\u9fff]")
_PRIMARY_INPUTS = {
    "Ability.Melee": "A",
    "Ability.Skill": "E",
    "Ability.UltraSkill": "Q",
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


def build_action_cards(
    detail: CharacterDetail,
    combat_links: tuple[CombatLink, ...],
    *,
    terminology: StaticCatalogTerminologyService | None = None,
) -> tuple[CharacterActionCard, ...]:
    """Use only structured tags/input IDs to assign visible action slots."""

    by_slot: dict[str, CharacterSkill] = {}
    skill_names = {
        skill.skill_id: skill.name_zh
        for skill in detail.skills
        if skill.name_zh
    }
    for skill in detail.skills:
        slot = _PRIMARY_INPUTS.get(skill.gameplay_tag or "")
        if slot is not None:
            by_slot[slot] = skill
    cards = [
        _skill_card(
            detail.character.character_id,
            slot,
            by_slot.get(slot),
            terminology=terminology,
            missing_reason=(
            "当前正式数据未提供 R 动作槽位映射。"
            if slot == "R" else f"当前正式数据未提供 {slot} 技能。"
            ),
        )
        for slot in ("A", "E", "Q", "R")
    ]

    qte = next((skill for skill in detail.skills if skill.skill_id.endswith("_QTE")), None)
    if qte is not None:
        qte_card = _skill_card(
            detail.character.character_id,
            "QTE",
            qte,
            terminology=terminology,
            missing_reason="",
        )
        if (
            qte_card.title != "名称暂未提供"
            and _HAN_CHARACTER.search(qte_card.title)
        ):
            cards.append(qte_card)

    active_links = tuple(link for link in combat_links if link.binding_kind == "active")
    g_link = next((link for link in active_links if (link.input_id or "").endswith("InputID_GSkill")), None)
    if g_link is not None:
        name = _extra_action_name(g_link, skill_names, terminology)
        if name is not None:
            cards.append(_link_card(
                detail.character.character_id, "G", name, g_link,
            ))
    evade_counter = next((
        link for link in combat_links
        if link.ability_id and link.ability_id.endswith("_PerfectEvade")
    ), None)
    if evade_counter is not None:
        name = _extra_action_name(evade_counter, skill_names, terminology)
        if name is not None:
            cards.append(_link_card(
                detail.character.character_id, "闪避反击", name, evade_counter,
            ))
    return tuple(cards)


def _extra_action_name(
    link: CombatLink,
    skill_names: dict[str, str | None],
    terminology: StaticCatalogTerminologyService | None,
) -> str | None:
    """Return only a localized player name; raw ability identity is not a title."""

    name = skill_names.get(link.ability_id or "") or project_character_term(
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


def _skill_card(
    character_id: int,
    slot: str,
    skill: CharacterSkill | None,
    *,
    terminology: StaticCatalogTerminologyService | None,
    missing_reason: str,
) -> CharacterActionCard:
    if skill is None:
        return CharacterActionCard(
            character_id, slot, "当前正式数据未提供", None, None, None,
            False, missing_reason,
        )
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


def _link_card(
    character_id: int,
    slot: str,
    title: str,
    link: CombatLink,
) -> CharacterActionCard:
    return CharacterActionCard(
        character_id=character_id,
        slot=slot,
        title=title,
        skill=None,
        ability_id=link.ability_id,
        resource_path=link.ability_asset_path,
        available=True,
    )


class SkillActionCard(QFrame):
    requested = Signal(object, str)

    def __init__(self, action: CharacterActionCard, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action = action
        self.setProperty("skillActionCard", True)
        self.setMinimumSize(130, 88)
        self.setStyleSheet(themed_style(
            "QFrame[skillActionCard='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:14px;}"
        ))
        self.setMinimumHeight(88)
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 7, 9, 7)
        root.setSpacing(3)
        heading = QHBoxLayout()
        slot = QLabel(action.slot, self)
        slot.setObjectName("characterSkillSlot")
        slot.setFixedSize(
            max(32, slot.fontMetrics().horizontalAdvance(action.slot) + 14),
            32,
        )
        slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        slot.setStyleSheet(themed_style(
            "background:#1f6feb33;color:#58a6ff;border:1px solid #58a6ff;"
            "border-radius:9px;font-size:12px;font-weight:900"
        ))
        name = QLabel(action.title, self)
        name.setObjectName("characterSkillTitle")
        name.setWordWrap(True)
        name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:12px;font-weight:800"
            if action.available else "color:#6e7681;font-size:11px;font-weight:700"
        ))
        heading.addWidget(slot)
        heading.addWidget(name, 1)
        root.addLayout(heading)
        if action.reason:
            identity = QLabel(action.reason, self)
            identity.setWordWrap(True)
            identity.setStyleSheet(themed_style("color:#8b949e;font-size:9px"))
            root.addWidget(identity)
        controls = QHBoxLayout()
        details = QPushButton("说明", self)
        details.setObjectName("btnSm")
        details.setEnabled(action.available)
        details.clicked.connect(lambda: self.requested.emit(self.action, "details"))
        training = QPushButton("养成", self)
        training.setObjectName("btnSm")
        training.setEnabled(action.skill is not None and bool(action.skill.levels))
        training.clicked.connect(lambda: self.requested.emit(self.action, "training"))
        controls.addWidget(details)
        controls.addWidget(training)
        controls.addStretch(1)
        root.addLayout(controls)


class SkillDetailDrawer(QFrame):
    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._terminology = terminology
        self.setObjectName("characterSkillDrawer")
        self.setStyleSheet(themed_style(
            "QFrame#characterSkillDrawer{background:#0d1117;border:1px solid #30363d;"
            "border-radius:14px;}"
        ))
        self.setMinimumHeight(150)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        self.title = QLabel("选择技能卡查看详情", self)
        self.title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:17px;font-weight:900"
        ))
        root.addWidget(self.title)
        self.stack = QStackedWidget(self)
        self.details_host, self.details_layout = self._host()
        self.training_host, self.training_layout = self._host()
        self.stack.addWidget(self.details_host)
        self.stack.addWidget(self.training_host)
        root.addWidget(self.stack)
        self._action: CharacterActionCard | None = None

    @staticmethod
    def _host() -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:none;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        host = QWidget(scroll)
        host.setStyleSheet(themed_style("background:#0d1117;"))
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 4, 4, 4)
        layout.setSpacing(8)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(host)
        return scroll, layout

    def show_action(self, action: CharacterActionCard, mode: str) -> None:
        self._action = action
        self.setVisible(True)
        self.title.setText(f"{action.slot} · {action.title}")
        if mode == "training":
            self._render_training(action)
            self.stack.setCurrentWidget(self.training_host)
        else:
            self._render_details(action)
            self.stack.setCurrentWidget(self.details_host)

    def clear_selection(self) -> None:
        """Discard identity and content from the previously loaded character."""

        self._action = None
        self.title.setText("选择技能卡查看详情")
        self._clear(self.details_layout)
        self._clear(self.training_layout)
        self.details_layout.addWidget(self._muted("请选择技能卡。"))
        self.stack.setCurrentWidget(self.details_host)

    def active_skill_id(self) -> str | None:
        action = self._action
        if action is None or action.skill is None:
            return None
        return action.skill.skill_id

    def _render_details(self, action: CharacterActionCard) -> None:
        self._clear(self.details_layout)
        skill = action.skill
        if skill is not None:
            descriptions = tuple(
                _plain(item.description_zh or item.short_description_zh)
                for item in skill.descriptions
                if _plain(item.description_zh or item.short_description_zh)
            )
            if descriptions:
                self.details_layout.addWidget(self._description_label(
                    descriptions[0],
                ))
                if len(descriptions) > 1:
                    expand = QPushButton(
                        f"展开完整说明（{len(descriptions)} 段）  ▾",
                        self,
                    )
                    expand.setObjectName("characterSkillDescriptionToggle")
                    expand.setCheckable(True)
                    extra = QFrame(self)
                    extra.setObjectName("characterSkillDescriptionExtra")
                    extra_layout = QVBoxLayout(extra)
                    extra_layout.setContentsMargins(0, 0, 0, 0)
                    extra_layout.setSpacing(8)
                    for text in descriptions[1:]:
                        extra_layout.addWidget(self._description_label(text))
                    extra.setVisible(False)

                    def toggle_descriptions(expanded: bool) -> None:
                        extra.setVisible(expanded)
                        expand.setText(
                            "收起完整说明  ▴"
                            if expanded else
                            f"展开完整说明（{len(descriptions)} 段）  ▾"
                        )

                    expand.toggled.connect(toggle_descriptions)
                    self.details_layout.addWidget(
                        expand, 0, Qt.AlignmentFlag.AlignLeft,
                    )
                    self.details_layout.addWidget(extra)
            else:
                self.details_layout.addWidget(self._muted("当前正式数据未提供技能描述"))
            self._render_level_hints(skill)
        self.details_layout.addStretch(1)

    def _render_level_hints(self, skill: CharacterSkill) -> None:
        if not skill.level_hints:
            return
        panel = QFrame(self)
        panel.setObjectName("characterSkillLevelHints")
        panel.setStyleSheet(themed_style(
            "QFrame#characterSkillLevelHints{background:#161b22;"
            "border:1px solid #30363d;border-radius:10px;}"
        ))
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        title = QLabel(f"等级效果 · {len(skill.level_hints)} 项", panel)
        title.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:11px;font-weight:900"
        ))
        layout.addWidget(title)
        for hint in skill.level_hints:
            text = _plain(hint.description_zh) or "名称暂未提供"
            label = QLabel(text, panel)
            label.setWordWrap(True)
            label.setStyleSheet(themed_style("color:#c9d1d9;font-size:10px"))
            layout.addWidget(label)
        self.details_layout.addWidget(panel)

    def _description_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("characterSkillDescription")
        label.setWordWrap(True)
        label.setMinimumHeight(36)
        label.setStyleSheet(themed_style(
            "color:#c9d1d9;line-height:1.45"
        ))
        return label

    def _render_training(self, action: CharacterActionCard) -> None:
        self._clear(self.training_layout)
        skill = action.skill
        if skill is None or not skill.levels:
            self.training_layout.addWidget(self._muted("当前正式数据未提供该技能的养成消耗"))
            return
        levels = sorted({1, *(level.level for level in skill.levels)})
        controls = QHBoxLayout()
        controls.addWidget(QLabel("技能等级", self))
        start = QComboBox(self)
        end = QComboBox(self)
        for level in levels:
            start.addItem(f"Lv.{level}", level)
            end.addItem(f"Lv.{level}", level)
        end.setCurrentIndex(end.count() - 1)
        controls.addWidget(start)
        controls.addWidget(QLabel("→", self))
        controls.addWidget(end)
        controls.addStretch(1)
        self.training_layout.addLayout(controls)
        formal_costs = QVBoxLayout()
        self.training_layout.addLayout(formal_costs)
        result = QLabel(
            "公共养成计算尚未接入。点击后由材料体力计算服务计算材料缺口、"
            "正式副本档位、预计次数与总活力。",
            self,
        )
        result.setWordWrap(True)
        result.setObjectName("skillProgressionResult")
        result.setStyleSheet(themed_style(
            "color:#d29922;background:#161b22;border:1px solid #d29922;"
            "border-radius:8px;padding:9px"
        ))

        def refresh_formal_costs() -> None:
            self._clear(formal_costs)
            selected_start = int(start.currentData())
            selected_end = int(end.currentData())
            rows = tuple(
                level for level in skill.levels
                if selected_start < level.level <= selected_end
            )
            if not rows:
                formal_costs.addWidget(self._muted("所选区间没有逐级正式消耗行。"))
                return
            for level in rows:
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
                costs = "、".join(
                    f"{projection.display_name} × " + (
                        _number(item.quantity)
                        if not item.hidden_amount else "数量未提供"
                    )
                    for item, projection in zip(level.costs, projections)
                ) or "当前正式数据未提供消耗项"
                formal_costs.addWidget(self._material_chip(f"升至 Lv.{level.level}", costs))
            formal_costs.addWidget(self._muted(
                "这里仅陈列逐级消耗记录，不做材料折算或副本/活力推算。"
            ))

        def request_progression() -> None:
            selected_start = int(start.currentData())
            selected_end = int(end.currentData())
            requirements = project_skill_level_requirements(
                skill,
                from_level=selected_start,
                to_level=selected_end,
                terminology=self._terminology,
            )
            self.progression_requested.emit({
                "kind": "skill",
                "character_id": action.character_id,
                "skill_id": skill.skill_id,
                "from_level": selected_start,
                "to_level": selected_end,
                "requirements": requirements.requirements,
                "requirement_status": requirements.status.value,
                "requirement_gaps": requirements.gaps,
            })

        request = QPushButton("计算材料缺口与活力", self)
        request.setObjectName("btnAction")
        request.clicked.connect(request_progression)
        start.currentIndexChanged.connect(refresh_formal_costs)
        end.currentIndexChanged.connect(refresh_formal_costs)
        refresh_formal_costs()
        self.training_layout.addWidget(
            request, 0, Qt.AlignmentFlag.AlignLeft,
        )
        self.training_layout.addWidget(result)

    def set_progression_result(
        self,
        text: str,
        *,
        available: bool,
        more_info: tuple[tuple[str, str], ...] = (),
    ) -> None:
        label = self.findChild(QLabel, "skillProgressionResult")
        if label is None:
            return
        label.setText(text)
        label.setStyleSheet(themed_style(
            "color:#3fb950;background:#161b22;border:1px solid #3fb950;"
            "border-radius:8px;padding:9px"
            if available else
            "color:#d29922;background:#161b22;border:1px solid #d29922;"
            "border-radius:8px;padding:9px"
        ))
        del more_info

    def _material_chip(self, item_id: str, amount: str) -> QLabel:
        label = QLabel(f"{item_id}    {amount}", self)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        label.setStyleSheet(themed_style(
            "color:#f0f6fc;background:#1f6feb33;border:1px solid #1f6feb;"
            "border-radius:8px;padding:8px;font-weight:700"
        ))
        return label

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        return label

    @staticmethod
    def _clear(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                SkillDetailDrawer._clear(child)


class CharacterSkillView(QWidget):
    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards: list[SkillActionCard] = []
        self._columns = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        intro = QLabel(
            "A / E / Q / R 固定置顶；额外动作只在正式资料明确确认时显示。",
            self,
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        root.addWidget(intro)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        root.addLayout(self.grid)
        self.drawer = SkillDetailDrawer(
            terminology=terminology,
            parent=self,
        )
        self.drawer.progression_requested.connect(self.progression_requested)
        self.drawer.setVisible(False)
        root.addWidget(self.drawer)
        root.addStretch(1)

    def set_actions(self, actions: tuple[CharacterActionCard, ...]) -> None:
        self.drawer.clear_selection()
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = []
        for action in actions:
            card = SkillActionCard(action, self)
            card.requested.connect(self.drawer.show_action)
            self._cards.append(card)
        self._relayout(force=True)

    def active_skill_id(self) -> str | None:
        """Return the selected formal skill identity without exposing widgets."""

        return self.drawer.active_skill_id()

    def _relayout(self, *, force: bool = False) -> None:
        columns = 2 if self.width() < 620 else (3 if self.width() < 1000 else 4)
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()
