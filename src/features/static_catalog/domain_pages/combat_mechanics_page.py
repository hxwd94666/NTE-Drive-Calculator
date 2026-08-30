# 战斗机制图鉴唯一公开页面与工厂。
"""Unified combat-mechanics catalog page and its public factory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
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
from src.features.static_catalog.contracts import CatalogLink
from src.features.static_catalog.domain_pages.mechanics_counterfactual_panel import (
    MechanicsCounterfactualPanel,
)
from src.features.static_catalog.domain_pages.mechanics_effect_panel import (
    MechanicsEffectPanel,
)
from src.features.static_catalog.domain_pages.mechanics_formula_panel import (
    MechanicsFormulaPanel,
)
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    MechanicsGalleryCard,
    pill,
    status_pill,
)
from src.services.static_catalog_mechanics_service import (
    FAMILY_BY_KEY,
    MechanicsCard,
    MechanicsDetail,
    StaticCatalogMechanicsService,
    decode_record,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


CatalogNavigator = Callable[[CatalogLink], None]


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        widget = item.widget()
        if child_layout is not None:
            _clear_layout(child_layout)
        if widget is not None:
            widget.deleteLater()


class CombatMechanicsCatalogPage(QWidget):
    """One entry for public mechanisms; object-owned records redirect outward."""

    def __init__(
        self,
        *,
        service: StaticCatalogMechanicsService,
        game_ui_asset_root: str | Path,
        open_catalog_link: CatalogNavigator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("combatMechanicsCatalogPage")
        self.setStyleSheet(themed_style(
            "QWidget#combatMechanicsCatalogPage{background:#0d1117;}"
        ))
        self._service = service
        self._game_ui_asset_root = Path(game_ui_asset_root)
        self._external_navigator = open_catalog_link
        self._family_key = "attributes"
        self._cards: tuple[MechanicsCard, ...] = ()
        self._card_widgets: list[MechanicsGalleryCard] = []
        self._columns = 0
        self._history: list[str] = []
        self.current_record_id: str | None = None
        self._build()
        self.select_family(self._family_key)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        self.gallery_page = self._build_gallery_page()
        self.detail_page = self._build_detail_page()
        self.stack.addWidget(self.gallery_page)
        self.stack.addWidget(self.detail_page)
        root.addWidget(self.stack)

    def _build_gallery_page(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(5, 3, 5, 6)
        root.setSpacing(8)

        control = QFrame(page)
        control.setObjectName("mechanicsControlDeck")
        control.setMaximumHeight(112)
        control.setStyleSheet(themed_style(
            "QFrame#mechanicsControlDeck{background:#161b22;"
            "border:1px solid #30363d;border-radius:14px;}"
        ))
        deck = QVBoxLayout(control)
        deck.setContentsMargins(12, 8, 12, 8)
        deck.setSpacing(6)
        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.gallery_title = QLabel("战斗机制图鉴", control)
        self.gallery_title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:17px;font-weight:900;"
        ))
        self.gallery_subtitle = QLabel("效果 · 公式 · 反事实覆盖", control)
        self.gallery_subtitle.setStyleSheet(themed_style(
            "color:#8b949e;font-size:9px;font-weight:700;"
        ))
        title_box.addWidget(self.gallery_title)
        title_box.addWidget(self.gallery_subtitle)
        heading.addLayout(title_box)
        heading.addStretch(1)
        self.search = QLineEdit(control)
        self.search.setObjectName("mechanicsSearch")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜索效果、变量或机制")
        self.search.setMaximumWidth(360)
        self.search.setFixedHeight(30)
        self.search.textChanged.connect(self._schedule_refresh)
        heading.addWidget(self.search, 1)
        self.result_count = QLabel("0 项", control)
        self.result_count.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px;font-weight:800;"
        ))
        heading.addWidget(self.result_count)
        deck.addLayout(heading)

        self.family_scroll = QScrollArea(control)
        self.family_scroll.setObjectName("mechanicsFamilyStrip")
        self.family_scroll.setWidgetResizable(True)
        self.family_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.family_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.family_scroll.setFrameShape(QFrame.NoFrame)
        self.family_scroll.setFixedHeight(39)
        family_host = QWidget(self.family_scroll)
        family_layout = QHBoxLayout(family_host)
        family_layout.setContentsMargins(0, 0, 0, 0)
        family_layout.setSpacing(5)
        self.family_group = QButtonGroup(self)
        self.family_group.setExclusive(True)
        self.family_buttons: dict[str, QPushButton] = {}
        for family in self._service.families:
            button = QPushButton(f"{family.glyph}  {family.title}", family_host)
            button.setObjectName("mechanicsFamilyButton")
            button.setProperty("familyKey", family.key)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(family.subtitle)
            button.setStyleSheet(themed_style(
                "QPushButton#mechanicsFamilyButton{color:#8b949e;background:#0d1117;"
                "border:1px solid #30363d;border-radius:9px;padding:5px 9px;"
                "font-size:9px;font-weight:800;}"
                "QPushButton#mechanicsFamilyButton:hover{color:#c9d1d9;"
                "border-color:#58a6ff;}QPushButton#mechanicsFamilyButton:checked{"
                "color:#f0f6fc;background:#1f6feb;border-color:#58a6ff;}"
            ))
            button.clicked.connect(
                lambda _checked=False, key=family.key: self.select_family(key)
            )
            family_layout.addWidget(button)
            self.family_group.addButton(button)
            self.family_buttons[family.key] = button
        family_layout.addStretch(1)
        self.family_scroll.setWidget(family_host)
        deck.addWidget(self.family_scroll)
        self.family_combo = QComboBox(control)
        self.family_combo.setObjectName("mechanicsFamilyCombo")
        self.family_combo.setFixedHeight(32)
        for family in self._service.families:
            self.family_combo.addItem(f"{family.glyph}  {family.title}", family.key)
        self.family_combo.currentIndexChanged.connect(self._select_combo_family)
        self.family_combo.hide()
        deck.addWidget(self.family_combo)
        root.addWidget(control)

        self.gallery_scroll = QScrollArea(page)
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setFrameShape(QFrame.NoFrame)
        self.gallery_host = QWidget(self.gallery_scroll)
        self.gallery_grid = QGridLayout(self.gallery_host)
        self.gallery_grid.setContentsMargins(3, 2, 3, 8)
        self.gallery_grid.setHorizontalSpacing(9)
        self.gallery_grid.setVerticalSpacing(9)
        self.gallery_grid.setAlignment(Qt.AlignTop)
        self.gallery_scroll.setWidget(self.gallery_host)
        root.addWidget(self.gallery_scroll, 1)
        return page

    def _build_detail_page(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(7, 5, 7, 7)
        root.setSpacing(8)
        top = QHBoxLayout()
        self.back_button = QPushButton("‹  返回图鉴", page)
        self.back_button.setObjectName("mechanicsBackButton")
        self.back_button.setCursor(Qt.PointingHandCursor)
        self.back_button.setStyleSheet(themed_style(
            "QPushButton#mechanicsBackButton{color:#58a6ff;background:#161b22;"
            "border:1px solid #30363d;border-radius:9px;padding:6px 10px;"
            "font-size:10px;font-weight:800;}"
        ))
        self.back_button.clicked.connect(self.go_back)
        top.addWidget(self.back_button)
        top.addStretch(1)
        root.addLayout(top)

        self.detail_scroll = QScrollArea(page)
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        self.detail_host = QWidget(self.detail_scroll)
        self.detail_layout = QVBoxLayout(self.detail_host)
        self.detail_layout.setContentsMargins(3, 2, 3, 10)
        self.detail_layout.setSpacing(10)
        self.detail_scroll.setWidget(self.detail_host)
        root.addWidget(self.detail_scroll, 1)
        return page

    def select_family(self, family_key: str) -> None:
        if family_key not in FAMILY_BY_KEY:
            raise ValueError(f"未知机制家族：{family_key!r}")
        self._family_key = family_key
        self.family_buttons[family_key].setChecked(True)
        combo_index = self.family_combo.findData(family_key)
        if combo_index >= 0 and self.family_combo.currentIndex() != combo_index:
            self.family_combo.blockSignals(True)
            self.family_combo.setCurrentIndex(combo_index)
            self.family_combo.blockSignals(False)
        self._refresh_gallery()
        self.stack.setCurrentWidget(self.gallery_page)
        self.current_record_id = None

    def _schedule_refresh(self) -> None:
        QTimer.singleShot(0, self._refresh_gallery)

    def _select_combo_family(self, index: int) -> None:
        family_key = self.family_combo.itemData(index)
        if family_key and family_key != self._family_key:
            self.select_family(str(family_key))

    def _refresh_gallery(self) -> None:
        self._cards = self._service.browse(self._family_key, self.search.text())
        self.result_count.setText(f"{len(self._cards)} 项")
        self._card_widgets = []
        _clear_layout(self.gallery_grid)
        if not self._cards:
            empty = QLabel("没有匹配的公共机制；唯一归属内容请到对应对象页查看。", self.gallery_host)
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(themed_style(
                "color:#8b949e;font-size:11px;padding:36px;"
            ))
            self.gallery_grid.addWidget(empty, 0, 0)
            self._columns = 1
            return
        for card in self._cards:
            widget = MechanicsGalleryCard(card, self.gallery_host)
            widget.activated.connect(self.open_record)
            self._card_widgets.append(widget)
        self._reflow_cards(force=True)

    def _reflow_cards(self, *, force: bool = False) -> None:
        if not self._card_widgets:
            return
        width = max(260, self.gallery_scroll.viewport().width() - 12)
        columns = max(1, min(4, width // 270))
        if columns == self._columns and not force:
            return
        self._columns = columns
        while self.gallery_grid.count():
            self.gallery_grid.takeAt(0)
        for index, card in enumerate(self._card_widgets):
            self.gallery_grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            self.gallery_grid.setColumnStretch(column, 1 if column < columns else 0)

    def open_record(self, record_id: str, *, remember: bool = True) -> None:
        kind, key = decode_record(record_id)
        if kind == "search":
            self.search.setText(key)
            self.stack.setCurrentWidget(self.gallery_page)
            return
        if remember and self.current_record_id:
            self._history.append(self.current_record_id)
        detail = self._service.detail(record_id)
        self._render_detail(detail)
        self.current_record_id = record_id
        self.stack.setCurrentWidget(self.detail_page)
        self.detail_scroll.verticalScrollBar().setValue(0)

    def _render_detail(self, detail: MechanicsDetail) -> None:
        _clear_layout(self.detail_layout)
        self.detail_layout.addWidget(self._detail_hero(detail))
        if detail.card_kind == "effect":
            panel = MechanicsEffectPanel(detail, self.open_link, self.detail_host)
        elif detail.card_kind == "formula":
            panel = MechanicsFormulaPanel(detail, self.open_link, self.detail_host)
        else:
            panel = MechanicsCounterfactualPanel(detail, self.open_link, self.detail_host)
        self.detail_layout.addWidget(panel)
        self.detail_layout.addStretch(1)

    def _detail_hero(self, detail: MechanicsDetail) -> QFrame:
        hero = QFrame(self.detail_host)
        hero.setObjectName("mechanicsDetailHero")
        hero.setStyleSheet(themed_style(
            "QFrame#mechanicsDetailHero{background:#161b22;"
            "border:1px solid #30363d;border-radius:15px;}"
        ))
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(7)
        top = QHBoxLayout()
        family = FAMILY_BY_KEY[detail.family_key]
        eyebrow = QLabel(f"{family.glyph}  {family.title}", hero)
        eyebrow.setStyleSheet(themed_style(
            f"color:{family.accent};font-size:9px;font-weight:900;letter-spacing:1px;"
        ))
        top.addWidget(eyebrow)
        top.addStretch(1)
        if detail.status:
            top.addWidget(status_pill(detail.status, hero))
        layout.addLayout(top)
        title = QLabel(detail.title, hero)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:20px;font-weight:900;"
        ))
        layout.addWidget(title)
        if detail.card_kind != "formula":
            subtitle = QLabel(detail.subtitle, hero)
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:10px;"))
            layout.addWidget(subtitle)
        badges = QHBoxLayout()
        for text in detail.badges:
            badges.addWidget(pill(text, color="#8b949e", parent=hero))
        badges.addStretch(1)
        layout.addLayout(badges)
        return hero

    def open_link(self, link: CatalogLink) -> None:
        if link.domain_key == "combat_mechanics":
            self.open_record(link.record_id)
            return
        self._external_navigator(link)

    def go_back(self) -> None:
        if self._history:
            self.open_record(self._history.pop(), remember=False)
            return
        self.stack.setCurrentWidget(self.gallery_page)
        self.current_record_id = None

    def show_gallery(self) -> None:
        self._history.clear()
        self.stack.setCurrentWidget(self.gallery_page)
        self.current_record_id = None

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        narrow = event.size().width() < 720
        self.family_scroll.setVisible(not narrow)
        self.family_combo.setVisible(narrow)
        self.gallery_subtitle.setVisible(not narrow)
        self.search.setPlaceholderText("搜索机制" if narrow else "搜索效果、变量或机制")
        QTimer.singleShot(0, self._reflow_cards)

    def dispose(self) -> None:
        self._history.clear()


def build_combat_mechanics_catalog_page(
    *,
    database_path: str | Path,
    game_ui_asset_root: str | Path,
    open_catalog_link: CatalogNavigator,
    terminology_service: StaticCatalogTerminologyService | None = None,
    parent: QWidget | None = None,
) -> CombatMechanicsCatalogPage:
    """Build the single public combat-mechanics catalog entry."""
    return CombatMechanicsCatalogPage(
        service=StaticCatalogMechanicsService(
            database_path,
            terminology_service=terminology_service,
        ),
        game_ui_asset_root=game_ui_asset_root,
        open_catalog_link=open_catalog_link,
        parent=parent,
    )


__all__ = ["CombatMechanicsCatalogPage", "build_combat_mechanics_catalog_page"]
