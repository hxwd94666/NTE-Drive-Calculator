# 游戏资料库来源追溯组件；发行 payload 省略时只展示保留元数据。
"""Source provenance detail widget for the static game catalog."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.services.static_catalog_misc_service import (
    CatalogRelationPage,
    SourceTrace,
)


class SourceTraceDetail(QWidget):
    """Show retained provenance and page source-row metadata on demand."""

    load_more_requested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_file_id: int | None = None
        self._next_offset = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title_label = QLabel("来源追溯")
        self.title_label.setObjectName("cardTitle")
        layout.addWidget(self.title_label)
        self.explanation_label = QLabel("请选择带来源标识的资料。")
        self.explanation_label.setWordWrap(True)
        layout.addWidget(self.explanation_label)
        self.metadata_group = QGroupBox("保留的来源元数据")
        self.metadata_form = QFormLayout(self.metadata_group)
        self.metadata_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layout.addWidget(self.metadata_group)
        self.rows_group = QGroupBox("来源行")
        self.rows_layout = QVBoxLayout(self.rows_group)
        self.rows_placeholder = QLabel("点击“加载来源行”后按页读取。")
        self.rows_layout.addWidget(self.rows_placeholder)
        layout.addWidget(self.rows_group)
        self.load_more_button = QPushButton("加载来源行")
        self.load_more_button.setObjectName("btnAction")
        self.load_more_button.clicked.connect(self._request_more)
        layout.addWidget(self.load_more_button)
        layout.addStretch(1)

    def render_trace(self, trace: SourceTrace) -> None:
        self._clear_form()
        self._clear_rows()
        self._source_file_id = trace.source_file_id
        self._next_offset = 0
        self.explanation_label.setText(trace.explanation)
        fields = (
            ("来源路径", trace.relative_path, True),
            ("文件 SHA-256", trace.source_file_sha256, True),
            ("声明行数", str(trace.declared_row_count), False),
            ("来源行 ID", self._optional(trace.source_row_id), False),
            ("来源行 key", self._optional(trace.row_key), True),
            ("内容 SHA-256", self._optional(trace.content_sha256), True),
            ("原始 payload", "发行包已省略" if trace.payloads_omitted else "未在此页展示", False),
        )
        for label, value, copyable in fields:
            self.metadata_form.addRow(label, self._value_widget(value, copyable))
        self.load_more_button.setEnabled(trace.declared_row_count > 0)
        self.load_more_button.setText("加载来源行")

    def append_rows(self, page: CatalogRelationPage) -> None:
        if page.offset == 0:
            self._clear_rows()
        for section in page.rows:
            group = QGroupBox(section.title)
            form = QFormLayout(group)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            for field in section.fields:
                form.addRow(
                    field.label,
                    self._value_widget(field.value, field.copy_kind is not None),
                )
            self.rows_layout.addWidget(group)
        self._next_offset = page.offset + len(page.rows)
        self.load_more_button.setEnabled(page.has_more)
        self.load_more_button.setText("加载下一页" if page.has_more else "已加载全部来源行")

    def _request_more(self) -> None:
        if self._source_file_id is not None:
            self.load_more_requested.emit(self._source_file_id, self._next_offset)

    def _clear_form(self) -> None:
        while self.metadata_form.rowCount():
            self.metadata_form.removeRow(0)

    def _clear_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _optional(value: object | None) -> str:
        return "—" if value is None else str(value)

    @staticmethod
    def _value_widget(value: str, copyable: bool) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(value)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label, 1)
        marker = QLabel("来源元数据")
        marker.setObjectName("statusBadge")
        marker.setProperty("tone", "neutral")
        layout.addWidget(marker, 0, Qt.AlignTop)
        if copyable and value != "—":
            button = QPushButton("复制")
            button.setObjectName("btnSm")
            button.clicked.connect(
                lambda _checked=False, text=value: QApplication.clipboard().setText(text)
            )
            layout.addWidget(button, 0, Qt.AlignTop)
        return host
