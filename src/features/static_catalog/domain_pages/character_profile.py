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
from src.features.static_catalog.domain_pages.character_more_info import (
    build_more_info,
)
from src.features.static_catalog.domain_pages.character_terminology import (
    project_character_term,
)
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadata,
)
from src.features.static_catalog.domain_pages.character_skills import (
    CharacterSkillView,
    build_action_cards,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_models import (
    CharacterDetail,
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
    back_requested = Signal()
    progression_requested = Signal(object)
    catalog_link_requested = Signal(object)

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
        navigation = QHBoxLayout()
        back = QPushButton("‹  返回角色图鉴", self)
        back.setObjectName("characterBackButton")
        back.clicked.connect(self.back_requested)
        navigation.addWidget(back)
        navigation.addStretch(1)
        root.addLayout(navigation)

        self.hero = QFrame(self)
        self.hero.setObjectName("characterProfileHero")
        self.hero.setMinimumHeight(192)
        self.hero.setMaximumHeight(214)
        self.hero.setStyleSheet(themed_style(
            "QFrame#characterProfileHero{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:0.68 #161b22,stop:1 #0d1117);"
            "border:1px solid #1f6feb;border-radius:18px;}"
        ))
        self.hero_grid = QGridLayout(self.hero)
        self.hero_grid.setContentsMargins(12, 10, 12, 10)
        self.hero_grid.setHorizontalSpacing(10)
        self.hero_grid.setVerticalSpacing(8)

        self.art_panel = QFrame(self.hero)
        self.art_panel.setObjectName("characterFullArtPanel")
        self.art_panel.setMinimumWidth(190)
        self.art_panel.setStyleSheet(themed_style(
            "QFrame#characterFullArtPanel{background:#0d1117;"
            "border:1px dashed #30363d;border-radius:13px;}"
        ))
        art_layout = QVBoxLayout(self.art_panel)
        art_layout.setContentsMargins(10, 9, 10, 9)
        art_caption = QLabel("完整立绘", self.art_panel)
        art_caption.setStyleSheet(themed_style(
            "color:#58a6ff;background:transparent;border:none;"
            "font-size:10px;font-weight:900"
        ))
        self.art = QLabel("当前正式资源未提供", self.art_panel)
        self.art.setObjectName("characterFullArt")
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setWordWrap(True)
        self.art.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:11px"
        ))
        art_layout.addWidget(art_caption)
        art_layout.addWidget(self.art, 1)

        self.identity_panel = QFrame(self.hero)
        self.identity_panel.setObjectName("characterIdentityPanel")
        self.identity_panel.setStyleSheet(themed_style(
            "QFrame#characterIdentityPanel{background:transparent;border:none;}"
        ))
        identity_layout = QHBoxLayout(self.identity_panel)
        identity_layout.setContentsMargins(2, 0, 2, 0)
        identity_layout.setSpacing(12)
        avatar_shell = QFrame(self.identity_panel)
        avatar_shell.setObjectName("characterAvatarShell")
        avatar_shell.setFixedSize(116, 116)
        avatar_shell.setStyleSheet(themed_style(
            "QFrame#characterAvatarShell{background:#0d1117;"
            "border:1px solid #58a6ff;border-radius:15px;}"
        ))
        avatar_layout = QVBoxLayout(avatar_shell)
        avatar_layout.setContentsMargins(5, 5, 5, 5)
        self.avatar = QLabel(avatar_shell)
        self.avatar.setObjectName("characterFormalAvatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_layout.addWidget(self.avatar)
        identity_layout.addWidget(avatar_shell)

        copy = QVBoxLayout()
        copy.setSpacing(7)
        self.eyebrow = QLabel("CHARACTER ARCHIVE", self.identity_panel)
        self.eyebrow.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:10px;font-weight:900;letter-spacing:2px"
        ))
        self.name = QLabel("选择角色", self.identity_panel)
        self.name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:28px;font-weight:900"
        ))
        self.identity = QLabel("—", self.identity_panel)
        self.identity.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self.identity.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:12px;font-weight:700"
        ))
        badges = QHBoxLayout()
        self.element_badge = self._badge("属性未提供", "#58a6ff")
        self.availability_badge = self._badge("获取未提供", "#3fb950")
        self.quality_badge = self._badge("品质未提供", "#d29922")
        badges.addWidget(self.element_badge)
        badges.addWidget(self.availability_badge)
        badges.addWidget(self.quality_badge)
        badges.addStretch(1)
        self.description = QLabel(
            "角色描述 · 当前正式数据未提供", self.identity_panel,
        )
        self.description.setWordWrap(True)
        self.description.setStyleSheet(themed_style(
            "color:#8b949e;font-size:12px;line-height:1.45"
        ))
        copy.addWidget(self.eyebrow)
        copy.addWidget(self.name)
        copy.addWidget(self.identity)
        copy.addLayout(badges)
        copy.addWidget(self.description)
        identity_layout.addLayout(copy, 1)

        self.fact_panel = QFrame(self.hero)
        self.fact_panel.setObjectName("characterQuickFacts")
        self.fact_panel.setStyleSheet(themed_style(
            "QFrame#characterQuickFacts{background:#161b22;"
            "border:1px solid #30363d;border-radius:13px;}"
        ))
        fact_grid = QGridLayout(self.fact_panel)
        fact_grid.setContentsMargins(10, 9, 10, 9)
        fact_grid.setHorizontalSpacing(8)
        fact_grid.setVerticalSpacing(7)
        self.fact_values: dict[str, QLabel] = {}
        for index, title in enumerate(("属性", "品质", "获取", "上线")):
            tile = self._fact_tile(title)
            self.fact_values[title] = tile.findChild(QLabel, "characterFactValue")
            fact_grid.addWidget(tile, index // 2, index % 2)
        fact_grid.setColumnStretch(0, 1)
        fact_grid.setColumnStretch(1, 1)
        self._layout_hero(False)
        root.addWidget(self.hero)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("characterProfileTabs")
        self.overview_host, self.overview_layout = self._scroll_tab()
        self.skill_view = CharacterSkillView(
            terminology=terminology,
            parent=self,
        )
        self.skill_view.progression_requested.connect(self.progression_requested)
        self.skill_view.catalog_link_requested.connect(
            self.catalog_link_requested,
        )
        self.growth_view = CharacterGrowthView(self)
        self.growth_view.progression_requested.connect(self.progression_requested)
        self.awakening_view = CharacterAwakeningView(
            terminology=terminology,
            parent=self,
        )
        self.awakening_view.catalog_link_requested.connect(
            self.catalog_link_requested,
        )
        self.affinity_host, self.affinity_layout = self._scroll_tab()
        self.route_view = CharacterBuildView(
            terminology=terminology,
            parent=self,
        )
        self.route_view.catalog_link_requested.connect(
            self.catalog_link_requested,
        )
        self.tabs.addTab(self.overview_host, "角色档案")
        self.tabs.addTab(self.skill_view, "技能")
        self.tabs.addTab(self.growth_view, "等级与养成")
        self.tabs.addTab(self.awakening_view, "觉醒")
        self.tabs.addTab(self.affinity_host, "好感度")
        self.tabs.addTab(self.route_view, "图纸与毕业")
        root.addWidget(self.tabs, 1)

    @staticmethod
    def _fact_tile(title: str) -> QFrame:
        tile = QFrame()
        tile.setProperty("characterFactTile", True)
        tile.setStyleSheet(themed_style(
            "QFrame[characterFactTile='true']{background:#10243f;"
            "border:1px solid #1f6feb;border-radius:9px;}"
        ))
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(1)
        caption = QLabel(title, tile)
        caption.setStyleSheet(themed_style(
            "color:#8b949e;background:transparent;border:none;font-size:9px"
        ))
        value = QLabel("当前正式数据未提供", tile)
        value.setObjectName("characterFactValue")
        value.setWordWrap(True)
        value.setStyleSheet(themed_style(
            "color:#f0f6fc;background:transparent;border:none;"
            "font-size:11px;font-weight:800"
        ))
        layout.addWidget(caption)
        layout.addWidget(value)
        return tile

    def _layout_hero(self, compact: bool) -> None:
        for widget in (self.art_panel, self.identity_panel, self.fact_panel):
            self.hero_grid.removeWidget(widget)
        if compact:
            self.hero.setMinimumHeight(318)
            self.hero.setMaximumHeight(342)
            self.hero_grid.addWidget(self.art_panel, 0, 0)
            self.hero_grid.addWidget(self.identity_panel, 0, 1)
            self.hero_grid.addWidget(self.fact_panel, 1, 0, 1, 2)
            self.hero_grid.setColumnStretch(0, 2)
            self.hero_grid.setColumnStretch(1, 3)
        else:
            self.hero.setMinimumHeight(192)
            self.hero.setMaximumHeight(214)
            self.hero_grid.addWidget(self.art_panel, 0, 0)
            self.hero_grid.addWidget(self.identity_panel, 0, 1)
            self.hero_grid.addWidget(self.fact_panel, 0, 2)
            self.hero_grid.setColumnStretch(0, 2)
            self.hero_grid.setColumnStretch(1, 5)
            self.hero_grid.setColumnStretch(2, 3)

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
    ) -> None:
        character_id = detail.character.character_id
        if growth.character_id != character_id or combat.character_id != character_id:
            return
        self._detail = detail
        self._growth = growth
        self._release = release
        character = detail.character
        self.name.setText(character.name_zh)
        self.identity.setText(f"正式 character_id  {character.character_id}")
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
        self.art.clear()
        self.art.setText("当前正式资源未提供")
        avatar_path = self._asset_catalog.character_icon(character_id)
        pixmap = QPixmap(str(avatar_path)) if avatar_path is not None else QPixmap()
        if pixmap.isNull():
            self.avatar.setPixmap(QPixmap())
            self.avatar.setText("正式头像\n当前未提供")
            self.avatar.setStyleSheet(themed_style(
                "color:#8b949e;background:transparent;border:none;font-size:10px"
            ))
        else:
            self.avatar.setText("")
            self.avatar.setStyleSheet("")
            self.avatar.setPixmap(pixmap.scaled(
                104,
                104,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self.fact_values["属性"].setText(character.element_label)
        self.fact_values["品质"].setText(
            f"{release.quality} 级"
            if release is not None and release.quality
            else "当前正式数据未提供"
        )
        self.fact_values["获取"].setText(acquisition_label)
        date_prefix = (
            "预计 " if character.classification == "scheduled_character" else ""
        )
        self.fact_values["上线"].setText(
            date_prefix + release.release_date
            if release is not None and release.release_date
            else "当前正式数据未提供"
        )
        self._render_overview(detail, growth, release)
        self.skill_view.set_actions(build_action_cards(
            detail,
            combat.items,
            terminology=self._terminology,
        ))
        self.growth_view.set_data(detail, growth)
        self.awakening_view.set_data(detail)
        self._render_affinity(detail)
        self.route_view.set_data(detail)

    def _render_overview(
        self,
        detail: CharacterDetail,
        growth: GrowthPage,
        release: CharacterReleaseMetadata | None,
    ) -> None:
        self._clear(self.overview_layout)
        metrics = QGridLayout()
        metric_values = (
            ("等级面板", f"{detail.growth_count} 条" if detail.growth_count else "未提供"),
            ("正式技能", f"{detail.character.skill_count} 项"),
            ("觉醒", f"{detail.character.awakening_count} 项"),
            ("毕业模板", "已提供" if detail.character.has_graduation else "未提供"),
        )
        metric_columns = 2 if self._compact else 4
        for index, (title, value) in enumerate(metric_values):
            metrics.addWidget(
                self._metric_card(title, value),
                index // metric_columns,
                index % metric_columns,
            )
        for column in range(metric_columns):
            metrics.setColumnStretch(column, 1)
        self.overview_layout.addLayout(metrics)

        identity = self._panel("身份与定位")
        rows = (
            ("正式 ID", str(detail.character.character_id)),
            ("中文名", detail.character.name_zh),
            ("属性", detail.character.element_label),
            (
                "品质",
                f"{release.quality} 级"
                if release is not None and release.quality
                else "当前正式数据未提供",
            ),
            (
                "获取方式",
                _acquisition_name(release),
            ),
            (
                (
                    "预计上线日期"
                    if detail.character.classification == "scheduled_character"
                    else "上线日期"
                ),
                release.release_date
                if release is not None and release.release_date
                else "当前正式数据未提供",
            ),
        )
        identity_grid = QGridLayout()
        identity_grid.setHorizontalSpacing(8)
        identity_grid.setVerticalSpacing(8)
        identity_columns = 2 if self._compact else 3
        for index, (title, value) in enumerate(rows):
            identity_grid.addWidget(
                self._detail_tile(title, value),
                index // identity_columns,
                index % identity_columns,
            )
        for column in range(identity_columns):
            identity_grid.setColumnStretch(column, 1)
        identity.layout().addLayout(identity_grid)
        self.overview_layout.addWidget(identity)

        panel = self._panel("面板速览")
        panel_grid = QGridLayout()
        panel_grid.setHorizontalSpacing(8)
        panel_grid.setVerticalSpacing(8)
        panel_columns = 2 if self._compact else 3
        panel_index = 0
        for level in (1, 20, 40, 60, 70):
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
            more_rows: list[tuple[str, str | None]] = [
                ("正式修改 ID", bonus.modify_data_id),
            ]
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
                more_rows.extend((
                    (f"{display_name} 属性 ID", item.property_id),
                    (f"{display_name} 修改操作 ID", item.modifier_operation),
                ))
            more_info = build_more_info(more_rows, parent=hero)
            if more_info is not None:
                hero.layout().addWidget(more_info)
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
        card.setMinimumHeight(50)
        card.setStyleSheet(themed_style(
            "QFrame[characterDetailTile='true']{background:#10243f;"
            "border:1px solid #1f6feb;border-radius:10px;}"
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
