# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from src.app import runtime
from src.app.theme import themed_style
from src.domain.stat_catalog import StatCatalog
from src.features.allocation import results_view as legacy_results
from src.features.inventory.warehouse import WarehouseResultCard, warehouse_item_view
from src.services.official_role_page_service import (
    calculate_official_role_damage_breakdown,
    calculate_official_role_equipment_gain,
    calculate_official_role_item_gain,
    calculate_official_role_margins,
    load_official_role_detail,
    load_official_role_index,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
    save_official_role_tab_order,
)
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.services.sqlite_allocation_inventory import (
    AllocationInventoryProjectionError,
    legacy_shape_id,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)
from src.ui.persistent_tab_order import bind_persistent_tab_order
from src.ui.widgets import (
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    match_pinyin,
)
from .role_calculation import (
    _attribute_name,
    _clear_layout,
    _mark_dirty,
    _refresh_role_calculations,
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
    property_id: label for label, property_id in _WEIGHT_PROPERTY_CHOICES
}


from . import role_calculation as _calculation
for _module in (_calculation,):
    for _name, _value in vars(_module).items():
        if callable(_value) and not _name.startswith("__"):
            globals().setdefault(_name, _value)

def _build_weight_group(
    window, character_id: int, detail: dict, editor: dict,
) -> QGroupBox:
    group = QGroupBox("词条权重（只读）")
    group.setObjectName("officialRoleWeightGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    top = QHBoxLayout()
    top.addWidget(QLabel("当前面板权重:"))
    source_label = QLabel(
        "直伤公式词条按当前边际收益归一化；未参与直伤公式的词条保留基础权重"
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
        table.setHorizontalHeaderLabels(["词条", "基础权重", "直伤权重", "最终权重"])
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
