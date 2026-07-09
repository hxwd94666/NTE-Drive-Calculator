# 构建角色和套装配置编辑页面。
"""Configuration page builders for roles.json and sets.json."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, SearchableComboBox, match_pinyin
from src.app.theme import theme_color, themed_style
from src.domain.stat_catalog import StatCatalog
from src.storage.json_store import read_json, write_json_atomic


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
    top_row.addWidget(QLabel("编辑配置文件:"))
    window.config_tabs = QComboBox()
    window.config_tabs.addItems(["roles.json", "sets.json"])
    window.config_tabs.currentTextChanged.connect(window._switch_config_form)
    top_row.addWidget(window.config_tabs)

    window.config_add_btn = QPushButton("+ 添加角色")
    window.config_add_btn.setObjectName("btnPrimary")
    window.config_add_btn.clicked.connect(window._config_add_item)
    top_row.addWidget(window.config_add_btn)
    top_row.addStretch()

    reset_btn = QPushButton("重置")
    reset_btn.setObjectName("btnDanger")
    reset_btn.clicked.connect(window._reset_config_form)
    top_row.addWidget(reset_btn)

    save_btn = QPushButton("保存")
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
    if hasattr(window, "config_tabs"):
        switch_config_form(window, window.config_tabs.currentText(), config_dir)


def load_config_data(name, config_dir):
    return read_json(config_dir / name, default={})


def confirm_pending_config_changes(window, config_dir):
    if not getattr(window, "_config_dirty", False):
        return True
    current_name = getattr(window, "_current_config_name", None)
    if not current_name:
        return True
    ret = QMessageBox.question(
        window,
        "未保存配置",
        f"{current_name} 有未保存修改，是否先保存？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if ret == QMessageBox.Cancel:
        return False
    if ret == QMessageBox.Save:
        save_config_form(window, config_dir, None)
    else:
        window._config_dirty = False
        window._config_form_data = None
    return True


def switch_config_form(window, name, config_dir, use_draft=False, active_role=None):
    if not name:
        return
    current_name = getattr(window, "_current_config_name", None)
    if current_name and current_name != name and not confirm_pending_config_changes(window, config_dir):
        if hasattr(window, "config_tabs"):
            window.config_tabs.blockSignals(True)
            window.config_tabs.setCurrentText(current_name)
            window.config_tabs.blockSignals(False)
        return
    if current_name and current_name != name and getattr(window, "_config_dirty", False):
        ret = QMessageBox.question(
            window,
            "未保存配置",
            f"{current_name} 有未保存修改，是否先保存？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if ret == QMessageBox.Cancel:
            if hasattr(window, "config_tabs"):
                window.config_tabs.blockSignals(True)
                window.config_tabs.setCurrentText(current_name)
                window.config_tabs.blockSignals(False)
            return
        if ret == QMessageBox.Save:
            save_config_form(window, config_dir, None)
        else:
            window._config_dirty = False
    if name == "roles.json":
        window.config_add_btn.setText("+ 添加角色")
    elif name == "sets.json":
        window.config_add_btn.setText("+ 添加套装")

    while window.config_form_layout.count():
        item = window.config_form_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    path = config_dir / name
    if not path.exists():
        window.config_form_layout.addWidget(QLabel(f"文件不存在: {name}"))
        return

    if hasattr(window, "config_form_area"):
        window.config_form_area.setUpdatesEnabled(False)
    if use_draft and name == current_name and getattr(window, "_config_dirty", False) and hasattr(window, "_config_form_data"):
        data = window._config_form_data
    else:
        data = load_config_data(name, config_dir)
    window._current_config_name = name
    window._config_form_data = data
    if name != current_name:
        window._config_dirty = False
    if name == "roles.json":
        render_roles_form(window, data, active_role=active_role)
    elif name == "sets.json":
        render_sets_form(window, data)
    if hasattr(window, "config_form_area"):
        window.config_form_area.setUpdatesEnabled(True)


def _add_section(title):
    group = QGroupBox(title)
    layout = QVBoxLayout(group)
    return group, layout


def _field(label, widget, layout):
    row = QHBoxLayout()
    row.addWidget(QLabel(label))
    row.addWidget(widget, 1)
    layout.addLayout(row)


def _board_lock_icon(locked: bool) -> QIcon:
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(theme_color("#6e7681" if locked else "#58a6ff"))
    painter.setPen(QPen(color, 2))
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(5, 10, 12, 8), 2, 2)
    painter.setBrush(Qt.NoBrush)
    if locked:
        painter.drawArc(QRectF(7, 4, 8, 10), 0, 180 * 16)
        painter.drawLine(7, 9, 7, 12)
        painter.drawLine(15, 9, 15, 12)
    else:
        painter.drawArc(QRectF(8, 4, 8, 10), 40 * 16, 180 * 16)
        painter.drawLine(8, 10, 8, 12)
    painter.end()
    return QIcon(pixmap)


def _add_role_config_header(window, data, role_name, form_layout, rebuild_all_tabs):
    role_header = QHBoxLayout()
    role_header.addWidget(QLabel(f"角色: {role_name}"))
    role_header.addStretch()
    del_btn = QPushButton("删除此角色")
    del_btn.setObjectName("btnDanger")
    del_btn.clicked.connect(lambda checked=False, rn=role_name: window._del_role(rn, data, rebuild_all_tabs))
    role_header.addWidget(del_btn)
    form_layout.addLayout(role_header)


def _add_default_set_row(window, data, role_name, role_data, form_layout):
    set_combo = SearchableComboBox()
    for set_name in window.all_set_names:
        set_combo.addItem(set_name, set_name)
    set_combo.refresh_search_items()
    if role_data.get("default_set", "") in window.all_set_names:
        set_combo.setCurrentText(role_data.get("default_set", ""))
    set_combo.activated.connect(
        lambda _idx, rn=role_name, c=set_combo: window._save_role_field(
            rn, "default_set", c.currentData() or c.currentText(), data
        )
    )
    if set_combo.lineEdit():
        set_combo.lineEdit().editingFinished.connect(
            lambda rn=role_name, c=set_combo: window._save_role_field(rn, "default_set", c.currentText(), data)
        )
    _field("默认套装", set_combo, form_layout)


def _add_extra_shape_row(window, data, role_name, role_data, form_layout):
    extra_combo = NoWheelComboBox()
    extra_combo.addItems(["Type-2", "Type-3", "Type-4"])
    extra_combo.setCurrentText(role_data.get("extra_shape_label", ""))
    extra_combo.currentTextChanged.connect(
        lambda text, rn=role_name: window._save_role_field(rn, "extra_shape_label", text, data)
    )
    _field("额外形状标签", extra_combo, form_layout)


def _add_extra_shape_buff_row(window, data, role_name, role_data, form_layout):
    buff_row = QHBoxLayout()
    buff_row.setSpacing(8)
    buff_row.addWidget(QLabel("额外形状加成"))
    buff_stat_combo = SearchableComboBox()
    for stat in window._stat_choice_pool():
        buff_stat_combo.addItem(stat, stat)
    buff_stat_combo.refresh_search_items()
    extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
    current_buff = (
        next(iter(extra_buffs.items()), ("", 0.0))
        if isinstance(extra_buffs, dict) and extra_buffs
        else ("", 0.0)
    )
    if current_buff[0]:
        buff_stat_combo.setCurrentText(current_buff[0])
    else:
        buff_stat_combo.setCurrentIndex(-1)
        buff_stat_combo.setEditText("")
    buff_value = NoWheelDoubleSpinBox()
    buff_value.setRange(-99999, 99999)
    buff_value.setDecimals(2)
    buff_value.setSingleStep(1.0)
    buff_value.setValue(float(current_buff[1] or 0))
    buff_value.setMinimumWidth(96)
    buff_value.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    buff_value.setKeyboardTracking(False)
    buff_stat_combo.activated.connect(
        lambda _idx, rn=role_name, c=buff_stat_combo, s=buff_value: window._save_single_extra_shape_buff(
            rn, c.currentData() or c.currentText(), s.value(), data
        )
    )
    if buff_stat_combo.lineEdit():
        buff_stat_combo.lineEdit().editingFinished.connect(
            lambda rn=role_name, c=buff_stat_combo, s=buff_value: window._save_single_extra_shape_buff(
                rn, c.currentText(), s.value(), data
            )
        )
    buff_value.editingFinished.connect(
        lambda rn=role_name, c=buff_stat_combo, s=buff_value: window._save_single_extra_shape_buff(
            rn, c.currentText(), s.value(), data
        )
    )
    buff_row.addWidget(buff_stat_combo, 1)
    buff_row.addWidget(buff_value)
    form_layout.addLayout(buff_row)


def _add_board_matrix_editor(window, data, role_name, role_data, form_layout):
    board_header = QHBoxLayout()
    board_header.addWidget(QLabel("底盘矩阵 (0=空格, -1=锁定):"))
    board_header.addStretch()
    board_lock_btn = QPushButton()
    board_lock_btn.setObjectName("btnBoardLock")
    board_lock_btn.setStyleSheet("QPushButton#btnBoardLock{padding:4px;border-radius:6px}")
    board_lock_btn.setCheckable(True)
    board_lock_btn.setChecked(True)
    board_lock_btn.setText("")
    board_lock_btn.setIcon(_board_lock_icon(True))
    board_lock_btn.setIconSize(QSize(18, 18))
    board_lock_btn.setMinimumSize(34, 30)
    board_lock_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    board_lock_btn.setToolTip("默认锁定，点击后才可修改底盘矩阵。")
    board_header.addWidget(board_lock_btn)
    form_layout.addLayout(board_header)

    board_matrix = role_data.get("board_matrix", [[0] * 5 for _ in range(5)])
    board_widget = QWidget()
    board_grid = QGridLayout(board_widget)
    board_grid.setSpacing(2)
    board_combos = []
    for row in range(5):
        for col in range(5):
            value = str(board_matrix[row][col]) if row < len(board_matrix) and col < len(board_matrix[row]) else "0"
            combo = QComboBox()
            combo.addItems(["-1", "0"])
            combo.setCurrentText(value)
            combo.setMinimumWidth(52)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            combo.setEnabled(False)
            combo.currentTextChanged.connect(
                lambda text, rn=role_name, r=row, c=col: window._save_role_board_cell(rn, r, c, text, data)
            )
            board_combos.append(combo)
            board_grid.addWidget(combo, row, col)
    for col in range(5):
        board_grid.setColumnStretch(col, 1)

    def set_board_locked(locked):
        for item in board_combos:
            item.setEnabled(not locked)
        board_lock_btn.setIcon(_board_lock_icon(locked))
        board_lock_btn.setToolTip(
            "底盘矩阵已锁定，点击后才可修改。" if locked else "底盘矩阵可修改，点击重新锁定。"
        )

    board_lock_btn.toggled.connect(set_board_locked)
    set_board_locked(True)
    form_layout.addWidget(board_widget)


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
        empty_label = QLabel("暂无配置")
        empty_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        form_layout.addWidget(empty_label)
        return
    for weight_key in sorted(weights.keys()):
        weight_row = QHBoxLayout()
        weight_row.setSpacing(6)
        weight_row.addWidget(QLabel(weight_key))
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

    _add_role_config_header(window, data, role_name, form_layout, rebuild_all_tabs)
    _add_default_set_row(window, data, role_name, role_data, form_layout)
    _add_extra_shape_row(window, data, role_name, role_data, form_layout)
    _add_extra_shape_buff_row(window, data, role_name, role_data, form_layout)
    _add_board_matrix_editor(window, data, role_name, role_data, form_layout)
    _add_role_weight_group(window, data, role_name, role_data, form_layout, rebuild_all_tabs, "副词条权重", "weights", "+ 添加副词条")
    _add_role_weight_group(window, data, role_name, role_data, form_layout, rebuild_all_tabs, "卡带主词条权重", "main_weights", "+ 添加主词条")
    form_layout.addStretch()


def render_roles_form(window, data, active_role=None):
    header = QHBoxLayout()
    role_search = QLineEdit()
    role_search.setPlaceholderText("搜索角色（支持拼音）...")
    role_search.setClearButtonEnabled(True)
    header.addWidget(role_search)
    header.addStretch()
    window.config_form_layout.addLayout(header)

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
            index = roles_tabs.addTab(tab_scroll, role_name)
            tab_indices[role_name] = index

        filter_tabs(role_search.text())
        if active_role in tab_indices:
            roles_tabs.setCurrentIndex(tab_indices[active_role])
        load_current_tab()

    rebuild_all_tabs(active_role)
    role_search.textChanged.connect(filter_tabs)
    roles_tabs.currentChanged.connect(lambda _index: load_current_tab())
    roles_tabs.setMovable(True)

    def on_tab_moved(_from_idx, _to_idx):
        ordered_names = []
        for index in range(roles_tabs.count()):
            tab = roles_tabs.widget(index)
            role_name = tab.property("role_name") if tab else ""
            if role_name in data:
                ordered_names.append(role_name)
        if not ordered_names:
            return
        reordered = {name: data[name] for name in ordered_names}
        for name, value in data.items():
            if name not in reordered:
                reordered[name] = value
        data.clear()
        data.update(reordered)
        tab_indices.clear()
        for index in range(roles_tabs.count()):
            tab = roles_tabs.widget(index)
            if tab:
                tab_indices[tab.property("role_name")] = index
        window._config_form_data = data
        window._config_dirty = True

    roles_tabs.tabBar().tabMoved.connect(on_tab_moved)
    window.config_form_layout.addWidget(roles_tabs)


def render_sets_form(window, data):
    sets_data = data.get("sets", {})
    header = QHBoxLayout()
    set_search = QLineEdit()
    set_search.setPlaceholderText("搜索套装（支持拼音）...")
    set_search.setClearButtonEnabled(True)
    header.addWidget(set_search)
    header.addStretch()
    window.config_form_layout.addLayout(header)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll_widget = QWidget()
    scroll_layout = QVBoxLayout(scroll_widget)
    set_groups = {}

    for set_name in sorted(sets_data.keys()):
        set_info = sets_data[set_name]
        group, group_layout = _add_section(set_name)
        set_groups[set_name] = group

        set_header = QHBoxLayout()
        set_header.addWidget(QLabel(f"套装名称: {set_name}"))
        set_header.addStretch()
        del_btn = QPushButton("删除")
        del_btn.setObjectName("btnDanger")
        del_btn.clicked.connect(lambda checked=False, sn=set_name: window._del_set(sn, sets_data))
        set_header.addWidget(del_btn)
        group_layout.addLayout(set_header)

        shapes_edit = QLineEdit()
        shapes_edit.setText(", ".join(set_info.get("shapes", [])))
        group_layout.addWidget(QLabel("形状列表（逗号分隔）:"))
        group_layout.addWidget(shapes_edit)

        save_btn = QPushButton("保存形状列表")
        save_btn.setObjectName("btnAction")
        save_btn.clicked.connect(
            lambda checked=False, sn=set_name, se=shapes_edit, sdata=sets_data: window._save_set_shapes(sn, se, sdata)
        )
        group_layout.addWidget(save_btn)
        scroll_layout.addWidget(group)

    def filter_sets(filter_text=""):
        keyword = filter_text.strip()
        for set_name, group in set_groups.items():
            group.setVisible(match_pinyin(set_name, keyword) if keyword else True)

    set_search.textChanged.connect(filter_sets)
    scroll_layout.addStretch()
    scroll.setWidget(scroll_widget)
    window.config_form_layout.addWidget(scroll)


def config_add_item(window, config_dir):
    name = getattr(window, "_current_config_name", "")
    if name == "roles.json":
        data = {}
        path = config_dir / name
        if getattr(window, "_config_dirty", False) and hasattr(window, "_config_form_data"):
            data = window._config_form_data
        elif path.exists():
            data = read_json(path, default={})
        add_role(window, data, config_dir)
    elif name == "sets.json":
        data = {}
        path = config_dir / name
        if getattr(window, "_config_dirty", False) and hasattr(window, "_config_form_data"):
            raw = window._config_form_data
            data = raw.get("sets", {}) if isinstance(raw, dict) else {}
        elif path.exists():
            raw = read_json(path, default={})
            data = raw.get("sets", {})
        add_set(window, data, config_dir)


def _main_weight_choice_pool(config_dir):
    stats_path = config_dir / "stats.json"
    if not stats_path.exists():
        return []
    catalog = StatCatalog.from_config_dir(config_dir)
    pool = list(catalog.tape_main_stats or [])
    pool.extend((catalog.tape_main_values or {}).keys())
    return sorted(s for s in dict.fromkeys(str(stat).strip() for stat in pool if str(stat).strip()))


def add_weight(window, rn, data, cb, config_dir, weight_field="weights"):
    stats_path = config_dir / "stats.json"
    pool = []
    if stats_path.exists():
        if weight_field == "main_weights":
            pool = _main_weight_choice_pool(config_dir)
        else:
            pool = StatCatalog.from_config_dir(config_dir).weight_choice_pool()
    existing = set(data[rn].get(weight_field, {}).keys())
    available = [s for s in pool if s not in existing]
    if not available:
        QMessageBox.information(window, "提示", "所有词条已添加。")
        return
    name, ok = QInputDialog.getItem(window, "添加词条", "选择词条:", available, 0, False)
    if ok and name.strip():
        data[rn].setdefault(weight_field, {})[name.strip()] = 0.5
        save_config_data(window, data, config_dir)
        cb()


def stat_choice_pool(window):
    pool = set()
    if isinstance(window.stats_config, dict):
        pool.update((window.stats_config.get("gold_base_values", {}) or {}).keys())
        pool.update((window.stats_config.get("tape_main_stat_values", {}) or {}).keys())
    if window.scoring_engine:
        catalog = getattr(window.scoring_engine, "stat_catalog", None)
        if catalog:
            pool.update(catalog.weight_choice_pool())
        else:
            pool.update(getattr(window.scoring_engine, "gold_base_values", {}).keys())
    return sorted(s for s in pool if s)


def save_single_extra_shape_buff(window, rn, raw_stat, value, data, config_dir):
    if rn not in data:
        return
    raw = str(raw_stat or "").strip()
    if not raw:
        if data[rn].pop("extra_shape_buffs", None) is not None:
            save_config_data(window, data, config_dir)
        return
    pool = stat_choice_pool(window)
    stat = next((s for s in pool if s == raw or match_pinyin(s, raw)), raw)
    data[rn]["extra_shape_buffs"] = {stat: round(float(value), 2)}
    save_config_data(window, data, config_dir)


def save_role_weight_value(window, rn, key, value, data, config_dir, weight_field="weights"):
    if rn in data and key in data[rn].get(weight_field, {}):
        data[rn][weight_field][key] = round(float(value), 3)
        save_config_data(window, data, config_dir)


def save_role_board_cell(window, rn, row, col, value, data, config_dir):
    if rn not in data:
        return
    try:
        cell_value = int(value)
    except (TypeError, ValueError):
        cell_value = 0
    cell_value = -1 if cell_value == -1 else 0
    matrix = data[rn].get("board_matrix")
    if not isinstance(matrix, list):
        matrix = []
    normalized = []
    for r in range(5):
        source_row = matrix[r] if r < len(matrix) and isinstance(matrix[r], list) else []
        normalized.append([
            int(source_row[c]) if c < len(source_row) and str(source_row[c]) in ("-1", "0") else 0
            for c in range(5)
        ])
    if not 0 <= row < 5 or not 0 <= col < 5:
        return
    if normalized[row][col] == cell_value:
        data[rn]["board_matrix"] = normalized
        return
    normalized[row][col] = cell_value
    data[rn]["board_matrix"] = normalized
    save_config_data(window, data, config_dir)


def save_role_field(window, rn, key, value, data, config_dir):
    if rn not in data:
        return
    value = str(value or "").strip()
    if key == "default_set":
        value = next((s for s in window.all_set_names if s == value or match_pinyin(s, value)), value)
    if data[rn].get(key) == value:
        return
    data[rn][key] = value
    save_config_data(window, data, config_dir)


def del_weight(window, rn, key, data, cb, config_dir, weight_field="weights"):
    if rn in data and key in data[rn].get(weight_field, {}):
        del data[rn][weight_field][key]
        save_config_data(window, data, config_dir)
        cb()


def add_role(window, data, config_dir):
    name, ok = QInputDialog.getText(window, "添加角色", "角色名称:")
    role_name = name.strip()
    if ok and role_name and role_name not in data:
        data[role_name] = {
            "role_name": role_name,
            "default_set": window.all_set_names[0] if window.all_set_names else "",
            "extra_shape_label": "",
            "extra_shape_buffs": {},
            "board_matrix": [[0] * 5 for _ in range(5)],
            "weights": {},
            "main_weights": {},
        }
        save_config_data(window, data, config_dir)
        switch_config_form(window, "roles.json", config_dir, use_draft=True, active_role=role_name)


def del_role(window, rn, data, config_dir, cb=None):
    if QMessageBox.question(window, "确认", f"确定删除角色「{rn}」？") == QMessageBox.Yes:
        if rn in data:
            del data[rn]
        save_config_data(window, data, config_dir)
        if cb:
            cb()
        else:
            switch_config_form(window, "roles.json", config_dir, use_draft=True)


def save_set_shapes(window, set_name, line_edit, sd, config_dir):
    shapes_text = line_edit.text().strip()
    shapes = [s.strip() for s in shapes_text.split(",") if s.strip()]
    sd[set_name]["shapes"] = shapes
    save_config_data(window, {"sets": sd}, config_dir)
    QMessageBox.information(window, "保存", f"套装「{set_name}」形状列表已保存")


def add_set(window, sd, config_dir):
    name, ok = QInputDialog.getText(window, "添加套装", "套装名称:")
    if ok and name.strip() and name.strip() not in sd:
        sd[name.strip()] = {"set_name": name.strip(), "shapes": []}
        save_config_data(window, {"sets": sd}, config_dir)
        switch_config_form(window, "sets.json", config_dir, use_draft=True)


def del_set(window, sn, sd, config_dir):
    if QMessageBox.question(window, "确认", f"确定删除套装「{sn}」？") == QMessageBox.Yes:
        if sn in sd:
            del sd[sn]
        save_config_data(window, {"sets": sd}, config_dir)
        switch_config_form(window, "sets.json", config_dir, use_draft=True)


def save_config_form(window, config_dir, json_edit_dialog_cls):
    name = getattr(window, "_current_config_name", None)
    if not name:
        return
    path = config_dir / name
    data = getattr(window, "_config_form_data", None)
    if data is None:
        data = read_json(path, default={})
    write_json_atomic(path, data, indent=4)
    window._config_dirty = False
    window._load_data()
    QMessageBox.information(window, "保存", f"{name} 已保存")


def reset_config_form(window, config_dir, bundled_config_dir):
    name = getattr(window, "_current_config_name", None)
    if name not in {"roles.json", "sets.json"}:
        return
    source_path = bundled_config_dir / name
    if not source_path.exists():
        QMessageBox.warning(window, "无法重置", f"找不到默认配置文件：{source_path}")
        return
    ret = QMessageBox.warning(
        window,
        "确认重置配置",
        f"将把当前 {name} 恢复为程序默认配置。\n\n"
        "这会覆盖当前账号中的同名配置文件，未保存的修改也会丢失。确定继续吗？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    data = read_json(source_path, default={})
    write_json_atomic(config_dir / name, data, indent=4)
    window._config_form_data = data
    window._config_dirty = False
    window._load_data()
    switch_config_form(window, name, config_dir)
    QMessageBox.information(window, "重置完成", f"{name} 已恢复为默认配置。")


def save_config_data(window, data, config_dir):
    name = getattr(window, "_current_config_name", None)
    if not name:
        return
    window._config_form_data = data
    window._config_dirty = True
