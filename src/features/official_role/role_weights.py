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
from src.services.character_weight_service import save_account_character_weights
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
    group = QGroupBox("词条权重")
    group.setObjectName("officialRoleWeightGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)

    editor_panel = QWidget()
    editor_layout = QVBoxLayout(editor_panel)
    editor_layout.setContentsMargins(0, 0, 0, 0)
    editor_layout.setSpacing(8)
    top = QHBoxLayout()
    top.addWidget(QLabel("词条权重:"))
    source = str(detail.get("property_weight_source") or "default")
    source_label = QLabel(
        "账号权重"
        if detail.get("property_weights_from_account")
        else f"推荐权重 · {source}"
    )
    source_label.setStyleSheet("color:#8b949e;font-size:11px;")
    top.addWidget(source_label)
    top.addStretch()
    add = QPushButton("+ 添加词条")
    add.setObjectName("btnAction")
    top.addWidget(add)
    editor_layout.addLayout(top)

    container = QWidget()
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(0, 0, 0, 0)
    container_layout.setSpacing(4)
    editor_layout.addWidget(container)
    layout.addWidget(editor_panel, 1)
    weights = editor["property_weights"]

    def changed() -> None:
        editor["weights_dirty"] = True
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    def update_weight(property_id: str, value: float) -> None:
        weights[property_id] = float(value)
        changed()

    def rebuild() -> None:
        _clear_layout(container_layout)
        ordered_ids = sorted(
            weights,
            key=lambda property_id: (
                _WEIGHT_LABEL_BY_PROPERTY.get(
                    property_id, _attribute_name(detail, property_id)
                ),
                property_id,
            ),
        )
        for property_id in ordered_ids:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(QLabel(
                _WEIGHT_LABEL_BY_PROPERTY.get(
                    property_id, _attribute_name(detail, property_id)
                )
            ))
            spin = NoWheelDoubleSpinBox()
            spin.setRange(0, 10)
            spin.setSingleStep(0.05)
            spin.setDecimals(3)
            spin.setKeyboardTracking(False)
            spin.setValue(float(weights[property_id]))
            spin.valueChanged.connect(
                lambda value, pid=property_id: update_weight(pid, value)
            )
            row.addWidget(spin)
            remove = QPushButton("×")
            remove.setObjectName("btnSm")
            remove.setFixedSize(28, 28)

            def remove_weight(_checked=False, pid=property_id) -> None:
                weights.pop(pid, None)
                rebuild()
                changed()

            remove.clicked.connect(remove_weight)
            row.addWidget(remove)
            container_layout.addLayout(row)
        container_layout.addStretch()

    def add_weight() -> None:
        available = [
            (label, property_id)
            for label, property_id in _WEIGHT_PROPERTY_CHOICES
            if property_id not in weights
        ]
        if not available:
            QMessageBox.information(window, "提示", "所有词条已添加。")
            return
        labels = [label for label, _property_id in available]
        selected, accepted = QInputDialog.getItem(
            window, "添加词条", "选择词条:", labels, 0, False,
        )
        if not accepted:
            return
        property_id = dict(available).get(str(selected))
        if property_id:
            weights[property_id] = 0.5
            rebuild()
            changed()

    add.clicked.connect(add_weight)
    add.setToolTip(
        "优先使用当前账号权重；账号未配置时会以只读静态库的工坊推荐初始化。"
    )
    editor["refresh_weights"] = rebuild
    rebuild()
    return group

