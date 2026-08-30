# 游戏资料库空幕与驱动独立卡片页面。
"""Player-facing release catalog with an optional frozen inventory projection."""
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from src.app.theme import theme_color, themed_style
from src.domain.warehouse_filter import build_warehouse_filter_catalog
from src.features.static_catalog.domain_pages.equipment_catalog_model import (
    AttributeCurve,
    EquipmentCatalogPageController,
    EquipmentRecord,
    ReleaseEquipmentCatalogSource,
    ShapeRecord,
    StrengthExperience,
    SuitRecord,
    official_suit_number,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.ui.equipment_presentation import EquipmentPresentation
_STATUS_CATALOG = build_warehouse_filter_catalog(())
_STATUS_LABELS = {option.value: option.label for option in _STATUS_CATALOG.statuses}
_QUALITY_COLORS = {"ORANGE": "#f2cc60", "PURPLE": "#bc8cff", "BLUE": "#58a6ff"}
_QUALITY_ORDER = {key: index for index, key in enumerate(_QUALITY_COLORS)}
_ATTRIBUTE_ICON_KEYS = {"AtkAdd": "attack", "AtkUp": "attack", "CritBase": "crit_rate",
                        "CritDamageBase": "crit_damage", "DefIgnore": "def_ignore", "UnbalIntensityBase": "inclination_strength"}
class ShapeGlyph(QWidget):
    def __init__(self, shape: ShapeRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.shape = shape
        self.setMinimumSize(88, 88)
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.shape.cells:
            painter.setPen(QColor(theme_color("#8b949e")))
            painter.drawText(self.rect(), Qt.AlignCenter, "形状暂缺")
            return
        xs, ys = zip(*self.shape.cells, strict=True)
        size = min(self.width(), self.height()) / 5
        origin_x = (self.width() - size * 4) / 2
        origin_y = (self.height() - size * 4) / 2
        painter.setPen(QPen(QColor(theme_color("#58a6ff")), 1.5))
        painter.setBrush(QColor(theme_color("#1f6feb33")))
        for x, y in self.shape.cells:
            painter.drawRoundedRect(
                origin_x + (x - min(xs)) * size, origin_y + (y - min(ys)) * size,
                size - 3, size - 3, 4, 4,
            )
class CurvePlot(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(themed_style(
            "background:#0d1117;color:#58a6ff;border:1px solid #30363d;"
            "border-radius:10px;font-family:'Cascadia Mono','Consolas';font-size:16px"
        ))
    def set_curve(self, curve: AttributeCurve | None) -> None:
        if curve is None or not curve.points:
            self.setText("当前没有可展示的强化曲线")
            return
        values = [point[1] for point in curve.points]
        low, high = min(values), max(values)
        span, bars = max(1e-9, high - low), "▁▂▃▄▅▆▇█"
        sparkline = "".join(bars[min(7, int((value - low) / span * 7))] for value in values)
        suffix = "%" if curve.show_percent else ""
        self.setText(
            f"{sparkline}\nLv.0  {self.number(low, curve.show_percent)}{suffix}"
            f"    →    Lv.{curve.points[-1][0]}  {self.number(high, curve.show_percent)}{suffix}"
        )
    @staticmethod
    def number(value: float, percent: bool) -> str:
        number = value * 100.0 if percent else value
        return f"{number:.2f}".rstrip("0").rstrip(".")
def _card(parent: QWidget, title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName("equipmentArchiveSection")
    frame.setStyleSheet(themed_style(
        "QFrame#equipmentArchiveSection{background:#161b22;border:1px solid #30363d;border-radius:14px;}"
    ))
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    title_label = QLabel(title, frame)
    title_label.setStyleSheet(themed_style("color:#f0f6fc;font-size:15px;font-weight:900"))
    layout.addWidget(title_label)
    return frame, layout
def _text(text: str, parent: QWidget, *, muted: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setStyleSheet(themed_style(f"color:{'#8b949e' if muted else '#c9d1d9'};font-size:11px"))
    return label
class ExpandableCard(QFrame):
    def __init__(self, title: str, summary: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._title = title
        self._summary = summary
        self._expanded = False
        self.setObjectName("equipmentExpandableCard")
        self.setStyleSheet(themed_style(
            "QFrame#equipmentExpandableCard{background:#0d1117;border:1px solid #30363d;border-radius:10px;}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        self.toggle = QPushButton(self)
        self.toggle.setProperty("expandableSection", True)
        self.toggle.setCheckable(True)
        self.toggle.setMinimumWidth(0)
        self.toggle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.toggle.setToolTip(f"{title} · {summary}")
        self.toggle.setStyleSheet(themed_style(
            "QPushButton{border:none;text-align:left;color:#f0f6fc;font-weight:800;padding:5px;}"
        ))
        layout.addWidget(self.toggle)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 4, 6, 6)
        self.body.setVisible(False)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self._toggle)
        self._refresh_toggle_text()
    def _toggle(self, expanded: bool) -> None:
        self._expanded = expanded
        self.body.setVisible(expanded)
        self._refresh_toggle_text()
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_toggle_text()
    def _refresh_toggle_text(self) -> None:
        text = (
            f"收起  {self._title}"
            if self._expanded
            else f"展开  {self._title}  ·  {self._summary}"
        )
        width = max(80, self.toggle.width() - 18)
        self.toggle.setText(
            self.toggle.fontMetrics().elidedText(text, Qt.ElideRight, width)
        )
class EquipmentGalleryCard(QFrame):
    activated = Signal(str)
    def __init__(self, record: EquipmentRecord, *, owned_count: int | None, shape_name: str, presentation: EquipmentPresentation,
                 asset_catalog: GameUiAssetCatalog, quality_name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.record = record
        tone = _QUALITY_COLORS.get(record.quality, "#8b949e")
        self.setObjectName("equipmentArchiveItemCard")
        self.setStyleSheet(themed_style(
            f"QFrame#equipmentArchiveItemCard{{background:#0d1117;border:1px solid {tone};border-radius:14px;}}"
        ))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 10)
        icon_path = asset_catalog.inventory_item_icon(record.kind, record.item_id)
        layout.addWidget(presentation.equipment_card(
            record.name, "", {}, "", record.item_id, {}, quality=record.quality,
            card_variant="inventory", item_icon_path=icon_path,
        ))
        line = (
            f"{quality_name}空幕 · 最高 Lv.{record.max_level} · {record.sub_count} 条副属性"
            if record.kind == "core"
            else f"{quality_name}驱动 · {record.area} 格 · {shape_name}"
        )
        layout.addWidget(_text(line, self, muted=True))
        ownership = (
            "库存暂不可用"
            if owned_count is None
            else "未拥有" if owned_count == 0 else f"已拥有 {owned_count} 件"
        )
        layout.addWidget(_text(ownership, self))
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record.item_id)
        super().mouseReleaseEvent(event)
class EquipmentDetailView(QScrollArea):
    def __init__(self, *, controller: EquipmentCatalogPageController, asset_catalog: GameUiAssetCatalog,
                 presentation: EquipmentPresentation, parent: QWidget) -> None:
        super().__init__(parent)
        self._controller = controller
        self._asset_catalog = asset_catalog
        self._presentation = presentation
        self.setWidgetResizable(True)
        self.host = QWidget(self)
        self.layout = QVBoxLayout(self.host)
        self.layout.setContentsMargins(10, 8, 18, 24)
        self.layout.setSpacing(12)
        self.setWidget(self.host)
    def _reset(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
    def clear_account_projection(self) -> None:
        """Remove all detail widgets that may contain a prior account instance."""
        self._reset()
        panel, panel_layout = _card(self.host, "当前账号库存")
        panel_layout.addWidget(_text(
            "库存暂不可用，请刷新后再查看。",
            panel,
            muted=True,
        ))
        self.layout.addWidget(panel)
        self.layout.addStretch(1)
    def show_equipment(
        self,
        record: EquipmentRecord,
        curves: tuple[AttributeCurve, ...],
        experience: StrengthExperience | None,
    ) -> None:
        self._reset()
        quality_label = self._controller.quality_name(record.quality)
        tone = _QUALITY_COLORS.get(record.quality, "#8b949e")
        hero = QFrame(self.host)
        hero.setObjectName("equipmentArchiveDetailHero")
        hero.setStyleSheet(themed_style(
            f"QFrame#equipmentArchiveDetailHero{{background:#161b22;border:2px solid {tone};border-radius:18px;}}"
        ))
        row = QHBoxLayout(hero)
        row.setContentsMargins(16, 12, 16, 12)
        icon_path = self._asset_catalog.inventory_item_icon(record.kind, record.item_id)
        image = QLabel(hero)
        image.setFixedSize(120, 120)
        pixmap = QPixmap(str(icon_path or ""))
        if not pixmap.isNull():
            image.setPixmap(pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        row.addWidget(image)
        copy = QVBoxLayout()
        title = QLabel(record.name, hero)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:22px;font-weight:900"))
        copy.addWidget(title)
        kind = "空幕 / 卡带" if record.kind == "core" else "驱动"
        copy.addWidget(_text(f"{kind}  ·  {quality_label}  ·  最高 Lv.{record.max_level}", hero))
        description = f"套装归属  {record.suit_name}" if record.kind == "core" else f"{self._controller.shape_name(record.shape_id)}  ·  占用 {record.area} 格"
        copy.addWidget(_text(description, hero, muted=True))
        row.addLayout(copy, 1)
        self.layout.addWidget(hero)
        strength, strength_layout = _card(self.host, "等级 / 强化曲线")
        strength_layout.addWidget(_text(
            f"强化上限 Lv.{record.max_level}  ·  基础属性 {record.main_count} 条  ·  随机副属性 {record.sub_count} 条",
            strength,
        ))
        self._add_strength_experience(strength, strength_layout, experience)
        selector = QComboBox(strength)
        for curve in curves:
            icon_path = self._asset_catalog.attribute_icon(_ATTRIBUTE_ICON_KEYS.get(curve.property_id, ""))
            selector.addItem(QIcon(str(icon_path)) if icon_path else QIcon(), curve.label, curve)
        strength_layout.addWidget(selector)
        plot = CurvePlot(strength)
        strength_layout.addWidget(plot)
        value = QLabel(strength)
        value.setStyleSheet(themed_style("color:#58a6ff;font-size:13px;font-weight:900"))
        strength_layout.addWidget(value)
        def select_curve(index: int) -> None:
            curve = selector.itemData(index) if index >= 0 else None
            plot.set_curve(curve)
            if isinstance(curve, AttributeCurve):
                number = CurvePlot.number(curve.max_value, curve.show_percent)
                value.setText(f"满级 {curve.label}  {number}{'%' if curve.show_percent else ''}")
            else:
                value.setText("当前装备没有可展示的强化曲线")
        selector.currentIndexChanged.connect(select_curve)
        select_curve(selector.currentIndex())
        self.layout.addWidget(strength)
        rules = ExpandableCard("词条生成规则", f"{record.main_count} 条基础属性 · 最多 {record.sub_count} 条副属性", self.host)
        rules.body_layout.addWidget(_text(
            "属性候选会随装备类别、驱动面积和品质变化；上方可切换每一条正式强化曲线。", rules.body,
        ))
        self.layout.addWidget(rules)
        self._add_owned_instances(record)
        if record.suit_id and (suit := self._controller.suit(record.suit_id)) is not None:
            self._add_suit_sections(suit)
        self._add_graduations(record.suit_id)
        self.layout.addStretch(1)

    @staticmethod
    def _add_strength_experience(
        strength: QFrame,
        layout: QVBoxLayout,
        experience: StrengthExperience | None,
    ) -> None:
        if experience is None:
            layout.addWidget(_text("强化经验暂未提供。", strength, muted=True))
            return
        summary = ExpandableCard(
            "强化经验",
            f"{len(experience.levels)} 级 · 累计 {experience.total:,}",
            strength,
        )
        running_total = 0
        for start in range(0, len(experience.levels), 5):
            segment = experience.levels[start:start + 5]
            segment_exp = sum(need_exp for _level, need_exp in segment)
            running_total += segment_exp
            first_level, last_level = segment[0][0], segment[-1][0]
            summary.body_layout.addWidget(_text(
                f"Lv.{first_level}–{last_level}  需要 {segment_exp:,}  ·  累计 {running_total:,}",
                summary.body,
            ))
        layout.addWidget(summary)
    def _add_owned_instances(self, record: EquipmentRecord) -> None:
        projection = self._controller.inventory
        items = self._controller.owned_for(record)
        if projection is None:
            panel, panel_layout = _card(self.host, "我的同款")
            panel_layout.addWidget(_text("当前账号库存暂不可用，请刷新后再查看。", panel, muted=True))
            self.layout.addWidget(panel)
            return
        expanded = ExpandableCard("我的同款", f"稳定仓库中有 {len(items)} 件", self.host)
        if not items:
            expanded.body_layout.addWidget(_text("当前稳定仓库没有这件装备。", expanded.body, muted=True))
        for number, item in enumerate(items, 1):
            main_name, main_value = item.main_stats[0] if item.main_stats else ("", None)
            card = self._presentation.equipment_card(
                f"第 {number} 件 · {'等级未知' if not item.level_known else f'Lv.{item.level}'}",
                main_name, dict(item.sub_stats), item.shape_id, item.instance_key, {},
                quality=item.quality, is_discarded=item.discarded, card_variant="inventory",
                item_icon_path=self._asset_catalog.inventory_item_icon(record.kind, record.item_id),
                main_value=main_value,
            )
            card.setObjectName("ownedEquipmentInstance")
            expanded.body_layout.addWidget(card)
            if not item.state_known:
                status = "名称暂未提供"
            else:
                tags = [_STATUS_LABELS["locked"]] if item.locked else []
                if item.equipped_name:
                    tags.append(f"{_STATUS_LABELS['equipped']}：{item.equipped_name}")
                status = " · ".join(tags) or _STATUS_LABELS["other"]
            expanded.body_layout.addWidget(_text(status, expanded.body, muted=True))
        self.layout.addWidget(expanded)
    def show_suit(self, suit: SuitRecord) -> None:
        self._reset()
        hero, hero_layout = _card(self.host, suit.name)
        hero_layout.addWidget(_text(
            f"空幕套装  ·  解锁 {len(suit.required_shape_ids)} 种驱动形状  ·  "
            f"{self._owned_summary(self._controller.owned_count(suit_id=suit.suit_id), '空幕')}", hero,
        ))
        self.layout.addWidget(hero)
        self._add_suit_sections(suit)
        self._add_graduations(suit.suit_id)
        self.layout.addStretch(1)
    def _add_suit_sections(self, suit: SuitRecord) -> None:
        shapes, shapes_layout = _card(self.host, "可搭载驱动形状")
        shape_row = QHBoxLayout()
        for shape_id in suit.required_shape_ids:
            shape = self._controller.shape(shape_id)
            tile = QFrame(shapes)
            tile.setStyleSheet(themed_style("background:#0d1117;border:1px solid #30363d;border-radius:10px"))
            tile_layout = QVBoxLayout(tile)
            if shape is not None:
                tile_layout.addWidget(ShapeGlyph(shape, tile))
                tile_layout.addWidget(_text(f"{shape.name} · {shape.area} 格", tile, muted=True))
            else:
                tile_layout.addWidget(_text("形状关系暂缺", tile, muted=True))
            shape_row.addWidget(tile)
        shape_row.addStretch(1)
        shapes_layout.addLayout(shape_row)
        self.layout.addWidget(shapes)
        effects, effects_layout = _card(self.host, "套装效果")
        for effect in suit.effects:
            summary = effect.description.split("。", 1)[0]
            if len(summary) > 34:
                summary = summary[:34] + "…"
            panel = ExpandableCard(f"{effect.required_count} 件套", summary, effects)
            panel.body_layout.addWidget(_text(effect.description, panel.body))
            for property_id, modifier_value in effect.modifiers:
                label, percent = self._controller.property_info(property_id)
                value = modifier_value * 100 if percent else modifier_value
                panel.body_layout.addWidget(_text(
                    f"固定加成  {label} +{value:g}{'%' if percent else ''}", panel.body, muted=True,
                ))
            if effect.has_conditional_effect:
                panel.body_layout.addWidget(_text(
                    "条件、持续与叠层规则已按当前可读说明在本卡内展示。",
                    panel.body,
                    muted=True,
                ))
            effects_layout.addWidget(panel)
        self.layout.addWidget(effects)
    def _add_graduations(self, suit_id: str) -> None:
        links = self._controller.graduations(suit_id)
        related = ExpandableCard("推荐角色", f"{len(links)} 份毕业模板采用该套装", self.host)
        if not links:
            related.body_layout.addWidget(_text("当前没有关联的毕业模板。", related.body, muted=True))
        for link in links:
            label = self._controller.property_info(link.main_property_id)[0]
            related.body_layout.addWidget(_text(
                f"{link.character_name}  ·  推荐主属性 {label}  ·  驱动总面积 {link.drive_area}", related.body,
            ))
        self.layout.addWidget(related)
    def show_shape(self, shape: ShapeRecord) -> None:
        self._reset()
        hero, hero_layout = _card(self.host, shape.name)
        row = QHBoxLayout()
        row.addWidget(ShapeGlyph(shape, hero))
        row.addWidget(_text(
            f"占用 {shape.area} 格  ·  {self._owned_summary(self._controller.owned_count(shape_id=shape.shape_id), '该形状驱动')}",
            hero,
        ), 1)
        hero_layout.addLayout(row)
        self.layout.addWidget(hero)
        suits = tuple(suit for suit in self._controller.archive.suits if shape.shape_id in suit.required_shape_ids)
        relation, relation_layout = _card(self.host, "支持该形状的空幕套装")
        for suit in sorted(suits, key=lambda value: official_suit_number(value.suit_id)):
            relation_layout.addWidget(_text(suit.name, relation))
        self.layout.addWidget(relation)
        self.layout.addStretch(1)

    @staticmethod
    def _owned_summary(count: int | None, noun: str) -> str:
        if count is None:
            return "当前账号库存暂不可用"
        return f"当前拥有 {count} 件{noun}"
class EquipmentCatalogPage(QWidget):
    """Independent player archive; account inventory is an injected projection."""
    def __init__(self, *, controller: EquipmentCatalogPageController, asset_catalog: GameUiAssetCatalog,
                 presentation: EquipmentPresentation,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._asset_catalog = asset_catalog
        self._presentation = presentation
        self._catalog_navigation_listener: Callable[[], None] = lambda: None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        self.gallery = self._build_gallery()
        self.detail = EquipmentDetailView(
            controller=controller, asset_catalog=asset_catalog,
            presentation=presentation, parent=self,
        )
        self.stack.addWidget(self.gallery)
        self.stack.addWidget(self.detail)
        root.addWidget(self.stack)
        self._select_category("core")
    def apply_inventory_snapshot(self, *, account_id: str, generation: int, snapshot: Mapping[str, Any]) -> bool:
        """Replace display state with one frozen public WarehouseInventoryService result."""
        accepted = self._controller.apply_inventory_snapshot(account_id=account_id, generation=generation, snapshot=snapshot)
        if accepted:
            self._refresh_cards()
        return accepted
    def invalidate_inventory_projection(self) -> None:
        """Clear account-owned display state before starting a refresh."""
        self._controller.invalidate_inventory()
        self.detail.clear_account_projection()
        self.show_gallery()
        self._refresh_cards()
    def _catalog_equipment(self, kind: str) -> tuple[EquipmentRecord, ...]:
        unique: dict[tuple[object, ...], EquipmentRecord] = {}
        for record in self._controller.archive.equipment:
            if record.kind == kind:
                key = (record.name, record.quality, record.suit_id, record.shape_id, record.area)
                unique.setdefault(key, record)
        if kind == "core":
            key_fn = lambda row: (official_suit_number(row.suit_id), _QUALITY_ORDER.get(row.quality, 9), row.name)
        else:
            key_fn = lambda row: (row.area, self._controller.shape(row.shape_id).cells, _QUALITY_ORDER.get(row.quality, 9))
        return tuple(sorted(unique.values(), key=key_fn))
    def _build_gallery(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 4, 8, 10)
        hero, hero_layout = _card(page, "空幕与驱动")
        hero_layout.addWidget(_text(
            "浏览正式装备图鉴、强化曲线与套装搭配；接入当前账号后还可查看自己实际拥有的装备。", hero, muted=True,
        ))
        categories = QHBoxLayout()
        self.category_group = QButtonGroup(page)
        self.category_group.setExclusive(True)
        specs = (
            ("空幕 / 卡带", "core", len(self._catalog_equipment("core"))),
            ("驱动", "module", len(self._catalog_equipment("module"))),
            ("套装", "suit", len(self._controller.archive.suits)),
            ("形状", "shape", len(self._controller.archive.shapes)),
        )
        for title, key, count in specs:
            button = QPushButton(f"{title}\n{count}", page)
            button.setCheckable(True)
            button.setProperty("categoryKey", key)
            button.setMinimumHeight(54)
            button.setStyleSheet(themed_style(
                "QPushButton{background:#0d1117;color:#8b949e;border:1px solid #30363d;border-radius:12px;font-weight:800;}"
                "QPushButton:checked{background:#1f6feb33;color:#58a6ff;border-color:#58a6ff;}"
            ))
            self.category_group.addButton(button)
            categories.addWidget(button)
        self.category_group.buttonClicked.connect(
            lambda button: self._select_category(str(button.property("categoryKey")))
        )
        hero_layout.addLayout(categories)
        layout.addWidget(hero)
        filters = QHBoxLayout()
        self.search = QLineEdit(page)
        self.search.setPlaceholderText("搜索空幕、套装或驱动形状")
        self.search.textChanged.connect(self._refresh_cards)
        filters.addWidget(self.search, 1)
        self.quality = QComboBox(page)
        self.quality.addItem("全部品质", "all")
        for key in _QUALITY_COLORS:
            self.quality.addItem(self._controller.quality_name(key), key)
        self.quality.currentIndexChanged.connect(self._refresh_cards)
        filters.addWidget(self.quality)
        self.ownership = QComboBox(page)
        self.ownership.addItem("全部图鉴", "all")
        self.ownership.addItem("只看已拥有", "owned")
        self.ownership.currentIndexChanged.connect(self._refresh_cards)
        filters.addWidget(self.ownership)
        self.result_count = QLabel(page)
        filters.addWidget(self.result_count)
        layout.addLayout(filters)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        self.card_host = QWidget(scroll)
        self.grid = QGridLayout(self.card_host)
        self.grid.setContentsMargins(2, 4, 12, 18)
        self.grid.setSpacing(12)
        scroll.setWidget(self.card_host)
        layout.addWidget(scroll, 1)
        return page
    def _select_category(self, key: str) -> None:
        self.category_key = key
        for button in self.category_group.buttons():
            button.setChecked(button.property("categoryKey") == key)
        self.quality.setVisible(key in {"core", "module"})
        self._refresh_cards()
    def _refresh_cards(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        query = self.search.text().strip().casefold()
        quality = str(self.quality.currentData() or "all")
        owned_only = self.ownership.currentData() == "owned"
        cards: list[QWidget] = []
        if self.category_key in {"core", "module"}:
            for record in self._catalog_equipment(self.category_key):
                owned = (
                    len(self._controller.owned_for(record))
                    if self._controller.inventory is not None
                    else None
                )
                haystack = " ".join((record.name, record.suit_name, self._controller.shape_name(record.shape_id))).casefold()
                if (query and query not in haystack) or (quality != "all" and record.quality != quality):
                    continue
                if owned_only and not owned:
                    continue
                card = EquipmentGalleryCard(
                    record, owned_count=owned, shape_name=self._controller.shape_name(record.shape_id), presentation=self._presentation,
                    asset_catalog=self._asset_catalog,
                    quality_name=self._controller.quality_name(record.quality),
                    parent=self.card_host,
                )
                card.activated.connect(self.open_equipment)
                cards.append(card)
        elif self.category_key == "suit":
            for suit in sorted(self._controller.archive.suits, key=lambda row: official_suit_number(row.suit_id)):
                owned = self._controller.owned_count(suit_id=suit.suit_id)
                if (query and query not in suit.name.casefold()) or (owned_only and not owned):
                    continue
                cards.append(self._suit_card(suit, owned))
        else:
            for shape in sorted(self._controller.archive.shapes, key=lambda row: (row.area, row.cells)):
                owned = self._controller.owned_count(shape_id=shape.shape_id)
                if (query and query not in shape.name.casefold()) or (owned_only and not owned):
                    continue
                cards.append(self._shape_card(shape, owned))
        self.result_count.setText(
            "库存暂不可用"
            if owned_only and self._controller.inventory is None
            else f"{len(cards)} 项"
        )
        columns = 3 if self.width() >= 1050 else 2 if self.width() >= 650 else 1
        for index, card in enumerate(cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(3):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
    def _suit_card(self, suit: SuitRecord, owned: int | None) -> QFrame:
        card = QFrame(self.card_host)
        card.setStyleSheet(themed_style("background:#161b22;border:1px solid #30363d;border-radius:14px"))
        layout = QVBoxLayout(card)
        title = QLabel(suit.name, card)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:16px;font-weight:900"))
        layout.addWidget(title)
        layout.addWidget(_text(
            f"解锁 {len(suit.required_shape_ids)} 种驱动形状  ·  {self._ownership_text(owned)}",
            card, muted=True,
        ))
        button = QPushButton("查看套装效果", card)
        button.setObjectName("btnAction")
        button.clicked.connect(lambda _checked=False: self.open_suit(suit.suit_id))
        layout.addWidget(button)
        return card
    def _shape_card(self, shape: ShapeRecord, owned: int | None) -> QFrame:
        card = QFrame(self.card_host)
        card.setStyleSheet(themed_style("background:#161b22;border:1px solid #30363d;border-radius:14px"))
        layout = QVBoxLayout(card)
        layout.addWidget(ShapeGlyph(shape, card))
        layout.addWidget(_text(
            f"{shape.name} · {shape.area} 格 · {self._ownership_text(owned)}",
            card, muted=True,
        ))
        button = QPushButton("查看支持套装", card)
        button.setObjectName("btnAction")
        button.clicked.connect(lambda _checked=False: self.open_shape(shape.shape_id))
        layout.addWidget(button)
        return card
    def open_equipment(self, item_id: str) -> None:
        record = next(row for row in self._controller.archive.equipment if row.item_id == item_id)
        self.detail.show_equipment(
            record,
            self._controller.curves(item_id),
            self._controller.strength_experience(item_id),
        )
        self.stack.setCurrentWidget(self.detail)
        self._catalog_navigation_listener()
    def open_suit(self, suit_id: str) -> None:
        suit = self._controller.suit(suit_id)
        if suit is not None:
            self.detail.show_suit(suit)
            self.stack.setCurrentWidget(self.detail)
            self._catalog_navigation_listener()
    def open_shape(self, shape_id: str) -> None:
        shape = self._controller.shape(shape_id)
        if shape is not None:
            self.detail.show_shape(shape)
            self.stack.setCurrentWidget(self.detail)
            self._catalog_navigation_listener()
    def show_gallery(self) -> None:
        self.stack.setCurrentWidget(self.gallery)
        self._catalog_navigation_listener()

    def set_catalog_navigation_listener(
        self,
        listener: Callable[[], None],
    ) -> None:
        self._catalog_navigation_listener = listener

    def catalog_back_label(self) -> str | None:
        return "空幕与驱动列表" if self.stack.currentWidget() is self.detail else None

    def catalog_go_back(self) -> bool:
        if self.stack.currentWidget() is not self.detail:
            return False
        self.show_gallery()
        return True
    @staticmethod
    def _ownership_text(count: int | None) -> str:
        if count is None:
            return "库存暂不可用"
        return "未拥有" if count == 0 else f"已拥有 {count} 件"
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "grid") and hasattr(self, "category_key"):
            self._refresh_cards()
def build_equipment_catalog_page(
    *, database_path: str | Path, game_ui_asset_root: str | Path,
    presentation: EquipmentPresentation,
    terminology_service: StaticCatalogTerminologyService | None = None,
    parent: QWidget | None = None,
) -> EquipmentCatalogPage:
    return EquipmentCatalogPage(
        controller=EquipmentCatalogPageController(
            ReleaseEquipmentCatalogSource(database_path),
            terminology_service,
        ),
        asset_catalog=GameUiAssetCatalog(game_ui_asset_root),
        presentation=presentation,
        parent=parent,
    )
