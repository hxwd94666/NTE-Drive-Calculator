# 游戏资料库怪物/玩法域独立主从浏览组件。
"""Monster and encounter catalog panel; integration owns navigation wiring."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.services.static_catalog_monster_service import (
    CatalogDetail,
    CatalogFilter,
    CatalogSection,
    CatalogValue,
    StaticCatalogMonsterService,
    play_mode_choices,
    provenance_label,
    release_scope_choices,
)


_PROVENANCE_COLORS = {
    "official_static": "#58a6ff",
    "formula_profile": "#d29922",
    "project_annotation": "#a371f7",
    "unavailable": "#f85149",
}


class MonsterCatalogPanel(QWidget):
    """Paginated catalog component with no dependency on MainWindow or navigation."""

    relationship_requested = Signal(str)

    def __init__(
        self,
        service: StaticCatalogMonsterService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._filter = CatalogFilter()
        self._loaded_keys: set[str] = set()
        self._build_ui()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        dataset = self._service.dataset()
        heading = QHBoxLayout()
        title = QLabel("怪物与玩法资料")
        title.setStyleSheet(themed_style("font-size:18px;font-weight:800;color:#f0f6fc"))
        heading.addWidget(title)
        heading.addStretch()
        metadata = QLabel(
            f"{dataset.dataset_id} · schema {dataset.schema_version} · "
            f"importer {dataset.importer_version} · 只读"
        )
        metadata.setTextInteractionFlags(Qt.TextSelectableByMouse)
        metadata.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        heading.addWidget(metadata)
        root.addLayout(heading)

        filters = QGridLayout()
        filters.setHorizontalSpacing(8)
        filters.setVerticalSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索正式 ID、模板、类路径、profile/pack")
        self.search_edit.textChanged.connect(self._schedule_refresh)
        filters.addWidget(self.search_edit, 0, 0, 1, 3)

        self.domain_combo = QComboBox()
        self.domain_combo.addItem("全部数据域", "all")
        self.domain_combo.addItem("怪物", "monster")
        self.domain_combo.addItem("玩法", "encounter")
        self.domain_combo.currentIndexChanged.connect(self._schedule_refresh)
        filters.addWidget(self.domain_combo, 0, 3)

        self.mode_combo = QComboBox()
        for value, label in play_mode_choices():
            self.mode_combo.addItem(label, value)
        self.mode_combo.currentIndexChanged.connect(self._schedule_refresh)
        filters.addWidget(self.mode_combo, 1, 0)

        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText("地区 / 副本类目")
        self.region_edit.textChanged.connect(self._schedule_refresh)
        filters.addWidget(self.region_edit, 1, 1)

        self.difficulty_edit = QLineEdit()
        self.difficulty_edit.setPlaceholderText("难度 / 层")
        self.difficulty_edit.textChanged.connect(self._schedule_refresh)
        filters.addWidget(self.difficulty_edit, 1, 2)

        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("数据集 / 轨外配置 ID")
        self.version_edit.textChanged.connect(self._schedule_refresh)
        filters.addWidget(self.version_edit, 1, 3)

        self.release_combo = QComboBox()
        for value, label in release_scope_choices():
            self.release_combo.addItem(label, value)
        self.release_combo.currentIndexChanged.connect(self._schedule_refresh)
        filters.addWidget(self.release_combo, 2, 0)

        reset = QPushButton("重置筛选")
        reset.clicked.connect(self.reset_filters)
        filters.addWidget(reset, 2, 3)
        root.addLayout(filters)

        splitter = QSplitter(Qt.Horizontal)
        left = QFrame()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.result_status = QLabel()
        self.result_status.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        left_layout.addWidget(self.result_status)
        self.result_list = QListWidget()
        self.result_list.currentItemChanged.connect(self._show_selected)
        left_layout.addWidget(self.result_list, 1)
        self.load_more_button = QPushButton("加载更多")
        self.load_more_button.clicked.connect(self.load_more)
        left_layout.addWidget(self.load_more_button)
        splitter.addWidget(left)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        self.detail_content = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_content)
        self.detail_layout.setAlignment(Qt.AlignTop)
        self.detail_scroll.setWidget(self.detail_content)
        splitter.addWidget(self.detail_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([360, 800])
        root.addWidget(splitter, 1)

    def reset_filters(self) -> None:
        widgets = (self.search_edit, self.region_edit, self.difficulty_edit, self.version_edit)
        for widget in widgets:
            widget.blockSignals(True)
            widget.clear()
            widget.blockSignals(False)
        for combo in (self.domain_combo, self.mode_combo, self.release_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.refresh()

    def _schedule_refresh(self, *_args) -> None:
        if hasattr(self, "_search_timer"):
            self._search_timer.start()

    def _read_filter(self, *, offset: int = 0) -> CatalogFilter:
        return CatalogFilter(
            search=self.search_edit.text().strip(),
            domain=str(self.domain_combo.currentData() or "all"),
            play_mode=str(self.mode_combo.currentData() or "all"),
            region=self.region_edit.text().strip(),
            difficulty=self.difficulty_edit.text().strip(),
            version=self.version_edit.text().strip(),
            release_scope=str(self.release_combo.currentData() or "all"),
            page_size=50,
            offset=offset,
        )

    def refresh(self) -> None:
        self._filter = self._read_filter()
        self._loaded_keys.clear()
        self.result_list.clear()
        self._append_page(self._filter)

    def load_more(self) -> None:
        next_filter = replace(self._filter, offset=self.result_list.count())
        self._append_page(next_filter)

    def _append_page(self, filters: CatalogFilter) -> None:
        try:
            page = self._service.list_entries(filters)
        except Exception as exc:  # UI boundary: present query failure without hiding it.
            self.result_status.setText(f"读取失败：{exc}")
            self.load_more_button.setEnabled(False)
            return
        for entry in page.items:
            if entry.key in self._loaded_keys:
                continue
            self._loaded_keys.add(entry.key)
            item = QListWidgetItem(f"{entry.title}\n{entry.subtitle}\n{entry.primary_id}")
            item.setData(Qt.UserRole, entry.key)
            if not entry.localization_available:
                item.setToolTip("中文文本在发行静态库中不可用；未做补猜。")
            self.result_list.addItem(item)
        self.result_status.setText(
            f"已加载 {self.result_list.count()} / {page.total} 条"
        )
        self.load_more_button.setEnabled(page.has_more)
        self.load_more_button.setVisible(page.has_more)
        if self.result_list.count() and self.result_list.currentRow() < 0:
            self.result_list.setCurrentRow(0)
        elif not self.result_list.count():
            self._render_empty("没有匹配的怪物或玩法记录。")

    def select_key(self, key: str) -> bool:
        for index in range(self.result_list.count()):
            item = self.result_list.item(index)
            if item.data(Qt.UserRole) == key:
                self.result_list.setCurrentItem(item)
                return True
        try:
            detail = self._service.get_detail(key)
        except (ValueError, TypeError) as exc:
            self._render_empty(str(exc))
            return False
        if detail is None:
            self._render_empty("关联记录已不可用。")
            return False
        self._render_detail(detail)
        return True

    def _show_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        key = str(current.data(Qt.UserRole) or "")
        try:
            detail = self._service.get_detail(key)
        except (ValueError, TypeError) as exc:
            self._render_empty(str(exc))
            return
        if detail is None:
            self._render_empty("该记录已不可用。")
            return
        self._render_detail(detail)

    def _clear_detail(self) -> None:
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_empty(self, text: str) -> None:
        self._clear_detail()
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;padding:24px"))
        self.detail_layout.addWidget(label)

    def _render_detail(self, detail: CatalogDetail) -> None:
        self._clear_detail()
        title = QLabel(detail.entry.title)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title.setStyleSheet(themed_style("font-size:17px;font-weight:800;color:#f0f6fc"))
        self.detail_layout.addWidget(title)
        subtitle = QLabel(f"{detail.entry.subtitle}\n{detail.entry.primary_id}")
        subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        self.detail_layout.addWidget(subtitle)
        for notice in detail.notices:
            notice_label = QLabel(notice)
            notice_label.setWordWrap(True)
            notice_label.setStyleSheet(themed_style(
                "background:#2d1f13;color:#d29922;border:1px solid #9e6a03;"
                "border-radius:6px;padding:7px"
            ))
            self.detail_layout.addWidget(notice_label)
        for section in detail.sections:
            self.detail_layout.addWidget(self._section_widget(section))
        if detail.relations:
            group = QGroupBox("关联跳转")
            layout = QVBoxLayout(group)
            for relation in detail.relations:
                button = QPushButton(relation.label)
                button.setToolTip(relation.note or relation.relation_kind)
                button.clicked.connect(
                    lambda _checked=False, key=relation.target_key: self._follow_relation(key)
                )
                layout.addWidget(button)
            self.detail_layout.addWidget(group)
        self.detail_layout.addStretch()
        self.detail_scroll.verticalScrollBar().setValue(0)

    def _follow_relation(self, key: str) -> None:
        self.relationship_requested.emit(key)
        self.select_key(key)

    def _section_widget(self, section: CatalogSection) -> QWidget:
        group = QGroupBox(section.title)
        layout = QVBoxLayout(group)
        if section.note:
            note = QLabel(section.note)
            note.setWordWrap(True)
            note.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            layout.addWidget(note)
        for value in section.values:
            layout.addWidget(self._value_widget(value))
        return group

    def _value_widget(self, value: CatalogValue) -> QWidget:
        row = QFrame()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        label = QLabel(value.label)
        label.setMinimumWidth(132)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        layout.addWidget(label)
        text = QLabel(value.value)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(text, 1)
        provenance = QLabel(provenance_label(value.provenance))
        color = _PROVENANCE_COLORS.get(value.provenance, "#8b949e")
        provenance.setStyleSheet(themed_style(
            f"color:{color};font-size:10px;font-weight:700"
        ))
        provenance.setToolTip(value.note)
        layout.addWidget(provenance)
        if value.copyable and value.value != "不可用":
            copy_button = QPushButton("复制")
            copy_button.setMaximumWidth(54)
            copy_button.clicked.connect(
                lambda _checked=False, text=value.value: QApplication.clipboard().setText(text)
            )
            layout.addWidget(copy_button)
        return row


def build_monster_catalog_panel(
    service: StaticCatalogMonsterService,
    parent: QWidget | None = None,
) -> MonsterCatalogPanel:
    """Narrow integration hook for the future shared 游戏资料库 page."""

    return MonsterCatalogPanel(service, parent)
