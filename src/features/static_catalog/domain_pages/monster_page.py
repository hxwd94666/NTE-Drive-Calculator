# 游戏资料库怪物与玩法的独立游戏化页面与公开接线工厂。
"""Card-based monster and encounter archive backed only by public DTOs."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import Qt
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.monster_browse_models import (
    PLAY_COPY as _PLAY_COPY,
    PLAY_LABELS as _PLAY_LABELS,
    BrowseCard,
    BrowseSection,
    BrowseState,
    group_entries as _group,
    home_badge as _home_badge,
    key_parts as _key_parts,
    object_name as _object_name,
    period_label as _period_label,
    profile_parts as _profile_parts,
    witch_blessing_state as _witch_blessing_state,
)
from src.features.static_catalog.domain_pages.monster_detail_view import (
    MonsterContext,
    MonsterDetailView,
)
from src.features.static_catalog.domain_pages.monster_widgets import (
    ArchiveCard,
    clear_layout,
    section_title,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_monster_service import (
    CatalogDetail,
    CatalogEntry,
    CatalogFilter,
    StaticCatalogMonsterService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)

class MonsterCatalogPageController:
    """Own release-static reads and preserve the Service/DAO/UI boundary."""

    def __init__(self, service: StaticCatalogMonsterService) -> None:
        self._service = service
        self._entries: dict[str, tuple[CatalogEntry, ...]] = {}
        self._details: dict[str, CatalogDetail | None] = {}

    def entries_for(self, play_mode: str) -> tuple[CatalogEntry, ...]:
        if play_mode not in self._entries:
            rows: list[CatalogEntry] = []
            offset = 0
            while True:
                page = self._service.list_entries(CatalogFilter(
                    play_mode=play_mode, page_size=200, offset=offset,
                ))
                rows.extend(page.items)
                if not page.has_more:
                    break
                offset += len(page.items)
            if play_mode == "high_risk":
                rows = [row for row in rows if self._has_formal_pool(row)]
            self._entries[play_mode] = tuple(rows)
        return self._entries[play_mode]

    def detail(self, key: str) -> CatalogDetail | None:
        if key not in self._details:
            self._details[key] = self._service.get_detail(key)
        return self._details[key]

    @staticmethod
    def value(detail: CatalogDetail, label: str) -> str | None:
        for section in detail.sections:
            for value in section.values:
                if value.label == label:
                    return value.value
        return None

    def _has_formal_pool(self, entry: CatalogEntry) -> bool:
        detail = self.detail(entry.key)
        value = self.value(detail, "逐难度怪物池") if detail else None
        return bool(value and value != "不可用")

    def outer_rotations(self) -> tuple[CatalogEntry, ...]:
        representative: dict[str, CatalogEntry] = {}
        for entry in self.entries_for("outer_realm"):
            representative.setdefault(entry.primary_id, entry)

        def ordinal(entry: CatalogEntry) -> int:
            tail = entry.primary_id.rsplit("_", 1)[-1]
            return int(tail) if tail.isdigit() else -1

        current = sorted(
            (row for row in representative.values() if row.release_state == "current"),
            key=ordinal,
        )
        upcoming = sorted(
            (row for row in representative.values() if row.release_state in {"next", "scheduled"}),
            key=ordinal,
        )
        history = sorted(
            (row for row in representative.values() if row.release_state == "historical"),
            key=ordinal,
            reverse=True,
        )
        return tuple((*current, *upcoming, *history))

    def witch_blessings(self) -> tuple[CatalogEntry, ...]:
        return self._service.list_witch_blessings()

    def outer_buff(self, config_id: str) -> CatalogDetail | None:
        return self.detail(f"outer_buff|{config_id}")


class MonsterCatalogPage(QWidget):
    """Independent card archive; shared catalog composition owns final wiring."""

    def __init__(
        self,
        *,
        controller: MonsterCatalogPageController,
        asset_catalog: GameUiAssetCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("monsterCatalogPage")
        self.setStyleSheet(themed_style("QWidget#monsterCatalogPage{background:#0d1117;}"))
        self._controller = controller
        self._asset_catalog = asset_catalog
        self._history: list[BrowseState] = []
        self._active_state: BrowseState | None = None
        self._expanded_sections: set[str] = set()
        self._category_filter = ""
        self._updating_filters = False
        self._browser_layout_bucket = ""
        self._catalog_navigation_listener: Callable[[], None] = lambda: None
        self.play_group_cards: list[ArchiveCard] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        self.home = self._build_home()
        self.browser = self._build_browser()
        self.detail_view = MonsterDetailView(self)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.detail_view)
        root.addWidget(self.stack)

    def _build_home(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 2, 4, 8)
        hero = QFrame(page)
        hero.setObjectName("monsterGalleryHero")
        hero.setStyleSheet(themed_style(
            "QFrame#monsterGalleryHero{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:.70 #161b22,stop:1 #23170b);"
            "border:1px solid #1f6feb;border-radius:18px;}"
        ))
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(22, 17, 22, 17)
        copy = QVBoxLayout()
        eyebrow = QLabel("敌方与挑战档案", hero)
        eyebrow.setStyleSheet(themed_style("color:#58a6ff;font-size:10px;font-weight:900"))
        title = QLabel("怪物与玩法", hero)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:28px;font-weight:900"))
        subtitle = QLabel("从正式玩法进入期数、层、半场与刷怪槽位，沿正式绑定关系查看怪物画像。", hero)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        hero_layout.addLayout(copy, 1)
        root.addWidget(hero)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:0;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        host = QWidget(scroll)
        grid = QGridLayout(host)
        grid.setContentsMargins(4, 15, 12, 20)
        grid.setSpacing(13)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        for index, mode in enumerate(_PLAY_LABELS):
            card = ArchiveCard(BrowseCard(
                _PLAY_LABELS[mode], _PLAY_COPY[mode], self._home_badge(mode),
                self._mode_icon(mode), lambda checked=False, key=mode: self.open_mode(key),
                formal_id=mode,
            ), host)
            self.play_group_cards.append(card)
            grid.addWidget(card, index // 3, index % 3)
        blessings = QPushButton("查看魔女赐福 · 战前可选规则", host)
        blessings.setObjectName("monsterWitchBlessingEntry")
        blessings.clicked.connect(self.open_witch_blessings)
        grid.addWidget(blessings, 2, 0, 1, 3)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)
        return page

    def _build_browser(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 2, 4, 8)
        top = QHBoxLayout()
        self.browser_title = QLabel(page)
        self.browser_title.setStyleSheet(themed_style("color:#f0f6fc;font-size:22px;font-weight:900"))
        top.addWidget(self.browser_title)
        top.addStretch(1)
        root.addLayout(top)
        self.browser_subtitle = QLabel(page)
        self.browser_subtitle.setWordWrap(True)
        self.browser_subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        root.addWidget(self.browser_subtitle)
        search_row = QHBoxLayout()
        self.browser_search = QLineEdit(page)
        self.browser_search.setClearButtonEnabled(True)
        self.browser_search.setPlaceholderText("搜索怪物或玩法")
        self.browser_search.textChanged.connect(self._render_browser_state)
        search_row.addWidget(self.browser_search, 1)
        self.more_filters_button = QToolButton(page)
        self.more_filters_button.setText("更多筛选")
        self.more_filters_button.setCheckable(True)
        search_row.addWidget(self.more_filters_button)
        root.addLayout(search_row)
        self.category_host = QWidget(page)
        self.category_layout = QHBoxLayout(self.category_host)
        self.category_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.category_host)
        self.more_filters = QFrame(page)
        self.more_filters.setObjectName("monsterMoreFilters")
        self.more_filters.setStyleSheet(themed_style(
            "QFrame#monsterMoreFilters{background:#161b22;border:1px solid #30363d;"
            "border-radius:10px;}"
        ))
        filters_layout = QHBoxLayout(self.more_filters)
        filters_layout.setContentsMargins(10, 8, 10, 8)
        self.difficulty_filter = self._filter_combo("全部难度", self.more_filters)
        self.region_filter = self._filter_combo("全部地区", self.more_filters)
        self.period_filter = self._filter_combo("全部期数", self.more_filters)
        for combo in (self.difficulty_filter, self.region_filter, self.period_filter):
            combo.currentIndexChanged.connect(self._render_browser_state)
            filters_layout.addWidget(combo)
        filters_layout.addStretch(1)
        self.more_filters.setVisible(False)
        self.more_filters_button.toggled.connect(self.more_filters.setVisible)
        root.addWidget(self.more_filters)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:0;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        self.browser_host = QWidget(scroll)
        self.browser_body = QVBoxLayout(self.browser_host)
        self.browser_body.setContentsMargins(0, 6, 12, 20)
        self.browser_body.setSpacing(11)
        scroll.setWidget(self.browser_host)
        root.addWidget(scroll, 1)
        return page

    @staticmethod
    def _filter_combo(default_text: str, parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItem(default_text, "")
        combo.setProperty("defaultText", default_text)
        return combo

    def open_mode(self, mode: str) -> None:
        self._history.clear()
        if mode == "outer_realm":
            state = self._outer_state()
        else:
            entries = self._controller.entries_for(mode)
            grouped = _group(entries, lambda row: row.primary_id)
            cards = tuple(self._entity_card(mode, key, rows) for key, rows in grouped.items())
            state = BrowseState(
                _PLAY_LABELS[mode], _PLAY_COPY[mode],
                (BrowseSection(_PLAY_LABELS[mode], f"{len(cards)} 个正式对象", cards),),
            )
        self._show_state(state, push=True)

    def open_witch_blessings(self) -> None:
        """Open the secondary blessing browser without adding a top-level domain."""

        self._history.clear()
        self._show_state(_witch_blessing_state(
            self._controller.witch_blessings(), self.open_detail,
        ), push=True)

    def open_record(self, record_id: str) -> None:
        """Open a typed relation without exposing controller internals."""

        requested = str(record_id)
        detail = self._controller.detail(requested)
        if detail is not None:
            self.open_detail(detail)
            return
        for mode in _PLAY_LABELS:
            matches = tuple(
                entry for entry in self._controller.entries_for(mode)
                if entry.key == requested or entry.primary_id == requested
            )
            if not matches:
                continue
            if len(matches) == 1:
                self._open_encounter(matches[0])
            else:
                self._open_tiers(mode, matches)
            return

    def _outer_state(self) -> BrowseState:
        rotations = self._controller.outer_rotations()
        active = tuple(
            self._rotation_card(row) for row in rotations
            if row.release_state in {"current", "next", "scheduled"}
        )
        history = tuple(
            self._rotation_card(row) for row in rotations
            if row.release_state == "historical"
        )
        sections = []
        if active:
            sections.append(BrowseSection(
                "当前与预计",
                "左侧为当前生效期，右侧为尚未开放的预计期。",
                active,
            ))
        if history:
            sections.append(BrowseSection(
                "往期", "最近结束的期数优先；可展开查看全部。", history,
                initial_limit=3,
            ))
        return BrowseState(_PLAY_LABELS["outer_realm"], _PLAY_COPY["outer_realm"], tuple(sections))

    def _rotation_card(self, representative: CatalogEntry) -> BrowseCard:
        rows = tuple(
            row for row in self._controller.entries_for("outer_realm")
            if row.primary_id == representative.primary_id
        )
        state_label = {"current": "当期", "next": "预计", "scheduled": "预计", "historical": "往期"}
        ordinal = representative.primary_id.rsplit("_", 1)[-1]
        title = f"第 {ordinal} 期" if ordinal.isdigit() else representative.primary_id
        return BrowseCard(
            title,
            f"{len(rows) // 2} 层 · 按大陆服开放时间更新",
            state_label.get(representative.release_state, representative.release_state),
            self._first_icon(rows),
            lambda checked=False, values=rows: self._open_rotation(values),
            formal_id=representative.primary_id,
            category=state_label.get(representative.release_state, ""),
            period=title,
        )

    def _open_rotation(self, entries: tuple[CatalogEntry, ...]) -> None:
        period_label = _period_label(entries[0].primary_id)
        levels = _group(entries, lambda row: _key_parts(row.key)[2])
        sections = []
        season_buff = self._controller.outer_buff(entries[0].primary_id)
        if season_buff is not None:
            sections.append(BrowseSection(
                "本期规则", season_buff.entry.subtitle,
                (BrowseCard(
                    season_buff.entry.title, "查看正式说明与结构化分量", "赛季 Buff",
                    self._first_icon(entries),
                    lambda checked=False, detail=season_buff: self.open_detail(detail),
                    formal_id=season_buff.entry.primary_id,
                    period=period_label,
                ),),
            ))
        cards = []
        for level, rows in levels.items():
            cards.append(BrowseCard(
                f"第 {level} 层 · {rows[0].title}",
                " / ".join(row.secondary_label or "名称暂未提供" for row in rows),
                "层数",
                self._first_icon(rows),
                lambda checked=False, values=rows: self._open_outer_level(values),
                formal_id=rows[0].primary_id,
                difficulty=level,
                period=period_label,
            ))
        sections.append(BrowseSection("层数", f"{len(cards)} 层", tuple(cards)))
        self._show_state(BrowseState(
            f"轨外之境 · {period_label}", "选择层数后查看上下半场与正式刷怪槽位。",
            tuple(sections),
        ), push=True)

    def _open_outer_level(self, entries: tuple[CatalogEntry, ...]) -> None:
        sections = []
        for entry in entries:
            detail = self._controller.detail(entry.key)
            cards = self._monster_cards(detail, entry) if detail else ()
            sections.append(BrowseSection(
                entry.secondary_label or "名称暂未提供",
                "按刷怪槽位展示，正式绑定缺失时保持不可用。", cards,
            ))
        self._show_state(BrowseState(
            entries[0].title,
            f"{_period_label(entries[0].primary_id)} · 第 {_key_parts(entries[0].key)[2]} 层",
            tuple(sections),
        ), push=True)

    def _entity_card(
        self, mode: str, formal_id: str, entries: tuple[CatalogEntry, ...],
    ) -> BrowseCard:
        entry = entries[0]
        action = (
            (lambda checked=False, row=entry: self._open_encounter(row))
            if len(entries) == 1
            else (lambda checked=False, rows=entries: self._open_tiers(mode, rows))
        )
        return BrowseCard(
            entry.title, f"{len(entries)} 个难度 / 档位" if len(entries) > 1 else entry.subtitle,
            _PLAY_LABELS[mode], self._first_icon(entries), action, formal_id=formal_id,
            category=self._entry_category(entry), region=self._entry_region(entry),
        )

    def _open_tiers(self, mode: str, entries: tuple[CatalogEntry, ...]) -> None:
        cards = tuple(BrowseCard(
            f"难度 / 档位 {_key_parts(entry.key)[2]}", entry.subtitle, "正式档位",
            self._formal_icon(self._controller.detail(entry.key)),
            lambda checked=False, row=entry: self._open_encounter(row),
            formal_id=entry.primary_id,
            difficulty=_key_parts(entry.key)[2],
            category=self._entry_category(entry),
            region=self._entry_region(entry),
        ) for entry in entries)
        self._show_state(BrowseState(
            entries[0].title, "选择正式难度 / 档位",
            (BrowseSection("难度 / 档位", f"共 {len(cards)} 档", cards),),
        ), push=True)

    def _open_encounter(self, entry: CatalogEntry) -> None:
        detail = self._controller.detail(entry.key)
        if detail is None:
            return
        if entry.play_mode == "feast":
            difficulty = self._controller.value(detail, "难度") or ""
            self.open_detail(detail, MonsterContext(
                play=_PLAY_LABELS[entry.play_mode],
                scene=entry.title,
                level=f"难度 {difficulty}" if difficulty else "",
            ))
            return
        cards = self._monster_cards(detail, entry)
        if cards:
            self._show_state(BrowseState(
                detail.entry.title, detail.entry.subtitle,
                (BrowseSection("正式怪物", "沿正式绑定进入对应怪物画像。", cards),),
            ), push=True)
        else:
            self.open_detail(detail)

    def _monster_cards(
        self, detail: CatalogDetail, entry: CatalogEntry,
    ) -> tuple[BrowseCard, ...]:
        slot_sections = tuple(section for section in detail.sections if any(
            section.title.startswith(prefix)
            for prefix in ("刷怪槽位", "怪物池成员", "模板绑定")
        ) and "画像" not in section.title)
        if not slot_sections and entry.play_mode == "feast":
            slot_sections = tuple(section for section in detail.sections if section.title == "正式玩法配置")
        cards = []
        for section in slot_sections:
            fields = {value.label: value.value for value in section.values}
            path = fields.get("怪物类路径") or fields.get("类路径") or fields.get("模板路径") or ""
            formal_id = fields.get("模板 ID") or fields.get("Boss 模板 ID") or _object_name(path)
            target = self._match_profile_relation(detail, formal_id)
            target_detail = self._controller.detail(target) if target else None
            localized_name = fields.get("怪物中文名") or fields.get("Boss 中文名")
            if localized_name in {None, "", "不可用", "名称暂未提供"}:
                localized_name = (
                    target_detail.entry.title
                    if target_detail and target_detail.entry.localization_available
                    else "名称暂未提供"
                )
            monster_level = fields.get("等级") or fields.get("配置等级")
            encounter_difficulty = self._controller.value(detail, "难度")
            layer = self._controller.value(detail, "层")
            if monster_level:
                level_label = f"等级 {monster_level}"
            elif encounter_difficulty:
                level_label = f"难度 {encounter_difficulty}"
            elif layer:
                level_label = f"第 {layer} 层"
            else:
                level_label = ""
            context = MonsterContext(
                play=_PLAY_LABELS.get(entry.play_mode, entry.play_mode),
                scene=entry.title,
                level=level_label,
                half=entry.secondary_label if entry.play_mode == "outer_realm" else "",
                slot=section.title,
            )
            cards.append(BrowseCard(
                localized_name, f"{section.title} · 数量 {fields.get('数量', '暂无数据')}",
                _PLAY_LABELS.get(entry.play_mode, entry.play_mode),
                self._formal_icon(target_detail),
                (lambda checked=False, value=target_detail, ctx=context: self.open_detail(value, ctx))
                if target_detail else None,
                formal_id=formal_id or "unavailable",
                unavailable=target_detail is None,
                difficulty=context.level,
                region=context.play,
                period=entry.primary_id if entry.play_mode == "outer_realm" else "",
            ))
        return tuple(cards)

    def _entry_category(self, entry: CatalogEntry) -> str:
        detail = self._controller.detail(entry.key)
        if detail is None:
            return ""
        return (
            self._controller.value(detail, "类目")
            or _PLAY_LABELS.get(entry.play_mode, "")
        )

    def _entry_region(self, entry: CatalogEntry) -> str:
        detail = self._controller.detail(entry.key)
        if detail is None:
            return ""
        return self._controller.value(detail, "地区 / 位置") or ""

    @staticmethod
    def _match_profile_relation(detail: CatalogDetail, formal_id: str) -> str:
        normalized = formal_id.casefold()
        for relation in detail.relations:
            parsed = _profile_parts(relation.target_key)
            if parsed and parsed[1].casefold() == normalized:
                return relation.target_key
        return ""

    def open_detail(
        self, detail_or_entry: CatalogDetail | CatalogEntry | None,
        context: MonsterContext | None = None,
    ) -> None:
        if detail_or_entry is None:
            return
        detail = (
            detail_or_entry if isinstance(detail_or_entry, CatalogDetail)
            else self._controller.detail(detail_or_entry.key)
        )
        if detail is None:
            return
        self.detail_view.set_detail(detail, icon=self._formal_icon(detail), context=context)
        self.stack.setCurrentWidget(self.detail_view)
        self._catalog_navigation_listener()

    def formal_icon_candidates(self, detail: CatalogDetail) -> tuple[str, ...]:
        candidates = []
        if _profile_parts(detail.entry.key):
            candidates.append(detail.entry.key)
        candidates.extend(
            relation.target_key for relation in detail.relations
            if _profile_parts(relation.target_key)
        )
        return tuple(dict.fromkeys(candidates))

    def _formal_icon(self, detail: CatalogDetail | None) -> Path | None:
        if detail is None:
            return None
        if detail.entry.resource_path:
            icon = self._asset_catalog.encounter_icon(detail.entry.resource_path)
            if icon:
                return icon
        for candidate in self.formal_icon_candidates(detail):
            parts = _profile_parts(candidate)
            if parts:
                icon = self._asset_catalog.monster_icon(*parts)
                if icon:
                    return icon
                icon = self._asset_catalog.monster_family_icon(parts[1])
                if icon:
                    return icon
        return self._asset_catalog.monster_family_icon(detail.entry.primary_id)

    def _first_icon(self, entries: Iterable[CatalogEntry]) -> Path | None:
        for entry in entries:
            icon = self._formal_icon(self._controller.detail(entry.key))
            if icon:
                return icon
        return None

    def _mode_icon(self, mode: str) -> Path | None:
        return self._first_icon(self._controller.entries_for(mode))

    def _show_state(self, state: BrowseState, *, push: bool) -> None:
        if push:
            self._history.append(state)
            self._expanded_sections.clear()
            self._category_filter = ""
            self.browser_search.clear()
            self.more_filters_button.setChecked(False)
        self._active_state = state
        self.browser_title.setText(state.title)
        self.browser_subtitle.setText(state.subtitle)
        self._configure_filters(state)
        self._render_browser_state()
        self.stack.setCurrentWidget(self.browser)
        self._catalog_navigation_listener()

    def _configure_filters(self, state: BrowseState) -> None:
        self._updating_filters = True
        clear_layout(self.category_layout)
        self._category_group = QButtonGroup(self.category_host)
        self._category_group.setExclusive(True)
        categories = ("", *tuple(dict.fromkeys(
            card.category
            for section in state.sections
            for card in section.cards
            if card.category
        )))
        for index, category in enumerate(categories):
            button = QPushButton("全部" if not category else category, self.category_host)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(
                lambda _checked=False, value=category: self._set_category(value)
            )
            self._category_group.addButton(button)
            self.category_layout.addWidget(button)
        self.category_layout.addStretch(1)
        cards = tuple(card for section in state.sections for card in section.cards)
        self._fill_combo(self.difficulty_filter, (card.difficulty for card in cards))
        self._fill_combo(self.region_filter, (card.region for card in cards))
        self._fill_combo(self.period_filter, (card.period for card in cards))
        self._updating_filters = False

    @staticmethod
    def _fill_combo(combo: QComboBox, values: Iterable[str]) -> None:
        default_text = str(combo.property("defaultText"))
        combo.clear()
        combo.addItem(default_text, "")
        for value in sorted({str(value) for value in values if value}):
            combo.addItem(value, value)

    def _set_category(self, category: str) -> None:
        self._category_filter = category
        self._render_browser_state()

    def _render_browser_state(self) -> None:
        if self._updating_filters or self._active_state is None:
            return
        clear_layout(self.browser_body)
        query = self.browser_search.text().strip().casefold()
        difficulty = str(self.difficulty_filter.currentData() or "")
        region = str(self.region_filter.currentData() or "")
        period = str(self.period_filter.currentData() or "")
        for section in self._active_state.sections:
            cards = tuple(
                card for card in section.cards
                if (not query or query in " ".join((
                    card.title, card.subtitle, card.formal_id,
                )).casefold())
                and (not self._category_filter or card.category == self._category_filter)
                and (not difficulty or card.difficulty == difficulty)
                and (not region or card.region == region)
                and (not period or card.period == period)
            )
            if not cards:
                continue
            limited = (
                cards[:section.initial_limit]
                if section.initial_limit
                and section.title not in self._expanded_sections
                else cards
            )
            self.browser_body.addWidget(section_title(section.title, section.note))
            host = QWidget(self.browser_host)
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(11)
            grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            columns = 1 if self.width() < 900 else min(4, len(limited))
            columns = max(1, columns)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            for index, model in enumerate(limited):
                grid.addWidget(
                    ArchiveCard(model, host), index // columns, index % columns,
                )
            self.browser_body.addWidget(host)
            if len(limited) < len(cards):
                expand = QPushButton(f"展开全部往期（{len(cards)}）", self.browser_host)
                expand.clicked.connect(
                    lambda _checked=False, value=section.title: self._expand_section(value)
                )
                self.browser_body.addWidget(expand, 0, Qt.AlignLeft)
        self.browser_body.addStretch(1)
        self._browser_layout_bucket = "narrow" if self.width() < 900 else "wide"

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        bucket = "narrow" if event.size().width() < 900 else "wide"
        if (
            bucket != self._browser_layout_bucket
            and self._active_state is not None
            and self.stack.currentWidget() is self.browser
        ):
            self._render_browser_state()

    def _expand_section(self, section_title_value: str) -> None:
        self._expanded_sections.add(section_title_value)
        self._render_browser_state()

    def _browser_back(self) -> None:
        if len(self._history) <= 1:
            self._history.clear()
            self.stack.setCurrentWidget(self.home)
            self._catalog_navigation_listener()
            return
        self._history.pop()
        self._show_state(self._history[-1], push=False)

    def _back_from_detail(self) -> None:
        if self._history:
            self._show_state(self._history[-1], push=False)
        else:
            self.stack.setCurrentWidget(self.home)
            self._catalog_navigation_listener()

    def set_catalog_navigation_listener(
        self,
        listener: Callable[[], None],
    ) -> None:
        self._catalog_navigation_listener = listener

    def catalog_back_label(self) -> str | None:
        current = self.stack.currentWidget()
        if current is self.detail_view:
            return self._active_state.title if self._active_state is not None else "玩法列表"
        if current is self.browser:
            return self._history[-2].title if len(self._history) > 1 else "玩法分类"
        return None

    def catalog_go_back(self) -> bool:
        current = self.stack.currentWidget()
        if current is self.detail_view:
            self._back_from_detail()
            return True
        if current is self.browser:
            self._browser_back()
            return True
        return False

    def _home_badge(self, mode: str) -> str:
        return _home_badge(mode, self._controller.entries_for(mode))

def build_monster_catalog_page(
    *,
    service: StaticCatalogMonsterService,
    terminology_service: StaticCatalogTerminologyService,
    game_ui_asset_root: str | Path,
    parent: QWidget | None = None,
) -> MonsterCatalogPage:
    """Public factory for the shared catalog composition root."""

    if service.terminology_service is not terminology_service:
        raise ValueError("怪物页面与 Service 必须共享同一个正式术语依赖")
    page = MonsterCatalogPage(
        controller=MonsterCatalogPageController(service),
        asset_catalog=GameUiAssetCatalog(game_ui_asset_root),
        parent=parent,
    )
    return page
