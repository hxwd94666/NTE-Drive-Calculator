# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from src.i18n import tr, display_term
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from .role_calculation import (
    _attribute_name,
    _clear_layout,
)

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes"]

_WEIGHT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"),
    ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"),
    ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"),
    ("防御力", "DefAdd"),
    ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"),
    ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
)
_WEIGHT_LABEL_BY_PROPERTY = {
    property_id: display_term(label) for label, property_id in _WEIGHT_PROPERTY_CHOICES
}


def _build_weight_group(
    window, character_id: int, detail: dict, editor: dict,
) -> QGroupBox:
    group = QGroupBox(tr("词条权重（只读）"))
    group.setObjectName("officialRoleWeightGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    top = QHBoxLayout()
    top.addWidget(QLabel(tr("当前面板权重:")))
    source_label = QLabel(
        tr("直伤公式词条按当前边际收益归一化；未参与直伤公式的词条保留基础权重")
    )
    source_label.setStyleSheet("color:#8b949e;font-size:11px;")
    top.addWidget(source_label)
    top.addStretch()
    layout.addLayout(top)

    table_host = QWidget()
    table_layout = QVBoxLayout(table_host)
    table_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(table_host, 1)

    def _format_weight(value: float) -> str:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")

    def rebuild() -> None:
        _clear_layout(table_layout)
        base_weights = {
            str(property_id): float(value)
            for property_id, value in (detail.get("property_weights") or {}).items()
        }
        final_weights = dict(
            editor.get("marginal_property_weights")
            or base_weights
            or {}
        )
        formula_ids = set(editor.get("formula_property_ids") or ())
        property_ids = set(base_weights) | set(final_weights) | formula_ids
        ordered_ids = sorted(
            property_ids,
            key=lambda property_id: (
                -float(final_weights.get(property_id, 0.0)),
                _WEIGHT_LABEL_BY_PROPERTY.get(
                    property_id, _attribute_name(detail, property_id)
                ),
                property_id,
            ),
        )
        table = QTableWidget(len(ordered_ids), 4)
        table.setObjectName("officialRoleWeightTable")
        table.setHorizontalHeaderLabels(
            [tr("词条"), tr("基础权重"), tr("直伤权重"), tr("最终权重")]
        )
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for row_index, property_id in enumerate(ordered_ids):
            base_weight = float(base_weights.get(property_id, 0.0))
            final_weight = float(final_weights.get(property_id, base_weight))
            direct_weight = final_weight if property_id in formula_ids else 0.0
            table.setItem(
                row_index,
                0,
                QTableWidgetItem(
                    _WEIGHT_LABEL_BY_PROPERTY.get(
                        property_id, _attribute_name(detail, property_id)
                    )
                ),
            )
            table.setItem(row_index, 1, QTableWidgetItem(_format_weight(base_weight)))
            table.setItem(row_index, 2, QTableWidgetItem(_format_weight(direct_weight)))
            table.setItem(row_index, 3, QTableWidgetItem(_format_weight(final_weight)))
        for column in range(4):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        table.setFixedHeight(
            table.horizontalHeader().height()
            + table.verticalHeader().defaultSectionSize() * len(ordered_ids)
            + table.frameWidth() * 2
        )
        table_layout.addWidget(table)
    editor["refresh_weights"] = rebuild
    rebuild()
    return group
