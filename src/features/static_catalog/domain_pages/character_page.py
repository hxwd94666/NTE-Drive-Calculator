# 游戏资料库角色图鉴独立页面与公开接线工厂。
"""Character-card gallery and profile page for the game data archive."""

from __future__ import annotations

from collections.abc import Callable
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
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadata,
    CharacterReleaseMetadataService,
)
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
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_ELEMENT_ORDER = ("咒", "暗", "相", "灵", "魂", "光", "心灵")
_ACTIVE_CLASSIFICATIONS = {"available_character", "available_avatar_variant"}
_HIDDEN_CLASSIFICATIONS = {"combat_transformation"}


@dataclass(frozen=True, slots=True)
class CharacterProfileData:
    detail: CharacterDetail
    growth: GrowthPage
    combat: CombatLinkPage
    release: CharacterReleaseMetadata | None
    variants: tuple[CharacterSummary, ...]


class CharacterCatalogPageController:
    """Own synchronous release-static requests; widgets only project results."""

    def __init__(
        self,
        service: StaticCatalogCharacterService,
        release_metadata: CharacterReleaseMetadataService,
    ) -> None:
        self._service = service
        self._release_metadata = release_metadata

    def release_metadata(
        self,
        character_id: int,
    ) -> CharacterReleaseMetadata | None:
        return self._release_metadata.metadata(character_id)

    def acquisition_filter_options(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (
                term.display_name or "名称暂未提供",
                term.requested_id,
            )
            for term in self._release_metadata.acquisition_terms()
        )

    def gallery(self) -> CharacterPage:
        page = self._service.list_characters(limit=200)
        visible_items = tuple(
            item for item in page.items
            if item.classification not in _HIDDEN_CLASSIFICATIONS
        )
        grouped: dict[str, list[CharacterSummary]] = {}
        for item in visible_items:
            logical_key = item.logical_character_key or f"character:{item.character_id}"
            grouped.setdefault(logical_key, []).append(item)
        # One gallery card represents one logical role.  Avatar variants remain
        # selectable inside the detail page and keep their official IDs/data.
        representatives = tuple(
            max(variants, key=lambda item: item.character_id)
            for variants in grouped.values()
        )
        items = tuple(sorted(
            representatives,
            key=lambda item: (
                (
                    metadata.release_date
                    if (
                        (metadata := self.release_metadata(item.character_id))
                        is not None
                        and metadata.release_date
                    )
                    else ""
                ),
                -item.character_id,
            ),
            reverse=True,
        ))
        return CharacterPage(
            dataset=page.dataset,
            query=page.query,
            offset=page.offset,
            limit=page.limit,
            total=len(items),
            items=items,
        )

    def variants(self, character_id: int) -> tuple[CharacterSummary, ...]:
        page = self._service.list_characters(limit=200)
        selected = next(
            (item for item in page.items if item.character_id == character_id),
            None,
        )
        if selected is None:
            return ()
        logical_key = selected.logical_character_key or f"character:{character_id}"
        return tuple(sorted(
            (
                item for item in page.items
                if (
                    item.logical_character_key
                    or f"character:{item.character_id}"
                ) == logical_key
                and item.classification not in _HIDDEN_CLASSIFICATIONS
            ),
            key=lambda item: item.character_id,
        ))

    def profile(self, character_id: int) -> CharacterProfileData | None:
        detail = self._service.get_character_detail(character_id)
        if detail is None:
            return None
        return CharacterProfileData(
            detail=detail,
            growth=self._service.list_growth(character_id, limit=200),
            combat=self._service.list_combat_links(character_id, limit=500),
            release=self.release_metadata(character_id),
            variants=self.variants(character_id),
        )


class CharacterCatalogPage(QWidget):
    """Independent character archive; the shared catalog entry owns navigation."""

    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        controller: CharacterCatalogPageController,
        asset_catalog: GameUiAssetCatalog,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterCatalogPage")
        self.setStyleSheet(themed_style(
            "QWidget#characterCatalogPage{background:#0d1117;}"
        ))
        self._controller = controller
        self._asset_catalog = asset_catalog
        self._terminology = terminology
        self._all_characters: tuple[CharacterSummary, ...] = ()
        self._cards: dict[int, CharacterGalleryCard] = {}
        self._visible_ids: tuple[int, ...] = ()
        self._active_character_id: int | None = None
        self._catalog_navigation_listener: Callable[[], None] | None = None
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
            terminology=self._terminology,
            parent=self,
        )
        self.detail_view.variant_requested.connect(self.open_character)
        self.detail_view.progression_requested.connect(self.progression_requested)
        self.stack.addWidget(self.gallery_page)
        self.stack.addWidget(self.detail_view)
        root.addWidget(self.stack)

    def _build_gallery(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 2, 4, 6)
        root.setSpacing(8)

        filters = QFrame(page)
        filters.setObjectName("characterGalleryFilters")
        filters.setStyleSheet(themed_style(
            "QFrame#characterGalleryFilters{background:#161b22;"
            "border:1px solid #30363d;border-radius:14px;}"
        ))
        filter_layout = QVBoxLayout(filters)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(5)
        search_row = QHBoxLayout()
        self.search = QLineEdit(filters)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜索正式中文名或 character_id")
        self.search.textChanged.connect(self._apply_filters)
        search_row.addWidget(self.search, 1)
        self.sort_hint = QLabel("上线时间 ↓", filters)
        self.sort_hint.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:10px;font-weight:800"
        ))
        search_row.addWidget(self.sort_hint)
        self.result_count = QLabel("0 位角色", filters)
        self.result_count.setStyleSheet(themed_style(
            "color:#8b949e;font-size:11px;font-weight:700"
        ))
        search_row.addWidget(self.result_count)
        filter_layout.addLayout(search_row)

        summary_row = QHBoxLayout()
        self.filter_summary = QLabel("全部角色 · 全属性 · 全品质 · 全获取", filters)
        self.filter_summary.setObjectName("characterFilterSummary")
        self.filter_summary.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:700"
        ))
        summary_row.addWidget(self.filter_summary, 1)
        self.filter_toggle = QPushButton("展开筛选  ▾", filters)
        self.filter_toggle.setObjectName("characterFilterToggle")
        self.filter_toggle.setCheckable(True)
        self.filter_toggle.setStyleSheet(themed_style(
            "QPushButton#characterFilterToggle{background:#0d1117;color:#58a6ff;"
            "border:1px solid #30363d;border-radius:10px;padding:4px 12px;"
            "font-size:10px;font-weight:800;}"
            "QPushButton#characterFilterToggle:checked{border-color:#58a6ff;}"
        ))
        self.filter_toggle.toggled.connect(self._set_filter_expanded)
        summary_row.addWidget(self.filter_toggle)
        filter_layout.addLayout(summary_row)

        self.filter_body = QWidget(filters)
        self.filter_body.setObjectName("characterFilterBody")
        body_layout = QVBoxLayout(self.filter_body)
        body_layout.setContentsMargins(0, 2, 0, 0)
        body_layout.setSpacing(5)

        availability_row, self.availability_group = self._filter_row(
            filters,
            "上线",
            (
                ("全部", "all", True, ""),
                ("已上线", "active", True, ""),
                ("待上线", "scheduled", True, ""),
            ),
        )
        self.availability_group.buttonClicked.connect(self._apply_filters)
        body_layout.addLayout(availability_row)

        element_options = [("全部", "all", True, "")]
        element_options.extend((value, value, True, "") for value in _ELEMENT_ORDER)
        element_row, self.element_group = self._filter_row(
            filters, "属性", tuple(element_options),
        )
        self.element_group.buttonClicked.connect(self._apply_filters)
        body_layout.addLayout(element_row)

        quality_row, self.quality_group = self._filter_row(
            filters,
            "品质",
            (
                ("全部", "all", True, ""),
                ("S 级", "S", True, ""),
                ("A 级", "A", True, ""),
            ),
        )
        self.quality_group.buttonClicked.connect(self._apply_filters)
        body_layout.addLayout(quality_row)
        acquisition_options = [("全部", "all", True, "")]
        acquisition_options.extend(
            (label, stable_key, True, "")
            for label, stable_key in self._controller.acquisition_filter_options()
        )
        acquisition_row, self.acquisition_group = self._filter_row(
            filters,
            "获取",
            tuple(acquisition_options),
        )
        self.acquisition_group.buttonClicked.connect(self._apply_filters)
        body_layout.addLayout(acquisition_row)
        filter_layout.addWidget(self.filter_body)
        self.filter_body.setVisible(False)
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
        self.empty_label = QLabel("没有匹配的角色", self.gallery_host)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(themed_style(
            "color:#8b949e;background:#161b22;border:1px dashed #30363d;"
            "border-radius:14px;padding:28px;font-weight:700"
        ))
        scroll.setWidget(self.gallery_host)
        root.addWidget(scroll, 1)
        return page

    def _set_filter_expanded(self, expanded: bool) -> None:
        self.filter_body.setVisible(expanded)
        self.filter_toggle.setText(
            "收起筛选  ▴" if expanded else "展开筛选  ▾"
        )

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
            metadata = self._controller.release_metadata(summary.character_id)
            variants = self._controller.variants(summary.character_id)
            card = CharacterGalleryCard(
                summary,
                art_path=self._asset_catalog.character_icon(summary.character_id),
                variant_art_paths=tuple(
                    (
                        variant.character_id,
                        self._asset_catalog.character_icon(variant.character_id),
                    )
                    for variant in variants
                ),
                release_metadata=metadata,
                parent=self.gallery_host,
            )
            card.activated.connect(self.open_character)
            self._cards[summary.character_id] = card
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.search.text().strip().casefold()
        availability = self._checked_key(self.availability_group)
        element = self._checked_key(self.element_group)
        quality = self._checked_key(self.quality_group)
        acquisition = self._checked_key(self.acquisition_group)
        visible = tuple(
            summary.character_id
            for summary in self._all_characters
            if self._matches(
                summary,
                query=query,
                availability=availability,
                element=element,
                quality=quality,
                acquisition=acquisition,
            )
        )
        self._visible_ids = visible
        for character_id, card in self._cards.items():
            card.setVisible(character_id in visible)
        self.result_count.setText(f"{len(visible)} 位角色")
        self.filter_summary.setText(" · ".join((
            self._checked_text(self.availability_group, "全部角色"),
            self._checked_text(self.element_group, "全属性"),
            self._checked_text(self.quality_group, "全品质"),
            self._checked_text(self.acquisition_group, "全获取"),
        )))
        self._relayout_cards(force=True)

    @staticmethod
    def _checked_key(group: QButtonGroup) -> str:
        button = group.checkedButton()
        return str(button.property("filterKey")) if button is not None else "all"

    @staticmethod
    def _checked_text(group: QButtonGroup, all_text: str) -> str:
        button = group.checkedButton()
        if button is None or button.property("filterKey") == "all":
            return all_text
        return button.text()

    def _matches(
        self,
        summary: CharacterSummary,
        *,
        query: str,
        availability: str,
        element: str,
        quality: str,
        acquisition: str,
    ) -> bool:
        if summary.classification in _HIDDEN_CLASSIFICATIONS:
            return False
        metadata = self._controller.release_metadata(summary.character_id)
        variant_ids = tuple(
            item.character_id
            for item in self._controller.variants(summary.character_id)
        )
        if (
            query
            and query not in summary.name_zh.casefold()
            and not any(query in str(character_id) for character_id in variant_ids)
        ):
            return False
        if element != "all" and summary.element_label != element:
            return False
        if quality != "all" and (
            metadata is None or metadata.quality != quality
        ):
            return False
        if acquisition != "all" and (
            metadata is None or metadata.acquisition_type != acquisition
        ):
            return False
        classification = summary.classification or ""
        if availability == "active" and classification not in _ACTIVE_CLASSIFICATIONS:
            return False
        if availability == "scheduled" and classification != "scheduled_character":
            return False
        return True

    def _relayout_cards(self, *, force: bool = False) -> None:
        width = max(1, self.width() - 44)
        columns = max(2, min(5, width // 204))
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self.card_grid.count():
            self.card_grid.takeAt(0)
        self.empty_label.setVisible(False)
        scheduled_ids = tuple(
            character_id for character_id in self._visible_ids
            if self._cards[character_id].summary.classification
            == "scheduled_character"
        )
        active_ids = tuple(
            character_id for character_id in self._visible_ids
            if character_id not in scheduled_ids
        )
        display_ids = (*scheduled_ids, *active_ids)
        for index, character_id in enumerate(display_ids):
            self.card_grid.addWidget(
                self._cards[character_id],
                index // columns,
                index % columns,
            )
        self.empty_label.setVisible(not self._visible_ids)
        if not self._visible_ids:
            self.card_grid.addWidget(self.empty_label, 0, 0, 1, columns)
        for column in range(columns):
            self.card_grid.setColumnStretch(column, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout_cards()

    def open_character(self, character_id: int) -> None:
        profile = self._controller.profile(character_id)
        if profile is None:
            return
        self.detail_view.set_data(
            profile.detail,
            profile.growth,
            profile.combat,
            profile.release,
            profile.variants,
        )
        self._active_character_id = profile.detail.character.character_id
        self.stack.setCurrentWidget(self.detail_view)
        self._notify_catalog_navigation_changed()

    def show_gallery(self) -> None:
        self._active_character_id = None
        self.stack.setCurrentWidget(self.gallery_page)
        self._notify_catalog_navigation_changed()

    def set_catalog_navigation_listener(
        self,
        listener: Callable[[], None],
    ) -> None:
        """Let the shared catalog shell refresh its single back action."""

        self._catalog_navigation_listener = listener
        listener()

    def catalog_back_label(self) -> str | None:
        if self.stack.currentWidget() is self.detail_view:
            return "角色列表"
        return None

    def catalog_go_back(self) -> bool:
        if self.catalog_back_label() is None:
            return False
        self.show_gallery()
        return True

    def _notify_catalog_navigation_changed(self) -> None:
        if self._catalog_navigation_listener is not None:
            self._catalog_navigation_listener()

    def set_progression_result(
        self,
        *,
        target: str,
        character_id: int,
        result: ProgressionStaminaResult,
        skill_id: str | None = None,
    ) -> bool:
        """Project only a result matching the currently visible frozen identity."""

        if int(character_id) != self._active_character_id:
            return False
        if target == "skill":
            if (
                not skill_id
                or str(skill_id)
                != self.detail_view.skill_training_view.active_skill_id()
            ):
                return False
        elif target != "character_level":
            return False
        projection = project_progression_result(
            result,
            terminology=self._terminology,
        )
        if target == "skill":
            self.detail_view.skill_training_view.set_progression_result(
                projection.text,
                available=projection.available,
                more_info=projection.more_info,
            )
        else:
            self.detail_view.growth_view.set_progression_result(
                projection.text,
                available=projection.available,
                more_info=projection.more_info,
            )
        return True


def build_character_catalog_page(
    *,
    service: StaticCatalogCharacterService,
    release_metadata_service: CharacterReleaseMetadataService,
    game_ui_asset_root: str | Path,
    terminology_service: StaticCatalogTerminologyService | None = None,
    parent: QWidget | None = None,
) -> CharacterCatalogPage:
    """Public factory used by the shared game-catalog composition root."""

    page = CharacterCatalogPage(
        controller=CharacterCatalogPageController(
            service,
            release_metadata_service,
        ),
        asset_catalog=GameUiAssetCatalog(game_ui_asset_root),
        terminology=terminology_service,
        parent=parent,
    )
    return page
