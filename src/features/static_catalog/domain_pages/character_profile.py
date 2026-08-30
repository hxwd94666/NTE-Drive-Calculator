# 角色图鉴详情：大立绘、档案、技能、等级、觉醒、好感与培养路线。
"""Game-styled character profile assembled from immutable catalog DTOs."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.character_growth import CharacterGrowthView
from src.features.static_catalog.domain_pages.character_skills import (
    CharacterSkillView,
    build_action_cards,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_models import (
    AwakeningEffect,
    CharacterDetail,
    CombatLinkPage,
    GrowthPage,
)


_MARKUP = re.compile(r"<[^>]+>")
_CLASSIFICATION_LABELS = {
    "available_character": "已实装角色",
    "available_avatar_variant": "可用主角形态",
    "scheduled_character": "待登场角色",
    "combat_transformation": "战斗变身形态",
}


def _plain(value: str | None) -> str:
    return " ".join(_MARKUP.sub("", value or "").replace("</>", "").split())


def _number(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


class CharacterDetailView(QWidget):
    back_requested = Signal()
    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        asset_catalog: GameUiAssetCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterDetailView")
        self.setStyleSheet(themed_style(
            "QWidget#characterDetailView{background:#0d1117;}"
        ))
        self._asset_catalog = asset_catalog
        self._detail: CharacterDetail | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        navigation = QHBoxLayout()
        back = QPushButton("‹  返回角色图鉴", self)
        back.setObjectName("characterBackButton")
        back.clicked.connect(self.back_requested)
        navigation.addWidget(back)
        navigation.addStretch(1)
        root.addLayout(navigation)

        self.hero = QFrame(self)
        self.hero.setObjectName("characterProfileHero")
        self.hero.setMinimumHeight(244)
        self.hero.setStyleSheet(themed_style(
            "QFrame#characterProfileHero{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:0.68 #161b22,stop:1 #0d1117);"
            "border:1px solid #1f6feb;border-radius:18px;}"
        ))
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(24, 14, 24, 0)
        self.art = QLabel(self.hero)
        self.art.setFixedSize(300, 230)
        self.art.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        hero_layout.addWidget(self.art, 0, Qt.AlignmentFlag.AlignBottom)
        copy = QVBoxLayout()
        copy.setSpacing(7)
        self.eyebrow = QLabel("CHARACTER ARCHIVE", self.hero)
        self.eyebrow.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:10px;font-weight:900;letter-spacing:2px"
        ))
        self.name = QLabel("选择角色", self.hero)
        self.name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:32px;font-weight:900"
        ))
        self.identity = QLabel("—", self.hero)
        self.identity.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self.identity.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:12px;font-weight:700"
        ))
        badges = QHBoxLayout()
        self.element_badge = self._badge("属性未提供", "#58a6ff")
        self.availability_badge = self._badge("正式资料", "#3fb950")
        self.quality_badge = self._badge("品质未提供", "#d29922")
        badges.addWidget(self.element_badge)
        badges.addWidget(self.availability_badge)
        badges.addWidget(self.quality_badge)
        badges.addStretch(1)
        self.description = QLabel("角色描述 · 当前正式数据未提供", self.hero)
        self.description.setWordWrap(True)
        self.description.setStyleSheet(themed_style(
            "color:#8b949e;font-size:12px;line-height:1.45"
        ))
        copy.addStretch(1)
        copy.addWidget(self.eyebrow)
        copy.addWidget(self.name)
        copy.addWidget(self.identity)
        copy.addLayout(badges)
        copy.addWidget(self.description)
        copy.addStretch(1)
        hero_layout.addLayout(copy, 1)
        root.addWidget(self.hero)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("characterProfileTabs")
        self.overview_host, self.overview_layout = self._scroll_tab()
        self.skill_view = CharacterSkillView(self)
        self.skill_view.progression_requested.connect(self.progression_requested)
        self.growth_view = CharacterGrowthView(self)
        self.growth_view.progression_requested.connect(self.progression_requested)
        self.awakening_host, self.awakening_layout = self._scroll_tab()
        self.affinity_host, self.affinity_layout = self._scroll_tab()
        self.route_host, self.route_layout = self._scroll_tab()
        self.tabs.addTab(self.overview_host, "角色档案")
        self.tabs.addTab(self.skill_view, "技能")
        self.tabs.addTab(self.growth_view, "等级与养成")
        self.tabs.addTab(self.awakening_host, "觉醒")
        self.tabs.addTab(self.affinity_host, "好感度")
        self.tabs.addTab(self.route_host, "培养路线")
        root.addWidget(self.tabs, 1)

    @staticmethod
    def _scroll_tab() -> tuple[QScrollArea, QVBoxLayout]:
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
        layout.setContentsMargins(10, 12, 10, 18)
        layout.setSpacing(12)
        scroll.setWidget(host)
        return scroll, layout

    @staticmethod
    def _badge(text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(themed_style(
            f"color:{color};background:#0d1117;border:1px solid {color};"
            "border-radius:10px;padding:3px 9px;font-size:10px;font-weight:800"
        ))
        return label

    def set_data(
        self,
        detail: CharacterDetail,
        growth: GrowthPage,
        combat: CombatLinkPage,
    ) -> None:
        character_id = detail.character.character_id
        if growth.character_id != character_id or combat.character_id != character_id:
            return
        self._detail = detail
        character = detail.character
        self.name.setText(character.name_zh)
        self.identity.setText(f"正式 character_id  {character.character_id}")
        self.element_badge.setText(f"{character.element_label}属性")
        self.availability_badge.setText(_CLASSIFICATION_LABELS.get(
            character.classification or "", "正式资料",
        ))
        art_path = self._asset_catalog.character_icon(character_id)
        pixmap = QPixmap(str(art_path)) if art_path is not None else QPixmap()
        if pixmap.isNull():
            self.art.setText("立绘\n暂不可用")
            self.art.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        else:
            self.art.setText("")
            self.art.setStyleSheet("")
            self.art.setPixmap(pixmap.scaled(
                300,
                300,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self._render_overview(detail, growth)
        self.skill_view.set_actions(build_action_cards(detail, combat.items))
        self.growth_view.set_data(detail, growth)
        self._render_awakenings(detail)
        self._render_affinity(detail)
        self._render_route(detail)

    def _render_overview(self, detail: CharacterDetail, growth: GrowthPage) -> None:
        self._clear(self.overview_layout)
        metrics = QGridLayout()
        metric_values = (
            ("等级面板", f"{detail.growth_count} 条" if detail.growth_count else "未提供"),
            ("正式技能", f"{detail.character.skill_count} 项"),
            ("觉醒", f"{detail.character.awakening_count} 项"),
            ("毕业模板", "已提供" if detail.character.has_graduation else "未提供"),
        )
        for index, (title, value) in enumerate(metric_values):
            metrics.addWidget(self._metric_card(title, value), 0, index)
            metrics.setColumnStretch(index, 1)
        self.overview_layout.addLayout(metrics)

        identity = self._panel("身份与定位")
        rows = (
            ("正式 ID", str(detail.character.character_id)),
            ("中文名", detail.character.name_zh),
            ("属性", f"{detail.character.element_label} · {detail.character.element_type or '未提供'}"),
            ("品质", "当前正式数据未提供"),
            ("定位", "当前正式数据未提供"),
            ("常驻 / 限定", "当前正式数据未提供"),
            ("组别枚举", detail.character.group_type or "当前正式数据未提供"),
            ("大陆服展示时间", detail.character.mainland_show_time or "当前正式数据未提供"),
        )
        identity_grid = QGridLayout()
        identity_grid.setHorizontalSpacing(8)
        identity_grid.setVerticalSpacing(8)
        for index, (title, value) in enumerate(rows):
            identity_grid.addWidget(self._detail_tile(title, value), index // 2, index % 2)
        identity_grid.setColumnStretch(0, 1)
        identity_grid.setColumnStretch(1, 1)
        identity.layout().addLayout(identity_grid)
        self.overview_layout.addWidget(identity)

        panel = self._panel("面板速览")
        panel_grid = QGridLayout()
        panel_grid.setHorizontalSpacing(8)
        panel_grid.setVerticalSpacing(8)
        panel_index = 0
        for level in (1, 20, 40, 60, 70):
            points = tuple(item for item in growth.items if item.level == level)
            point = next((item for item in points if item.state == "breakthrough_after"), points[-1] if points else None)
            if point is None:
                continue
            panel_grid.addWidget(self._detail_tile(
                f"Lv.{level}",
                f"生命 {_number(point.hp_base)}  ·  攻击 {_number(point.atk_base)}  ·  防御 {_number(point.def_base)}",
            ), panel_index // 3, panel_index % 3)
            panel_index += 1
        for column in range(3):
            panel_grid.setColumnStretch(column, 1)
        panel.layout().addLayout(panel_grid)
        if not growth.items:
            panel.layout().addWidget(self._muted("当前正式数据未提供等级面板"))
        self.overview_layout.addWidget(panel)

        self.overview_layout.addStretch(1)

    def _render_awakenings(self, detail: CharacterDetail) -> None:
        self._clear(self.awakening_layout)
        if not detail.awakenings:
            self.awakening_layout.addWidget(self._muted("当前正式数据未提供觉醒资料"))
        for awakening in detail.awakenings:
            self.awakening_layout.addWidget(self._awakening_card(awakening))
        self.awakening_layout.addStretch(1)

    def _awakening_card(self, awakening: AwakeningEffect) -> QFrame:
        card = self._panel(
            f"{awakening.ordinal + 1:02d} · {awakening.title_zh or awakening.effect_id}"
        )
        description = QLabel(
            _plain(awakening.description_zh) or "当前正式数据未提供说明",
            card,
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#c9d1d9;line-height:1.45"))
        card.layout().addWidget(description)
        card.layout().addWidget(self._info_row("正式 effect_id", awakening.effect_id))
        card.layout().addWidget(self._info_row("觉醒类型", awakening.awaken_type))
        if awakening.gameplay_effect_ids:
            card.layout().addWidget(self._info_row(
                "正式 GE", "、".join(awakening.gameplay_effect_ids),
            ))
        return card

    def _render_affinity(self, detail: CharacterDetail) -> None:
        self._clear(self.affinity_layout)
        bonus = detail.likeability
        if bonus is None:
            self.affinity_layout.addWidget(self._muted("当前正式数据未提供好感度属性"))
        else:
            hero = self._panel(f"好感度 Lv.{bonus.required_level} 奖励")
            for item in bonus.properties:
                value = item.value * 100 if item.show_percent else item.value
                suffix = "%" if item.show_percent else ""
                hero.layout().addWidget(self._info_row(
                    item.display_name,
                    f"{_number(value)}{suffix} · {item.modifier_operation}",
                ))
            hero.layout().addWidget(self._info_row("正式修改 ID", bonus.modify_data_id))
            self.affinity_layout.addWidget(hero)
        self.affinity_layout.addStretch(1)

    def _render_route(self, detail: CharacterDetail) -> None:
        self._clear(self.route_layout)
        guide = detail.cultivation
        if guide is None:
            self.route_layout.addWidget(self._muted("当前正式数据未提供培养路线"))
        else:
            stages = QGridLayout()
            for index, stage in enumerate(guide.stages):
                card = self._panel(f"阶段 {stage.ordinal + 1}")
                card.layout().addWidget(self._info_row("人物", f"Lv.{stage.character_level}"))
                card.layout().addWidget(self._info_row("弧盘", f"Lv.{stage.fork_level}"))
                card.layout().addWidget(self._info_row("空幕", f"{stage.core_item_id} · Lv.{stage.core_level}"))
                skill_text = "、".join(
                    f"{slot} {skill_id} Lv.{level}"
                    for slot, skill_id, level in stage.recommended_skills
                ) or "当前正式数据未提供"
                card.layout().addWidget(self._info_row("技能", skill_text))
                stages.addWidget(card, index // 3, index % 3)
            for column in range(3):
                stages.setColumnStretch(column, 1)
            self.route_layout.addLayout(stages)
        graduation = detail.graduation
        if graduation is not None:
            card = self._panel("推荐弧盘与毕业模板")
            card.layout().addWidget(self._info_row(
                "推荐弧盘",
                f"{graduation.fork_name_zh or '未提供'} · {graduation.fork_id or '未提供'} · "
                f"Lv.{graduation.fork_level or '—'}",
            ))
            card.layout().addWidget(self._info_row(
                "空幕套装", graduation.core_suit_name_zh or "当前正式数据未提供",
            ))
            card.layout().addWidget(self._info_row(
                "主词条", graduation.core_main_property_name_zh or "当前正式数据未提供",
            ))
            self.route_layout.addWidget(card)
        self.route_layout.addStretch(1)

    @staticmethod
    def _panel(title: str) -> QFrame:
        frame = QFrame()
        frame.setProperty("characterInfoPanel", True)
        frame.setStyleSheet(themed_style(
            "QFrame[characterInfoPanel='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        heading = QLabel(title, frame)
        heading.setStyleSheet(themed_style(
            "color:#58a6ff;background:transparent;border:none;"
            "font-size:14px;font-weight:900"
        ))
        layout.addWidget(heading)
        return frame

    @staticmethod
    def _metric_card(title: str, value: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(themed_style(
            "background:#10243f;border:1px solid #1f6feb;border-radius:12px"
        ))
        layout = QVBoxLayout(card)
        caption = QLabel(title, card)
        caption.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:10px"
        ))
        metric = QLabel(value, card)
        metric.setStyleSheet(themed_style(
            "color:#f0f6fc;background:transparent;border:none;"
            "font-size:18px;font-weight:900"
        ))
        layout.addWidget(caption)
        layout.addWidget(metric)
        return card

    @staticmethod
    def _info_row(title: str, value: str) -> QFrame:
        row = QFrame()
        row.setProperty("characterInfoRow", True)
        row.setStyleSheet(themed_style(
            "QFrame[characterInfoRow='true']{background:transparent;border:none;}"
        ))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        caption = QLabel(title, row)
        caption.setMinimumWidth(120)
        caption.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:11px"
        ))
        content = QLabel(value, row)
        content.setWordWrap(True)
        content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        content.setStyleSheet(themed_style(
            "color:#c9d1d9;background:transparent;border:none;font-size:11px"
        ))
        layout.addWidget(caption)
        layout.addWidget(content, 1)
        return row

    @staticmethod
    def _detail_tile(title: str, value: str) -> QFrame:
        card = QFrame()
        card.setProperty("characterDetailTile", True)
        card.setMinimumHeight(62)
        card.setStyleSheet(themed_style(
            "QFrame[characterDetailTile='true']{background:#10243f;"
            "border:1px solid #1f6feb;border-radius:10px;}"
        ))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        caption = QLabel(title, card)
        caption.setStyleSheet(themed_style(
            "color:#58a6ff;background:transparent;border:none;"
            "font-size:10px;font-weight:800"
        ))
        content = QLabel(value, card)
        content.setWordWrap(True)
        content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        content.setStyleSheet(themed_style(
            "color:#c9d1d9;background:transparent;border:none;font-size:11px"
        ))
        layout.addWidget(caption)
        layout.addWidget(content)
        return card

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style(
            "color:#8b949e;background:#161b22;border:1px dashed #30363d;"
            "border-radius:10px;padding:12px"
        ))
        return label

    @staticmethod
    def _clear(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                CharacterDetailView._clear(child)
