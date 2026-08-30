# 构建工具下的游戏资料库三栏只读页面。
"""Three-pane Qt page for catalog domains, paged results, and details."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.contracts import (
    GLOBAL_DOMAIN_KEY,
    SOURCE_LABELS,
    CatalogDetail,
    CatalogField,
    CatalogItem,
    CatalogRelationGroup,
    CatalogSection,
)
from src.features.static_catalog.controller import StaticCatalogController


class StaticCatalogPage:
    """Own only search controls, selections, and discardable projections."""

    def __init__(self, *, controller: StaticCatalogController, dialog_parent: QWidget) -> None:
        self._controller = controller
        self._dialog_parent = dialog_parent
        self._page: QWidget | None = None
        self._domain_list: QListWidget | None = None
        self._result_list: QListWidget | None = None
        self._search_edit: QLineEdit | None = None
        self._detail_layout: QVBoxLayout | None = None
        self._page_label: QLabel | None = None
        self._previous_button: QPushButton | None = None
        self._next_button: QPushButton | None = None
        self._status_labels: dict[str, QLabel] = {}
        self._offset = 0
        self._total = 0
        self._domain_key = GLOBAL_DOMAIN_KEY
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_search)

    def build(self) -> QWidget:
        if self._page is not None:
            return self._page
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(12)
        root.addWidget(self._build_release_bar(page))

        splitter = QSplitter(Qt.Horizontal, page)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_domain_pane(splitter))
        splitter.addWidget(self._build_result_pane(splitter))
        splitter.addWidget(self._build_detail_pane(splitter))
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([190, 350, 560])
        root.addWidget(splitter, 1)
        self._page = page
        self.refresh()
        return page

    def refresh(self) -> None:
        """Start a new frozen release request and rebuild domain metadata."""

        if self._page is None:
            return
        try:
            request = self._controller.refresh_release()
        except Exception as exc:
            self._show_error("无法打开游戏资料库", exc)
            return
        release = request.release
        values = {
            "dataset": release.dataset_id,
            "schema": f"v{release.schema_version}",
            "importer": f"v{release.importer_version}",
            "mode": "只读",
            "path": release.database_path.name,
        }
        for key, value in values.items():
            label = self._status_labels.get(key)
            if label is not None:
                label.setText(value)
        domain_list = self._require(self._domain_list)
        domain_list.blockSignals(True)
        domain_list.clear()
        self._append_domain(GLOBAL_DOMAIN_KEY, "全部", "跨领域搜索")
        for domain in request.domains:
            self._append_domain(domain.key, domain.label, domain.description)
        domain_list.setCurrentRow(0)
        domain_list.blockSignals(False)
        self._domain_key = GLOBAL_DOMAIN_KEY
        self._offset = 0
        self._run_search()

    def close(self) -> None:
        self._search_timer.stop()
        self._controller.close()

    def _build_release_bar(self, parent: QWidget) -> QWidget:
        bar = QFrame(parent)
        bar.setObjectName("staticCatalogReleaseBar")
        bar.setStyleSheet(themed_style(
            "QFrame#staticCatalogReleaseBar{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        ))
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        for key, title in (
            ("dataset", "Dataset"),
            ("schema", "Schema"),
            ("importer", "Importer"),
            ("mode", "状态"),
        ):
            caption = QLabel(f"{title}：", bar)
            caption.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            value = QLabel("—", bar)
            value.setStyleSheet(themed_style("color:#58a6ff;font-weight:700;font-size:11px"))
            self._status_labels[key] = value
            layout.addWidget(caption)
            layout.addWidget(value)
        layout.addStretch(1)
        path_caption = QLabel("静态库：", bar)
        path_caption.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        path_value = QLabel("—", bar)
        path_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_value.setStyleSheet(themed_style("color:#c9d1d9;font-size:11px"))
        path_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._status_labels["path"] = path_value
        layout.addWidget(path_caption)
        layout.addWidget(path_value, 1)
        return bar

    def _build_domain_pane(self, parent: QWidget) -> QWidget:
        pane = QFrame(parent)
        pane.setObjectName("staticCatalogDomainPane")
        pane.setStyleSheet(themed_style(
            "QFrame#staticCatalogDomainPane{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        ))
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 12, 10, 10)
        title = QLabel("目录", pane)
        title.setStyleSheet(themed_style("font-size:15px;font-weight:800;color:#f0f6fc"))
        layout.addWidget(title)
        self._domain_list = QListWidget(pane)
        self._domain_list.setObjectName("staticCatalogDomains")
        self._domain_list.currentItemChanged.connect(self._on_domain_changed)
        layout.addWidget(self._domain_list, 1)
        return pane

    def _build_result_pane(self, parent: QWidget) -> QWidget:
        pane = QFrame(parent)
        pane.setObjectName("staticCatalogResultPane")
        pane.setStyleSheet(themed_style(
            "QFrame#staticCatalogResultPane{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        ))
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 12, 10, 10)
        self._search_edit = QLineEdit(pane)
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setPlaceholderText("搜索中文名、正式 ID、GA、GE、Buff key、Gameplay Tag、资源路径")
        self._search_edit.textChanged.connect(lambda _text: self._queue_search())
        layout.addWidget(self._search_edit)
        self._result_list = QListWidget(pane)
        self._result_list.setObjectName("staticCatalogResults")
        self._result_list.currentItemChanged.connect(self._on_result_changed)
        layout.addWidget(self._result_list, 1)
        pager = QHBoxLayout()
        self._previous_button = QPushButton("上一页", pane)
        self._next_button = QPushButton("下一页", pane)
        self._page_label = QLabel("0 项", pane)
        self._page_label.setAlignment(Qt.AlignCenter)
        self._page_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        self._previous_button.clicked.connect(lambda: self._change_page(-1))
        self._next_button.clicked.connect(lambda: self._change_page(1))
        pager.addWidget(self._previous_button)
        pager.addWidget(self._page_label, 1)
        pager.addWidget(self._next_button)
        layout.addLayout(pager)
        return pane

    def _build_detail_pane(self, parent: QWidget) -> QWidget:
        pane = QFrame(parent)
        pane.setObjectName("staticCatalogDetailPane")
        pane.setStyleSheet(themed_style(
            "QFrame#staticCatalogDetailPane{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        ))
        outer = QVBoxLayout(pane)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(pane)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(scroll)
        self._detail_layout = QVBoxLayout(host)
        self._detail_layout.setContentsMargins(16, 14, 16, 16)
        self._detail_layout.setSpacing(10)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        self._render_empty_detail("选择一条资料查看正式字段、关联与来源标记。")
        return pane

    def _append_domain(self, key: str, label: str, description: str) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, key)
        item.setToolTip(description)
        self._require(self._domain_list).addItem(item)

    def _queue_search(self) -> None:
        self._offset = 0
        self._search_timer.start()

    def _on_domain_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._domain_key = str(current.data(Qt.UserRole)) if current is not None else GLOBAL_DOMAIN_KEY
        self._offset = 0
        self._run_search()

    def _run_search(self) -> None:
        if self._page is None:
            return
        query = self._require(self._search_edit).text()
        try:
            page = self._controller.search(
                domain_key=self._domain_key,
                query=query,
                offset=self._offset,
            )
        except Exception as exc:
            self._show_error("查询游戏资料库失败", exc)
            return
        self._total = page.total
        results = self._require(self._result_list)
        results.blockSignals(True)
        results.clear()
        for catalog_item in page.items:
            item = QListWidgetItem(catalog_item.title)
            item.setData(Qt.UserRole, catalog_item)
            item.setToolTip(catalog_item.subtitle)
            results.addItem(item)
        results.blockSignals(False)
        if results.count():
            results.setCurrentRow(0)
        else:
            self._render_empty_detail("没有匹配的资料。")
        start = page.offset + 1 if page.items else 0
        end = page.offset + len(page.items)
        self._require(self._page_label).setText(f"{start}–{end} / {page.total}")
        self._require(self._previous_button).setEnabled(page.offset > 0)
        self._require(self._next_button).setEnabled(end < page.total)

    def _change_page(self, direction: int) -> None:
        step = self._controller.PAGE_SIZE
        self._offset = max(0, self._offset + direction * step)
        self._run_search()

    def _on_result_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        item = current.data(Qt.UserRole)
        if not isinstance(item, CatalogItem):
            return
        self._load_detail(item.domain_key, item.record_id)

    def _load_detail(self, domain_key: str, record_id: str) -> None:
        try:
            detail = self._controller.detail(domain_key=domain_key, record_id=record_id)
        except Exception as exc:
            self._show_error("读取资料详情失败", exc)
            return
        if detail is None:
            self._render_empty_detail("该记录已不存在或不属于当前 dataset。")
            return
        self._render_detail(detail)

    def _render_detail(self, detail: CatalogDetail) -> None:
        layout = self._clear_detail()
        title = QLabel(detail.item.title)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style("font-size:18px;font-weight:800;color:#f0f6fc"))
        layout.addWidget(title)
        if detail.item.subtitle:
            subtitle = QLabel(detail.item.subtitle)
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet(themed_style("color:#8b949e"))
            layout.addWidget(subtitle)
        for section in detail.sections:
            heading = QLabel(section.title)
            heading.setStyleSheet(themed_style(
                "font-size:14px;font-weight:800;color:#58a6ff;margin-top:6px"
            ))
            layout.addWidget(heading)
            for field in section.fields:
                layout.addWidget(self._field_widget(field))
            if section.references:
                references = QHBoxLayout()
                references.setSpacing(6)
                for reference in section.references:
                    button = QPushButton(reference.label)
                    button.setObjectName("btnSm")
                    button.clicked.connect(partial(
                        self._load_detail,
                        reference.domain_key,
                        reference.record_id,
                    ))
                    references.addWidget(button)
                references.addStretch(1)
                layout.addLayout(references)
        for group in detail.relation_groups:
            layout.addWidget(self._relation_group_widget(detail, group))
        for note in detail.notes:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#d29922;background:#d2992222;border:1px solid #d2992266;"
                "border-radius:6px;padding:7px"
            ))
            layout.addWidget(label)
        layout.addStretch(1)

    def _relation_group_widget(
        self,
        detail: CatalogDetail,
        group: CatalogRelationGroup,
    ) -> QWidget:
        host = QFrame()
        host.setObjectName("staticCatalogRelationGroup")
        host.setStyleSheet(themed_style(
            "QFrame#staticCatalogRelationGroup{border:1px solid #30363d;"
            "border-radius:7px;padding:5px}"
        ))
        layout = QVBoxLayout(host)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(7)
        heading = QLabel(f"{group.label}（{group.total}）", host)
        heading.setStyleSheet(themed_style("font-weight:800;color:#58a6ff"))
        layout.addWidget(heading)
        rows_layout = QVBoxLayout()
        rows_layout.setSpacing(7)
        layout.addLayout(rows_layout)
        button = QPushButton(f"加载{group.label}", host)
        button.setObjectName("staticCatalogLoadRelations")
        layout.addWidget(button)
        state = {"offset": 0}

        def load_page() -> None:
            try:
                page = self._controller.relations(
                    domain_key=detail.item.domain_key,
                    record_id=detail.item.record_id,
                    relation_kind=group.kind,
                    offset=state["offset"],
                )
            except Exception as exc:
                button.setText(f"加载失败：{exc}")
                button.setEnabled(False)
                return
            for row in page.rows:
                rows_layout.addWidget(self._relation_row_widget(row))
            state["offset"] = page.offset + len(page.rows)
            has_more = state["offset"] < page.total
            button.setEnabled(has_more)
            button.setText(
                f"加载下一页（{state['offset']} / {page.total}）"
                if has_more else f"已加载全部（{page.total}）"
            )

        button.clicked.connect(load_page)
        return host

    def _relation_row_widget(self, section: CatalogSection) -> QWidget:
        host = QFrame()
        host.setObjectName("staticCatalogRelationRow")
        host.setStyleSheet(themed_style(
            "QFrame#staticCatalogRelationRow{background:#0d1117;"
            "border:1px solid #21262d;border-radius:6px;padding:4px}"
        ))
        layout = QVBoxLayout(host)
        layout.setContentsMargins(7, 5, 7, 6)
        title = QLabel(section.title, host)
        title.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
        layout.addWidget(title)
        for field in section.fields:
            layout.addWidget(self._field_widget(field))
        if section.references:
            links = QHBoxLayout()
            for reference in section.references:
                button = QPushButton(reference.label, host)
                button.setObjectName("staticCatalogRelationTarget")
                button.clicked.connect(partial(
                    self._load_detail,
                    reference.domain_key,
                    reference.record_id,
                ))
                links.addWidget(button)
            links.addStretch(1)
            layout.addLayout(links)
        return host

    def _field_widget(self, field: CatalogField) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        name = QLabel(field.label)
        name.setMinimumWidth(96)
        name.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        value = QLabel(field.value or "—")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value.setStyleSheet(themed_style("color:#c9d1d9"))
        source = QLabel(SOURCE_LABELS[field.source])
        source.setStyleSheet(themed_style(
            "color:#58a6ff;background:#1f6feb22;border:1px solid #1f6feb66;"
            "border-radius:5px;padding:2px 5px;font-size:10px"
        ))
        layout.addWidget(name)
        layout.addWidget(value, 1)
        layout.addWidget(source, 0, Qt.AlignTop)
        if field.copyable:
            copy_button = QPushButton("复制")
            copy_button.setObjectName("btnSm")
            copy_button.clicked.connect(
                lambda _checked=False, text=field.value: QApplication.clipboard().setText(text)
            )
            layout.addWidget(copy_button, 0, Qt.AlignTop)
        return row

    def _render_empty_detail(self, message: str) -> None:
        layout = self._clear_detail()
        label = QLabel(message)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(themed_style("color:#8b949e;padding:24px"))
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)

    def _clear_detail(self) -> QVBoxLayout:
        layout = self._require(self._detail_layout)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                self._delete_layout(child_layout)
        return layout

    def _delete_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                self._delete_layout(child)
        layout.deleteLater()

    def _show_error(self, title: str, error: Exception) -> None:
        self._render_empty_detail(f"{title}：{error}")
        QMessageBox.warning(self._dialog_parent, title, str(error))

    @staticmethod
    def _require(value):
        if value is None:
            raise RuntimeError("游戏资料库页面尚未构建")
        return value
