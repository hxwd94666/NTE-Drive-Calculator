# 构建设置页的日志、热键、更新和文件管理区域。
"""Settings page builder.

The settings page shows hotkeys, updates, screenshot management, and quick-access
folders. MainWindow still owns all callbacks.
"""

from __future__ import annotations

import os
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)

from src.app.constants import BILIBILI_HOME_URL, NETDISK_DOWNLOAD_LINKS
from src.app.theme import THEME_LABELS, themed_style


DEFAULT_SYNC_SETTINGS = {
    "inventory_sync_method": "nte_core",
    "equipment_apply_method": "nte_core",
    "inventory_settle_seconds": 5.0,
    "capture_device_id": None,
    "auto_start_inventory_sync": False,
    "raw_capture_enabled": False,
}


def _normalize_netdisk_links(netdisk_links=None):
    if netdisk_links is None:
        return tuple(NETDISK_DOWNLOAD_LINKS)
    if isinstance(netdisk_links, str):
        return (("夸克网盘", netdisk_links),) if netdisk_links else tuple()
    return tuple((str(name), str(url)) for name, url in netdisk_links if name and url)


def _move_card_title_to_row(card, title, button):
    layout = card.layout()
    title_label = None
    if layout and layout.count():
        first_item = layout.itemAt(0)
        first_widget = first_item.widget() if first_item else None
        if isinstance(first_widget, QLabel) and first_widget.text() == title:
            layout.takeAt(0)
            title_label = first_widget
    if title_label is None:
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:14px;font-weight:600;color:#58a6ff")

    title_row = QHBoxLayout()
    title_row.setSpacing(10)
    title_row.addWidget(title_label)
    title_row.addWidget(button)
    title_row.addStretch()
    layout.insertLayout(0, title_row)


def build_settings_page(window, app_version, get_paths, iter_image_files, netdisk_links=None):
    page = QWidget()
    page.setObjectName("settingsPage")
    scroll = QScrollArea()
    scroll.setObjectName("settingsScroll")
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    scroll.setStyleSheet(
        themed_style(
            "QScrollArea#settingsScroll{background:#0d1117;border:none}"
            "QWidget#settingsPage{background:#0d1117}"
        )
    )
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(16)

    log_card = window._card("工具设置")
    log_row = QHBoxLayout()
    log_row.addWidget(QLabel("实时日志输出:"))
    log_toggle = QCheckBox("启用运行日志")
    log_toggle.setChecked(window._log_enabled)
    log_toggle.toggled.connect(window._toggle_log)
    window._log_toggle = log_toggle
    log_row.addWidget(log_toggle)
    log_row.addStretch()
    log_card.layout().addLayout(log_row)

    theme_row = QHBoxLayout()
    theme_row.addWidget(QLabel("主题颜色:"))
    current_theme = (getattr(window, "_ui_preferences", {}) or {}).get("theme", "dark")
    dark_radio = QRadioButton(THEME_LABELS["dark"])
    black_radio = QRadioButton(THEME_LABELS["black"])
    light_radio = QRadioButton(THEME_LABELS["light"])
    theme_radios = {"dark": dark_radio, "black": black_radio, "light": light_radio}
    current_radio = theme_radios.get(current_theme, dark_radio)
    current_radio.setChecked(True)

    def select_theme(theme: str):
        if window._set_theme_preference(theme):
            return
        active_theme = (getattr(window, "_ui_preferences", {}) or {}).get("theme", "dark")
        for value, radio in theme_radios.items():
            radio.blockSignals(True)
            radio.setChecked(value == active_theme)
            radio.blockSignals(False)

    dark_radio.toggled.connect(lambda checked: checked and select_theme("dark"))
    black_radio.toggled.connect(lambda checked: checked and select_theme("black"))
    light_radio.toggled.connect(lambda checked: checked and select_theme("light"))
    theme_row.addWidget(dark_radio)
    theme_row.addWidget(black_radio)
    theme_row.addWidget(light_radio)
    theme_row.addStretch()
    log_card.layout().addLayout(theme_row)
    layout.addWidget(log_card)

    sync_card = window._card("背包同步与装配")
    sync_description = QLabel(
        "流式同步会在背包内容连续数秒没有变化后写入 SQLite，并继续后台监听。"
        "原始诊断文件默认关闭。"
    )
    sync_description.setWordWrap(True)
    sync_description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    sync_card.layout().addWidget(sync_description)
    sync_form = QFormLayout()
    sync_form.setSpacing(10)

    settings_reader = getattr(window, "_get_sync_settings", None)
    loaded_settings = settings_reader() if callable(settings_reader) else {}
    settings = {**DEFAULT_SYNC_SETTINGS, **(loaded_settings or {})}
    window._sync_inventory_method_combo = QComboBox()
    window._sync_inventory_method_combo.addItem("本地核心组件流式同步", "nte_core")
    window._sync_inventory_method_combo.addItem("手柄扫描", "gamepad")
    inventory_index = window._sync_inventory_method_combo.findData(
        settings["inventory_sync_method"]
    )
    window._sync_inventory_method_combo.setCurrentIndex(max(0, inventory_index))
    sync_form.addRow("背包获取方式:", window._sync_inventory_method_combo)

    window._sync_apply_method_combo = QComboBox()
    window._sync_apply_method_combo.addItem("本地核心组件一键装配", "nte_core")
    window._sync_apply_method_combo.addItem("手柄装配", "gamepad")
    apply_index = window._sync_apply_method_combo.findData(
        settings["equipment_apply_method"]
    )
    window._sync_apply_method_combo.setCurrentIndex(max(0, apply_index))
    sync_form.addRow("装配执行方式:", window._sync_apply_method_combo)

    window._sync_settle_spin = QDoubleSpinBox()
    window._sync_settle_spin.setRange(1.0, 30.0)
    window._sync_settle_spin.setDecimals(1)
    window._sync_settle_spin.setSingleStep(0.5)
    window._sync_settle_spin.setSuffix(" 秒")
    window._sync_settle_spin.setValue(float(settings["inventory_settle_seconds"]))
    sync_form.addRow("内容稳定等待:", window._sync_settle_spin)

    window._sync_capture_device_edit = QLineEdit()
    window._sync_capture_device_edit.setPlaceholderText("留空表示自动选择网卡")
    window._sync_capture_device_edit.setText(settings.get("capture_device_id") or "")
    sync_form.addRow("抓取网卡:", window._sync_capture_device_edit)

    window._sync_auto_start_toggle = QCheckBox("软件启动后自动在后台等待背包")
    window._sync_auto_start_toggle.setChecked(
        bool(settings["auto_start_inventory_sync"])
    )
    sync_form.addRow("自动启动:", window._sync_auto_start_toggle)

    window._sync_raw_capture_toggle = QCheckBox("保存底层诊断文件（排错时才开启）")
    window._sync_raw_capture_toggle.setChecked(bool(settings["raw_capture_enabled"]))
    sync_form.addRow("诊断文件:", window._sync_raw_capture_toggle)
    sync_card.layout().addLayout(sync_form)

    save_sync_button = QPushButton("保存同步设置")
    save_sync_button.setObjectName("btnPrimary")
    save_sync_handler = getattr(window, "_save_sync_settings", None)
    if callable(save_sync_handler):
        save_sync_button.clicked.connect(save_sync_handler)
    else:
        save_sync_button.setEnabled(False)
        save_sync_button.setToolTip("当前页面宿主未启用 SQLite 同步设置")
    sync_actions = QHBoxLayout()
    sync_actions.addWidget(save_sync_button)
    sync_actions.addStretch()
    sync_card.layout().addLayout(sync_actions)
    layout.addWidget(sync_card)

    hotkey_card = window._card("快捷键绑定")
    save_hotkeys = QPushButton("保存快捷键")
    save_hotkeys.setObjectName("btnPrimary")
    save_hotkeys.setFixedWidth(112)
    save_hotkeys.clicked.connect(window._save_hotkeys)
    _move_card_title_to_row(hotkey_card, "快捷键绑定", save_hotkeys)

    form = QFormLayout()
    form.setSpacing(10)

    cap_row = QHBoxLayout()
    cap_row.setSpacing(8)
    window._hk_capture_edit = QKeySequenceEdit(QKeySequence(window._hk_capture))
    window._hk_capture_edit.setMaximumWidth(160)
    cap_row.addWidget(QLabel("全局截图按键:"))
    cap_row.addWidget(window._hk_capture_edit)
    cap_row.addStretch()
    form.addRow(cap_row)

    finish_row = QHBoxLayout()
    finish_row.setSpacing(8)
    window._hk_finish_edit = QKeySequenceEdit(QKeySequence(window._hk_finish))
    window._hk_finish_edit.setMaximumWidth(160)
    finish_row.addWidget(QLabel("截图完成按键:"))
    finish_row.addWidget(window._hk_finish_edit)
    finish_row.addStretch()
    form.addRow(finish_row)

    stop_row = QHBoxLayout()
    stop_row.setSpacing(8)
    window._hk_stop_edit = QKeySequenceEdit(QKeySequence(window._hk_stop))
    window._hk_stop_edit.setMaximumWidth(160)
    stop_row.addWidget(QLabel("紧急停止按键:"))
    stop_row.addWidget(window._hk_stop_edit)
    stop_row.addStretch()
    form.addRow(stop_row)

    hotkey_card.layout().addLayout(form)
    layout.addWidget(hotkey_card)

    update_card = window._card("软件更新")
    window._update_status = QLabel(f"当前版本: {app_version}")
    update_card.layout().addWidget(window._update_status)
    update_row = QHBoxLayout()
    update_row.setSpacing(10)
    window._check_update_btn = QPushButton("检查更新")
    window._check_update_btn.setObjectName("btnPrimary")
    window._check_update_btn.clicked.connect(lambda: window._check_updates(manual=True))
    home_btn = QPushButton("GitHub 主页")
    home_btn.clicked.connect(window._open_update_homepage)
    bilibili_btn = QPushButton("B站主页")
    bilibili_btn.clicked.connect(
        window._open_bilibili_homepage
        if hasattr(window, "_open_bilibili_homepage")
        else lambda: window._open_url(BILIBILI_HOME_URL)
    )
    netdisk_btn = QPushButton("网盘下载")
    netdisk_options = _normalize_netdisk_links(netdisk_links)
    netdisk_btn.clicked.connect(
        lambda: window._show_netdisk_download_dialog(netdisk_options)
        if hasattr(window, "_show_netdisk_download_dialog") and netdisk_options
        else None
    )
    update_row.addWidget(window._check_update_btn)
    update_row.addWidget(netdisk_btn)
    update_row.addWidget(home_btn)
    update_row.addWidget(bilibili_btn)
    update_row.addStretch()
    update_card.layout().addLayout(update_row)
    layout.addWidget(update_card)

    paths = get_paths()
    screenshot_dir = paths["screenshot_dir"]
    screenshot_files = iter_image_files(screenshot_dir)
    count = len(screenshot_files)
    size_mb = sum(f.stat().st_size for f in screenshot_files) / (1024 * 1024) if screenshot_files else 0

    screenshot_card = window._card("截图文件管理")
    window._ss_info = QLabel(f"当前截图: {count} 个 · {size_mb:.1f} MB")
    screenshot_card.layout().addWidget(window._ss_info)
    screenshot_row = QHBoxLayout()
    screenshot_row.setSpacing(10)
    actions = [
        ("清理所有截图", window._clear_ss),
        ("打开文件夹", lambda: os.startfile(str(get_paths()["screenshot_dir"])) if get_paths()["screenshot_dir"].exists() else None),
    ]
    for text, slot in actions:
        button = QPushButton(text)
        if "清理" in text:
            button.setObjectName("btnDanger")
        button.clicked.connect(slot)
        screenshot_row.addWidget(button)
    screenshot_row.addStretch()
    screenshot_card.layout().addLayout(screenshot_row)
    layout.addWidget(screenshot_card)

    quick_card = window._card("快捷访问")
    quick_row = QHBoxLayout()
    quick_row.setSpacing(10)
    quick_paths = [
        ("config", lambda: get_paths()["config_dir"]),
        ("accounts", lambda: get_paths()["accounts_dir"]),
        ("logs", lambda: get_paths()["log_dir"]),
    ]
    for label, path_factory in quick_paths:
        button = QPushButton(label)
        button.clicked.connect(lambda checked, pf=path_factory: os.startfile(str(pf())) if pf().exists() else None)
        quick_row.addWidget(button)
    quick_row.addStretch()
    quick_card.layout().addLayout(quick_row)
    layout.addWidget(quick_card)

    thanks_card = window._card("致谢")
    thanks_card.layout().setSpacing(12)
    thanks_row = QHBoxLayout()
    thanks_row.setSpacing(8)
    thanks_name = QLabel("异环工坊")
    thanks_name.setStyleSheet(
        themed_style(
            "color:#58a6ff;font-weight:700;background:#0d1f35;"
            "border:1px solid #1f6feb;border-radius:6px;padding:5px 10px"
        )
    )
    thanks_desc = QLabel("提供角色评分标准与词条权重参考")
    thanks_desc.setStyleSheet(
        themed_style(
            "color:#c9d1d9;background:#161b22;"
            "border:1px solid #30363d;border-radius:6px;padding:5px 10px"
        )
    )
    thanks_row.addWidget(thanks_name)
    thanks_row.addWidget(thanks_desc)
    thanks_row.addStretch()
    thanks_card.layout().addLayout(thanks_row)
    layout.addWidget(thanks_card)

    layout.addStretch()
    return scroll
