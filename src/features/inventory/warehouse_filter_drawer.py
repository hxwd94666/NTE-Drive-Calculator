# 构建仓库页面仿游戏样式的右侧筛选抽屉。
"""Game-style right-side filter drawer for the warehouse page."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    QSize,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr, display_term
from src.app.theme import themed_style
from src.domain.warehouse_filter import (
    WarehouseFilterCatalog,
    WarehouseFilterOption,
    WarehouseFilterSpec,
    build_warehouse_filter_catalog,
    normalize_warehouse_filter_spec,
    warehouse_shape_size,
)
from src.features.inventory.warehouse import (
    warehouse_core_pixmap,
    warehouse_shape_pixmap,
)


class WarehouseFilterDrawer(QFrame):
    applied = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("warehouseFilterDrawer")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            themed_style(
                "#warehouseFilterDrawer{background:#161b22;border-left:1px solid #30363d;}"
                "#warehouseFilterDrawer QLabel{color:#c9d1d9;}"
                "QPushButton#warehouseFilterChip{padding:3px 10px;"
                "border:1px solid #30363d;border-radius:8px;background:#21262d;color:#c9d1d9;}"
                "QPushButton#warehouseFilterChip:hover{border-color:#58a6ff;}"
                "QPushButton#warehouseFilterChip:checked{background:#0d419d;"
                "border:1px solid #58a6ff;color:#ffffff;font-weight:700;}"
                "QPushButton#warehouseFilterKindTab{padding:3px 10px;"
                "border:1px solid #30363d;"
                "border-radius:9px;background:#21262d;color:#c9d1d9;font-weight:700;}"
                "QPushButton#warehouseFilterKindTab:checked{background:#0d419d;"
                "border-color:#58a6ff;color:#ffffff;}"
                "QFrame#warehouseFilterSection{background:#0d1117;border:1px solid #21262d;"
                "border-radius:10px;}"
                "QLabel#warehouseFilterSectionTitle{color:#58a6ff;font-weight:700;}"
                "QToolButton#warehouseFilterClose{min-width:32px;min-height:32px;"
                "max-width:32px;max-height:32px;font-size:18px;font-weight:700;"
                "border:1px solid #30363d;border-radius:6px;background:#21262d;color:#c9d1d9;}"
                "QToolButton#warehouseFilterClose:hover{border-color:#58a6ff;color:#ffffff;}"
                "QPushButton#warehouseFilterVisualChip{padding:4px 8px;"
                "text-align:left;border:1px solid #30363d;border-radius:9px;"
                "background:#21262d;color:#c9d1d9;}"
                "QPushButton#warehouseFilterVisualChip:hover{border-color:#58a6ff;}"
                "QPushButton#warehouseFilterVisualChip:checked{background:#0d419d;"
                "border-color:#58a6ff;color:#ffffff;font-weight:700;}"
            )
        )
        self._catalog = WarehouseFilterCatalog()
        self._items: tuple[dict[str, Any], ...] = ()
        self._draft = WarehouseFilterSpec()
        self._page_kind = "core"
        self._hide_when_animation_finishes = False
        self._animation = QPropertyAnimation(self, b"geometry", self)
        self._animation.setDuration(170)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)
        parent.installEventFilter(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel(tr("筛选"))
        title.setStyleSheet(themed_style("font-size:20px;font-weight:700;color:#f0f6fc"))
        header.addWidget(title)
        self.summary = QLabel(tr("{count} 项条件", count=0))
        self.summary.setObjectName("warehouseFilterSummary")
        self.summary.setStyleSheet(themed_style("color:#8b949e"))
        header.addWidget(self.summary)
        header.addStretch()
        close_button = QToolButton()
        close_button.setObjectName("warehouseFilterClose")
        close_button.setText("×")
        close_button.setFixedSize(32, 32)
        close_button.setToolTip(tr("关闭且不应用本次修改"))
        close_button.clicked.connect(self.close_panel)
        header.addWidget(close_button)
        outer.addLayout(header)

        kind_row = QHBoxLayout()
        kind_row.setSpacing(8)
        self.card_tab = QPushButton(tr("卡带"))
        self.drive_tab = QPushButton(tr("驱动块"))
        for button, kind in ((self.card_tab, "core"), (self.drive_tab, "module")):
            button.setObjectName("warehouseFilterKindTab")
            button.setFixedHeight(38)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, current_kind=kind: self._switch_kind(current_kind)
            )
            kind_row.addWidget(button, 1)
        outer.addLayout(kind_row)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("warehouseFilterScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 4, 0)
        self.content_layout.setSpacing(8)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        reset_button = QPushButton(tr("重置"))
        reset_button.setObjectName("warehouseFilterReset")
        reset_button.clicked.connect(self._reset_draft)
        footer.addWidget(reset_button, 1)
        apply_button = QPushButton(tr("确认"))
        apply_button.setObjectName("warehouseFilterApply")
        apply_button.setStyleSheet(
            themed_style(
                "QPushButton{background:#1f6feb;border:1px solid #388bfd;"
                "border-radius:7px;color:#ffffff;font-weight:700;}"
                "QPushButton:hover{background:#388bfd;}"
            )
        )
        apply_button.clicked.connect(self._apply_draft)
        footer.addWidget(apply_button, 1)
        outer.addLayout(footer)
        self.hide()

    @property
    def draft(self) -> WarehouseFilterSpec:
        return self._draft

    def set_items(
        self,
        items: Iterable[Mapping[str, Any]],
        applied_spec: WarehouseFilterSpec,
    ) -> WarehouseFilterSpec:
        self._items = tuple(dict(item) for item in items)
        full_catalog = build_warehouse_filter_catalog(self._items)
        normalized_applied = normalize_warehouse_filter_spec(
            applied_spec,
            full_catalog,
        )
        self._prepare_draft(normalized_applied)
        if self.isVisible():
            self._rebuild()
        return normalized_applied

    def open_for(self, spec: WarehouseFilterSpec) -> None:
        full_catalog = build_warehouse_filter_catalog(self._items)
        self._prepare_draft(normalize_warehouse_filter_spec(spec, full_catalog))
        self._rebuild()
        self._scroll_to_top()
        parent = self.parentWidget()
        if parent is None:
            return
        width = self._panel_width()
        end = QRect(parent.width() - width, 0, width, parent.height())
        start = QRect(parent.width(), 0, width, parent.height())
        self._animation.stop()
        self._hide_when_animation_finishes = False
        self.setGeometry(start)
        self.show()
        self.raise_()
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def _prepare_draft(self, spec: WarehouseFilterSpec) -> None:
        self._page_kind = spec.kind if spec.kind in {"core", "module"} else "core"
        self._catalog = build_warehouse_filter_catalog(self._items, kind=self._page_kind)
        self._draft = normalize_warehouse_filter_spec(spec, self._catalog)

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        parent = self.parentWidget()
        if parent is None:
            self.hide()
            return
        start = self.geometry()
        end = QRect(parent.width(), 0, start.width(), parent.height())
        self._animation.stop()
        self._hide_when_animation_finishes = True
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def _on_animation_finished(self) -> None:
        if self._hide_when_animation_finishes:
            self._hide_when_animation_finishes = False
            self.hide()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            parent = self.parentWidget()
            if parent is not None:
                width = self._panel_width()
                self.setGeometry(parent.width() - width, 0, width, parent.height())
        return super().eventFilter(watched, event)

    def _panel_width(self) -> int:
        parent = self.parentWidget()
        parent_width = parent.width() if parent is not None else 1000
        return min(parent_width, 500)

    def _rebuild(self) -> None:
        old_content = self.scroll.takeWidget()
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 4, 0)
        self.content_layout.setSpacing(8)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll.setWidget(self.content)
        if old_content is not None:
            old_content.deleteLater()
        self.card_tab.setChecked(self._page_kind == "core")
        self.drive_tab.setChecked(self._page_kind == "module")
        if self._page_kind == "core":
            self._add_section(
                "套装",
                self._catalog.item_types,
                "item_type_ids",
                self._draft.item_type_ids,
                visual=True,
            )
        else:
            grouped_shape_ids: set[str] = set()
            for size, title in ((2, display_term("II型驱动")), (3, display_term("III型驱动")),
                                (4, display_term("IV型驱动"))):
                options = tuple(
                    option
                    for option in self._catalog.item_types
                    if warehouse_shape_size(option.value) == size
                )
                grouped_shape_ids.update(option.value for option in options)
                self._add_section(
                    title,
                    options,
                    "item_type_ids",
                    self._draft.item_type_ids,
                    visual=True,
                )
            remaining = tuple(
                option
                for option in self._catalog.item_types
                if option.value not in grouped_shape_ids
            )
            self._add_section(
                "其他驱动形状",
                remaining,
                "item_type_ids",
                self._draft.item_type_ids,
                visual=True,
            )
        self._add_section("状态", self._catalog.statuses, "statuses", self._draft.statuses)
        self._add_section("品质", self._catalog.qualities, "qualities", self._draft.qualities)
        if self._page_kind == "core":
            self._add_section(
                "主属性",
                self._catalog.main_properties,
                "main_property_ids",
                self._draft.main_property_ids,
            )
        self._add_section(
            "副属性",
            self._catalog.sub_properties,
            "sub_property_ids",
            self._draft.sub_property_ids,
        )
        count_options = tuple(
            WarehouseFilterOption(str(value), str(value), 0) for value in range(5)
        )
        self._add_section(
            "至少符合的副属性词条数量",
            count_options,
            "min_sub_stat_matches",
            (
                frozenset((str(self._draft.min_sub_stat_matches),))
                if self._draft.min_sub_stat_matches is not None
                else frozenset()
            ),
        )
        self.content_layout.addStretch()
        self.summary.setText(
            tr("{count} 项条件", count=self._draft.active_group_count)
        )

    def _add_section(
        self,
        title: str,
        options: tuple[WarehouseFilterOption, ...],
        group: str,
        selected: frozenset[str],
        *,
        visual: bool = False,
    ) -> None:
        if not options and group != "min_sub_stat_matches":
            return
        box = QFrame()
        box.setObjectName("warehouseFilterSection")
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        if title:
            label = QLabel(tr(title))
            label.setObjectName("warehouseFilterSectionTitle")
            layout.addWidget(label)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(7)
        row_height = 56 if visual else 42
        for index, option in enumerate(options):
            # option.value stays raw: it is the filter key. Only the label changes.
            chip_label = display_term(option.label)
            button = QPushButton(
                f"{chip_label}  {option.count}" if option.count else chip_label
            )
            button.setObjectName(
                "warehouseFilterVisualChip" if visual else "warehouseFilterChip"
            )
            button.setCheckable(True)
            button.setChecked(option.value in selected)
            button.setToolTip(chip_label)
            if visual:
                button.setIcon(self._visual_option_icon(option))
                button.setIconSize(QSize(36, 36))
            button.setFixedHeight(row_height)
            button.setProperty("filterGroup", group)
            button.setProperty("filterValue", option.value)
            button.clicked.connect(
                lambda checked=False, current_group=group, value=option.value: self._toggle(
                    current_group, value, checked
                )
            )
            grid.addWidget(button, index // 2, index % 2)
            grid.setRowMinimumHeight(index // 2, row_height)
        layout.addLayout(grid)
        layout.activate()
        box.setMinimumHeight(layout.sizeHint().height())
        self.content_layout.addWidget(box)

    @staticmethod
    def _visual_option_icon(option: WarehouseFilterOption) -> QIcon:
        kind, _separator, official_id = option.value.partition(":")
        if kind == "core":
            return QIcon(warehouse_core_pixmap(official_id, "Gold"))
        if kind == "module":
            return QIcon(warehouse_shape_pixmap(official_id, "Gold"))
        return QIcon()

    def _switch_kind(self, kind: str) -> None:
        if kind not in {"core", "module"} or kind == self._page_kind:
            self._scroll_to_top()
            return
        self._page_kind = kind
        self._catalog = build_warehouse_filter_catalog(self._items, kind=kind)
        self._draft = normalize_warehouse_filter_spec(
            replace(
                self._draft,
                kind=kind,
                item_type_ids=frozenset(),
                main_property_ids=frozenset(),
            ),
            self._catalog,
        )
        self._rebuild()
        self._scroll_to_top()

    def _scroll_to_top(self) -> None:
        self.scroll.verticalScrollBar().setValue(0)

    def _toggle(self, group: str, value: str, checked: bool) -> None:
        if self._draft.kind == "all":
            self._draft = replace(self._draft, kind=self._page_kind)
        if group == "min_sub_stat_matches":
            self._draft = replace(self._draft, min_sub_stat_matches=int(value))
            for button in self.findChildren(QPushButton, "warehouseFilterChip"):
                if button.property("filterGroup") == "min_sub_stat_matches":
                    button.setChecked(
                        self._draft.min_sub_stat_matches is not None
                        and button.property("filterValue")
                        == str(self._draft.min_sub_stat_matches)
                    )
            return
        current = set(getattr(self._draft, group))
        if checked:
            current.add(value)
        else:
            current.discard(value)
        selected = frozenset(current)
        if group == "item_type_ids":
            self._draft = replace(self._draft, item_type_ids=selected)
        elif group == "statuses":
            self._draft = replace(self._draft, statuses=selected)
        elif group == "qualities":
            self._draft = replace(self._draft, qualities=selected)
        elif group == "main_property_ids":
            self._draft = replace(self._draft, main_property_ids=selected)
        elif group == "sub_property_ids":
            minimum = self._draft.min_sub_stat_matches
            if selected and minimum is None:
                minimum = 1
            elif not selected:
                minimum = None
            self._draft = replace(
                self._draft,
                sub_property_ids=selected,
                min_sub_stat_matches=minimum,
            )
        self.summary.setText(
            tr("{count} 项条件", count=self._draft.active_group_count)
        )

    def _reset_draft(self) -> None:
        self._draft = WarehouseFilterSpec()
        self._catalog = build_warehouse_filter_catalog(
            self._items,
            kind=self._page_kind,
        )
        self._rebuild()

    def _apply_draft(self) -> None:
        self.applied.emit(self._draft)
        self.close_panel()
