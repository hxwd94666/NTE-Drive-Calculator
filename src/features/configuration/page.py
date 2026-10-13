# 构建账号 SQLite 词条权重编辑页面。
"""Configuration page builder for account-scoped SQLite weights."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING
from time import perf_counter

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, match_pinyin
from src.app.theme import themed_style
from src.features.configuration.dependencies import freeze_basic_weight_dependencies
from src.features.configuration.shape_display import official_shape_display_rows
from .commit_actions import (
    create_custom_role, delete_custom_role, save_config_form,
    reset_current_config_weights as reset_current_config_weights,
    reset_all_config_weights as reset_all_config_weights,
)
from src.utils.logger import logger
from src.utils.perf import log_perf


if TYPE_CHECKING:
    from .page_owner import BasicWeightPage


_ACCOUNT_WEIGHT_CONFIG = "account_weights"


def build_config_widgets(page: BasicWeightPage) -> None:
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    page.setStyleSheet(
        themed_style(
        """
        QLabel{font-size:14px}
        QLineEdit,QComboBox,QDoubleSpinBox{font-size:14px;padding:8px 11px;border-radius:7px}
        QPushButton{font-size:13px;padding:8px 15px;border-radius:7px}
        QTabBar::tab{font-size:13px;padding:10px 20px}
        QGroupBox{font-size:15px;border:1px solid #30363d;border-radius:10px;padding:24px;padding-top:36px}
        """
        )
    )

    top_row = QHBoxLayout()
    return_button = QPushButton("← 返回角色")
    return_button.setObjectName("returnToRoleButton")
    return_button.clicked.connect(lambda: page._navigate("my_role"))
    top_row.addWidget(return_button)

    page.config_role_search = QLineEdit()
    page.config_role_search.setPlaceholderText("搜索角色（支持拼音）...")
    page.config_role_search.setClearButtonEnabled(True)
    page.config_role_search.textChanged.connect(
        lambda text: getattr(page, "_filter_config_roles", lambda _text: None)(text)
    )
    top_row.addWidget(page.config_role_search, 1)

    top_row.addStretch()

    reset_current_btn = QPushButton("重置当前")
    reset_current_btn.setObjectName("btnDanger")
    reset_current_btn.clicked.connect(lambda: reset_current_config_weights(page))
    top_row.addWidget(reset_current_btn)

    reset_all_btn = QPushButton("重置所有")
    reset_all_btn.setObjectName("btnDanger")
    reset_all_btn.clicked.connect(lambda: reset_all_config_weights(page))
    top_row.addWidget(reset_all_btn)

    new_btn = QPushButton("新建")
    new_btn.setObjectName("btnNew")
    new_btn.clicked.connect(lambda: create_custom_role(page))
    top_row.addWidget(new_btn)

    save_btn = QPushButton("保存")
    save_btn.setObjectName("btnPrimary")
    save_btn.clicked.connect(lambda: save_config_form(page))
    top_row.addWidget(save_btn)
    layout.addLayout(top_row)
    page.config_mutation_buttons = (reset_current_btn, reset_all_btn, new_btn, save_btn)
    page.config_load_status = QLabel()
    layout.addWidget(page.config_load_status)

    page.config_form_area = QScrollArea()
    page.config_form_area.setWidgetResizable(True)
    page.config_form_widget = QWidget()
    page.config_form_layout = QVBoxLayout(page.config_form_widget)
    page.config_form_area.setWidget(page.config_form_widget)
    layout.addWidget(page.config_form_area, 1)


def confirm_pending_config_changes(page: BasicWeightPage, *, completion=None):
    if not getattr(page, "_config_dirty", False):
        return True
    current_name = getattr(page, "_current_config_name", None)
    if not current_name:
        return True
    ret = QMessageBox.question(
        page,
        "未保存配置",
        "当前账号词条权重有未保存修改，是否先保存？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if ret == QMessageBox.Cancel:
        page.controller().log_dirty_exit(
            "cancel",
            len(getattr(page, "_config_dirty_character_ids", set())),
        )
        return False
    if ret == QMessageBox.Save:
        page.controller().log_dirty_exit(
            "save",
            len(getattr(page, "_config_dirty_character_ids", set())),
        )
        save_config_form(page, completion=completion, show_message=False)
        return False  # Only the commit acknowledgement may continue navigation.
    else:
        page.controller().log_dirty_exit(
            "discard",
            len(getattr(page, "_config_dirty_character_ids", set())),
        )
        page._config_dirty = False
        page._config_form_data = None
        page._config_loaded_model = None
        for field in ("character", "shape_bonus", "board", "target_suit"):
            getattr(page, f"_config_dirty_{field}_ids", set()).clear()
    return True


def switch_config_form(page: BasicWeightPage, *, active_role=None):
    """显示当前账号的 SQLite 词条权重；不再提供 JSON 配置入口。"""
    if getattr(page, "_config_dirty", False):
        return  # 同账号刷新保留未提交草稿；账号切换由页面 owner 先清空。
    controller = page.controller()
    if controller.is_writing():
        return
    page.config_load_status.setText("正在读取当前账号权重…")
    page.config_form_area.setEnabled(False)
    for button in page.config_mutation_buttons:
        button.setEnabled(False)
    page._current_config_name = _ACCOUNT_WEIGHT_CONFIG

    def current():
        return (controller is getattr(page, "_basic_weight_controller", None)
                and controller.dependencies == freeze_basic_weight_dependencies(page.app_context))

    def apply(loaded):
        if not current() or getattr(page, "_config_dirty", False):
            return
        started = perf_counter()
        try:
            if (controller.dependencies != getattr(page, "_config_model_dependencies", None)
                    or loaded != getattr(page, "_config_loaded_model", None)):
                while page.config_form_layout.count():
                    item = page.config_form_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                page._config_loaded_model = deepcopy(loaded)
                page._config_model_dependencies = controller.dependencies
                page._config_form_data = loaded["roles"]
                for field in ("property_labels", "sub_choices", "main_choices", "shape_bonus_choices", "shape_label_choices", "suit_choices"):
                    target = "_config_weight_" + field if field in {"property_labels", "sub_choices", "main_choices"} else "_config_" + field
                    setattr(page, target, loaded[field])
                for field in ("character", "shape_bonus", "board", "target_suit"):
                    setattr(page, f"_config_dirty_{field}_ids", set())
                render_roles_form(page, loaded["roles"], active_role=active_role or getattr(page, "_config_active_role", None))
            page.config_load_status.clear()
            page.setEnabled(True)
            page.config_form_area.setEnabled(True)
            for button in page.config_mutation_buttons:
                button.setEnabled(True)
            log_perf(logger, "basic_weight.form_apply", elapsed_ms=(perf_counter() - started) * 1000)
        except Exception as exc:
            failed(str(exc))

    def failed(error):
        if current():
            page.config_load_status.setText(f"权重读取失败：{error}；重新进入本页可重试。")
            page._config_loaded_model = None

    controller.request_form_data(apply, failed)


def _field(label, widget, layout):
    row = QHBoxLayout()
    row.addWidget(QLabel(label))
    row.addWidget(widget, 1)
    layout.addLayout(row)


def _add_extra_shape_row(page, data, role_name, role_data, form_layout):
    value = NoWheelComboBox()
    value.setMaxVisibleItems(6)
    value.addItems(getattr(page, "_config_shape_label_choices", ()))
    choices = tuple(getattr(page, "_config_shape_label_choices", ()))
    current_label = str(role_data.get("extra_shape_label") or "") or (
        choices[0] if choices else "Type-3"
    )
    role_data["extra_shape_label"] = current_label
    value.setCurrentText(current_label)
    value.setPlaceholderText("选择额外形状标签")
    ownership = "点击“保存”后写入当前账号的自建角色数据。"
    value.setToolTip(f"选择额外形状标签；{ownership}")
    value.currentTextChanged.connect(
        lambda text, rn=role_name: save_extra_shape_label(page, rn, text, data)
    )
    _field("额外形状标签", value, form_layout)


def _add_extra_shape_buff_row(
    page, data, role_name, role_data, form_layout, rebuild_all_tabs,
):
    """额外形状仅有一个属性槽；成对编辑，避免草稿积累互斥属性。"""
    extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
    selected_property, selected_value = next(iter(extra_buffs.items()), ("", 0.0))
    row = QHBoxLayout()
    row.addWidget(QLabel("额外形状加成："))
    property_combo = NoWheelComboBox()
    property_combo.setMaxVisibleItems(6)
    property_combo.addItem("无加成", "")
    for label, property_id in getattr(page, "_config_shape_bonus_choices", ()):
        property_combo.addItem(str(label), str(property_id))
    selected_index = property_combo.findData(str(selected_property))
    property_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
    ownership = "点击“保存”后写入当前账号的自建角色数据。"
    property_combo.setToolTip(f"选择额外形状提供的属性；{ownership}")
    row.addWidget(property_combo, 1)
    value_spin = NoWheelDoubleSpinBox()
    value_spin.setRange(0, 1000000)
    value_spin.setDecimals(3)
    value_spin.setSingleStep(0.1)
    value_spin.setValue(float(selected_value))
    value_spin.setKeyboardTracking(False)
    value_spin.setEnabled(bool(property_combo.currentData()))
    value_spin.setToolTip(f"额外形状加成数值；{ownership}")
    row.addWidget(value_spin)
    form_layout.addLayout(row)

    def update_bonus(*_args) -> None:
        property_id = str(property_combo.currentData() or "")
        value_spin.setEnabled(bool(property_id))
        save_single_extra_shape_bonus(
            page, role_name, property_id, value_spin.value(), data,
        )

    property_combo.currentIndexChanged.connect(update_bonus)
    value_spin.editingFinished.connect(update_bonus)


def _add_role_weight_group(page, data, role_name, role_data, form_layout, rebuild_all_tabs, title, field_name, add_label):
    weights_header = QHBoxLayout()
    weights_header.addWidget(QLabel(f"{title}:"))
    weights_header.addStretch()
    add_weight_btn = QPushButton(add_label)
    add_weight_btn.setObjectName("btnAction")
    add_weight_btn.clicked.connect(
        lambda checked=False, rn=role_name, field=field_name: add_weight(
            page, rn, data, lambda active=rn: rebuild_all_tabs(active), field
        )
    )
    weights_header.addWidget(add_weight_btn)
    form_layout.addLayout(weights_header)

    weights = role_data.get(field_name, {}) or {}
    if not weights:
        empty_label = QLabel("暂无配置")
        empty_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        form_layout.addWidget(empty_label)
        return
    for weight_key in sorted(weights.keys()):
        weight_row = QHBoxLayout()
        weight_row.setSpacing(6)
        weight_row.addWidget(QLabel(
            getattr(page, "_config_weight_property_labels", {}).get(
                weight_key, weight_key
            )
        ))
        spin = NoWheelDoubleSpinBox()
        spin.setObjectName(f"weight-{field_name}-{weight_key}")
        spin.setRange(0, 10)
        spin.setSingleStep(0.05)
        spin.setValue(float(weights[weight_key]))
        spin.setDecimals(3)
        spin.setKeyboardTracking(False)
        spin.editingFinished.connect(
            lambda rn=role_name, k=weight_key, s=spin, field=field_name: save_role_weight_value(
                page, rn, k, s.value(), data, field
            )
        )
        weight_row.addWidget(spin)
        del_weight_btn = QPushButton("×")
        del_weight_btn.setObjectName("btnSm")
        del_weight_btn.setMinimumSize(28, 28)
        del_weight_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        del_weight_btn.clicked.connect(
            lambda checked=False, rn=role_name, k=weight_key, field=field_name: del_weight(
                page, rn, k, data, lambda active=rn: rebuild_all_tabs(active), field
            )
        )
        weight_row.addWidget(del_weight_btn)
        form_layout.addLayout(weight_row)


def _add_custom_target_suit_row(page, role_name, role_data, form_layout):
    combo = NoWheelComboBox()
    combo.setMaxVisibleItems(8)
    combo.addItem("请选择套装", "")
    for label, suit_id in getattr(page, "_config_suit_choices", ()):
        combo.addItem(str(label), str(suit_id))
    target_suit_id = str(role_data.get("target_suit_id") or "")
    index = combo.findData(target_suit_id)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.setToolTip("自建角色的默认计算套装；角色管理可在单次计算中临时调整。")

    def update_target_suit(_index: int) -> None:
        selected = str(combo.currentData() or "")
        if selected == str(role_data.get("target_suit_id") or ""):
            return
        role_data["target_suit_id"] = selected
        page._config_dirty_target_suit_ids.add(int(role_data["character_id"]))
        page._config_form_data = getattr(page, "_config_form_data", {})
        page._config_dirty = True

    combo.currentIndexChanged.connect(update_target_suit)
    _field("默认计算套装", combo, form_layout)


def _add_custom_board(page, role_name, role_data, form_layout):
    board_header = QHBoxLayout()
    board_header.addWidget(QLabel("底盘矩阵：必须保持20格"))
    board_header.addStretch()
    enabled_count_label = QLabel()
    enabled_count_label.setObjectName("customRoleBoardEnabledCount")
    board_header.addWidget(enabled_count_label)
    form_layout.addLayout(board_header)
    grid = QGridLayout()
    cells = role_data.get("board_cells") or []
    by_position = {(cell["row"], cell["column"]): cell for cell in cells}

    def enabled_count() -> int:
        return sum(
            bool(cell.get("is_enabled")) for cell in by_position.values()
        )

    def refresh_count() -> None:
        count = enabled_count()
        enabled_count_label.setText(f"已启用 {count}/20")
        enabled_count_label.setStyleSheet(themed_style(
            "color:#3fb950;font-weight:700" if count == 20 else
            "color:#f85149;font-weight:700"
        ))

    for row in range(1, 6):
        for column in range(1, 6):
            cell = by_position.setdefault((row, column), {"row": row, "column": column, "is_enabled": row <= 4, "is_locked": False})
            button = QPushButton()
            button.setProperty("boardRow", row)
            button.setProperty("boardColumn", column)
            button.setFixedSize(34, 30)
            def refresh(target=button, value=cell):
                target.setText("")
                base_style = (
                    "QPushButton{background:#1f6feb;border:1px solid #58a6ff;"
                    "border-radius:6px;} QPushButton:hover{background:#388bfd;}"
                    if value["is_enabled"]
                    else "QPushButton{background:#161b22;border:1px solid #30363d;"
                    "border-radius:6px;} QPushButton:hover{background:#21262d;}"
                )
                # Inline styles are refreshed after a global theme switch.
                # Keep the dark-theme source so an unselected cell does not
                # retain its black-theme fill after switching to white.
                target.setProperty("_nte_base_style", base_style)
                target.setStyleSheet(themed_style(base_style))
            def toggle(_checked=False, value=cell, refresh_callback=refresh):
                if not value["is_enabled"] and enabled_count() >= 20:
                    # A custom chassis has exactly 20 playable cells.  Prevent
                    # the accidental 21st selection that otherwise surfaces as
                    # a later, less actionable SQLite validation error.
                    refresh_count()
                    return
                value["is_enabled"] = not value["is_enabled"]
                role_data["board_cells"] = list(by_position.values())
                page._config_dirty_board_ids.add(int(role_data["character_id"]))
                page._config_dirty = True
                refresh_callback()
                refresh_count()
            refresh()
            button.clicked.connect(toggle)
            grid.addWidget(button, row - 1, column - 1)
    refresh_count()
    form_layout.addLayout(grid)


def _populate_config_role_tab(page, data, role_name, tab_scroll, rebuild_all_tabs):
    if tab_scroll.property("loaded"):
        return
    role_data = data[role_name]
    tab_widget = QWidget()
    tab_scroll.setWidget(tab_widget)
    tab_scroll.setProperty("loaded", True)

    form_layout = QVBoxLayout(tab_widget)
    form_layout.setSpacing(12)
    form_layout.setContentsMargins(12, 12, 12, 12)

    source_text = f"角色：{role_data['name_zh']}　当前账号权重设置"
    if not role_data.get("is_custom"):
        source_text += "\n额外形状：发行静态资源库 · 只读"
    source = QLabel(source_text)
    source.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    form_layout.addWidget(source)
    if role_data.get("is_custom"):
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("自建可计算角色：没有发行模板；请在此设置默认计算套装与额外形状。"))
        custom_row.addStretch()
        delete_btn = QPushButton("删除角色")
        delete_btn.setObjectName("btnDanger")
        delete_btn.clicked.connect(
            lambda: delete_custom_role(page, role_name, role_data, rebuild_all_tabs)
        )
        custom_row.addWidget(delete_btn)
        form_layout.addLayout(custom_row)
        _add_custom_target_suit_row(page, role_name, role_data, form_layout)
        _add_extra_shape_row(page, data, role_name, role_data, form_layout)
        _add_extra_shape_buff_row(
            page, data, role_name, role_data, form_layout, rebuild_all_tabs,
        )
        _add_custom_board(page, role_name, role_data, form_layout)
    else:
        for label, widget in official_shape_display_rows(page, role_data):
            _field(label, widget, form_layout)
    _add_role_weight_group(page, data, role_name, role_data, form_layout, rebuild_all_tabs, "卡带主词条权重", "main_weights", "+ 添加主词条")
    _add_role_weight_group(page, data, role_name, role_data, form_layout, rebuild_all_tabs, "副词条权重", "weights", "+ 添加副词条")
    form_layout.addStretch()


def render_roles_form(page, data, active_role=None):
    all_names = list(data.keys())
    roles_tabs = QTabWidget()
    page.config_roles_tabs = roles_tabs
    tab_indices = {}

    def filter_tabs(filter_text=""):
        keyword = filter_text.strip()
        tab_indices.clear()
        for index in range(roles_tabs.count()):
            tab = roles_tabs.widget(index)
            if tab:
                tab_indices[tab.property("role_key")] = index
        for role_name, index in tab_indices.items():
            visible = match_pinyin(data[role_name]["name_zh"], keyword) if keyword else True
            roles_tabs.setTabVisible(index, visible)

    def load_current_tab():
        index = roles_tabs.currentIndex()
        if index < 0:
            return
        tab_scroll = roles_tabs.widget(index)
        role_name = tab_scroll.property("role_key") if tab_scroll else ""
        if role_name in data:
            page._config_active_role = role_name
            _populate_config_role_tab(page, data, role_name, tab_scroll, rebuild_all_tabs)

    def rebuild_all_tabs(active_role=None):
        nonlocal all_names
        while roles_tabs.count():
            tab = roles_tabs.widget(0)
            roles_tabs.removeTab(0)
            if tab:
                tab.deleteLater()
        tab_indices.clear()
        all_names = list(data.keys())

        for role_name in all_names:
            tab_scroll = QScrollArea()
            tab_scroll.setWidgetResizable(True)
            tab_scroll.setProperty("role_key", role_name)
            tab_scroll.setProperty("loaded", False)
            index = roles_tabs.addTab(tab_scroll, data[role_name]["name_zh"])
            tab_indices[role_name] = index

        role_search = getattr(page, "config_role_search", None)
        filter_tabs(role_search.text() if role_search is not None else "")
        if active_role in tab_indices:
            roles_tabs.setCurrentIndex(tab_indices[active_role])
        load_current_tab()

    rebuild_all_tabs(active_role)
    page._filter_config_roles = filter_tabs
    roles_tabs.currentChanged.connect(lambda _index: load_current_tab())
    roles_tabs.setMovable(False)
    page.config_form_layout.addWidget(roles_tabs)


def add_weight(page, rn, data, cb, weight_field="weights"):
    choices = (
        getattr(page, "_config_weight_main_choices", ())
        if weight_field == "main_weights"
        else getattr(page, "_config_weight_sub_choices", ())
    )
    existing = set(data.get(rn, {}).get(weight_field, {}))
    available = [
        (label, property_id)
        for label, property_id in choices
        if property_id not in existing
    ]
    if not available:
        QMessageBox.information(page, "提示", "所有词条已添加。")
        return
    label, accepted = QInputDialog.getItem(
        page, "添加词条", "选择词条:", [row[0] for row in available], 0, False,
    )
    if accepted:
        property_id = dict(available).get(str(label))
        if property_id:
            data[rn].setdefault(weight_field, {})[property_id] = 0.5
            page._config_dirty_character_ids.add(int(data[rn]["character_id"]))
            _stage_weight_draft(page, data)
            cb()


def _mark_shape_bonus_dirty(page, role_data):
    if not role_data.get("is_custom"):
        return
    if getattr(page, "_current_config_name", "") != _ACCOUNT_WEIGHT_CONFIG:
        return
    page._config_dirty_shape_bonus_ids.add(int(role_data["character_id"]))


def save_extra_shape_label(page, rn, value, data):
    if rn not in data:
        return
    if not data[rn].get("is_custom"):
        return
    normalized = str(value or "").strip()
    if data[rn].get("extra_shape_label", "") == normalized:
        return
    data[rn]["extra_shape_label"] = normalized
    _mark_shape_bonus_dirty(page, data[rn])
    page._config_form_data = data
    page._config_dirty = True


def save_single_extra_shape_bonus(page, rn, property_id, value, data):
    """只暂存一个额外形状加成，显式保存时才交给 Service 写入。"""
    if rn not in data:
        return
    if not data[rn].get("is_custom"):
        return
    normalized_property = str(property_id or "")
    normalized_value = round(float(value), 3)
    bonuses = {normalized_property: normalized_value} if normalized_property else {}
    if data[rn].get("extra_shape_buffs") == bonuses:
        return
    data[rn]["extra_shape_buffs"] = bonuses
    _mark_shape_bonus_dirty(page, data[rn])
    page._config_form_data = data
    page._config_dirty = True


def save_role_weight_value(page, rn, key, value, data, weight_field="weights"):
    if rn in data and key in data[rn].get(weight_field, {}):
        normalized_value = round(float(value), 3)
        if data[rn][weight_field][key] == normalized_value:
            return
        data[rn][weight_field][key] = normalized_value
        if getattr(page, "_current_config_name", "") == _ACCOUNT_WEIGHT_CONFIG:
            page._config_dirty_character_ids.add(int(data[rn]["character_id"]))
        _stage_weight_draft(page, data)


def del_weight(page, rn, key, data, cb, weight_field="weights"):
    if rn in data and key in data[rn].get(weight_field, {}):
        del data[rn][weight_field][key]
        if getattr(page, "_current_config_name", "") == _ACCOUNT_WEIGHT_CONFIG:
            page._config_dirty_character_ids.add(int(data[rn]["character_id"]))
        _stage_weight_draft(page, data)
        cb()


def _stage_weight_draft(page, data):
    name = getattr(page, "_current_config_name", None)
    if not name:
        return
    page._config_form_data = data
    page._config_dirty = True
