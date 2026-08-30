# 游戏资料库装备与技能效果详情组件；由后续集成页负责接线。
"""Reusable detail widget for catalog equipment, skills, effects and assets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.services.static_catalog_misc_service import (
    CatalogDetail,
    CatalogField,
    CatalogRelation,
)


class EquipmentEffectDetail(QWidget):
    """Render one Qt-free detail DTO and emit typed relationship jumps."""

    relation_requested = Signal(str, str)
    source_requested = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.title_label = QLabel("请选择一条资料。")
        self.title_label.setObjectName("cardTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.subtitle_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)
        self.body_layout.addStretch(1)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

    def clear(self) -> None:
        self.title_label.setText("请选择一条资料。")
        self.subtitle_label.clear()
        self._clear_body()

    def render(self, detail: CatalogDetail) -> None:
        self._clear_body()
        self.title_label.setText(detail.title)
        self.subtitle_label.setText(
            f"{detail.subtitle} · {detail.origin_label} · {detail.entity_key}"
        )
        for section in detail.sections:
            if not section.fields:
                continue
            group = QGroupBox(section.title)
            form = QFormLayout(group)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            for field in section.fields:
                form.addRow(field.label, self._field_widget(field))
            self.body_layout.insertWidget(self.body_layout.count() - 1, group)
        if detail.relations:
            relations = QGroupBox("关联资料")
            layout = QVBoxLayout(relations)
            for relation in detail.relations:
                layout.addWidget(self._relation_button(relation))
            self.body_layout.insertWidget(self.body_layout.count() - 1, relations)
        if detail.source_row_id is not None or detail.source_file_id is not None:
            source_button = QPushButton("查看来源追溯")
            source_button.setObjectName("btnAction")
            source_button.clicked.connect(
                lambda _checked=False, row_id=detail.source_row_id,
                file_id=detail.source_file_id: self.source_requested.emit(row_id, file_id)
            )
            self.body_layout.insertWidget(self.body_layout.count() - 1, source_button)

    def _clear_body(self) -> None:
        while self.body_layout.count() > 1:
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _field_widget(self, field: CatalogField) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        value = QLabel(field.value)
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(value, 1)
        marker = QLabel(field.origin_label)
        marker.setObjectName("statusBadge")
        marker.setProperty(
            "tone", "active" if field.origin_kind == "formal_static" else "neutral"
        )
        marker.setToolTip(
            "正式静态来自发行静态库；项目注解和派生显示值会单独标记。"
        )
        layout.addWidget(marker, 0, Qt.AlignTop)
        if field.copy_kind is not None and field.value != "—":
            button = QPushButton("复制")
            button.setObjectName("btnSm")
            button.setToolTip(f"复制{field.label}")
            button.clicked.connect(
                lambda _checked=False, text=field.value: self._copy(text)
            )
            layout.addWidget(button, 0, Qt.AlignTop)
        return host

    def _relation_button(self, relation: CatalogRelation) -> QPushButton:
        button = QPushButton(f"{relation.label}：{relation.title}")
        button.setObjectName("btnAction")
        button.clicked.connect(
            lambda _checked=False, kind=relation.target_kind,
            key=relation.target_key: self.relation_requested.emit(kind, key)
        )
        return button

    @staticmethod
    def _copy(text: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
