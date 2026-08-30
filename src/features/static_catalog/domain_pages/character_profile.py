# 角色图鉴详情：大立绘、档案、技能、等级、觉醒、好感与培养路线。
"""Game-styled character profile assembled from immutable catalog DTOs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
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
from src.features.static_catalog.domain_pages.character_builds import (
    CharacterAwakeningView,
    CharacterBuildView,
)
from src.features.static_catalog.domain_pages.character_growth import CharacterGrowthView
from src.features.static_catalog.domain_pages.character_terminology import (
    project_character_term,
)
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadata,
)
from src.features.static_catalog.domain_pages.character_skills import (
    CharacterSkillTrainingView,
    CharacterSkillView,
    build_action_cards,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_models import (
    CharacterDetail,
    CharacterSummary,
    CombatLinkPage,
    GrowthPage,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


def _number(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _acquisition_name(
    release: CharacterReleaseMetadata | None,
) -> str:
    if (
        release is not None
        and release.acquisition_term is not None
        and release.acquisition_term.display_name
    ):
        return release.acquisition_term.display_name
    return "名称暂未提供"


class CharacterDetailView(QWidget):
    variant_requested = Signal(int)
    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        asset_catalog: GameUiAssetCatalog,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterDetailView")
        self.setStyleSheet(themed_style(
            "QWidget#characterDetailView{background:#0d1117;}"
        ))
        self._asset_catalog = asset_catalog
        self._terminology = terminology
        self._detail: CharacterDetail | None = None
        self._growth: GrowthPage | None = None
        self._release: CharacterReleaseMetadata | None = None
        self._compact = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        self.hero = QFrame(self)
        self.hero.setObjectName("characterProfileHero")
        self.hero.setMinimumHeight(152)
        self.hero.setMaximumHeight(160)
        self.hero.setStyleSheet(themed_style(
            "QFrame#characterProfileHero{background:transparent;border:none;}"
        ))
        self.hero_grid = QGridLayout(self.hero)
        self.hero_grid.setContentsMargins(12, 10, 12, 10)
        self.hero_grid.setHorizontalSpacing(10)
        self.hero_grid.setVerticalSpacing(8)

        self.art_panel = QFrame(self.hero)
        self.art_panel.setObjectName("characterFullArtPanel")
        self.art_panel.setFixedWidth(122)
        self.art_panel.setStyleSheet(themed_style(
            "QFrame#characterFullArtPanel{background:transparent;border:none;}"
        ))
        art_layout = QVBoxLayout(self.art_panel)
        art_layout.setContentsMargins(2, 2, 2, 2)
        self.art = QLabel("立绘未提供", self.art_panel)
        self.art.setObjectName("characterFullArt")
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setWordWrap(True)
        self.art.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:11px"
        ))
        art_layout.addWidget(self.art, 1)

        self.identity_panel = QFrame(self.hero)
        self.identity_panel.setObjectName("characterIdentityPanel")
        self.identity_panel.setStyleSheet(themed_style(
            "QFrame#characterIdentityPanel{background:transparent;border:none;}"
        ))
        identity_layout = QHBoxLayout(self.identity_panel)
        identity_layout.setContentsMargins(2, 0, 2, 0)
        identity_layout.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        self.eyebrow = QLabel("角色图鉴", self.identity_panel)
        self.eyebrow.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:10px;font-weight:900"
        ))
        self.name = QLabel("选择角色", self.identity_panel)
        self.name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:25px;font-weight:900"
        ))
        self.identity = QLabel("—", self.identity_panel)
        self.identity.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self.identity.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:12px;font-weight:700"
        ))
        self.release_line = QLabel("上线信息未提供", self.identity_panel)
        self.release_line.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:700"
        ))
        badges = QHBoxLayout()
        self.element_badge = self._badge("属性未提供", "#58a6ff")
        self.availability_badge = self._badge("获取未提供", "#3fb950")
        self.quality_badge = self._badge("品质未提供", "#d29922")
        badges.addWidget(self.element_badge)
        badges.addWidget(self.availability_badge)
        badges.addWidget(self.quality_badge)
        badges.addStretch(1)
        self.variant_row = QHBoxLayout()
        self.variant_row.setSpacing(6)
        copy.addWidget(self.eyebrow)
        copy.addWidget(self.name)
        copy.addWidget(self.identity)
        copy.addWidget(self.release_line)
        copy.addLayout(badges)
        copy.addLayout(self.variant_row)
        identity_layout.addLayout(copy, 1)
        self._layout_hero(False)
        root.addWidget(self.hero)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("characterProfileTabs")
        self.overview_host, self.overview_layout = self._scroll_tab()
        self.skill_view = CharacterSkillView(
            terminology=terminology,
            parent=self,
        )
        self.cultivation_tabs = QTabWidget(self)
        self.cultivation_tabs.setObjectName("characterCultivationTabs")
        self.growth_view = CharacterGrowthView(self.cultivation_tabs)
        self.growth_view.progression_requested.connect(self.progression_requested)
        self.skill_training_view = CharacterSkillTrainingView(
            terminology=terminology,
            parent=self.cultivation_tabs,
        )
        self.skill_training_view.progression_requested.connect(
            self.progression_requested
        )
        self.cultivation_tabs.addTab(self.growth_view, "等级养成")
        self.cultivation_tabs.addTab(self.skill_training_view, "技能养成")
        self.awakening_view = CharacterAwakeningView(
            terminology=terminology,
            parent=self,
        )
        self.affinity_host, self.affinity_layout = self._scroll_tab()
        self.route_view = CharacterBuildView(
            terminology=terminology,
            parent=self,
        )
        self.tabs.addTab(self.overview_host, "角色档案")
        self.tabs.addTab(self.skill_view, "技能")
        self.tabs.addTab(self.cultivation_tabs, "养成")
        self.tabs.addTab(self.awakening_view, "觉醒")
        self.tabs.addTab(self.affinity_host, "好感度")
        self.tabs.addTab(self.route_view, "图纸与毕业")
        root.addWidget(self.tabs, 1)

    def _layout_hero(self, compact: bool) -> None:
        for widget in (self.art_panel, self.identity_panel):
            self.hero_grid.removeWidget(widget)
        if compact:
            self.hero.setMinimumHeight(152)
            self.hero.setMaximumHeight(160)
            self.hero_grid.addWidget(self.art_panel, 0, 0)
            self.hero_grid.addWidget(self.identity_panel, 0, 1)
            self.hero_grid.setColumnStretch(0, 2)
            self.hero_grid.setColumnStretch(1, 3)
        else:
            self.hero.setMinimumHeight(152)
            self.hero.setMaximumHeight(160)
            self.hero_grid.addWidget(self.art_panel, 0, 0)
            self.hero_grid.addWidget(self.identity_panel, 0, 1)
            self.hero_grid.setColumnStretch(0, 2)
            self.hero_grid.setColumnStretch(1, 8)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        compact = event.size().width() < 820
        if compact == self._compact:
            return
        self._compact = compact
        self._layout_hero(compact)
        if self._detail is not None and self._growth is not None:
            self._render_overview(
                self._detail,
                self._growth,
                self._release,
            )

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
        release: CharacterReleaseMetadata | None,
        variants: tuple[CharacterSummary, ...] = (),
    ) -> None:
        character_id = detail.character.character_id
        if growth.character_id != character_id or combat.character_id != character_id:
            return
        self._detail = detail
        self._growth = growth
        self._release = release
        character = detail.character
        self.name.setText(character.name_zh)
        self.identity.setText(f"ID  {character.character_id}")
        self.element_badge.setText(f"{character.element_label}属性")
        acquisition_label = _acquisition_name(release)
        if character.classification == "scheduled_character":
            acquisition_label += " · 待上线"
        self.availability_badge.setText(acquisition_label)
        self.quality_badge.setText(
            f"{release.quality} 级"
            if release is not None and release.quality
            else "品质未提供"
        )
        if release is not None and release.release_date:
            prefix = (
                "预计上线 "
                if character.classification == "scheduled_character"
                else "上线 "
            )
            self.release_line.setText(prefix + release.release_date)
        else:
            self.release_line.setText("上线信息未提供")
        self.art.clear()
        art_path = self._asset_catalog.character_art(character_id)
        pixmap = QPixmap(str(art_path)) if art_path is not None else QPixmap()
        if pixmap.isNull():
            self.art.setPixmap(QPixmap())
            self.art.setText("立绘未提供")
        else:
            self.art.setText("")
            self.art.setPixmap(pixmap.scaled(
                116,
                126,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self._render_variants(character_id, variants)
        self._render_overview(detail, growth, release)
        actions = build_action_cards(
            detail,
            combat.items,
            terminology=self._terminology,
        )
        self.skill_view.set_actions(actions)
        self.skill_training_view.set_actions(actions)
        self.growth_view.set_data(detail, growth)
        self.awakening_view.set_data(detail)
        self._render_affinity(detail)
        self.route_view.set_data(detail)

    def _render_variants(
        self,
        active_character_id: int,
        variants: tuple[CharacterSummary, ...],
    ) -> None:
        self._clear(self.variant_row)
        if len(variants) <= 1:
            return
        for index, variant in enumerate(variants, start=1):
            character_id = variant.character_id
            actor_path = str(variant.actor_path or "").casefold()
            if "female" in actor_path:
                label = "女性形象"
            elif "male" in actor_path:
                label = "男性形象"
            else:
                label = f"形象 {index}"
            button = QPushButton(label, self.identity_panel)
            button.setCheckable(True)
            button.setChecked(character_id == active_character_id)
            button.setEnabled(character_id != active_character_id)
            button.clicked.connect(
                lambda _checked=False, target=character_id: self.variant_requested.emit(target)
            )
            self.variant_row.addWidget(button)
        self.variant_row.addStretch(1)

    def _render_overview(
        self,
        detail: CharacterDetail,
        growth: GrowthPage,
        release: CharacterReleaseMetadata | None,
    ) -> None:
        self._clear(self.overview_layout)
        summary = QLabel(
            f"{detail.character.skill_count} 项技能  ·  "
            f"{detail.character.awakening_count} 项觉醒  ·  "
            + ("有毕业模板" if detail.character.has_graduation else "暂无毕业模板"),
        )
        summary.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;font-size:11px"
        ))
        self.overview_layout.addWidget(summary)

        panel = self._panel("面板速览")
        panel_grid = QGridLayout()
        panel_grid.setHorizontalSpacing(8)
        panel_grid.setVerticalSpacing(8)
        panel_columns = 2 if self._compact else 3
        panel_index = 0
        for level in (1, 20, 40, 60, 70, 80):
            points = tuple(item for item in growth.items if item.level == level)
            point = next((item for item in points if item.state == "breakthrough_after"), points[-1] if points else None)
            if point is None:
                continue
            panel_grid.addWidget(self._detail_tile(
                f"Lv.{level}",
                f"生命 {_number(point.hp_base)}  ·  攻击 {_number(point.atk_base)}  ·  防御 {_number(point.def_base)}",
            ), panel_index // panel_columns, panel_index % panel_columns)
            panel_index += 1
        for column in range(panel_columns):
            panel_grid.setColumnStretch(column, 1)
        panel.layout().addLayout(panel_grid)
        if not growth.items:
            panel.layout().addWidget(self._muted("当前正式数据未提供等级面板"))
        self.overview_layout.addWidget(panel)

        self.overview_layout.addStretch(1)

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
                display_name = (
                    item.display_name
                    if item.display_name != item.property_id
                    else project_character_term(
                        self._terminology,
                        entity_kind="attribute",
                        stable_id=item.property_id,
                        identity_label="属性",
                    ).display_name
                )
                hero.layout().addWidget(self._info_row(
                    display_name,
                    f"{_number(value)}{suffix}",
                ))
            self.affinity_layout.addWidget(hero)
        self.affinity_layout.addStretch(1)

    @staticmethod
    def _panel(title: str) -> QFrame:
        frame = QFrame()
        frame.setProperty("characterInfoPanel", True)
        frame.setStyleSheet(themed_style(
            "QFrame[characterInfoPanel='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
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
            "font-size:16px;font-weight:900"
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
        card.setMinimumHeight(42)
        card.setStyleSheet(themed_style(
            "QFrame[characterDetailTile='true']{background:#161b22;"
            "border:none;border-radius:8px;}"
        ))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(2)
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
