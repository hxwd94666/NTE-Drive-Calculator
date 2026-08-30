# 角色图鉴的正式图纸、培养建议、派生毕业模板与觉醒结构效果。
"""Game-styled character build and awakening projections."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.character_terminology import (
    project_character_term,
)
from src.services.static_catalog_character_models import (
    AwakeningEffect,
    BuildProperty,
    CharacterDetail,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.ui.puzzle_board import PuzzleBoardWidget


def _number(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _plain(value: str | None) -> str:
    return " ".join(re.sub(r"<[^>]+>", "", value or "").split())


class CharacterBuildView(QWidget):
    """Responsive, non-tabular projection of formal and project build facts."""

    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._terminology = terminology
        self._cards: list[QFrame] = []
        self._columns = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:none;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        host = QWidget(scroll)
        self.grid = QGridLayout(host)
        self.grid.setContentsMargins(10, 12, 10, 18)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(host)
        root.addWidget(scroll)

    def set_data(self, detail: CharacterDetail) -> None:
        self._clear(self.grid)
        self._cards = [
            self._plan_card(detail),
            self._cultivation_card(detail),
            self._shape_card(detail),
            self._graduation_card(detail),
            self._weights_card(detail),
            self._stages_card(detail),
        ]
        self._relayout(force=True)

    def _plan_card(self, detail: CharacterDetail) -> QFrame:
        card = self._panel("角色图纸 · 正式 5×5")
        plan = detail.equipment_plan
        if plan is None:
            card.layout().addWidget(self._muted("当前正式数据未提供角色图纸"))
            return card
        summary = QHBoxLayout()
        core = project_character_term(
            self._terminology,
            entity_kind="item",
            stable_id=plan.core_item_id,
            identity_label="空幕",
        )
        summary.addWidget(self._chip(f"{core.display_name} · Lv.{plan.core_level}"))
        summary.addWidget(self._chip(f"驱动模板 · Lv.{plan.module_level}"))
        summary.addStretch(1)
        card.layout().addLayout(summary)
        matrix = [["0" for _column in range(5)] for _row in range(5)]
        for row, column, module_ordinal in plan.cells:
            if module_ordinal is not None:
                matrix[row - 1][column - 1] = str(module_ordinal + 1)
        card.layout().addWidget(
            PuzzleBoardWidget(matrix, cell_size=28, parent=card),
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        modules = "、".join(
            f"{module.display_name or '名称暂未提供'} {module.grid_count} 格"
            for module in plan.modules
        )
        card.layout().addWidget(self._info("驱动配置", modules))
        names = self._property_names(plan.core_attributes)
        card.layout().addWidget(self._info(
            "空幕主属性", "、".join(names) or "名称暂未提供",
        ))
        recommended = self._property_names(plan.recommended_attributes)
        card.layout().addWidget(self._info(
            "正式推荐属性", "、".join(recommended) or "名称暂未提供",
        ))
        return card

    def _cultivation_card(self, detail: CharacterDetail) -> QFrame:
        card = self._panel("正式培养方向")
        guide = detail.cultivation
        if guide is None:
            card.layout().addWidget(self._muted("当前正式数据未提供培养方向"))
            return card
        attributes = tuple(
            (
                display_name
                if display_name and display_name != property_id
                else self._property_name(BuildProperty(property_id, None))
            )
            for property_id, display_name in guide.attribute_recommendations
        )
        card.layout().addWidget(self._info(
            "培养属性", "、".join(attributes) or "名称暂未提供",
        ))
        if not guide.fork_recommendations:
            card.layout().addWidget(self._muted("当前正式数据未提供推荐弧盘"))
        for fork_id, formal_name, description in guide.fork_recommendations:
            fork_term = project_character_term(
                self._terminology,
                entity_kind="fork",
                stable_id=fork_id,
                identity_label="弧盘",
            )
            title = QLabel(
                formal_name
                if formal_name and formal_name != fork_id
                else fork_term.display_name,
                card,
            )
            title.setStyleSheet(themed_style("color:#f0f6fc;font-weight:800"))
            note = QLabel(description or "正式推荐弧盘", card)
            note.setWordWrap(True)
            note.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
            card.layout().addWidget(title)
            card.layout().addWidget(note)
        return card

    def _shape_card(self, detail: CharacterDetail) -> QFrame:
        card = self._panel("正式额外形状与加成")
        bonus = detail.shape_bonus
        if bonus is None:
            card.layout().addWidget(self._muted("当前正式数据未提供额外形状"))
            return card
        card.layout().addWidget(self._chip(f"{bonus.shape_grid_count} 格额外形状"))
        for prop in bonus.properties:
            value = "当前正式数据未提供"
            if prop.value is not None:
                value = f"{_number(prop.value)}{'%' if prop.show_percent else ''}"
            card.layout().addWidget(self._info(self._property_name(prop), value))
        return card

    def _graduation_card(self, detail: CharacterDetail) -> QFrame:
        card = self._panel("毕业装备模板")
        card.layout().addWidget(self._derived_badge())
        graduation = detail.graduation
        if graduation is None:
            card.layout().addWidget(self._muted("当前项目未提供派生毕业模板"))
            return card
        if graduation.fork_id:
            card.layout().addWidget(self._info(
                "推荐弧盘",
                f"{graduation.fork_name_zh or '名称暂未提供'} · "
                f"Lv.{graduation.fork_level or '—'}",
            ))
        if graduation.core_suit_id:
            card.layout().addWidget(self._info(
                "空幕套装",
                graduation.core_suit_name_zh or "名称暂未提供",
            ))
        core_stats = self._property_values(graduation.core_main_stats)
        drive_stats = self._property_values(graduation.drive_template_stats)
        card.layout().addWidget(self._info(
            "空幕主属性", "、".join(core_stats) or "名称暂未提供",
        ))
        card.layout().addWidget(self._info(
            "驱动模板", "、".join(drive_stats) or "名称暂未提供",
        ))
        card.layout().addWidget(self._info(
            "底盘目标", f"{graduation.drive_area} 格 · 额外形状 {graduation.extra_shape_count} 个",
        ))
        return card

    def _weights_card(self, detail: CharacterDetail) -> QFrame:
        card = self._panel("推荐权重")
        card.layout().addWidget(self._derived_badge())
        weights = detail.recommended_weights
        if weights is None or not weights.properties:
            card.layout().addWidget(self._muted("当前项目未提供派生推荐权重"))
            return card
        for prop, weight, main_weight in weights.properties:
            card.layout().addWidget(self._info(
                self._property_name(prop),
                f"综合 {_number(weight * 100)}% · 主词条 {_number(main_weight * 100)}%",
            ))
        return card

    def _stages_card(self, detail: CharacterDetail) -> QFrame:
        card = self._panel("阶段培养路线")
        guide = detail.cultivation
        if guide is None or not guide.stages:
            card.layout().addWidget(self._muted("当前正式数据未提供阶段培养路线"))
            return card
        skill_names = {skill.skill_id: skill.name_zh for skill in detail.skills}
        for stage in guide.stages:
            skills = "、".join(
                f"{'' if slot in {'', 'default'} else f'{slot} '}"
                f"{skill_names.get(skill_id) or '名称暂未提供'} Lv.{level}"
                for slot, skill_id, level in stage.recommended_skills
            ) or "名称暂未提供"
            card.layout().addWidget(self._info(
                f"阶段 {stage.ordinal + 1}",
                f"人物 Lv.{stage.character_level} · 弧盘 Lv.{stage.fork_level} · "
                f"空幕 Lv.{stage.core_level} · 驱动 Lv.{stage.equipment_level}\n{skills}",
            ))
        return card

    def _property_names(self, values: tuple[BuildProperty, ...]) -> tuple[str, ...]:
        return tuple(self._property_name(value) for value in values)

    def _property_values(self, values: tuple[BuildProperty, ...]) -> tuple[str, ...]:
        rows = []
        for value in values:
            suffix = ""
            if value.value is not None:
                number = value.value * 100 if value.show_percent else value.value
                suffix = f" {_number(number)}{'%' if value.show_percent else ''}"
            rows.append(f"{self._property_name(value)}{suffix}")
        return tuple(rows)

    def _property_name(self, value: BuildProperty) -> str:
        if value.display_name and value.display_name != value.property_id:
            return value.display_name
        return project_character_term(
            self._terminology,
            entity_kind="equipment_attribute",
            stable_id=value.property_id,
            identity_label="属性",
        ).display_name

    @staticmethod
    def _panel(title: str) -> QFrame:
        card = QFrame()
        card.setProperty("characterBuildPanel", True)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        card.setStyleSheet(themed_style(
            "QFrame[characterBuildPanel='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        heading = QLabel(title, card)
        heading.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:14px;font-weight:900"
        ))
        layout.addWidget(heading)
        return card

    @staticmethod
    def _derived_badge() -> QLabel:
        badge = QLabel("项目推荐 · 派生")
        badge.setObjectName("characterDerivedBadge")
        badge.setStyleSheet(themed_style(
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:3px 8px;font-size:10px;font-weight:900"
        ))
        return badge

    @staticmethod
    def _chip(text: str) -> QLabel:
        chip = QLabel(text)
        chip.setWordWrap(True)
        chip.setStyleSheet(themed_style(
            "color:#f0f6fc;background:#1f6feb33;border:1px solid #1f6feb;"
            "border-radius:8px;padding:6px 9px;font-weight:800"
        ))
        return chip

    @staticmethod
    def _info(title: str, value: str) -> QFrame:
        row = QFrame()
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(1)
        caption = QLabel(title, row)
        caption.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        content = QLabel(value, row)
        content.setWordWrap(True)
        content.setStyleSheet(themed_style("color:#c9d1d9;font-size:11px"))
        layout.addWidget(caption)
        layout.addWidget(content)
        return row

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        return label

    def _relayout(self, *, force: bool = False) -> None:
        columns = 1 if self.width() < 820 else 2
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        if columns == 1:
            for index, card in enumerate(self._cards):
                self.grid.addWidget(card, index, 0, Qt.AlignmentFlag.AlignTop)
        else:
            plan, cultivation, shape, graduation, weights, stages = self._cards
            self.grid.addWidget(plan, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(cultivation, 0, 1, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(graduation, 1, 1, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(shape, 2, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(weights, 2, 1, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(stages, 3, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
        for column in range(2):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    @staticmethod
    def _clear(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                CharacterBuildView._clear(child)


class CharacterAwakeningView(QWidget):
    """Readable awakening cards without internal identifiers."""

    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._terminology = terminology
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:none;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        host = QWidget(scroll)
        host.setStyleSheet(themed_style("background:#0d1117;"))
        self.layout_host = QVBoxLayout(host)
        self.layout_host.setContentsMargins(10, 12, 10, 18)
        self.layout_host.setSpacing(12)
        scroll.setWidget(host)
        root.addWidget(scroll)

    def set_data(self, detail: CharacterDetail) -> None:
        CharacterBuildView._clear(self.layout_host)
        if not detail.awakenings:
            self.layout_host.addWidget(CharacterBuildView._muted(
                "当前正式数据未提供觉醒资料",
            ))
        skill_names = {skill.skill_id: skill.name_zh for skill in detail.skills}
        for awakening in detail.awakenings:
            self.layout_host.addWidget(self._card(awakening, skill_names))
        self.layout_host.addStretch(1)

    def _card(
        self,
        awakening: AwakeningEffect,
        skill_names: dict[str, str | None],
    ) -> QFrame:
        card = CharacterBuildView._panel(
            awakening.title_zh or "名称暂未提供",
        )
        stage = QLabel(_awakening_stage(awakening), card)
        stage.setObjectName("characterAwakeningStage")
        stage.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:12px;font-weight:900"
        ))
        card.layout().insertWidget(0, stage)
        description = QLabel(
            _plain(awakening.description_zh) or "当前正式数据未提供说明",
            card,
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#c9d1d9;line-height:1.45"))
        card.layout().addWidget(description)
        for bonus in awakening.skill_level_bonuses:
            name = skill_names.get(bonus.skill_id)
            if not name:
                name = project_character_term(
                    self._terminology,
                    entity_kind="gameplay_ability",
                    stable_id=bonus.skill_id,
                    identity_label="技能",
                ).display_name
            card.layout().addWidget(CharacterBuildView._chip(
                f"技能等级加成 · {name} {bonus.level_delta:+d}",
            ))
        return card


def _awakening_stage(awakening: AwakeningEffect) -> str:
    if awakening.awaken_type == "Awaken_Resonance":
        return "三觉" if awakening.ordinal == 6 else "六觉"
    names = ("一觉", "二觉", "三觉", "四觉", "五觉", "六觉")
    return names[awakening.ordinal] if 0 <= awakening.ordinal < len(names) else "觉醒"


__all__ = ["CharacterAwakeningView", "CharacterBuildView"]
