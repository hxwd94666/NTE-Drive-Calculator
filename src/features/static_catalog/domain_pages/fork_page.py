# 构建“游戏资料库 → 弧盘图鉴”的卡片墙与独立公开 factory。
"""Game-styled fork catalog page without public navigation mutations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QMouseEvent, QPixmap, QResizeEvent
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
from src.domain.progression_stamina import ProgressionStaminaResult
from src.domain.static_catalog_terminology import LocalizedForkCampaign
from src.features.static_catalog.domain_pages.fork_profile import ForkProfileView
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_models import CharacterSummary
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_fork_release_metadata import (
    ForkItemDisplayNameService,
    sort_fork_catalog,
)
from src.services.static_catalog_fork_service import (
    ForkCatalogSummary,
    ForkCatalogType,
    StaticCatalogForkService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)


_QUALITY_COLORS = {"ORANGE": "#f0883e", "PURPLE": "#a371f7", "BLUE": "#58a6ff"}


class ForkOwnedResources:
    """Close every owner once, retaining only failed callbacks for retry."""

    def __init__(self, callbacks: tuple[Callable[[], object], ...]) -> None:
        self._pending = list(callbacks)

    @property
    def closed(self) -> bool:
        return not self._pending

    def close_all(self) -> None:
        failures: list[Exception] = []
        pending: list[Callable[[], object]] = []
        for callback in self._pending:
            try:
                callback()
            except Exception as exc:
                failures.append(exc)
                pending.append(callback)
        self._pending = pending
        if failures:
            raise ExceptionGroup("弧盘图鉴资源关闭失败", failures)


class ForkGalleryCard(QFrame):
    activated = Signal(str)

    def __init__(
        self,
        summary: ForkCatalogSummary,
        *,
        art_path: Path | None,
        quality_name: str,
        campaign: LocalizedForkCampaign | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.summary = summary
        color = _QUALITY_COLORS.get(summary.quality, "#8b949e")
        self.setObjectName(f"forkGalleryCard_{summary.fork_id}")
        self.setProperty("forkGalleryCard", True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(168, 224)
        self.setMaximumHeight(250)
        self.setStyleSheet(themed_style(
            "QFrame[forkGalleryCard='true']{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #10243f,stop:0.58 #161b22,stop:1 #0d1117);"
            "border:1px solid #30363d;border-radius:16px;}"
            "QFrame[forkGalleryCard='true']:hover,"
            "QFrame[forkGalleryCard='true']:focus{"
            f"border:2px solid {color};background:#1c2128;}}"
        ))
        self._build(art_path, color, quality_name, campaign)

    def _build(
        self,
        art_path: Path | None,
        color: str,
        quality_name: str,
        campaign: LocalizedForkCampaign | None,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 9, 11, 10)
        root.setSpacing(5)
        badges = QHBoxLayout()
        quality = QLabel(quality_name, self)
        quality.setStyleSheet(themed_style(
            f"color:{color};background:#0d1117;border:1px solid {color};"
            "border-radius:9px;padding:2px 8px;font-size:10px;font-weight:900"
        ))
        fork_type = QLabel(self.summary.fork_type_name_zh or "未分类", self)
        fork_type.setStyleSheet(themed_style(
            "color:#c9d1d9;background:#21262d;border:1px solid #30363d;"
            "border-radius:9px;padding:2px 8px;font-size:10px;font-weight:800"
        ))
        badges.addWidget(quality)
        badges.addWidget(fork_type)
        badges.addStretch(1)
        campaign_title = campaign.title.display_name if campaign else "首发"
        limited = QLabel(
            campaign_title or "名称暂未提供",
            self,
        )
        limited.setStyleSheet(themed_style(
            "color:#d29922;font-size:10px;font-weight:800"
            if campaign else "color:#6e7681;font-size:10px;font-weight:700"
        ))
        badges.addWidget(limited)
        root.addLayout(badges)

        art = QLabel(self)
        art.setFixedHeight(132)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(art_path)) if art_path is not None else QPixmap()
        if pixmap.isNull():
            art.setText("弧盘图标\n当前正式资源未提供")
            art.setStyleSheet(themed_style(
                "color:#6e7681;background:#0d1117;border:1px dashed #30363d;"
                "border-radius:12px;font-size:11px"
            ))
        else:
            art.setPixmap(pixmap.scaled(
                126,
                126,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        root.addWidget(art)
        name = QLabel(self.summary.name_zh, self)
        name.setWordWrap(True)
        name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        root.addWidget(name)
        root.addStretch(1)
        for widget in (quality, fork_type, limited, art, name):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.summary.fork_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.activated.emit(self.summary.fork_id)
            event.accept()
            return
        super().keyPressEvent(event)


class ForkCatalogPage(QWidget):
    """Own gallery filters and discardable fork-detail projections only."""

    catalog_link_requested = Signal(object)
    progression_requested = Signal(object)

    def __init__(
        self,
        *,
        fork_service: StaticCatalogForkService,
        character_service: StaticCatalogCharacterService,
        asset_catalog: GameUiAssetCatalog,
        item_name_service: ForkItemDisplayNameService,
        campaigns: tuple[LocalizedForkCampaign, ...],
        close_callbacks: tuple[Callable[[], object], ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("forkCatalogPage")
        self._fork_service = fork_service
        self._character_service = character_service
        self._asset_catalog = asset_catalog
        self._item_name_service = item_name_service
        self._campaigns = tuple(campaigns)
        grouped_campaigns: dict[str, list[LocalizedForkCampaign]] = {}
        for campaign in self._campaigns:
            grouped_campaigns.setdefault(campaign.featured_fork_id, []).append(campaign)
        self._campaigns_by_featured_fork = {
            fork_id: tuple(sorted(
                rows,
                key=lambda item: item.release_ordinal,
                reverse=True,
            ))
            for fork_id, rows in grouped_campaigns.items()
        }
        self._display_campaign_by_fork = {
            fork_id: rows[0]
            for fork_id, rows in self._campaigns_by_featured_fork.items()
        }
        self._owned_resources = ForkOwnedResources(close_callbacks)
        self._disposed = False
        self._last_dispose_error: ExceptionGroup | None = None
        self._active_fork_id: str | None = None
        self._metadata = fork_service.metadata()
        self._summaries = sort_fork_catalog(
            fork_service.list_forks(page_size=200).items,
            self._campaigns,
        )
        self._types = fork_service.list_types()
        self._characters = character_service.list_characters(limit=200).items
        self._type_filter: int | None = None
        self._quality_filter: str | None = None
        self._search_query = ""
        self._visible: tuple[ForkCatalogSummary, ...] = self._summaries
        self._cards: list[ForkGalleryCard] = []
        self._gallery_sections: list[
            tuple[QLabel, tuple[ForkGalleryCard, ...]]
        ] = []
        self._gallery_columns = 0
        self._build()
        self._render_gallery()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_gallery())
        self.profile_view = ForkProfileView(
            asset_catalog=self._asset_catalog,
            item_name_service=self._item_name_service,
            display_campaigns=self._display_campaign_by_fork,
            parent=self._stack,
        )
        self.profile_view.back_requested.connect(self.show_gallery)
        self.profile_view.catalog_link_requested.connect(self.catalog_link_requested)
        self.profile_view.progression_requested.connect(self.progression_requested)
        self._stack.addWidget(self.profile_view)
        root.addWidget(self._stack)

    def _build_gallery(self) -> QWidget:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(10, 8, 10, 12)
        layout.setSpacing(9)
        hero = QFrame(host)
        hero.setObjectName("forkCatalogHero")
        hero.setStyleSheet(themed_style(
            "QFrame#forkCatalogHero{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:0.7 #161b22,stop:1 #0d1117);"
            "border:1px solid #a371f7;border-radius:17px;}"
        ))
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 12, 20, 12)
        eyebrow = QLabel("NTE · FORK ARCHIVE", hero)
        eyebrow.setStyleSheet(themed_style(
            "color:#a371f7;font-size:10px;font-weight:900;letter-spacing:2px"
        ))
        title = QLabel("弧盘图鉴", hero)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:25px;font-weight:900"
        ))
        subtitle = QLabel(
            "限定特刊优先，点击卡片查看等级面板、混频技能、特效关系和完整养成路线。",
            hero,
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)
        layout.addWidget(hero)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit(host)
        self.search_edit.setObjectName("forkCatalogSearch")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText("搜索弧盘名称或 ID")
        self.search_edit.textChanged.connect(self.set_search_query)
        toolbar.addWidget(self.search_edit, 1)
        self.filter_toggle = QPushButton("筛选  ▾", host)
        self.filter_toggle.setCheckable(True)
        self.filter_toggle.setObjectName("forkFilterToggle")
        self.filter_toggle.clicked.connect(self._toggle_filters)
        toolbar.addWidget(self.filter_toggle)
        self._count_label = QLabel("", host)
        self._count_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        self._count_label.setMinimumWidth(72)
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        toolbar.addWidget(self._count_label)
        layout.addLayout(toolbar)

        self.filter_panel = self._build_filter_panel(host)
        self.filter_panel.setVisible(False)
        layout.addWidget(self.filter_panel)

        self._gallery_scroll = QScrollArea(host)
        self._gallery_scroll.setWidgetResizable(True)
        self._gallery_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._gallery_host = QWidget(self._gallery_scroll)
        self._gallery = QGridLayout(self._gallery_host)
        self._gallery.setContentsMargins(2, 2, 8, 18)
        self._gallery.setHorizontalSpacing(12)
        self._gallery.setVerticalSpacing(12)
        self._gallery_scroll.setWidget(self._gallery_host)
        layout.addWidget(self._gallery_scroll, 1)
        return host

    def _build_filter_panel(self, parent: QWidget) -> QFrame:
        panel = QFrame(parent)
        panel.setObjectName("forkFilterPanel")
        panel.setStyleSheet(themed_style(
            "QFrame#forkFilterPanel{background:#161b22;border:1px solid #30363d;"
            "border-radius:12px;}"
        ))
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 9, 12, 9)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        quality_label = QLabel("品质", panel)
        type_label = QLabel("类型", panel)
        for label in (quality_label, type_label):
            label.setStyleSheet(themed_style(
                "color:#8b949e;font-size:11px;font-weight:900"
            ))
        grid.addWidget(quality_label, 0, 0)
        grid.addWidget(type_label, 1, 0)

        self._quality_group = QButtonGroup(self)
        self._quality_group.setExclusive(True)
        quality_options = (("全部", None),) + tuple(
            (self._item_name_service.quality_name(quality), quality)
            for quality in ("ORANGE", "PURPLE", "BLUE")
        )
        for column, (text, quality) in enumerate(quality_options, start=1):
            button = self._filter_button(text, panel)
            button.clicked.connect(
                lambda _checked=False, value=quality: self.set_quality_filter(value),
            )
            self._quality_group.addButton(button)
            grid.addWidget(button, 0, column)
            if quality is None:
                button.setChecked(True)

        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        type_options = (("全部", None), *(
            (item.name_zh, item.fork_type_id) for item in self._types
        ))
        for column, (text, fork_type_id) in enumerate(type_options, start=1):
            button = self._filter_button(text, panel)
            button.clicked.connect(
                lambda _checked=False, value=fork_type_id: self.set_type_filter(value),
            )
            self._type_group.addButton(button)
            grid.addWidget(button, 1, column)
            if fork_type_id is None:
                button.setChecked(True)
        for column in range(1, 7):
            grid.setColumnStretch(column, 1)
        return panel

    @staticmethod
    def _filter_button(label: str, parent: QWidget) -> QPushButton:
        button = QPushButton(label, parent)
        button.setCheckable(True)
        button.setProperty("forkTypeFilter", True)
        button.setStyleSheet(themed_style(
            "QPushButton[forkTypeFilter='true']{background:#161b22;color:#8b949e;"
            "border:1px solid #30363d;border-radius:12px;padding:5px 12px;}"
            "QPushButton[forkTypeFilter='true']:checked{"
            "background:rgba(163,113,247,0.20);"
            "color:#a371f7;border:1px solid #a371f7;font-weight:900;}"
        ))
        return button

    def _render_gallery(self) -> None:
        for heading, cards in self._gallery_sections:
            heading.deleteLater()
            for card in cards:
                card.deleteLater()
        self._gallery_sections.clear()
        while self._gallery.count():
            self._gallery.takeAt(0)
        self._cards.clear()
        query = self._search_query.casefold()
        self._visible = tuple(
            item for item in self._summaries
            if self._type_filter is None or item.fork_type_id == self._type_filter
            if self._quality_filter is None or item.quality == self._quality_filter
            if not query or query in item.name_zh.casefold() or query in item.fork_id.casefold()
        )
        self._count_label.setText(f"{len(self._visible)} / {len(self._summaries)}")
        active_count = int(self._type_filter is not None) + int(
            self._quality_filter is not None
        )
        self.filter_toggle.setText(
            f"筛选{' · ' + str(active_count) if active_count else ''}  "
            f"{'▴' if self.filter_toggle.isChecked() else '▾'}"
        )
        limited = tuple(
            item for item in self._visible
            if item.fork_id in self._campaigns_by_featured_fork
        )
        regular = tuple(
            item for item in self._visible
            if item.fork_id not in self._campaigns_by_featured_fork
        )
        if limited:
            self._create_section("限定特刊 · 新到旧", limited)
        if regular:
            self._create_section("首发弧盘 · 品质 / 类型 / 名称", regular)
        if not self._visible:
            empty = QLabel("没有找到符合条件的弧盘", self._gallery_host)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(themed_style(
                "color:#8b949e;background:#161b22;border:1px dashed #30363d;"
                "border-radius:14px;padding:32px;font-size:13px"
            ))
            self._gallery_sections.append((empty, ()))
        self._layout_sections(force=True)

    def _create_section(
        self,
        title: str,
        summaries: tuple[ForkCatalogSummary, ...],
    ) -> None:
        heading = QLabel(title, self._gallery_host)
        heading.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:14px;font-weight:900;margin-top:6px"
        ))
        cards: list[ForkGalleryCard] = []
        for summary in summaries:
            card = ForkGalleryCard(
                summary,
                art_path=self._asset_catalog.fork_icon(summary.fork_id),
                quality_name=self._item_name_service.quality_name(summary.quality),
                campaign=self._display_campaign_by_fork.get(summary.fork_id),
                parent=self._gallery_host,
            )
            card.activated.connect(self.open_fork)
            self._cards.append(card)
            cards.append(card)
        self._gallery_sections.append((heading, tuple(cards)))

    def _layout_sections(self, *, force: bool = False) -> None:
        columns = self._column_count()
        if not force and columns == self._gallery_columns:
            return
        self._gallery_columns = columns
        while self._gallery.count():
            self._gallery.takeAt(0)
        row = 0
        for heading, cards in self._gallery_sections:
            self._gallery.addWidget(heading, row, 0, 1, columns)
            row += 1
            for index, card in enumerate(cards):
                self._gallery.addWidget(card, row + index // columns, index % columns)
            row += (len(cards) + columns - 1) // columns
        for column in range(6):
            self._gallery.setColumnStretch(column, 1 if column < columns else 0)
        self._gallery.setRowStretch(row, 1)

    def _column_count(self) -> int:
        width = self._gallery_scroll.viewport().width()
        return max(1, min(5, max(1, width) // 210))

    def _toggle_filters(self, expanded: bool) -> None:
        self.filter_panel.setVisible(bool(expanded))
        self._render_gallery()

    def set_search_query(self, query: str) -> None:
        self._search_query = str(query).strip()
        self._render_gallery()

    def set_type_filter(self, fork_type_id: int | None) -> None:
        self._type_filter = int(fork_type_id) if fork_type_id is not None else None
        self._render_gallery()

    def set_quality_filter(self, quality: str | None) -> None:
        self._quality_filter = str(quality) if quality is not None else None
        self._render_gallery()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._layout_sections)

    def open_fork(self, fork_id: str) -> None:
        detail = self._fork_service.get_fork(str(fork_id))
        if detail is None:
            return
        self.profile_view.set_data(detail, self._characters, self._metadata)
        self._active_fork_id = detail.summary.fork_id
        self._stack.setCurrentIndex(1)

    def show_gallery(self) -> None:
        """Leave the detail identity before exposing the gallery again."""

        self._active_fork_id = None
        self._stack.setCurrentIndex(0)

    def set_progression_result(
        self,
        *,
        fork_id: str,
        result: ProgressionStaminaResult,
    ) -> bool:
        """Project a shared result only onto the matching active fork."""

        if str(fork_id) != self._active_fork_id:
            return False
        self.profile_view.progression_controls.set_progression_result(result)
        return True

    def visible_summaries(self) -> tuple[ForkCatalogSummary, ...]:
        return self._visible

    def fork_types(self) -> tuple[ForkCatalogType, ...]:
        return self._types

    def characters(self) -> tuple[CharacterSummary, ...]:
        return self._characters

    def gallery_column_count(self) -> int:
        return self._gallery_columns

    def image_coverage(self) -> tuple[int, int]:
        resolved = sum(
            self._asset_catalog.fork_icon(item.fork_id) is not None
            for item in self._summaries
        )
        return resolved, len(self._summaries)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._active_fork_id = None
        try:
            self._owned_resources.close_all()
        except ExceptionGroup as exc:
            self._last_dispose_error = exc
            raise
        self._last_dispose_error = None
        self._disposed = self._owned_resources.closed

    @property
    def last_dispose_error(self) -> ExceptionGroup | None:
        return self._last_dispose_error

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self.dispose()
        except ExceptionGroup:
            event.ignore()
            return
        super().closeEvent(event)


def build_fork_catalog_page(
    *,
    database_path: str | Path,
    game_ui_asset_root: str | Path,
    terminology_service: StaticCatalogTerminologyService,
    parent: QWidget | None = None,
) -> ForkCatalogPage:
    """Build the standalone page while keeping all SQL inside existing DAOs."""

    fork_service = StaticCatalogForkService.from_database(database_path)
    character_queries: StaticCatalogCharacterQueries | None = None
    try:
        character_queries = StaticCatalogCharacterQueries(database_path)
        character_service = StaticCatalogCharacterService(character_queries)
        asset_catalog = GameUiAssetCatalog(game_ui_asset_root)
        campaigns = terminology_service.list_fork_campaigns()
        return ForkCatalogPage(
            fork_service=fork_service,
            character_service=character_service,
            asset_catalog=asset_catalog,
            item_name_service=ForkItemDisplayNameService(terminology_service),
            campaigns=campaigns,
            close_callbacks=(fork_service.close, character_queries.close),
            parent=parent,
        )
    except Exception:
        if character_queries is not None:
            character_queries.close()
        fork_service.close()
        raise


__all__ = [
    "ForkCatalogPage",
    "ForkGalleryCard",
    "ForkOwnedResources",
    "build_fork_catalog_page",
]
