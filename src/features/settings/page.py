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
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)


def build_settings_page(window, app_version, get_paths, iter_image_files, netdisk_url=""):
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(16)

    log_card = window._card("运行日志设置")
    log_row = QHBoxLayout()
    log_row.addWidget(QLabel("实时日志输出:"))
    log_toggle = QCheckBox("启用运行日志")
    log_toggle.setChecked(window._log_enabled)
    log_toggle.toggled.connect(window._toggle_log)
    log_row.addWidget(log_toggle)
    log_row.addStretch()
    log_card.layout().addLayout(log_row)
    layout.addWidget(log_card)

    hotkey_card = window._card("快捷键绑定")
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

    save_hotkeys = QPushButton("保存快捷键")
    save_hotkeys.setObjectName("btnPrimary")
    save_hotkeys.clicked.connect(window._save_hotkeys)
    form.addRow(save_hotkeys)
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
    netdisk_btn = QPushButton("网盘下载")
    netdisk_btn.clicked.connect(lambda: window._open_url(netdisk_url) if netdisk_url else None)
    update_row.addWidget(window._check_update_btn)
    update_row.addWidget(netdisk_btn)
    update_row.addWidget(home_btn)
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
        ("刷新统计", window._refresh_ss),
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

    layout.addStretch()
    return scroll
