# 构建游戏资料库的游戏化领域入口菜单。
"""Game-styled landing menu for the read-only static catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.contracts import CatalogDomain
from src.services.game_ui_asset_catalog import GameUiAssetCatalog


@dataclass(frozen=True, slots=True)
class CatalogMenuEntry:
    domain_key: str
    group: str
    title: str
    kicker: str
    description: str
    accent: str
    glyph: str
    asset_kind: str = ""
    asset_key: tuple[str, ...] = ()


MENU_GROUPS = (
    ("collection", "角色与养成", "从可操作对象进入，逐层查看成长、技能与关联机制。"),
    ("combat", "装备与敌人", "按游戏图鉴组织空幕、驱动、怪物和玩法环境。"),
    ("mechanics", "战斗机制", "拆解技能、效果、公式与反事实支持状态。"),
)


MENU_ENTRIES = (
    CatalogMenuEntry(
        "character", "collection", "角色图鉴", "CHARACTER ARCHIVE",
        "立绘、头像、身份、养成、AEQR 与角色专属动作。", "#58a6ff", "角",
        "character", ("1036",),
    ),
    CatalogMenuEntry(
        "fork", "collection", "弧盘图鉴", "FORK ARCHIVE",
        "等级突破、面板、混频技能、Buff 与适配角色。", "#a371f7", "弧",
        "fork", ("fork_DemonBlade",),
    ),
    CatalogMenuEntry(
        "equipment", "combat", "空幕与驱动", "EQUIPMENT CODEX",
        "套装、形状、主副属性、强化曲线和毕业搭配。", "#f0883e", "装",
        "equipment", ("Attack_orange",),
    ),
    CatalogMenuEntry(
        "monsters", "combat", "怪物与玩法", "ENCOUNTER ARCHIVE",
        "大世界、争锋、轨外、副本、Boss 与敌方属性。", "#f85149", "敌",
        "monster", ("monster_static_big_world", "mon_016_BP_Clone"),
    ),
    CatalogMenuEntry(
        "combat_mechanics", "mechanics", "战斗机制图鉴", "COMBAT MECHANICS",
        "公共 Buff、环合、DOT、倾陷、召唤、伤害公式与反事实建模。", "#bc8cff", "机制",
        "attribute", ("def_ignore",),
    ),
)


class CatalogMenuCard(QFrame):
    activated = Signal(str)

    def __init__(
        self,
        entry: CatalogMenuEntry,
        *,
        art: QPixmap | None,
        available: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._available = available
        self.setObjectName(f"staticCatalogMenuCard_{entry.domain_key}")
        self.setProperty("catalogMenuCard", True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.StrongFocus if available else Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor if available else Qt.ForbiddenCursor)
        self.setMinimumSize(250, 138)
        self.setStyleSheet(themed_style(
            "QFrame[catalogMenuCard='true']{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #161b22,stop:0.72 #10151c,stop:1 #0d1117);"
            "border:1px solid #30363d;border-radius:14px;}"
            "QFrame[catalogMenuCard='true']:hover{"
            f"border:2px solid {entry.accent};background:#1c2128;}}"
            "QFrame[catalogMenuCard='true']:focus{"
            f"border:2px solid {entry.accent};}}"
        ))
        self._build(art)

    def _build(self, art: QPixmap | None) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(15, 12, 12, 12)
        root.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        kicker = QLabel(self._entry.kicker, self)
        kicker.setStyleSheet(themed_style(
            f"color:{self._entry.accent};font-size:9px;font-weight:800;letter-spacing:1px"
        ))
        title = QLabel(self._entry.title, self)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:19px;font-weight:900"))
        description = QLabel(self._entry.description, self)
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:11px;line-height:1.35"))
        state = QLabel("进入档案  ›" if self._available else "当前发行不可用", self)
        state.setStyleSheet(themed_style(
            f"color:{self._entry.accent if self._available else '#6e7681'};"
            "font-size:11px;font-weight:800;margin-top:4px"
        ))
        copy.addWidget(kicker)
        copy.addWidget(title)
        copy.addWidget(description, 1)
        copy.addWidget(state)
        root.addLayout(copy, 1)

        art_label = QLabel(self)
        art_label.setFixedSize(84, 96)
        art_label.setAlignment(Qt.AlignCenter)
        if art is not None and not art.isNull():
            art_label.setPixmap(art.scaled(
                84, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
        else:
            art_label.setText(self._entry.glyph)
            art_label.setStyleSheet(themed_style(
                f"color:{self._entry.accent};background:#0d1117;"
                f"border:1px solid {self._entry.accent};border-radius:12px;"
                "font-size:22px;font-weight:900"
            ))
        root.addWidget(art_label, 0, Qt.AlignVCenter)
        for label in (kicker, title, description, state, art_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._available and event.button() == Qt.LeftButton:
            self.activated.emit(self._entry.domain_key)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._available and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self._entry.domain_key)
            event.accept()
            return
        super().keyPressEvent(event)


class StaticCatalogMenu(QWidget):
    domain_selected = Signal(str)

    def __init__(
        self,
        *,
        game_ui_asset_root: str | Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("staticCatalogMenu")
        self._asset_catalog = (
            GameUiAssetCatalog(game_ui_asset_root)
            if game_ui_asset_root is not None else None
        )
        self._group_cards: list[tuple[QGridLayout, tuple[CatalogMenuCard, ...]]] = []
        self._columns = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(scroll)
        self._content = QVBoxLayout(host)
        self._content.setContentsMargins(4, 4, 10, 18)
        self._content.setSpacing(15)
        scroll.setWidget(host)
        root.addWidget(scroll)

    def set_domains(self, domains: tuple[CatalogDomain, ...]) -> None:
        self._group_cards.clear()
        self._columns = 0
        while self._content.count():
            item = self._content.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            layout = item.layout()
            if layout is not None:
                self._delete_layout(layout)
        available = {domain.key for domain in domains if domain.key != "coverage"}
        self._content.addWidget(self._hero())
        for group_key, title, subtitle in MENU_GROUPS:
            heading = QLabel(f"{title}  ·  {subtitle}", self)
            heading.setWordWrap(True)
            heading.setStyleSheet(themed_style(
                "color:#c9d1d9;font-size:13px;font-weight:800;margin-top:2px"
            ))
            self._content.addWidget(heading)
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)
            entries = tuple(entry for entry in MENU_ENTRIES if entry.group == group_key)
            cards: list[CatalogMenuCard] = []
            for entry in entries:
                card = CatalogMenuCard(
                    entry,
                    art=self._art(entry),
                    available=entry.domain_key in available,
                    parent=self,
                )
                card.activated.connect(self.domain_selected)
                cards.append(card)
            self._group_cards.append((grid, tuple(cards)))
            self._content.addLayout(grid)
        self._content.addStretch(1)
        self._reflow_groups()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._reflow_groups()

    def _reflow_groups(self) -> None:
        columns = 1 if self.width() < 680 else 2
        if columns == self._columns:
            return
        self._columns = columns
        for grid, cards in self._group_cards:
            while grid.count():
                grid.takeAt(0)
            for index, card in enumerate(cards):
                grid.addWidget(card, index // columns, index % columns)
            for column in range(2):
                grid.setColumnStretch(column, 1 if column < columns else 0)

    def _hero(self) -> QWidget:
        hero = QFrame(self)
        hero.setObjectName("staticCatalogMenuHero")
        hero.setMinimumHeight(132)
        hero.setStyleSheet(themed_style(
            "QFrame#staticCatalogMenuHero{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:0.72 #161b22,stop:1 #0d1117);"
            "border:1px solid #1f6feb;border-radius:16px;}"
        ))
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(24, 18, 18, 10)
        copy = QVBoxLayout()
        eyebrow = QLabel("NTE · GAME DATA ARCHIVE", hero)
        eyebrow.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:10px;font-weight:900;letter-spacing:2px"
        ))
        title = QLabel("游戏资料库", hero)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:28px;font-weight:900"))
        subtitle = QLabel("选择一个档案领域。每个入口只呈现与该领域有关的正式数据和关系。", hero)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        copy.addStretch(1)
        layout.addLayout(copy, 1)
        art = self._resolve_art("character", ("1036",))
        if art is not None and not art.isNull():
            label = QLabel(hero)
            label.setFixedSize(132, 112)
            label.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)
            label.setPixmap(art.scaled(132, 132, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(label, 0, Qt.AlignBottom)
        return hero

    def _art(self, entry: CatalogMenuEntry) -> QPixmap | None:
        return self._resolve_art(entry.asset_kind, entry.asset_key)

    def _resolve_art(self, kind: str, key: tuple[str, ...]) -> QPixmap | None:
        catalog = self._asset_catalog
        if catalog is None or not kind:
            return None
        path = None
        if kind == "character":
            path = catalog.character_icon(int(key[0]))
        elif kind == "fork":
            path = catalog.fork_icon(key[0])
        elif kind == "monster":
            path = catalog.monster_icon(key[0], key[1])
        elif kind == "equipment":
            path = catalog.equipment_icon(key[0])
        elif kind == "attribute":
            path = catalog.attribute_icon(key[0])
        return QPixmap(str(path)) if path is not None else None

    @classmethod
    def _delete_layout(cls, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                cls._delete_layout(child)
        layout.deleteLater()
