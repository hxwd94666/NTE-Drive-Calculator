# 渲染弧盘图鉴详情、等级面板、混频技能、特效关系与养成路线。
"""Game-styled fork profile built only from immutable service DTOs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.static_catalog_terminology import LocalizedForkCampaign
from src.features.static_catalog.domain_pages.fork_components import (
    ForkCatalogLinkButton,
    ForkCharacterCard,
    ForkMoreInfo,
    ForkProgressionControls,
    add_effect_tiles,
    breakthrough_cost_text,
    breakthrough_raw_id_text,
    clear_layout,
    display_number,
    plain_text,
    present_effects,
    refinement_skill_text,
)
from src.services.advancement_stage_service import (
    fork_breakthrough_choices,
    fork_panel_stats,
    select_fork_breakthrough,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_models import CharacterSummary
from src.services.static_catalog_fork_release_metadata import (
    ForkItemDisplayNameService,
    build_fork_progression_request,
    fork_character_catalog_link,
    fork_mechanics_catalog_routes,
)
from src.services.static_catalog_fork_service import (
    ForkCatalogDetail,
    ForkCatalogMetadata,
    ForkModifier,
    ForkRefinementLevel,
)


class ForkProfileView(QWidget):
    back_requested = Signal()
    catalog_link_requested = Signal(object)
    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        asset_catalog: GameUiAssetCatalog,
        item_name_service: ForkItemDisplayNameService,
        display_campaigns: dict[str, LocalizedForkCampaign],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("forkProfileView")
        self._asset_catalog = asset_catalog
        self._item_names = item_name_service
        self._display_campaigns = dict(display_campaigns)
        self._detail: ForkCatalogDetail | None = None
        self._characters: tuple[CharacterSummary, ...] = ()
        self._level = 80
        self._stage: int | None = 6
        self._refinement = 1
        self._stage_buttons: list[QPushButton] = []
        self._panel_values: dict[str, float] = {}
        self._character_cards: list[ForkCharacterCard] = []
        self._breakthrough_cards: list[QFrame] = []
        self._refinement_cost_cards: list[QFrame] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 10)
        root.setSpacing(10)
        navigation = QHBoxLayout()
        back = QPushButton("‹  返回弧盘图鉴", self)
        back.setObjectName("forkBackButton")
        back.clicked.connect(self.back_requested)
        navigation.addWidget(back)
        navigation.addStretch(1)
        root.addLayout(navigation)
        self.hero = self._build_hero()
        root.addWidget(self.hero)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("forkProfileTabs")
        detail_scroll, detail_host = self._scroll_tab()
        cultivation_scroll, cultivation_host = self._scroll_tab()
        self._detail_layout = detail_host
        self._cultivation_layout = cultivation_host
        self._build_detail_tab()
        self._build_cultivation_tab()
        self.tabs.addTab(detail_scroll, "详情")
        self.tabs.addTab(cultivation_scroll, "养成")
        root.addWidget(self.tabs, 1)

    def _build_hero(self) -> QFrame:
        hero = QFrame(self)
        hero.setObjectName("forkProfileHero")
        hero.setMinimumHeight(204)
        hero.setStyleSheet(themed_style(
            "QFrame#forkProfileHero{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:0.62 #161b22,stop:1 #0d1117);"
            "border:1px solid #a371f7;border-radius:18px;}"
        ))
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(20, 10, 20, 10)
        self.art = QLabel(hero)
        self.art.setFixedSize(190, 180)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.art)
        copy = QVBoxLayout()
        copy.setSpacing(7)
        eyebrow = QLabel("FORK ARCHIVE", hero)
        eyebrow.setStyleSheet(themed_style(
            "color:#a371f7;font-size:10px;font-weight:900;letter-spacing:2px"
        ))
        self.name = QLabel("选择弧盘", hero)
        self.name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:27px;font-weight:900"
        ))
        badges = QHBoxLayout()
        self.quality_badge = self._badge("品质未提供", "#d29922")
        self.type_badge = self._badge("类型未提供", "#58a6ff")
        self.release_badge = self._badge("首发弧盘", "#8b949e")
        badges.addWidget(self.quality_badge)
        badges.addWidget(self.type_badge)
        badges.addWidget(self.release_badge)
        badges.addStretch(1)
        self.owner_label = QLabel("归属角色 · 尚未载入", hero)
        self.owner_label.setWordWrap(True)
        self.owner_label.setStyleSheet(themed_style(
            "color:#d29922;font-size:13px;font-weight:900"
        ))
        self.description = QLabel("", hero)
        self.description.setWordWrap(True)
        self.description.setStyleSheet(themed_style(
            "color:#8b949e;font-size:11px;line-height:1.45"
        ))
        self.hero_more_info = ForkMoreInfo(hero)
        copy.addStretch(1)
        copy.addWidget(eyebrow)
        copy.addWidget(self.name)
        copy.addLayout(badges)
        copy.addWidget(self.owner_label)
        copy.addWidget(self.description)
        copy.addWidget(self.hero_more_info)
        copy.addStretch(1)
        layout.addLayout(copy, 1)
        return hero

    @staticmethod
    def _scroll_tab() -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget(scroll)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(10, 12, 10, 20)
        layout.setSpacing(12)
        scroll.setWidget(host)
        return scroll, layout

    def _build_detail_tab(self) -> None:
        controls = self._panel("等级与混频")
        hint = QLabel(
            "选择 1–80 级；20/30/40/50/60/70 级会同时保留突破前与突破后。",
            controls,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        controls.layout().addWidget(hint)
        nodes = QGridLayout()
        self._level_nodes: dict[int, QPushButton] = {}
        for index, level in enumerate((1, 20, 30, 40, 50, 60, 70, 80)):
            button = self._node_button(f"{level}")
            button.clicked.connect(
                lambda _checked=False, value=level: self._choose_level(value),
            )
            self._level_nodes[level] = button
            nodes.addWidget(button, index // 4, index % 4)
        controls.layout().addLayout(nodes)
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("等级", controls))
        self.level_slider = QSlider(Qt.Orientation.Horizontal, controls)
        self.level_slider.setRange(1, 80)
        self.level_slider.setValue(80)
        self.level_slider.valueChanged.connect(self._level_changed)
        self.level_label = QLabel("Lv.80", controls)
        self.level_label.setMinimumWidth(60)
        self.level_label.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:15px;font-weight:900"
        ))
        level_row.addWidget(self.level_slider, 1)
        level_row.addWidget(self.level_label)
        controls.layout().addLayout(level_row)
        self.stage_layout = QHBoxLayout()
        controls.layout().addLayout(self.stage_layout)
        refinement_caption = QLabel("混频节点", controls)
        refinement_caption.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:11px;font-weight:900"
        ))
        controls.layout().addWidget(refinement_caption)
        refinement_row = QHBoxLayout()
        self._refinement_buttons: dict[int, QPushButton] = {}
        for level in range(1, 6):
            button = self._node_button(f"{'★' * level}\n混频 {level}")
            button.clicked.connect(
                lambda _checked=False, value=level: self.set_refinement(value),
            )
            self._refinement_buttons[level] = button
            refinement_row.addWidget(button)
        controls.layout().addLayout(refinement_row)
        self._detail_layout.addWidget(controls)

        self.panel_host = self._panel("面板")
        self.panel_grid = QGridLayout()
        self.panel_host.layout().addLayout(self.panel_grid)
        self._detail_layout.addWidget(self.panel_host)
        self.refinement_host = self._panel("弧盘技能")
        self.refinement_title = QLabel("混频 1", self.refinement_host)
        self.refinement_title.setStyleSheet(themed_style(
            "color:#a371f7;font-size:16px;font-weight:900"
        ))
        self.refinement_description = QLabel("", self.refinement_host)
        self.refinement_description.setWordWrap(True)
        self.refinement_description.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self.refinement_host.layout().addWidget(self.refinement_title)
        self.refinement_host.layout().addWidget(self.refinement_description)
        self._detail_layout.addWidget(self.refinement_host)
        self.effects_host = self._panel("效果说明")
        self.effects_layout = QVBoxLayout()
        self.effects_host.layout().addLayout(self.effects_layout)
        self._detail_layout.addWidget(self.effects_host)
        self.characters_host = self._panel("归属角色与适配角色")
        self.characters_grid = QGridLayout()
        self.characters_host.layout().addLayout(self.characters_grid)
        self._detail_layout.addWidget(self.characters_host)
        self._detail_layout.addStretch(1)

    def _build_cultivation_tab(self) -> None:
        planner = self._panel("养成计划 · 当前 → 目标")
        self.progression_controls = ForkProgressionControls(
            item_names=self._item_names,
            parent=planner,
        )
        self.progression_controls.request_clicked.connect(self._request_progression)
        planner.layout().addWidget(self.progression_controls)
        self._cultivation_layout.addWidget(planner)
        current = self._panel("当前等级养成信息")
        self.current_level_cost = QLabel("尚未选择弧盘", current)
        self.current_level_cost.setWordWrap(True)
        self.current_level_cost.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:12px;font-weight:800"
        ))
        current.layout().addWidget(self.current_level_cost)
        self.current_more_info = ForkMoreInfo(current)
        current.layout().addWidget(self.current_more_info)
        self._cultivation_layout.addWidget(current)
        self.breakthrough_host = self._panel("突破路线 · 消耗")
        self.breakthrough_grid = QGridLayout()
        self.breakthrough_host.layout().addLayout(self.breakthrough_grid)
        self._cultivation_layout.addWidget(self.breakthrough_host)
        self.refinement_cost_host = self._panel("混频 1–5 级消耗")
        self.refinement_cost_grid = QGridLayout()
        self.refinement_cost_host.layout().addLayout(self.refinement_cost_grid)
        self._cultivation_layout.addWidget(self.refinement_cost_host)
        self._cultivation_layout.addStretch(1)

    def set_data(
        self,
        detail: ForkCatalogDetail,
        characters: tuple[CharacterSummary, ...],
        metadata: ForkCatalogMetadata,
    ) -> None:
        self._detail = detail
        self._characters = characters
        self.progression_controls.set_detail(detail)
        self._level = 80
        self._stage = 6
        self._refinement = 1
        del metadata
        self.level_slider.blockSignals(True)
        self.level_slider.setValue(80)
        self.level_slider.blockSignals(False)
        summary = detail.summary
        self.name.setText(summary.name_zh)
        self.hero_more_info.set_text(f"正式 ID：{summary.fork_id}")
        self.quality_badge.setText(self._item_names.quality_name(summary.quality))
        self.type_badge.setText(summary.fork_type_name_zh or "类型未提供")
        campaign = self._display_campaigns.get(summary.fork_id)
        campaign_title = campaign.title.display_name if campaign else "首发弧盘"
        self.release_badge.setText(campaign_title or "名称暂未提供")
        self.description.setText(
            plain_text(summary.description_zh) or "说明 · 当前正式数据未提供"
        )
        art_path = self._asset_catalog.fork_icon(summary.fork_id)
        pixmap = QPixmap(str(art_path)) if art_path is not None else QPixmap()
        if pixmap.isNull():
            self.art.setText("弧盘大图\n当前正式资源未提供")
            self.art.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        else:
            self.art.setText("")
            self.art.setStyleSheet("")
            self.art.setPixmap(pixmap.scaled(
                176,
                176,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self._render_characters()
        self._render_cultivation_routes()
        self._sync_stage_buttons()
        self._refresh_selection()

    def set_level(self, level: int) -> None:
        self.level_slider.setValue(max(1, min(80, int(level))))

    def _choose_level(self, level: int) -> None:
        self.set_level(level)
        self._refresh_node_states()

    def set_refinement(self, level: int) -> None:
        self._refinement = max(1, min(5, int(level)))
        self._refresh_refinement()
        self._refresh_node_states()

    def _level_changed(self, level: int) -> None:
        self._level = int(level)
        self._sync_stage_buttons()
        self._refresh_selection()

    def _sync_stage_buttons(self) -> None:
        clear_layout(self.stage_layout)
        self._stage_buttons.clear()
        detail = self._detail
        if detail is None:
            return
        rows = self._breakthrough_rows(detail)
        choices = fork_breakthrough_choices(rows, self._level)
        selected = select_fork_breakthrough(
            rows,
            self._level,
            preferred_stage=self._stage,
        )
        self._stage = int(selected["stage"]) if selected is not None else None
        label = QLabel("突破状态", self)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        self.stage_layout.addWidget(label)
        for index, row in enumerate(choices):
            stage = int(row["stage"])
            cap = int(row["max_fork_level"])
            state = "突破前" if index == 0 and len(choices) == 2 else "突破后"
            if len(choices) == 1:
                state = f"阶段 {stage}"
            button = self._node_button(f"{state} · 上限 {cap}")
            button.setProperty("forkStage", stage)
            button.setChecked(stage == self._stage)
            button.clicked.connect(
                lambda _checked=False, value=stage: self._select_stage(value),
            )
            self._stage_buttons.append(button)
            self.stage_layout.addWidget(button)
        self.stage_layout.addStretch(1)

    def _select_stage(self, stage: int) -> None:
        self._stage = int(stage)
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        detail = self._detail
        if detail is None:
            return
        self.level_label.setText(f"Lv.{self._level}")
        template = {
            "upgrade_levels": [
                {
                    "level": row.level,
                    "modifiers": [
                        {"property_id": item.property_id, "value": item.raw_value}
                        for item in row.modifiers
                    ],
                }
                for row in detail.growth_levels
            ],
            "breakthroughs": self._breakthrough_rows(detail),
        }
        self._panel_values = fork_panel_stats(
            template,
            self._level,
            breakthrough_stage=self._stage,
        )
        self._render_panel(detail)
        growth = next(
            (row for row in detail.growth_levels if row.level == self._level),
            None,
        )
        stage = next(
            (row for row in detail.breakthroughs if row.stage == self._stage),
            None,
        )
        stage_text = f"突破 {self._stage} 阶" if self._stage is not None else "突破阶段未提供"
        exp_text = f"升级经验 {growth.need_exp}" if growth is not None else "升级经验未提供"
        material_text = breakthrough_cost_text(stage, self._item_names)
        self.current_level_cost.setText(
            f"Lv.{self._level} · {stage_text} · {exp_text}\n{material_text}\n"
            "逐级升级消耗：当前正式数据未提供；不会伪装为 0。"
        )
        self.current_more_info.set_text(
            breakthrough_raw_id_text(stage, self._item_names),
        )
        self.progression_controls.set_target_state(
            self._level,
            self._stage,
            self._refinement,
        )
        self._refresh_refinement()
        self._refresh_node_states()

    def _render_panel(self, detail: ForkCatalogDetail) -> None:
        clear_layout(self.panel_grid)
        if not self._panel_values:
            self.panel_grid.addWidget(self._gap("面板 · 当前正式数据未提供"), 0, 0)
            return
        names: dict[str, str] = {}
        percent: set[str] = set()
        for modifier in self._all_modifiers(detail):
            names.setdefault(
                modifier.property_id,
                modifier.property_name_zh or modifier.property_id,
            )
            if "%" in modifier.display_value:
                percent.add(modifier.property_id)
        for index, (property_id, value) in enumerate(self._panel_values.items()):
            shown = (
                f"{display_number(value * 100)}%"
                if property_id in percent else display_number(value)
            )
            self.panel_grid.addWidget(
                self._metric(names.get(property_id, property_id), shown),
                index // 2,
                index % 2,
            )
        self.panel_grid.setColumnStretch(0, 1)
        self.panel_grid.setColumnStretch(1, 1)

    def _refresh_refinement(self) -> None:
        detail = self._detail
        if detail is None:
            return
        refinement = next(
            (item for item in detail.refinement_levels if item.level == self._refinement),
            None,
        )
        self.refinement_title.setText(f"混频 {self._refinement}")
        if refinement is None:
            self.refinement_description.setText("弧盘技能 · 当前正式数据未提供")
        else:
            title = refinement.title_zh or "技能标题未提供"
            description = refinement_skill_text(refinement)
            self.refinement_description.setText(f"{title}\n{description}")
        self._render_effects(detail, refinement)

    def _render_effects(
        self,
        detail: ForkCatalogDetail,
        refinement: ForkRefinementLevel | None,
    ) -> None:
        clear_layout(self.effects_layout)
        buffs = tuple(
            item for item in detail.buff_definitions
            if item.refinement_level == self._refinement
        )
        presentation = present_effects(buffs)
        if presentation is None:
            message = (
                "当前混频没有额外效果说明"
                if refinement is not None else "当前混频技能尚未提供"
            )
            self.effects_layout.addWidget(self._gap(message))
        else:
            add_effect_tiles(self.effects_layout, presentation)
        for route in fork_mechanics_catalog_routes(refinement, buffs):
            button = ForkCatalogLinkButton(route.label, route.link, parent=self.effects_host)
            button.link_requested.connect(self.catalog_link_requested)
            self.effects_layout.addWidget(button)

    def _render_characters(self) -> None:
        clear_layout(self.characters_grid)
        self._character_cards.clear()
        detail = self._detail
        if detail is None:
            return
        relation_map: dict[int, set[str]] = {}
        for relation in detail.relations:
            if relation.kind != "character" or not relation.target_id.isdigit():
                continue
            relation_map.setdefault(int(relation.target_id), set()).add(relation.label)
        exclusive_ids = {
            character_id
            for character_id, labels in relation_map.items()
            if any("exclusive_character" in label for label in labels)
        }
        recommended_ids = {
            character_id
            for character_id, labels in relation_map.items()
            if any("cultivation_recommendation" in label for label in labels)
        }
        by_id = {item.character_id: item for item in self._characters}
        owner_names = [by_id[item].name_zh for item in sorted(exclusive_ids) if item in by_id]
        recommended_names = [
            by_id[item].name_zh for item in sorted(recommended_ids) if item in by_id
        ]
        if owner_names:
            self.owner_label.setText(f"专属弧盘 · {'、'.join(owner_names)}")
        elif recommended_names:
            self.owner_label.setText(f"养成推荐 · {'、'.join(recommended_names)}")
        else:
            self.owner_label.setText("通用弧盘 · 无专属角色")
        compatible = tuple(
            item for item in self._characters
            if detail.summary.raw_group_type
            and item.group_type == detail.summary.raw_group_type
        )
        if not compatible:
            self.characters_grid.addWidget(self._gap(
                "适配角色 · 当前正式类型关系未提供",
            ), 0, 0)
            return
        for index, character in enumerate(compatible):
            if character.character_id in exclusive_ids:
                relation = "专属"
            elif character.character_id in recommended_ids:
                relation = "养成推荐"
            else:
                relation = "同类型可用"
            card = ForkCharacterCard(
                character,
                relation_label=relation,
                art_path=self._asset_catalog.character_icon(character.character_id),
                parent=self.characters_host,
            )
            link = fork_character_catalog_link(
                character.character_id,
                owner=character.character_id in exclusive_ids,
            )
            card.clicked.connect(
                lambda _checked=False, value=link: self.catalog_link_requested.emit(value),
            )
            self._character_cards.append(card)
        self._layout_character_cards()

    def _layout_character_cards(self) -> None:
        while self.characters_grid.count():
            self.characters_grid.takeAt(0)
        columns = max(1, min(4, max(1, self.characters_host.width()) // 185))
        for index, card in enumerate(self._character_cards):
            self.characters_grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            self.characters_grid.setColumnStretch(column, 1 if column < columns else 0)

    def _render_cultivation_routes(self) -> None:
        clear_layout(self.breakthrough_grid)
        clear_layout(self.refinement_cost_grid)
        self._breakthrough_cards.clear()
        self._refinement_cost_cards.clear()
        detail = self._detail
        if detail is None:
            return
        for stage in detail.breakthroughs:
            card = self._panel(f"阶段 {stage.stage} · 上限 Lv.{stage.max_fork_level}")
            card.layout().addWidget(self._info_row(
                "消耗",
                breakthrough_cost_text(stage, self._item_names).removeprefix("消耗："),
            ))
            more_info = ForkMoreInfo(card)
            more_info.set_text(breakthrough_raw_id_text(stage, self._item_names))
            card.layout().addWidget(more_info)
            modifier_text = "、".join(
                f"{item.property_name_zh or item.property_id} {item.display_value}"
                for item in stage.modifiers
            ) or "面板修改未提供"
            card.layout().addWidget(self._info_row("突破面板", modifier_text))
            self._breakthrough_cards.append(card)
        for refinement in detail.refinement_levels:
            card = self._panel(f"{'★' * refinement.level} · 混频 {refinement.level}")
            costs = self._item_names.present_raw(refinement.need_gold_raw)
            card.layout().addWidget(self._info_row(
                "消耗",
                self._item_names.player_text(costs) or "暂未提供",
            ))
            more_info = ForkMoreInfo(card)
            raw_ids = self._item_names.raw_id_text(costs)
            more_info.set_text(f"正式 ID：{raw_ids}" if raw_ids else "")
            card.layout().addWidget(more_info)
            card.layout().addWidget(self._info_row(
                "技能",
                refinement.title_zh or "当前正式数据未提供",
            ))
            self._refinement_cost_cards.append(card)
        self._layout_cultivation_cards()

    def _layout_cultivation_cards(self) -> None:
        while self.breakthrough_grid.count():
            self.breakthrough_grid.takeAt(0)
        while self.refinement_cost_grid.count():
            self.refinement_cost_grid.takeAt(0)
        width = max(1, self.breakthrough_host.width())
        breakthrough_columns = 2 if width >= 900 else 1
        refinement_columns = 3 if width >= 900 else 2 if width >= 560 else 1
        for index, card in enumerate(self._breakthrough_cards):
            self.breakthrough_grid.addWidget(
                card,
                index // breakthrough_columns,
                index % breakthrough_columns,
            )
        for index, card in enumerate(self._refinement_cost_cards):
            self.refinement_cost_grid.addWidget(
                card,
                index // refinement_columns,
                index % refinement_columns,
            )
        for column in range(3):
            self.breakthrough_grid.setColumnStretch(
                column, 1 if column < breakthrough_columns else 0,
            )
            self.refinement_cost_grid.setColumnStretch(
                column, 1 if column < refinement_columns else 0,
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._character_cards:
            self._layout_character_cards()
        if self._breakthrough_cards or self._refinement_cost_cards:
            self._layout_cultivation_cards()

    def _request_progression(self) -> None:
        detail = self._detail
        if detail is None:
            return
        current, target = self.progression_controls.states()
        self.progression_requested.emit(build_fork_progression_request(
            detail,
            current=current,
            target=target,
        ))

    def _refresh_node_states(self) -> None:
        for level, button in self._level_nodes.items():
            button.setChecked(level == self._level)
        for level, button in self._refinement_buttons.items():
            button.setChecked(level == self._refinement)
        for button in self._stage_buttons:
            button.setChecked(button.property("forkStage") == self._stage)

    @staticmethod
    def _breakthrough_rows(detail: ForkCatalogDetail) -> list[dict[str, object]]:
        return [
            {
                "stage": row.stage,
                "max_fork_level": row.max_fork_level,
                "modifiers": [
                    {"property_id": item.property_id, "value": item.raw_value}
                    for item in row.modifiers
                ],
            }
            for row in detail.breakthroughs
        ]

    @staticmethod
    def _all_modifiers(detail: ForkCatalogDetail) -> tuple[ForkModifier, ...]:
        return tuple(
            modifier
            for row in (*detail.growth_levels, *detail.breakthroughs)
            for modifier in row.modifiers
        )

    def compatible_character_count(self) -> int:
        return len(self.characters_host.findChildren(ForkCharacterCard))

    def stage_button_count(self) -> int:
        return len(self._stage_buttons)

    def panel_values(self) -> dict[str, float]:
        return dict(self._panel_values)

    @staticmethod
    def _node_button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setProperty("forkNode", True)
        button.setStyleSheet(themed_style(
            "QPushButton[forkNode='true']{background:#161b22;color:#8b949e;"
            "border:1px solid #30363d;border-radius:10px;padding:7px 10px;}"
            "QPushButton[forkNode='true']:checked{"
            "background:rgba(163,113,247,0.20);color:#a371f7;"
            "border:1px solid #a371f7;font-weight:900;}"
        ))
        return button

    @staticmethod
    def _badge(text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(themed_style(
            f"color:{color};background:#0d1117;border:1px solid {color};"
            "border-radius:10px;padding:3px 9px;font-size:10px;font-weight:800"
        ))
        return label

    @staticmethod
    def _panel(title: str) -> QFrame:
        frame = QFrame()
        frame.setProperty("forkInfoPanel", True)
        frame.setStyleSheet(themed_style(
            "QFrame[forkInfoPanel='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        heading = QLabel(title, frame)
        heading.setStyleSheet(themed_style(
            "color:#a371f7;background:transparent;border:none;"
            "font-size:14px;font-weight:900"
        ))
        layout.addWidget(heading)
        return frame

    @staticmethod
    def _metric(title: str, value: str) -> QFrame:
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
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        caption = QLabel(title, row)
        caption.setMinimumWidth(88)
        caption.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:11px"
        ))
        content = QLabel(value, row)
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.setStyleSheet(themed_style(
            "color:#c9d1d9;background:transparent;border:none;font-size:11px"
        ))
        layout.addWidget(caption)
        layout.addWidget(content, 1)
        return row

    @staticmethod
    def _gap(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style(
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:8px;font-size:11px"
        ))
        return label

__all__ = ["ForkProfileView"]
