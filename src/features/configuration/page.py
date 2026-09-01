# 构建账号 SQLite 词条权重编辑页面。
"""Configuration page builder for account-scoped SQLite weights."""

from __future__ import annotations

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

from src.i18n import tr, display_term
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, match_pinyin
from src.app.theme import themed_style
from src.features.configuration.controller import BasicWeightController
from src.features.configuration.dependencies import BasicWeightDependencies


_ACCOUNT_WEIGHT_CONFIG = "account_weights"


def _basic_weight_controller(window) -> BasicWeightController:
    dependencies = BasicWeightDependencies.from_app_context(window.app_context)
    controller = getattr(window, "_basic_weight_controller", None)
    if (
        not isinstance(controller, BasicWeightController)
        or controller.dependencies != dependencies
    ):
        controller = BasicWeightController(dependencies)
        window._basic_weight_controller = controller
    return controller


def build_config_page(window):
    page = QWidget()
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
    return_button = QPushButton(tr("← 返回角色"))
    return_button.setObjectName("returnToRoleButton")
    return_button.clicked.connect(lambda: window._go("my_role"))
    top_row.addWidget(return_button)

    window.config_role_search = QLineEdit()
    window.config_role_search.setPlaceholderText(tr("搜索角色（支持拼音）..."))
    window.config_role_search.setClearButtonEnabled(True)
    window.config_role_search.textChanged.connect(
        lambda text: getattr(window, "_filter_config_roles", lambda _text: None)(text)
    )
    top_row.addWidget(window.config_role_search, 1)

    top_row.addStretch()

    reset_current_btn = QPushButton(tr("重置当前"))
    reset_current_btn.setObjectName("btnDanger")
    reset_current_btn.clicked.connect(window._reset_current_config_weights)
    top_row.addWidget(reset_current_btn)

    reset_all_btn = QPushButton(tr("重置所有"))
    reset_all_btn.setObjectName("btnDanger")
    reset_all_btn.clicked.connect(window._reset_all_config_weights)
    top_row.addWidget(reset_all_btn)

    new_btn = QPushButton(tr("新建"))
    new_btn.setObjectName("btnNew")
    new_btn.clicked.connect(lambda: create_custom_role(window))
    top_row.addWidget(new_btn)

    save_btn = QPushButton(tr("保存"))
    save_btn.setObjectName("btnPrimary")
    save_btn.clicked.connect(window._save_config_form)
    top_row.addWidget(save_btn)
    layout.addLayout(top_row)

    window.config_form_area = QScrollArea()
    window.config_form_area.setWidgetResizable(True)
    window.config_form_widget = QWidget()
    window.config_form_layout = QVBoxLayout(window.config_form_widget)
    window.config_form_area.setWidget(window.config_form_widget)
    layout.addWidget(window.config_form_area, 1)
    return page


def refresh_config_forms(window, config_dir):
    if hasattr(window, "config_form_layout"):
        switch_config_form(window, _ACCOUNT_WEIGHT_CONFIG, config_dir)


def confirm_pending_config_changes(window, config_dir):
    if not getattr(window, "_config_dirty", False):
        return True
    current_name = getattr(window, "_current_config_name", None)
    if not current_name:
        return True
    ret = QMessageBox.question(
        window,
        tr("未保存配置"),
        tr("当前账号词条权重有未保存修改，是否先保存？"),
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if ret == QMessageBox.Cancel:
        _basic_weight_controller(window).log_dirty_exit(
            "cancel",
            len(getattr(window, "_config_dirty_character_ids", set())),
        )
        return False
    if ret == QMessageBox.Save:
        _basic_weight_controller(window).log_dirty_exit(
            "save",
            len(getattr(window, "_config_dirty_character_ids", set())),
        )
        save_config_form(window, config_dir, None)
    else:
        _basic_weight_controller(window).log_dirty_exit(
            "discard",
            len(getattr(window, "_config_dirty_character_ids", set())),
        )
        window._config_dirty = False
        window._config_form_data = None
    return True


def switch_config_form(window, name=_ACCOUNT_WEIGHT_CONFIG, config_dir=None, use_draft=False, active_role=None):
    """显示当前账号的 SQLite 词条权重；不再提供 JSON 配置入口。"""
    if name != _ACCOUNT_WEIGHT_CONFIG:
        return
    current_name = getattr(window, "_current_config_name", None)
    if current_name and current_name != name and not confirm_pending_config_changes(window, config_dir):
        return
    if current_name and current_name != name and getattr(window, "_config_dirty", False):
        ret = QMessageBox.question(
            window,
            tr("未保存配置"),
            tr("{name} 有未保存修改，是否先保存？", name=display_term(str(current_name))),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if ret == QMessageBox.Cancel:
            return
        if ret == QMessageBox.Save:
            save_config_form(window, config_dir, None)
        else:
            window._config_dirty = False
    while window.config_form_layout.count():
        item = window.config_form_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    if hasattr(window, "config_form_area"):
        window.config_form_area.setUpdatesEnabled(False)
    if use_draft and name == current_name and getattr(window, "_config_dirty", False) and hasattr(window, "_config_form_data"):
        data = window._config_form_data
    else:
        loaded = _basic_weight_controller(window).load_form_data()
        data = loaded["roles"]
        window._config_weight_property_labels = loaded["property_labels"]
        window._config_weight_sub_choices = loaded["sub_choices"]
        window._config_weight_main_choices = loaded["main_choices"]
        window._config_shape_bonus_choices = loaded["shape_bonus_choices"]
        window._config_shape_label_choices = loaded["shape_label_choices"]
        window._config_suit_choices = loaded["suit_choices"]
        window._config_dirty_character_ids = set()
        window._config_dirty_shape_bonus_ids = set()
        window._config_dirty_board_ids = set()
        window._config_dirty_target_suit_ids = set()
    window._current_config_name = name
    window._config_form_data = data
    if name != current_name:
        window._config_dirty = False
    render_roles_form(window, data, active_role=active_role)
    if hasattr(window, "config_form_area"):
        window.config_form_area.setUpdatesEnabled(True)


def _field(label, widget, layout):
    row = QHBoxLayout()
    row.addWidget(QLabel(label))
    row.addWidget(widget, 1)
    layout.addLayout(row)


def _add_extra_shape_row(window, data, role_name, role_data, form_layout):
    value = NoWheelComboBox()
    value.setMaxVisibleItems(6)
    value.addItems(getattr(window, "_config_shape_label_choices", ()))
    choices = tuple(getattr(window, "_config_shape_label_choices", ()))
    current_label = str(role_data.get("extra_shape_label") or "") or (
        choices[0] if choices else "Type-3"
    )
    role_data["extra_shape_label"] = current_label
    value.setCurrentText(current_label)
    value.setPlaceholderText(tr("选择额外形状标签"))
    ownership = (
        tr("点击“保存”后写入当前账号的自建角色数据。")
        if role_data.get("is_custom")
        else tr("点击“保存”后写入本机公共额外形状覆盖库，所有账号共用。")
    )
    value.setToolTip(tr("选择额外形状标签；{ownership}", ownership=ownership))
    value.currentTextChanged.connect(
        lambda text, rn=role_name: save_extra_shape_label(window, rn, text, data)
    )
    _field(tr("额外形状标签"), value, form_layout)


def _add_extra_shape_buff_row(
    window, data, role_name, role_data, form_layout, rebuild_all_tabs,
):
    """Render the one extra-shape bonus as an attribute/value pair.

    Extra shapes have one bonus slot.  Keeping it as a pair avoids the former
    misleading multi-row editor and prevents a draft from silently accumulating
    mutually unrelated bonuses.
    """
    extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
    selected_property, selected_value = next(iter(extra_buffs.items()), ("", 0.0))
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("额外形状加成：")))
    property_combo = NoWheelComboBox()
    property_combo.setMaxVisibleItems(6)
    property_combo.addItem(tr("无加成"), "")
    for label, property_id in getattr(window, "_config_shape_bonus_choices", ()):
        property_combo.addItem(display_term(str(label)), str(property_id))
    selected_index = property_combo.findData(str(selected_property))
    property_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
    ownership = (
        tr("点击“保存”后写入当前账号的自建角色数据。")
        if role_data.get("is_custom")
        else tr("点击“保存”后写入本机公共额外形状覆盖库，所有账号共用。")
    )
    property_combo.setToolTip(tr("选择额外形状提供的属性；{ownership}", ownership=ownership))
    row.addWidget(property_combo, 1)
    value_spin = NoWheelDoubleSpinBox()
    value_spin.setRange(0, 1000000)
    value_spin.setDecimals(3)
    value_spin.setSingleStep(0.1)
    value_spin.setValue(float(selected_value))
    value_spin.setKeyboardTracking(False)
    value_spin.setEnabled(bool(property_combo.currentData()))
    value_spin.setToolTip(tr("额外形状加成数值；{ownership}", ownership=ownership))
    row.addWidget(value_spin)
    form_layout.addLayout(row)

    def update_bonus(*_args) -> None:
        property_id = str(property_combo.currentData() or "")
        value_spin.setEnabled(bool(property_id))
        save_single_extra_shape_bonus(
            window, role_name, property_id, value_spin.value(), data,
        )

    property_combo.currentIndexChanged.connect(update_bonus)
    value_spin.editingFinished.connect(update_bonus)


def _add_role_weight_group(window, data, role_name, role_data, form_layout, rebuild_all_tabs, title, field_name, add_label):
    weights_header = QHBoxLayout()
    weights_header.addWidget(QLabel(f"{title}:"))
    weights_header.addStretch()
    add_weight_btn = QPushButton(add_label)
    add_weight_btn.setObjectName("btnAction")
    add_weight_btn.clicked.connect(
        lambda checked=False, rn=role_name, field=field_name: window._add_weight(
            rn, data, lambda active=rn: rebuild_all_tabs(active), field
        )
    )
    weights_header.addWidget(add_weight_btn)
    form_layout.addLayout(weights_header)

    weights = role_data.get(field_name, {}) or {}
    if not weights:
        empty_label = QLabel(tr("暂无配置"))
        empty_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        form_layout.addWidget(empty_label)
        return
    for weight_key in sorted(weights.keys()):
        weight_row = QHBoxLayout()
        weight_row.setSpacing(6)
        weight_row.addWidget(QLabel(display_term(
            getattr(window, "_config_weight_property_labels", {}).get(
                weight_key, weight_key
            )
        )))
        spin = NoWheelDoubleSpinBox()
        spin.setRange(0, 10)
        spin.setSingleStep(0.05)
        spin.setValue(float(weights[weight_key]))
        spin.setDecimals(3)
        spin.setKeyboardTracking(False)
        spin.editingFinished.connect(
            lambda rn=role_name, k=weight_key, s=spin, field=field_name: window._save_role_weight_value(
                rn, k, s.value(), data, field
            )
        )
        weight_row.addWidget(spin)
        del_weight_btn = QPushButton("×")
        del_weight_btn.setObjectName("btnSm")
        del_weight_btn.setMinimumSize(28, 28)
        del_weight_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        del_weight_btn.clicked.connect(
            lambda checked=False, rn=role_name, k=weight_key, field=field_name: window._del_weight(
                rn, k, data, lambda active=rn: rebuild_all_tabs(active), field
            )
        )
        weight_row.addWidget(del_weight_btn)
        form_layout.addLayout(weight_row)


def _add_custom_target_suit_row(window, role_name, role_data, form_layout):
    combo = NoWheelComboBox()
    combo.setMaxVisibleItems(8)
    combo.addItem(tr("请选择套装"), "")
    for label, suit_id in getattr(window, "_config_suit_choices", ()):
        combo.addItem(str(label), str(suit_id))
    target_suit_id = str(role_data.get("target_suit_id") or "")
    index = combo.findData(target_suit_id)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.setToolTip(tr("自建角色的默认计算套装；角色管理可在单次计算中临时调整。"))

    def update_target_suit(_index: int) -> None:
        selected = str(combo.currentData() or "")
        if selected == str(role_data.get("target_suit_id") or ""):
            return
        role_data["target_suit_id"] = selected
        window._config_dirty_target_suit_ids.add(int(role_data["character_id"]))
        window._config_form_data = getattr(window, "_config_form_data", {})
        window._config_dirty = True

    combo.currentIndexChanged.connect(update_target_suit)
    _field(tr("默认计算套装"), combo, form_layout)


def _add_custom_board(window, role_name, role_data, form_layout):
    board_header = QHBoxLayout()
    board_header.addWidget(QLabel(tr("底盘矩阵：必须保持20格")))
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
        enabled_count_label.setText(tr("已启用 {count}/20", count=count))
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
                window._config_dirty_board_ids.add(int(role_data["character_id"]))
                window._config_dirty = True
                refresh_callback()
                refresh_count()
            refresh()
            button.clicked.connect(toggle)
            grid.addWidget(button, row - 1, column - 1)
    refresh_count()
    form_layout.addLayout(grid)


def _populate_config_role_tab(window, data, role_name, tab_scroll, rebuild_all_tabs):
    if tab_scroll.property("loaded"):
        return
    role_data = data[role_name]
    tab_widget = QWidget()
    tab_scroll.setWidget(tab_widget)
    tab_scroll.setProperty("loaded", True)

    form_layout = QVBoxLayout(tab_widget)
    form_layout.setSpacing(12)
    form_layout.setContentsMargins(12, 12, 12, 12)

    source_text = (
        tr("角色：{role}　当前账号 SQLite 权重设置（初始来源：{source}）",
           role=display_term(str(role_name)),
           source=role_data.get("source_kind") or "default")
    )
    if not role_data.get("is_custom"):
        source_text += tr("\n额外形状：全部账号共用 · {origin}",
                          origin=tr("本机覆盖")
                          if role_data.get("shape_bonus_source") == "shared_override"
                          else tr("发行默认"))
    source = QLabel(source_text)
    source.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    form_layout.addWidget(source)
    if role_data.get("is_custom"):
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel(tr("自建可计算角色：没有发行模板；请在此设置默认计算套装与额外形状。")))
        custom_row.addStretch()
        delete_btn = QPushButton(tr("删除角色"))
        delete_btn.setObjectName("btnDanger")
        delete_btn.clicked.connect(
            lambda: delete_custom_role(window, role_name, role_data, rebuild_all_tabs)
        )
        custom_row.addWidget(delete_btn)
        form_layout.addLayout(custom_row)
        _add_custom_target_suit_row(window, role_name, role_data, form_layout)
        _add_extra_shape_row(window, data, role_name, role_data, form_layout)
        _add_extra_shape_buff_row(
            window, data, role_name, role_data, form_layout, rebuild_all_tabs,
        )
        _add_custom_board(window, role_name, role_data, form_layout)
    else:
        _add_extra_shape_row(window, data, role_name, role_data, form_layout)
        _add_extra_shape_buff_row(
            window, data, role_name, role_data, form_layout, rebuild_all_tabs,
        )
    _add_role_weight_group(window, data, role_name, role_data, form_layout, rebuild_all_tabs, tr("卡带主词条权重"), "main_weights", tr("+ 添加主词条"))
    _add_role_weight_group(window, data, role_name, role_data, form_layout, rebuild_all_tabs, tr("副词条权重"), "weights", tr("+ 添加副词条"))
    form_layout.addStretch()


def render_roles_form(window, data, active_role=None):
    all_names = list(data.keys())
    roles_tabs = QTabWidget()
    tab_indices = {}

    def filter_tabs(filter_text=""):
        keyword = filter_text.strip()
        tab_indices.clear()
        for index in range(roles_tabs.count()):
            tab = roles_tabs.widget(index)
            if tab:
                tab_indices[tab.property("role_name")] = index
        for role_name, index in tab_indices.items():
            visible = match_pinyin(role_name, keyword) if keyword else True
            roles_tabs.setTabVisible(index, visible)

    def load_current_tab():
        index = roles_tabs.currentIndex()
        if index < 0:
            return
        tab_scroll = roles_tabs.widget(index)
        role_name = tab_scroll.property("role_name") if tab_scroll else ""
        if role_name in data:
            window._config_active_role = role_name
            _populate_config_role_tab(window, data, role_name, tab_scroll, rebuild_all_tabs)

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
            tab_scroll.setProperty("role_name", role_name)
            tab_scroll.setProperty("loaded", False)
            index = roles_tabs.addTab(tab_scroll, display_term(role_name))
            tab_indices[role_name] = index

        role_search = getattr(window, "config_role_search", None)
        filter_tabs(role_search.text() if role_search is not None else "")
        if active_role in tab_indices:
            roles_tabs.setCurrentIndex(tab_indices[active_role])
        load_current_tab()

    rebuild_all_tabs(active_role)
    window._filter_config_roles = filter_tabs
    roles_tabs.currentChanged.connect(lambda _index: load_current_tab())
    roles_tabs.setMovable(False)
    window.config_form_layout.addWidget(roles_tabs)


def add_weight(window, rn, data, cb, config_dir, weight_field="weights"):
    choices = (
        getattr(window, "_config_weight_main_choices", ())
        if weight_field == "main_weights"
        else getattr(window, "_config_weight_sub_choices", ())
    )
    existing = set(data.get(rn, {}).get(weight_field, {}))
    available = [
        (label, property_id)
        for label, property_id in choices
        if property_id not in existing
    ]
    if not available:
        QMessageBox.information(window, tr("提示"), tr("所有词条已添加。"))
        return
    label, accepted = QInputDialog.getItem(
        window, tr("添加词条"), tr("选择词条:"),
        [display_term(str(row[0])) for row in available], 0, False,
    )
    if accepted:
        property_id = dict(available).get(str(label))
        if property_id:
            data[rn].setdefault(weight_field, {})[property_id] = 0.5
            window._config_dirty_character_ids.add(int(data[rn]["character_id"]))
            save_config_data(window, data, config_dir)
            cb()


def _mark_shape_bonus_dirty(window, role_data):
    if getattr(window, "_current_config_name", "") != _ACCOUNT_WEIGHT_CONFIG:
        return
    window._config_dirty_shape_bonus_ids.add(int(role_data["character_id"]))


def save_extra_shape_label(window, rn, value, data):
    if rn not in data:
        return
    normalized = str(value or "").strip()
    if data[rn].get("extra_shape_label", "") == normalized:
        return
    data[rn]["extra_shape_label"] = normalized
    _mark_shape_bonus_dirty(window, data[rn])
    window._config_form_data = data
    window._config_dirty = True


def create_custom_role(window):
    name, accepted = QInputDialog.getText(
        window,
        tr("新建角色"),
        tr("角色名称（也作为游戏内名称）："),
    )
    if not accepted:
        return
    try:
        role = _basic_weight_controller(window).create_custom_role(name)
    except Exception as exc:
        QMessageBox.warning(window, tr("新建角色失败"), str(exc))
        return
    window._config_dirty = False
    window._config_form_data = None
    reload_data = getattr(window, "_load_data", None)
    if callable(reload_data):
        reload_data(reload_priority=False)
    switch_config_form(
        window,
        _ACCOUNT_WEIGHT_CONFIG,
        active_role=str(role["name_zh"]),
    )


def delete_custom_role(window, role_name, role_data, rebuild_all_tabs):
    if QMessageBox.question(
        window,
        tr("删除角色"),
        tr("删除 [{role}] 以及它的计算偏好和配装槽位？", role=display_term(str(role_name))),
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    ) != QMessageBox.Yes:
        return
    character_id = int(role_data["character_id"])
    try:
        _basic_weight_controller(window).delete_custom_role(character_id)
    except Exception as exc:
        QMessageBox.warning(window, tr("删除角色失败"), str(exc))
        return
    window._config_dirty = False
    window._config_dirty_character_ids.discard(character_id)
    window._config_dirty_board_ids.discard(character_id)
    data = getattr(window, "_config_form_data", {}) or {}
    data.pop(role_name, None)
    rebuild_all_tabs()


def save_single_extra_shape_bonus(window, rn, property_id, value, data):
    """Stage the sole account-level extra-shape bonus until Save is clicked."""
    if rn not in data:
        return
    normalized_property = str(property_id or "")
    normalized_value = round(float(value), 3)
    bonuses = {normalized_property: normalized_value} if normalized_property else {}
    if data[rn].get("extra_shape_buffs") == bonuses:
        return
    data[rn]["extra_shape_buffs"] = bonuses
    _mark_shape_bonus_dirty(window, data[rn])
    window._config_form_data = data
    window._config_dirty = True


def save_role_weight_value(window, rn, key, value, data, config_dir, weight_field="weights"):
    if rn in data and key in data[rn].get(weight_field, {}):
        normalized_value = round(float(value), 3)
        if data[rn][weight_field][key] == normalized_value:
            return
        data[rn][weight_field][key] = normalized_value
        if getattr(window, "_current_config_name", "") == _ACCOUNT_WEIGHT_CONFIG:
            window._config_dirty_character_ids.add(int(data[rn]["character_id"]))
        save_config_data(window, data, config_dir)


def del_weight(window, rn, key, data, cb, config_dir, weight_field="weights"):
    if rn in data and key in data[rn].get(weight_field, {}):
        del data[rn][weight_field][key]
        if getattr(window, "_current_config_name", "") == _ACCOUNT_WEIGHT_CONFIG:
            window._config_dirty_character_ids.add(int(data[rn]["character_id"]))
        save_config_data(window, data, config_dir)
        cb()


def save_config_form(window, config_dir, json_edit_dialog_cls):
    name = getattr(window, "_current_config_name", None)
    if not name:
        return
    if name != _ACCOUNT_WEIGHT_CONFIG:
        return
    data = getattr(window, "_config_form_data", {}) or {}
    dirty_ids = set(getattr(window, "_config_dirty_character_ids", set()))
    shape_bonus_dirty_ids = set(
        getattr(window, "_config_dirty_shape_bonus_ids", set())
    )
    board_dirty_ids = set(getattr(window, "_config_dirty_board_ids", set()))
    target_suit_dirty_ids = set(
        getattr(window, "_config_dirty_target_suit_ids", set())
    )
    try:
        _basic_weight_controller(window).save_changes(
            data,
            dirty_ids,
            shape_bonus_dirty_ids,
            board_dirty_ids,
            target_suit_dirty_ids,
        )
    except Exception as exc:
        QMessageBox.warning(window, tr("保存失败"), str(exc))
        return
    window._config_dirty = False
    for field_name in (
        "_config_dirty_character_ids",
        "_config_dirty_shape_bonus_ids",
        "_config_dirty_board_ids",
        "_config_dirty_target_suit_ids",
    ):
        dirty_values = getattr(window, field_name, None)
        if dirty_values is not None:
            dirty_values.clear()
    reload_data = getattr(window, "_load_data", None)
    if callable(reload_data):
        reload_data()
    QMessageBox.information(
        window,
        tr("保存"),
        tr("卡带主词条和驱动副词条权重已保存到当前账号 SQLite；额外形状加成已保存到本机公共覆盖库，所有账号共用。"),
    )


def _confirm_weight_reset(window, message: str) -> bool:
    if getattr(window, "_current_config_name", None) != _ACCOUNT_WEIGHT_CONFIG:
        return False
    return QMessageBox.question(
        window, tr("确认重置权重"), message,
        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
    ) == QMessageBox.Yes


def _reload_after_weight_reset(window, config_dir, active_role: str | None) -> None:
    # Reset applies only to persisted account weights.  A full form reload also
    # deliberately discards any draft, making the confirmation above explicit.
    window._config_dirty = False
    window._config_form_data = None
    window._config_dirty_character_ids = set()
    window._config_dirty_shape_bonus_ids = set()
    window._config_dirty_target_suit_ids = set()
    switch_config_form(window, _ACCOUNT_WEIGHT_CONFIG, config_dir, active_role=active_role)
    reload_data = getattr(window, "_load_data", None)
    if callable(reload_data):
        reload_data()


def reset_current_config_weights(window, config_dir):
    role_name = str(getattr(window, "_config_active_role", "") or "")
    data = getattr(window, "_config_form_data", {}) or {}
    role_data = data.get(role_name) or {}
    if not role_name or not role_data:
        QMessageBox.information(window, tr("重置当前"), tr("请先选择一个角色。"))
        return
    if role_data.get("is_custom"):
        QMessageBox.information(window, tr("重置当前"), tr("自建角色没有发行默认权重，保留当前自定义值。"))
        return
    if not _confirm_weight_reset(
        window,
        f"将清除当前账号中 [{role_name}] 的自定义卡带主词条和驱动副词条权重，"
        "并恢复为当前异环工坊默认值。\n\n"
        "额外形状标签和额外形状加成不会改变；当前未保存编辑会丢弃。",
    ):
        return
    try:
        _basic_weight_controller(window).reset_weights(
            (int(role_data["character_id"]),)
        )
    except Exception as exc:
        QMessageBox.warning(window, tr("重置失败"), str(exc))
        return
    _reload_after_weight_reset(window, config_dir, role_name)
    QMessageBox.information(
        window, tr("重置当前"), tr("[{role}] 已恢复为默认权重，后续可随新版本更新。", role=display_term(role_name)),
    )


def reset_all_config_weights(window, config_dir):
    data = getattr(window, "_config_form_data", {}) or {}
    character_ids = [
        int(role_data["character_id"])
        for role_data in data.values()
        if (
            isinstance(role_data, dict)
            and role_data.get("character_id") is not None
            and not role_data.get("is_custom")
        )
    ]
    if not character_ids:
        return
    if not _confirm_weight_reset(
        window,
        tr("将清除当前账号全部 {count} 名角色的自定义卡带主词条和驱动副词条权重，"
           "恢复为当前异环工坊默认值。\n\n"
           "此操作无法撤销；额外形状标签和额外形状加成不会改变，当前未保存编辑会丢弃。", count=len(character_ids)),
    ):
        return
    try:
        restored = _basic_weight_controller(window).reset_weights(character_ids)
    except Exception as exc:
        QMessageBox.warning(window, tr("重置失败"), str(exc))
        return
    active_role = str(getattr(window, "_config_active_role", "") or "")
    _reload_after_weight_reset(window, config_dir, active_role)
    QMessageBox.information(
        window, tr("重置所有"), tr("已恢复 {count} 名角色的默认权重，后续可随新版本更新。", count=len(restored)),
    )


def reset_config_form(window, config_dir, bundled_config_dir):
    """Legacy slot: retain the former discard-draft behavior for callers."""
    del bundled_config_dir
    _reload_after_weight_reset(window, config_dir, None)


def save_config_data(window, data, config_dir):
    name = getattr(window, "_current_config_name", None)
    if not name:
        return
    window._config_form_data = data
    window._config_dirty = True
