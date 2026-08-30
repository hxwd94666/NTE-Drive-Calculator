# 游戏资料库角色图鉴独立页面与公开接线工厂。
"""Character-card gallery and profile page for the game data archive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.character_card import CharacterGalleryCard
from src.features.static_catalog.domain_pages.character_profile import CharacterDetailView
from src.features.static_catalog.domain_pages.character_progression import (
    project_progression_result,
)
from src.domain.progression_stamina import ProgressionStaminaResult
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_models import (
    CharacterDetail,
    CharacterPage,
    CharacterSummary,
    CombatLinkPage,
    GrowthPage,
)
from src.services.static_catalog_character_service import StaticCatalogCharacterService


_ELEMENT_ORDER = ("咒", "暗", "相", "灵", "魂", "光", "心灵")
_ACTIVE_CLASSIFICATIONS = {"available_character", "available_avatar_variant"}


@dataclass(frozen=True, slots=True)
class CharacterProfileData:
    detail: CharacterDetail
    growth: GrowthPage
    combat: CombatLinkPage


class CharacterCatalogPageController:
    """Own synchronous release-static requests; widgets only project results."""

    def __init__(self, service: StaticCatalogCharacterService) -> None:
        self._service = service

    def gallery(self) -> CharacterPage:
        return self._service.list_characters(limit=200)

    def profile(self, character_id: int) -> CharacterProfileData | None:
        detail = self._service.get_character_detail(character_id)
        if detail is None:
            return None
        return CharacterProfileData(
            detail=detail,
            growth=self._service.list_growth(character_id, limit=200),
            combat=self._service.list_combat_links(character_id, limit=500),
        )


class CharacterCatalogPage(QWidget):
    """Independent character archive; the shared catalog entry owns navigation."""

    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        controller: CharacterCatalogPageController,
        asset_catalog: GameUiAssetCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterCatalogPage")
        self.setStyleSheet(themed_style(
            "QWidget#characterCatalogPage{background:#0d1117;}"
        ))
        self._controller = controller
        self._asset_catalog = asset_catalog
        self._all_characters: tuple[CharacterSummary, ...] = ()
        self._cards: dict[int, CharacterGalleryCard] = {}
        self._visible_ids: tuple[int, ...] = ()
        self._columns = 0
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        self.gallery_page = self._build_gallery()
        self.detail_view = CharacterDetailView(
            asset_catalog=self._asset_catalog,
            parent=self,
        )
        self.detail_view.back_requested.connect(self.show_gallery)
        self.detail_view.progression_requested.connect(self.progression_requested)
        self.stack.addWidget(self.gallery_page)
        self.stack.addWidget(self.detail_view)
        root.addWidget(self.stack)

    def _build_gallery(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 2, 4, 8)
        root.setSpacing(12)

        hero = QFrame(page)
        hero.setObjectName("characterGalleryHero")
        hero.setStyleSheet(themed_style(
            "QFrame#characterGalleryHero{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:0.76 #161b22,stop:1 #0d1117);"
            "border:1px solid #1f6feb;border-radius:17px;}"
        ))
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(22, 16, 22, 16)
        copy = QVBoxLayout()
        eyebrow = QLabel("CHARACTER ARCHIVE", hero)
        eyebrow.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:10px;font-weight:900;letter-spacing:2px"
        ))
        title = QLabel("角色图鉴", hero)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:27px;font-weight:900"
        ))
        subtitle = QLabel(
            "从人物卡片进入档案，查看正式身份、技能、等级面板、觉醒、好感度与培养路线。",
            hero,
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        hero_layout.addLayout(copy, 1)
        root.addWidget(hero)

        filters = QFrame(page)
        filters.setObjectName("characterGalleryFilters")
        filters.setStyleSheet(themed_style(
            "QFrame#characterGalleryFilters{background:#161b22;"
            "border:1px solid #30363d;border-radius:14px;}"
        ))
        filter_layout = QVBoxLayout(filters)
        filter_layout.setContentsMargins(14, 12, 14, 12)
        filter_layout.setSpacing(8)
        search_row = QHBoxLayout()
        self.search = QLineEdit(filters)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜索正式中文名或 character_id")
        self.search.textChanged.connect(self._apply_filters)
        search_row.addWidget(self.search, 1)
        self.result_count = QLabel("0 位角色", filters)
        self.result_count.setStyleSheet(themed_style(
            "color:#8b949e;font-size:11px;font-weight:700"
        ))
        search_row.addWidget(self.result_count)
        filter_layout.addLayout(search_row)

        availability_row, self.availability_group = self._filter_row(
            filters,
            "状态",
            (
                ("全部", "all", True, ""),
                ("已实装", "active", True, ""),
                ("待登场", "scheduled", True, ""),
                ("战斗形态", "transformation", True, ""),
            ),
        )
        self.availability_group.buttonClicked.connect(self._apply_filters)
        filter_layout.addLayout(availability_row)

        element_options = [("全部", "all", True, "")]
        element_options.extend((value, value, True, "") for value in _ELEMENT_ORDER)
        element_row, self.element_group = self._filter_row(
            filters, "属性", tuple(element_options),
        )
        self.element_group.buttonClicked.connect(self._apply_filters)
        filter_layout.addLayout(element_row)

        quality_row, self.quality_group = self._filter_row(
            filters,
            "品质",
            (
                ("全部", "all", True, ""),
                ("S 级", "S", False, "当前正式数据未提供角色品质"),
                ("A 级", "A", False, "当前正式数据未提供角色品质"),
            ),
        )
        filter_layout.addLayout(quality_row)
        acquisition_row, self.acquisition_group = self._filter_row(
            filters,
            "获取",
            (
                ("全部", "all", True, ""),
                ("常驻", "permanent", False, "当前正式数据未提供常驻/限定分类"),
                ("限定", "limited", False, "当前正式数据未提供常驻/限定分类"),
            ),
        )
        filter_layout.addLayout(acquisition_row)
        unavailable_filters = QLabel(
            "品质（S / A）与常驻 / 限定：当前正式数据未提供，筛选暂不可用。",
            filters,
        )
        unavailable_filters.setWordWrap(True)
        unavailable_filters.setStyleSheet(themed_style(
            "color:#d29922;background:transparent;border:none;font-size:10px"
        ))
        filter_layout.addWidget(unavailable_filters)
        root.addWidget(filters)

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.gallery_host = QWidget(scroll)
        self.card_grid = QGridLayout(self.gallery_host)
        self.card_grid.setContentsMargins(4, 4, 10, 18)
        self.card_grid.setHorizontalSpacing(12)
        self.card_grid.setVerticalSpacing(12)
        self.card_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.gallery_host)
        root.addWidget(scroll, 1)
        return page

    @staticmethod
    def _filter_row(
        parent: QWidget,
        title: str,
        options: tuple[tuple[str, str, bool, str], ...],
    ) -> tuple[QHBoxLayout, QButtonGroup]:
        row = QHBoxLayout()
        label = QLabel(title, parent)
        label.setFixedWidth(46)
        label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:800"
        ))
        row.addWidget(label)
        group = QButtonGroup(parent)
        group.setExclusive(True)
        for index, (text, key, enabled, tooltip) in enumerate(options):
            button = QPushButton(text, parent)
            button.setProperty("filterChip", True)
            button.setProperty("filterKey", key)
            button.setCheckable(True)
            button.setEnabled(enabled)
            button.setToolTip(tooltip)
            button.setStyleSheet(themed_style(
                "QPushButton[filterChip='true']{background:#0d1117;color:#8b949e;"
                "border:1px solid #30363d;border-radius:10px;padding:4px 10px;"
                "font-size:10px;font-weight:700;}"
                "QPushButton[filterChip='true']:checked{background:#1f6feb33;"
                "color:#58a6ff;border-color:#58a6ff;}"
            ))
            button.setChecked(index == 0)
            group.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        return row, group

    def refresh(self) -> None:
        page = self._controller.gallery()
        self._all_characters = page.items
        self._cards = {}
        for summary in page.items:
            card = CharacterGalleryCard(
                summary,
                art_path=self._asset_catalog.character_icon(summary.character_id),
                parent=self.gallery_host,
            )
            card.activated.connect(self.open_character)
            self._cards[summary.character_id] = card
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.search.text().strip().casefold()
        availability = self._checked_key(self.availability_group)
        element = self._checked_key(self.element_group)
        visible = tuple(
            summary.character_id
            for summary in self._all_characters
            if self._matches(summary, query=query, availability=availability, element=element)
        )
        self._visible_ids = visible
        for character_id, card in self._cards.items():
            card.setVisible(character_id in visible)
        self.result_count.setText(f"{len(visible)} 位角色")
        self._relayout_cards(force=True)

    @staticmethod
    def _checked_key(group: QButtonGroup) -> str:
        button = group.checkedButton()
        return str(button.property("filterKey")) if button is not None else "all"

    @staticmethod
    def _matches(
        summary: CharacterSummary,
        *,
        query: str,
        availability: str,
        element: str,
    ) -> bool:
        if query and query not in summary.name_zh.casefold() and query not in str(summary.character_id):
            return False
        if element != "all" and summary.element_label != element:
            return False
        classification = summary.classification or ""
        if availability == "active" and classification not in _ACTIVE_CLASSIFICATIONS:
            return False
        if availability == "scheduled" and classification != "scheduled_character":
            return False
        return not (
            availability == "transformation" and classification != "combat_transformation"
        )

    def _relayout_cards(self, *, force: bool = False) -> None:
        width = max(1, self.width() - 44)
        columns = max(2, min(5, width // 210))
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self.card_grid.count():
            self.card_grid.takeAt(0)
        for index, character_id in enumerate(self._visible_ids):
            self.card_grid.addWidget(
                self._cards[character_id], index // columns, index % columns,
            )
        for column in range(columns):
            self.card_grid.setColumnStretch(column, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout_cards()

    def open_character(self, character_id: int) -> None:
        profile = self._controller.profile(character_id)
        if profile is None:
            return
        self.detail_view.set_data(profile.detail, profile.growth, profile.combat)
        self.stack.setCurrentWidget(self.detail_view)

    def show_gallery(self) -> None:
        self.stack.setCurrentWidget(self.gallery_page)

    def set_progression_result(
        self,
        *,
        target: str,
        result: ProgressionStaminaResult,
    ) -> None:
        """Project a public ProgressionStaminaService result without computing it."""

        projection = project_progression_result(result)
        if target == "skill":
            self.detail_view.skill_view.drawer.set_progression_result(
                projection.text, available=projection.available,
            )
        else:
            self.detail_view.growth_view.set_progression_result(
                projection.text, available=projection.available,
            )


def build_character_catalog_page(
    *,
    service: StaticCatalogCharacterService,
    game_ui_asset_root: str | Path,
    parent: QWidget | None = None,
) -> CharacterCatalogPage:
    """Public factory used by the shared game-catalog composition root."""

    return CharacterCatalogPage(
        controller=CharacterCatalogPageController(service),
        asset_catalog=GameUiAssetCatalog(game_ui_asset_root),
        parent=parent,
    )
