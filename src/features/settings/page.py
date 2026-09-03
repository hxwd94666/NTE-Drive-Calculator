# 构建设置页的日志、热键、更新和文件管理区域。
"""Settings page builder.

The settings page shows hotkeys, updates, screenshot management, and quick-access
folders. MainWindow still owns all callbacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
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

from src.app.constants import NETDISK_DOWNLOAD_LINKS
from src.app.context import AppContext
from src.app.theme import THEME_LABELS, themed_style
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox


def _normalize_netdisk_links(netdisk_links=None):
    if netdisk_links is None:
        return tuple(NETDISK_DOWNLOAD_LINKS)
    if isinstance(netdisk_links, str):
        return (("夸克网盘", netdisk_links),) if netdisk_links else tuple()
    return tuple((str(name), str(url)) for name, url in netdisk_links if name and url)


def refresh_account_scoped_settings(window) -> None:
    """Refresh already-built settings controls after an account switch."""

    preferences = getattr(window, "_ui_preferences", {}) or {}
    for editor_name, value_name in (
        ("_hk_capture_edit", "_hk_capture"),
        ("_hk_finish_edit", "_hk_finish"),
        ("_hk_stop_edit", "_hk_stop"),
        ("_hk_battle_rerecord_edit", "_hk_battle_rerecord"),
    ):
        hotkey_editor = getattr(window, editor_name, None)
        if hotkey_editor is not None:
            hotkey_editor.blockSignals(True)
            hotkey_editor.setKeySequence(
                QKeySequence(str(getattr(window, value_name, "")))
            )
            hotkey_editor.blockSignals(False)
    edit = getattr(window, "_protagonist_game_name_edit", None)
    if edit is not None:
        edit.blockSignals(True)
        edit.setText(str(preferences.get("protagonist_game_name") or ""))
        edit.blockSignals(False)
    game_edit = getattr(window, "_equipment_plugin_game_executable_edit", None)
    if game_edit is not None:
        game_edit.blockSignals(True)
        game_edit.setText(
            str(preferences.get("equipment_plugin_game_executable") or "")
        )
        game_edit.blockSignals(False)
    method_combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    if method_combo is not None:
        method_combo.blockSignals(True)
        method_index = method_combo.findData(
            preferences.get("equipment_plugin_loading_method") or "proxy"
        )
        method_combo.setCurrentIndex(max(0, method_index))
        method_combo.blockSignals(False)
    consent = getattr(window, "_equipment_plugin_consent", None)
    if consent is not None:
        consent.blockSignals(True)
        consent.setChecked(
            bool(preferences.get("equipment_plugin_risk_acknowledged", False))
        )
        consent.blockSignals(False)
    refresh_plugin = getattr(window, "_refresh_equipment_plugin_status", None)
    if callable(refresh_plugin):
        refresh_plugin()


@dataclass(frozen=True)
class SettingsPaths:
    config_dir: Path
    accounts_dir: Path
    log_dir: Path
    screenshot_dir: Path


def _settings_paths(context: AppContext) -> SettingsPaths:
    return SettingsPaths(
        config_dir=context.paths.config_dir,
        accounts_dir=context.paths.accounts_dir,
        log_dir=context.account.log_dir,
        screenshot_dir=context.account.screenshot_dir,
    )


def _build_sync_card(window):
    card = window._card("背包同步")
    description = QLabel(
        "流式同步会在背包内容连续数秒没有变化后写入 SQLite，并继续后台监听。"
        "原始诊断文件默认关闭。"
    )
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    card.layout().addWidget(description)
    form = QFormLayout()
    form.setSpacing(10)

    settings_reader = getattr(window, "_get_sync_settings", None)
    settings = settings_reader() if callable(settings_reader) else {}
    if not settings:
        raise RuntimeError("无法读取静态数据库中的设置默认值。")
    window._sync_inventory_method_combo = NoWheelComboBox()
    window._sync_inventory_method_combo.addItem("本地核心组件流式同步", "nte_core")
    window._sync_inventory_method_combo.addItem("手柄扫描", "gamepad")
    inventory_index = window._sync_inventory_method_combo.findData(
        settings["inventory_sync_method"]
    )
    window._sync_inventory_method_combo.setCurrentIndex(max(0, inventory_index))
    form.addRow("背包获取方式:", window._sync_inventory_method_combo)

    window._sync_settle_spin = NoWheelDoubleSpinBox()
    window._sync_settle_spin.setRange(1.0, 30.0)
    window._sync_settle_spin.setDecimals(1)
    window._sync_settle_spin.setSingleStep(0.5)
    window._sync_settle_spin.setSuffix(" 秒")
    window._sync_settle_spin.setValue(float(settings["inventory_settle_seconds"]))
    form.addRow("内容稳定等待:", window._sync_settle_spin)

    window._snapshot_retention_spin = NoWheelSpinBox()
    window._snapshot_retention_spin.setRange(1, 365)
    window._snapshot_retention_spin.setValue(
        int(settings["inventory_snapshot_retention_count"])
    )
    window._snapshot_retention_spin.setSuffix(" 份")
    window._snapshot_retention_spin.setToolTip(
        "始终保留当前快照和已保存装配方案引用的快照。"
    )
    form.addRow("历史快照保留:", window._snapshot_retention_spin)

    window._sync_capture_device_edit = QLineEdit()
    window._sync_capture_device_edit.setPlaceholderText("特殊情况所需，请勿随意填写此空")
    window._sync_capture_device_edit.setText(settings.get("capture_device_id") or "")
    form.addRow("抓取网卡:", window._sync_capture_device_edit)

    window._sync_auto_start_toggle = QCheckBox("软件启动后自动在后台等待背包")
    window._sync_auto_start_toggle.setChecked(
        bool(settings["auto_start_inventory_sync"])
    )
    form.addRow("自动启动:", window._sync_auto_start_toggle)

    window._sync_raw_capture_toggle = QCheckBox(
        "保存原始抓包（.pcapng，排错时才开启）"
    )
    window._sync_raw_capture_toggle.setChecked(
        bool(settings["raw_capture_enabled"])
    )
    window._sync_raw_capture_toggle.setToolTip(
        "背包同步和战报采集都会按各自启动时的设置保存原始包；"
        "文件仅保存到当前账号的 logs/nte_core/raw_capture。"
        "采集结束后自动保留最近 5 份，并优先将历史文件压至 512 MiB；"
        "正在写入和最新的一份不会被删除。"
    )
    raw_capture_row = QHBoxLayout()
    raw_capture_row.addWidget(window._sync_raw_capture_toggle)
    raw_capture_open_button = QPushButton("打开抓包目录")
    raw_capture_open_handler = getattr(window, "_open_raw_capture_directory", None)
    if callable(raw_capture_open_handler):
        raw_capture_open_button.clicked.connect(raw_capture_open_handler)
    else:
        raw_capture_open_button.setEnabled(False)
    raw_capture_row.addWidget(raw_capture_open_button)
    raw_capture_row.addStretch()
    form.addRow("诊断抓包:", raw_capture_row)
    card.layout().addLayout(form)

    save_button = QPushButton("保存同步设置")
    save_button.setObjectName("btnPrimary")
    save_handler = getattr(window, "_save_sync_settings", None)
    if callable(save_handler):
        save_button.clicked.connect(save_handler)
    else:
        save_button.setEnabled(False)
        save_button.setToolTip("当前页面宿主未启用 SQLite 同步设置")
    prune_button = QPushButton("清理历史快照")
    prune_button.setObjectName("btnDanger")
    prune_handler = getattr(window, "_prune_inventory_snapshots", None)
    if callable(prune_handler):
        prune_button.clicked.connect(prune_handler)
    else:
        prune_button.setEnabled(False)
        prune_button.setToolTip("当前页面宿主未启用 SQLite 快照维护")
    window._prune_snapshots_button = prune_button
    actions = QHBoxLayout()
    actions.addWidget(save_button)
    actions.addWidget(prune_button)
    actions.addStretch()
    card.layout().addLayout(actions)
    return card


def _build_environment_card(window):
    card = window._card("环境配置")
    window._environment_configuration_card = card
    npcap_title = QLabel("Npcap · 背包同步必需")
    npcap_title.setStyleSheet(themed_style("font-weight:700;font-size:14px"))
    card.layout().addWidget(npcap_title)
    npcap_description = QLabel(
        "Npcap 抓包用于识别背包；虽有一定风险，但低于视觉扫描快照，建议优先使用。"
    )
    npcap_description.setTextFormat(Qt.RichText)
    npcap_description.setWordWrap(False)
    npcap_description.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    card.layout().addWidget(npcap_description)
    npcap_row = QHBoxLayout()
    npcap_install_button = QPushButton("下载 Npcap 1.88")
    npcap_install_button.clicked.connect(window._open_npcap_download)
    npcap_row.addWidget(npcap_install_button)
    npcap_status_button = QPushButton("检测 Npcap 状态")
    npcap_status_button.clicked.connect(window._show_npcap_status)
    npcap_row.addWidget(npcap_status_button)
    window._nte_core_diagnostic_button = QPushButton("诊断 nte-core")
    window._nte_core_diagnostic_button.clicked.connect(window._diagnose_nte_core)
    npcap_row.addWidget(window._nte_core_diagnostic_button)
    npcap_row.addStretch()
    card.layout().addLayout(npcap_row)

    equipment_title = QLabel("装备插件 · 极速装配必需")
    equipment_title.setStyleSheet(themed_style("font-weight:700;font-size:14px"))
    card.layout().addWidget(equipment_title)
    equipment_description = QLabel(
        "<b>简单原理：</b>默认把 dwmapi.dll 放入游戏目录，由游戏代理加载；"
        "少数环境不加载代理 DLL 时，可显式改用管理员 Mod Loader。"
        "<br><span style='color:#d29922'><b>风险提示：</b>该功能会介入游戏进程，但不会直接篡改"
        "游戏数据；仍可能触发游戏保护，产生兼容问题或账号风险。</span>"
    )
    equipment_description.setTextFormat(Qt.RichText)
    equipment_description.setWordWrap(True)
    equipment_description.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    card.layout().addWidget(equipment_description)
    form = QFormLayout()
    window._equipment_plugin_loading_method_combo = NoWheelComboBox()
    window._equipment_plugin_loading_method_combo.addItem(
        "代理 DLL（推荐）", "proxy"
    )
    window._equipment_plugin_loading_method_combo.addItem(
        "Mod Loader（备用）", "loader"
    )
    loading_method = str(
        (getattr(window, "_ui_preferences", {}) or {}).get(
            "equipment_plugin_loading_method"
        )
        or "proxy"
    )
    method_index = window._equipment_plugin_loading_method_combo.findData(
        loading_method
    )
    window._equipment_plugin_loading_method_combo.setCurrentIndex(
        max(0, method_index)
    )
    window._equipment_plugin_loading_method_combo.currentIndexChanged.connect(
        window._equipment_plugin_loading_method_changed
    )
    form.addRow("加载方式:", window._equipment_plugin_loading_method_combo)
    window._equipment_plugin_game_executable_edit = QLineEdit()
    window._equipment_plugin_game_executable_edit.setPlaceholderText(
        "可手动粘贴 HTGame.exe 的完整文件地址"
    )
    window._equipment_plugin_game_executable_edit.setText(
        str(
            (getattr(window, "_ui_preferences", {}) or {}).get(
                "equipment_plugin_game_executable"
            )
            or ""
        )
    )
    window._equipment_plugin_game_executable_edit.textChanged.connect(
        lambda _text: window._refresh_equipment_plugin_status()
    )
    game_picker = QPushButton("选择 HTGame.exe")
    game_picker.clicked.connect(window._select_equipment_plugin_game_executable)
    game_row = QHBoxLayout()
    game_row.addWidget(window._equipment_plugin_game_executable_edit, 1)
    game_row.addWidget(game_picker)
    window._equipment_plugin_detect_button = QPushButton("自动检测")
    window._equipment_plugin_detect_button.clicked.connect(
        window._detect_equipment_plugin_game_executable
    )
    game_row.addWidget(window._equipment_plugin_detect_button)
    form.addRow("游戏主程序:", game_row)
    card.layout().addLayout(form)

    consent_row = QHBoxLayout()
    window._equipment_plugin_consent = QCheckBox(
        "我已阅读并理解上述风险，仍自愿使用装备插件并承担相应风险"
    )
    window._equipment_plugin_consent.setStyleSheet(
        themed_style("color:#d29922;font-weight:600")
    )
    window._equipment_plugin_consent.setChecked(
        bool(
            (getattr(window, "_ui_preferences", {}) or {}).get(
                "equipment_plugin_risk_acknowledged", False
            )
        )
    )
    window._equipment_plugin_consent.toggled.connect(
        window._equipment_plugin_risk_acknowledgement_changed
    )
    consent_row.addWidget(window._equipment_plugin_consent)
    window._dwmapi_diagnostic_button = QPushButton("诊断 dwmapi")
    window._dwmapi_diagnostic_button.clicked.connect(window._diagnose_dwmapi)
    consent_row.addWidget(window._dwmapi_diagnostic_button)
    consent_row.addStretch()
    card.layout().addLayout(consent_row)
    window._equipment_plugin_status_label = QLabel()
    window._equipment_plugin_status_label.setWordWrap(True)
    window._equipment_plugin_status_label.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    card.layout().addWidget(window._equipment_plugin_status_label)
    actions = QHBoxLayout()
    window._equipment_plugin_primary_button = QPushButton("部署代理 DLL")
    window._equipment_plugin_primary_button.setObjectName("btnPrimary")
    window._equipment_plugin_primary_button.clicked.connect(
        window._activate_equipment_plugin_loading_method
    )
    actions.addWidget(window._equipment_plugin_primary_button)
    window._equipment_plugin_stop_button = QPushButton("还原游戏目录")
    window._equipment_plugin_stop_button.setObjectName("btnDanger")
    window._equipment_plugin_stop_button.clicked.connect(
        window._deactivate_equipment_plugin_loading_method
    )
    actions.addWidget(window._equipment_plugin_stop_button)
    actions.addStretch()
    card.layout().addLayout(actions)
    refresher = getattr(window, "_refresh_equipment_plugin_status", None)
    if callable(refresher):
        refresher()
    return card


def build_settings_page(
    window,
    app_version,
    app_context: AppContext,
    iter_image_files,
    netdisk_links=None,
):
    page = QWidget()
    page.setObjectName("settingsPage")
    scroll = QScrollArea()
    scroll.setObjectName("settingsScroll")
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    window._settings_scroll = scroll
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
    window._log_session_status_label = QLabel()
    window._log_session_status_label.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    log_row.addWidget(window._log_session_status_label)
    log_row.addStretch()
    log_card.layout().addLayout(log_row)
    window._refresh_log_session_status()

    protagonist_row = QHBoxLayout()
    protagonist_row.addWidget(QLabel("主角游戏名:"))
    window._protagonist_game_name_edit = QLineEdit()
    window._protagonist_game_name_edit.setPlaceholderText("零在游戏内显示的玩家名字")
    protagonist_name_width = (
        window._protagonist_game_name_edit.fontMetrics().horizontalAdvance("零" * 8) + 36
    )
    window._protagonist_game_name_edit.setFixedWidth(protagonist_name_width)
    window._protagonist_game_name_edit.setText(
        str((getattr(window, "_ui_preferences", {}) or {}).get("protagonist_game_name") or "")
    )

    def save_protagonist_name() -> None:
        preferences = getattr(window, "_ui_preferences", None)
        if not isinstance(preferences, dict):
            return
        preferences["protagonist_game_name"] = window._protagonist_game_name_edit.text().strip()
        # A deliberate edit is an explicit answer, so later automatic
        # assembly should use it without asking again.
        preferences["skip_protagonist_name_prompt"] = bool(
            preferences["protagonist_game_name"]
        )
        window._save_ui_preferences()

    window._protagonist_game_name_edit.editingFinished.connect(save_protagonist_name)
    protagonist_row.addWidget(window._protagonist_game_name_edit)
    protagonist_row.addStretch()
    log_card.layout().addLayout(protagonist_row)

    theme_row = QHBoxLayout()
    theme_row.addWidget(QLabel("主题颜色:"))
    current_theme = getattr(window, "_theme_preference", "black")
    dark_radio = QRadioButton(THEME_LABELS["dark"])
    black_radio = QRadioButton(THEME_LABELS["black"])
    light_radio = QRadioButton(THEME_LABELS["light"])
    theme_radios = {"dark": dark_radio, "black": black_radio, "light": light_radio}
    current_radio = theme_radios.get(current_theme, theme_radios["black"])
    current_radio.setChecked(True)

    def select_theme(theme: str):
        if window._set_theme_preference(theme):
            return
        active_theme = getattr(window, "_theme_preference", "black")
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

    sync_card = _build_sync_card(window)
    plugin_card = _build_environment_card(window)
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

    rerecord_row = QHBoxLayout()
    rerecord_row.setSpacing(8)
    window._hk_battle_rerecord_edit = QKeySequenceEdit(
        QKeySequence(window._hk_battle_rerecord)
    )
    window._hk_battle_rerecord_edit.setMaximumWidth(160)
    window._hk_battle_rerecord_edit.setToolTip(
        "战报采集中需在 1.5 秒内连续按两次才会放弃当前战报并重录。"
    )
    rerecord_row.addWidget(QLabel("战报重录按键:"))
    rerecord_row.addWidget(window._hk_battle_rerecord_edit)
    rerecord_row.addStretch()
    form.addRow(rerecord_row)

    def save_hotkeys_when_complete(_sequence) -> None:
        # QKeySequenceEdit temporarily clears its value before it accepts a
        # replacement shortcut.  Do not persist that transient blank state.
        editors = (
            window._hk_capture_edit,
            window._hk_finish_edit,
            window._hk_stop_edit,
            window._hk_battle_rerecord_edit,
        )
        if all(editor.keySequence().toString().strip() for editor in editors):
            window._save_hotkeys()

    # Every completed edit is persisted immediately, removing a separate save
    # step without treating the editor's intermediate blank state as a value.
    for editor in (
        window._hk_capture_edit,
        window._hk_finish_edit,
        window._hk_stop_edit,
        window._hk_battle_rerecord_edit,
    ):
        editor.keySequenceChanged.connect(save_hotkeys_when_complete)

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
    window._mirror_download_btn = QPushButton("Mirror 下载")
    window._mirror_download_btn.setObjectName("btnPrimary")
    window._mirror_download_btn.clicked.connect(window._start_mirror_download)
    home_btn = QPushButton("GitHub 下载")
    home_btn.clicked.connect(window._open_update_homepage)
    netdisk_btn = QPushButton("网盘下载")
    netdisk_options = _normalize_netdisk_links(netdisk_links)
    netdisk_btn.clicked.connect(
        lambda: window._show_netdisk_download_dialog(netdisk_options)
        if hasattr(window, "_show_netdisk_download_dialog") and netdisk_options
        else None
    )
    update_row.addWidget(window._check_update_btn)
    update_row.addWidget(window._mirror_download_btn)
    update_row.addWidget(netdisk_btn)
    update_row.addWidget(home_btn)
    update_row.addStretch()
    update_card.layout().addLayout(update_row)
    mirror_cdk_row = QHBoxLayout()
    mirror_cdk_row.setSpacing(10)
    mirror_cdk_label = QLabel("Mirror CDK")
    window._mirror_cdk_edit = QLineEdit()
    window._mirror_cdk_edit.setPlaceholderText("填写 Mirror CDK（仅用于请求下载地址）")
    window._mirror_cdk_edit.setEchoMode(QLineEdit.Password)
    window._mirror_cdk_edit.setFixedWidth(360)
    window._mirror_cdk_edit.setText(str(window._update_config.get("mirror_cdk") or ""))
    window._mirror_cdk_edit.editingFinished.connect(window._save_mirror_cdk)
    mirror_cdk_row.addWidget(mirror_cdk_label)
    mirror_cdk_row.addWidget(window._mirror_cdk_edit)
    mirror_cdk_row.addStretch()
    update_card.layout().addLayout(mirror_cdk_row)
    layout.addWidget(update_card)

    about_card = window._card("关于我们")
    about_row = QHBoxLayout()
    about_row.setSpacing(10)
    author_bilibili_btn = QPushButton("作者B站")
    author_bilibili_btn.clicked.connect(window._open_bilibili_homepage)
    project_btn = QPushButton("项目页面")
    project_btn.clicked.connect(window._open_project_homepage)
    support_btn = QPushButton("支持我们")
    support_btn.clicked.connect(window._open_support_homepage)
    group_chat_btn = QPushButton("加入群聊")
    group_chat_btn.clicked.connect(window._show_group_chat_notice)
    about_row.addWidget(author_bilibili_btn)
    about_row.addWidget(project_btn)
    about_row.addWidget(support_btn)
    about_row.addWidget(group_chat_btn)
    about_row.addStretch()
    about_card.layout().addLayout(about_row)
    layout.addWidget(about_card)

    layout.addWidget(plugin_card)
    layout.addWidget(sync_card)

    paths = _settings_paths(app_context)
    screenshot_dir = paths.screenshot_dir
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
        (
            "打开文件夹",
            lambda: os.startfile(str(_settings_paths(app_context).screenshot_dir))
            if _settings_paths(app_context).screenshot_dir.exists()
            else None,
        ),
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
        ("config", lambda: _settings_paths(app_context).config_dir),
        ("accounts", lambda: _settings_paths(app_context).accounts_dir),
        ("logs", lambda: _settings_paths(app_context).log_dir),
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
    toolkit_row = QHBoxLayout()
    toolkit_row.setSpacing(8)
    toolkit_name = QLabel(
        '<a href="https://github.com/kongbaiz/nte-dps-toolkit" '
        'style="color:#58a6ff;text-decoration:none;">nte-dps-toolkit</a>'
    )
    toolkit_name.setTextFormat(Qt.RichText)
    toolkit_name.setTextInteractionFlags(Qt.TextBrowserInteraction)
    toolkit_name.setOpenExternalLinks(True)
    toolkit_name.setStyleSheet(
        themed_style(
            "font-weight:700;background:#0d1f35;border:1px solid #1f6feb;"
            "border-radius:6px;padding:5px 10px"
        )
    )
    toolkit_desc = QLabel("提供协议解析核心程序以及装配插件支持")
    toolkit_desc.setStyleSheet(
        themed_style(
            "color:#c9d1d9;background:#161b22;"
            "border:1px solid #30363d;border-radius:6px;padding:5px 10px"
        )
    )
    toolkit_row.addWidget(toolkit_name)
    toolkit_row.addWidget(toolkit_desc)
    toolkit_row.addStretch()
    thanks_card.layout().addLayout(toolkit_row)
    layout.addWidget(thanks_card)

    layout.addStretch()
    return scroll
