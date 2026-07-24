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
    _build_damage_formula_group,
    _build_margin_group,
    _clear_layout,
    _selected_combo_data,
    _selected_growth,
)
from .role_equipment import _build_drive_summary_group
from .role_growth import _build_base_group, _build_fork_group
from .role_weights import _build_weight_group

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
from . import role_growth as _growth
from . import role_equipment as _equipment
from . import role_weights as _weights
for _module in (_calculation, _growth, _equipment, _weights):
    for _name, _value in vars(_module).items():
        if callable(_value) and not _name.startswith("__"):
            globals().setdefault(_name, _value)

def _populate_role_tab(window, scroll: QScrollArea, character_id: int) -> None:
    if scroll.property("loaded"):
        return
    detail = load_official_role_detail(runtime.USER_DATABASE_PATH, character_id)
    editor = {
        "detail": detail,
        "property_weights": dict(detail.get("property_weights") or {}),
        "weights_dirty": False,
        "equipment_context_key": (
            "saved" if detail["equipment_contexts"]["saved"]["available"] else "current"
        ),
    }
    window._official_role_editors[character_id] = editor
    content = QWidget()
    form = QVBoxLayout(content)
    form.setSpacing(15)
    form.setContentsMargins(15, 15, 15, 15)
    form.addWidget(_build_base_group(window, character_id, detail, editor))
    form.addWidget(_build_margin_group(window, character_id, detail, editor))
    form.addWidget(_build_fork_group(window, character_id, detail, editor))
    form.addWidget(_build_drive_summary_group(window, detail, editor))
    form.addWidget(_build_damage_formula_group(detail, editor))
    form.addWidget(_build_weight_group(window, character_id, detail, editor))
    form.addSpacing(100)
    form.addStretch()
    scroll.setWidget(content)
    scroll.setProperty("loaded", True)


def _save_profiles(window, *, show_message: bool = True) -> bool:
    dirty_ids = list(getattr(window, "_official_role_dirty_ids", set()))
    if not dirty_ids:
        if show_message:
            QMessageBox.information(window, "保存", "当前没有需要保存的角色修改。")
        return True
    weight_updates: list[tuple[int, dict[str, float]]] = []
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
            for character_id in dirty_ids:
                editor = window._official_role_editors.get(character_id)
                if not editor:
                    continue
                detail = editor["detail"]
                growth = _selected_growth(editor)
                if growth is None:
                    raise ValueError("角色等级不在官方成长数据范围内")
                fork_id = _selected_combo_data(editor["fork"])
                dao.save_character_profile(
                    character_id=character_id,
                    character_level=int(growth[0]),
                    breakthrough_stage=int(growth[1]),
                    awakening_level=editor["awakening"].value(),
                    fork_id=fork_id,
                    fork_level=editor["fork_level"].value() if fork_id else None,
                    fork_refinement_level=(
                        int(editor["refinement"].currentData() or 1)
                        if fork_id else None
                    ),
                    selected_skill_id=_selected_combo_data(editor["selected_skill"]),
                    skill_levels=dict(editor["skill_levels"]),
                    ordinal=int(detail["profile"].get("ordinal") or 0),
                )
                if editor.get("weights_dirty"):
                    weight_updates.append((
                        character_id, dict(editor.get("property_weights") or {}),
                    ))
        for character_id, property_weights in weight_updates:
            save_account_character_weights(
                runtime.USER_DATABASE_PATH, character_id, property_weights,
            )
    except Exception as exc:
        QMessageBox.warning(window, "保存失败", str(exc))
        return False
    window._official_role_dirty_ids.clear()
    window._my_role_dirty = False
    if show_message:
        QMessageBox.information(window, "保存", "角色养成指针和词条权重已保存到当前账号数据库。")
    _refresh_my_role(window)
    return True


def _reset_current_role(window) -> None:
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is None or tabs.currentIndex() < 0:
        return
    character_id = int(tabs.tabBar().tabData(tabs.currentIndex()))
    scroll = tabs.currentWidget()
    old = scroll.takeWidget()
    if old is not None:
        old.deleteLater()
    scroll.setProperty("loaded", False)
    window._official_role_editors.pop(character_id, None)
    window._official_role_dirty_ids.discard(character_id)
    window._my_role_dirty = bool(window._official_role_dirty_ids)
    _populate_role_tab(window, scroll, character_id)


def _page_my_role(window) -> QWidget:
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(20, 16, 20, 16)
    root.setSpacing(10)
    page.setStyleSheet(themed_style(
        """
        QLabel{font-size:14px}
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{font-size:14px;padding:8px 11px;border-radius:7px}
        QPushButton{font-size:13px;padding:8px 15px;border-radius:7px}
        QTabBar::tab{font-size:13px;padding:10px 20px}
        QGroupBox{font-size:15px;border:1px solid #30363d;border-radius:10px;padding:24px;padding-top:36px}
        """
    ))
    header = QHBoxLayout()
    search = QLineEdit()
    search.setObjectName("officialRoleSearch")
    search.setPlaceholderText("搜索角色（支持拼音）...")
    search.setClearButtonEnabled(True)
    header.addWidget(search, 1)
    reset = QPushButton("重置")
    reset.setObjectName("btnDanger")
    reset.setToolTip("放弃当前角色尚未保存的修改，重新读取账号数据库")
    reset.clicked.connect(lambda: _reset_current_role(window))
    save = QPushButton("保存")
    save.setObjectName("btnPrimary")
    save.clicked.connect(lambda: _save_profiles(window))
    header.addWidget(reset)
    header.addWidget(save)
    root.addLayout(header)

    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    area.setWidget(content)
    root.addWidget(area, 1)
    window.my_role_form_area = area
    window.my_role_form_widget = content
    window.my_role_form_layout = content_layout
    window._official_role_page = page
    window.official_role_search = search
    window._official_role_dirty_ids = set()
    window._official_role_editors = {}
    window._my_role_dirty = False
    _refresh_my_role(window)
    return page


def _refresh_my_role(window) -> None:
    layout = getattr(window, "my_role_form_layout", None)
    if layout is None:
        return
    current_id = getattr(window, "_current_official_role_id", None)
    _clear_layout(layout)
    window._official_role_editors = {}
    roles = load_official_role_index(runtime.USER_DATABASE_PATH)
    if not roles:
        layout.addWidget(QLabel("暂无官方角色数据。"))
        return

    search = getattr(window, "official_role_search", None)
    if not isinstance(search, QLineEdit):
        return
    tabs = QTabWidget()
    tabs.setObjectName("officialRoleTabs")
    tab_ids = {}
    for role in roles:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setProperty("loaded", False)
        character_id = int(role["character_id"])
        index = tabs.addTab(scroll, str(role.get("name_zh") or character_id))
        tabs.tabBar().setTabData(index, character_id)
        tab_ids[character_id] = index

    window._official_role_tab_order_binding = bind_persistent_tab_order(
        tabs,
        item_id_at=lambda index: int(tabs.tabBar().tabData(index)),
        save_order=lambda character_ids: save_official_role_tab_order(
            runtime.USER_DATABASE_PATH,
            tuple(int(character_id) for character_id in character_ids),
        ),
        on_error=lambda exc: QMessageBox.warning(
            window,
            "保存角色顺序失败",
            str(exc),
        ),
    )

    def load_visible(index: int) -> None:
        if index < 0:
            return
        character_id = int(tabs.tabBar().tabData(index))
        window._current_official_role_id = character_id
        _populate_role_tab(window, tabs.widget(index), character_id)

    def filter_tabs(text: str = "") -> None:
        keyword = text.strip()
        for index in range(tabs.count()):
            tabs.setTabVisible(index, not keyword or match_pinyin(tabs.tabText(index), keyword))

    tabs.currentChanged.connect(load_visible)
    previous_filter = getattr(window, "_official_role_search_filter", None)
    previous_search = getattr(window, "_official_role_search_filter_widget", None)
    if previous_filter is not None and previous_search is search:
        try:
            search.textChanged.disconnect(previous_filter)
        except (RuntimeError, TypeError):
            pass
    search.textChanged.connect(filter_tabs)
    window._official_role_search_filter = filter_tabs
    window._official_role_search_filter_widget = search
    wanted_index = tab_ids.get(current_id, 0)
    tabs.setCurrentIndex(wanted_index)
    load_visible(tabs.currentIndex())
    window.official_role_tabs = tabs
    layout.addWidget(tabs)


def confirm_pending_my_role_changes(window) -> bool:
    if not getattr(window, "_my_role_dirty", False):
        return True
    answer = QMessageBox.question(
        window,
        "未保存角色状态",
        "角色养成指针有未保存修改，是否先保存？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if answer == QMessageBox.Cancel:
        return False
    if answer == QMessageBox.Save:
        return _save_profiles(window, show_message=False)
    window._official_role_dirty_ids.clear()
    window._my_role_dirty = False
    return True
