# 构建设置页的日志、热键、更新和文件管理区域。
"""构造设置控件并接入窗口及注入 Controller 的显式动作；业务由对应 owner 执行。"""

from __future__ import annotations
from html import escape

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import rich_label, plain_label, plain_tooltip, resolve_text_catalog
import os
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)

from src.features.settings.work_mode_card import build_work_mode_card
from src.features.settings.language.widget import LanguageSettingsWidget
from src.services.preferences.language_settings_service import LanguageSettingsService
from src.features.settings.performance.card import PerformanceCard
from src.app.constants import NETDISK_DOWNLOAD_LINKS
from src.app.context import AppContext
from src.app.theme import THEME_LABELS, themed_style
from src.ui.common.widgets import NoWheelComboBox


def _normalize_netdisk_links(netdisk_links=None):
    if netdisk_links is None:
        return tuple(NETDISK_DOWNLOAD_LINKS)
    if isinstance(netdisk_links, str):
        return (("夸克网盘", netdisk_links),) if netdisk_links else tuple()
    return tuple((str(name), str(url)) for name, url in netdisk_links if name and url)


def refresh_account_scoped_settings(window) -> None:
    """账号切换后只刷新既有控件，屏蔽回填信号以免触发再次保存。"""

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
            window.work_mode_service.settings.game_executable
        )
        game_edit.blockSignals(False)
    method_combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    if method_combo is not None:
        method_combo.blockSignals(True)
        method_index = method_combo.findData(
            window.work_mode_service.deployment_record.get("loading_method") or "native-capture"
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


def _build_capture_diagnostics_card(window):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    card = window._card(_ui_text('settings.page.build_capture_diagnostics_card.card'))
    description = plain_label(_ui_text('settings.page.build_capture_diagnostics_card.description'))
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    card.layout().addWidget(description)
    form = QFormLayout()
    form.setSpacing(10)

    settings_reader = getattr(window, "_get_sync_settings", None)
    settings = settings_reader() if callable(settings_reader) else {}
    if not settings:
        raise RuntimeError("无法读取静态数据库中的设置默认值。")
    window._sync_capture_device_edit = QLineEdit()
    window._sync_capture_device_edit.setPlaceholderText(_ui_text('settings.page.build_capture_diagnostics_card.sync_capture_device_edit.placeholder'))
    window._sync_capture_device_edit.setText(settings.get("capture_device_id") or "")

    def resize_capture_device_edit(text: str) -> None:
        content = str(text or window._sync_capture_device_edit.placeholderText())
        text_width = window._sync_capture_device_edit.fontMetrics().horizontalAdvance(content)
        window._sync_capture_device_edit.setFixedWidth(
            max(360, min(680, text_width + 36))
        )

    resize_capture_device_edit(window._sync_capture_device_edit.text())
    window._sync_capture_device_edit.textChanged.connect(resize_capture_device_edit)
    form.addRow(_ui_text('settings.page.build_capture_diagnostics_card.form'), window._sync_capture_device_edit)
    save_handler = getattr(window, "_save_capture_diagnostics", None)

    def save_capture_diagnostics() -> None:
        if callable(save_handler):
            settings = save_handler()
            window.performance_controller.capture_changed(settings)
            if settings is None and callable(settings_reader):
                window._sync_raw_capture_toggle.setChecked(bool(settings_reader()["raw_capture_enabled"]))

    window._sync_capture_device_edit.editingFinished.connect(save_capture_diagnostics)

    window._sync_raw_capture_toggle = QCheckBox(_ui_text('settings.page.build_capture_diagnostics_card.sync_raw_capture_toggle'))
    window._sync_raw_capture_toggle.setChecked(
        bool(settings["raw_capture_enabled"])
    )
    window._sync_raw_capture_toggle.setToolTip(
        plain_tooltip(_ui_text('settings.page.build_capture_diagnostics_card.sync_raw_capture_toggle.hint'))
    )
    raw_capture_row = QHBoxLayout()
    raw_capture_row.addWidget(window._sync_raw_capture_toggle)
    raw_capture_open_button = QPushButton(_ui_text('settings.page.build_capture_diagnostics_card.raw_capture_open_button'))
    window._sync_raw_capture_open_button = raw_capture_open_button
    raw_capture_open_handler = getattr(window, "_open_raw_capture_directory", None)
    if callable(raw_capture_open_handler):
        raw_capture_open_button.clicked.connect(raw_capture_open_handler)
    else:
        raw_capture_open_button.setEnabled(False)
    raw_capture_row.addWidget(raw_capture_open_button)
    raw_capture_row.addStretch()
    def save_raw_capture_diagnostics(enabled: bool) -> None:
        window.work_mode_controller.set_raw_capture_draft(enabled)
        if window._sync_raw_capture_toggle.isChecked() == enabled:
            save_capture_diagnostics()

    window._sync_raw_capture_toggle.clicked.connect(save_raw_capture_diagnostics)
    form.addRow(_ui_text('settings.page.build_capture_diagnostics_card.form_2'), raw_capture_row)
    performance_link = QCheckBox(_ui_text('settings.page.build_capture_diagnostics_card.performance_link'))
    performance_link.setToolTip(plain_tooltip(_ui_text('settings.page.build_capture_diagnostics_card.performance_link.hint')))
    performance_link.clicked.connect(window.performance_controller.set_linked)
    def refresh_performance_link():
        performance_link.setChecked(window.performance_controller.snapshot()["linked"])
    window.performance_controller.changed.connect(refresh_performance_link)
    refresh_performance_link()
    form.addRow(_ui_text('settings.page.build_capture_diagnostics_card.form_3'), performance_link)
    card.layout().addLayout(form)
    return card


def _build_environment_card(window):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    card = window._card(_ui_text('settings.page.build_environment_card.card'))
    window._environment_configuration_card = card
    npcap_title = plain_label(_ui_text('settings.page.build_environment_card.npcap_title'))
    npcap_title.setStyleSheet(themed_style("font-weight:700;font-size:14px"))
    card.layout().addWidget(npcap_title)
    npcap_row = QHBoxLayout()
    npcap_install_button = QPushButton(_ui_text('settings.page.build_environment_card.npcap_install_button'))
    window._npcap_install_button = npcap_install_button
    npcap_install_button.clicked.connect(window._open_npcap_download)
    npcap_row.addWidget(npcap_install_button)
    npcap_status_button = QPushButton(_ui_text('settings.page.build_environment_card.npcap_status_button'))
    npcap_status_button.clicked.connect(window._show_npcap_status)
    npcap_row.addWidget(npcap_status_button)
    window._nte_core_diagnostic_button = QPushButton(_ui_text('settings.page.build_environment_card.nte_core_diagnostic_button'))
    window._nte_core_diagnostic_button.clicked.connect(window._diagnose_nte_core)
    npcap_row.addWidget(window._nte_core_diagnostic_button)
    npcap_row.addStretch()
    card.layout().addLayout(npcap_row)

    equipment_title = plain_label(_ui_text('settings.page.build_environment_card.equipment_title'))
    equipment_title.setStyleSheet(themed_style("font-weight:700;font-size:14px"))
    card.layout().addWidget(equipment_title)
    equipment_description = rich_label(
        f"<span style='color:#d29922'><b>{escape(_ui_text('ui.settings.component_risk_prefix'))}</b>"
        f"{escape(_ui_text('ui.settings.component_risk_body'))}</span>"
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
        resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.settings.build_environment_card.equipment_plugin_loading_method_combo'), "native-capture"
    )
    window._equipment_plugin_loading_method_combo.addItem(
        resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.settings.build_environment_card.equipment_plugin_loading_method_combo_2'), "loader"
    )
    loading_method = str(
        window.work_mode_service.deployment_record.get("loading_method")
        or "native-capture"
    )
    method_index = window._equipment_plugin_loading_method_combo.findData(
        loading_method
    )
    window._equipment_plugin_loading_method_combo.setCurrentIndex(
        max(0, method_index)
    )
    window._equipment_plugin_loading_method_combo.setFixedWidth(180)
    window._equipment_plugin_loading_method_combo.currentIndexChanged.connect(
        window._equipment_plugin_loading_method_changed
    )
    loading_row = QHBoxLayout()
    loading_row.addWidget(window._equipment_plugin_loading_method_combo)
    loading_hint = plain_label(_ui_text('settings.page.build_environment_card.loading_hint'))
    loading_hint.setWordWrap(True)
    loading_hint.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    loading_row.addWidget(loading_hint, 1)
    form.addRow(_ui_text('settings.page.build_environment_card.form'), loading_row)
    window._equipment_plugin_game_executable_edit = QLineEdit()
    window._equipment_plugin_game_executable_edit.setPlaceholderText(
        _ui_text('settings.page.build_environment_card.equipment_plugin_game_executable_edit.placeholder')
    )
    window._equipment_plugin_game_executable_edit.setText(window.work_mode_service.settings.game_executable)
    def commit_game_path():
        # 输入完成后先保存规范化身份，再回填并检测；逐字检测会读到上一次保存的路径。
        edit = window._equipment_plugin_game_executable_edit
        window.work_mode_service.set_game_executable(edit.text())
        with QSignalBlocker(edit):
            edit.setText(window.work_mode_service.settings.game_executable)
        window._refresh_equipment_plugin_status()

    window._equipment_plugin_game_executable_edit.editingFinished.connect(commit_game_path)
    game_picker = QPushButton(_ui_text('settings.page.build_environment_card.game_picker'))
    game_picker.clicked.connect(window._select_equipment_plugin_game_executable)
    game_row = QHBoxLayout()
    game_row.addWidget(window._equipment_plugin_game_executable_edit, 1)
    game_row.addWidget(game_picker)
    window._equipment_plugin_detect_button = QPushButton(_ui_text('settings.page.build_environment_card.equipment_plugin_detect_button'))
    window._equipment_plugin_detect_button.clicked.connect(
        window._detect_equipment_plugin_game_executable
    )
    game_row.addWidget(window._equipment_plugin_detect_button)
    form.addRow(_ui_text('settings.page.build_environment_card.form_2'), game_row)
    card.layout().addLayout(form)

    window._equipment_plugin_status_label = plain_label()
    window._equipment_plugin_status_label.setMinimumWidth(0)
    window._equipment_plugin_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    window._equipment_plugin_status_label.setTextFormat(Qt.PlainText)
    window._equipment_plugin_status_label.setWordWrap(True)
    window._equipment_plugin_status_label.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    card.layout().addWidget(window._equipment_plugin_status_label)

    actions = QHBoxLayout()
    window._equipment_plugin_primary_button = QPushButton(_ui_text('settings.page.build_environment_card.equipment_plugin_primary_button'))
    window._equipment_plugin_primary_button.setObjectName("btnPrimary")
    window._equipment_plugin_primary_button.clicked.connect(
        window._activate_equipment_plugin_loading_method
    )
    actions.addWidget(window._equipment_plugin_primary_button)
    window._equipment_plugin_stop_button = QPushButton(_ui_text('settings.page.build_environment_card.equipment_plugin_stop_button'))
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


def build_tool_settings_card(window, language_settings: LanguageSettingsService | None = None):
    """共用工具选项容器，语言控件与日志、主题处于同一设置卡片。"""
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    log_card = window._card(_ui_text('settings.page.build_tool_settings_card.log_card'))
    log_row = QHBoxLayout()
    log_row.addWidget(plain_label(_ui_text('settings.page.build_tool_settings_card.label')))
    log_toggle = QCheckBox(_ui_text('settings.page.build_tool_settings_card.log_toggle'))
    log_toggle.setChecked(window._log_enabled)
    log_toggle.toggled.connect(window._toggle_log)
    window._log_toggle = log_toggle
    log_row.addWidget(log_toggle)
    window._log_session_status_label = plain_label()
    window._log_session_status_label.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    log_row.addWidget(window._log_session_status_label)
    log_row.addStretch()
    log_card.layout().addLayout(log_row)
    window._refresh_log_session_status()

    protagonist_row = QHBoxLayout()
    protagonist_row.addWidget(plain_label(_ui_text('settings.page.build_tool_settings_card.label_2')))
    window._protagonist_game_name_edit = QLineEdit()
    window._protagonist_game_name_edit.setPlaceholderText(_ui_text('settings.page.build_tool_settings_card.protagonist_game_name_edit.placeholder'))
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
        # 用户显式填写后，后续界面装配复用该回答；不从角色展示名猜游戏名。
        preferences["skip_protagonist_name_prompt"] = bool(
            preferences["protagonist_game_name"]
        )
        window._save_ui_preferences()

    window._protagonist_game_name_edit.editingFinished.connect(save_protagonist_name)
    protagonist_row.addWidget(window._protagonist_game_name_edit)
    protagonist_row.addStretch()
    log_card.layout().addLayout(protagonist_row)

    theme_row = QHBoxLayout()
    theme_row.addWidget(plain_label(_ui_text('settings.page.build_tool_settings_card.label_3')))
    current_theme = getattr(window, "_theme_preference", "black")
    dark_radio = QRadioButton(ui_enum_text(getattr(window, 'text_catalog', None), THEME_LABELS)["dark"])
    black_radio = QRadioButton(ui_enum_text(getattr(window, 'text_catalog', None), THEME_LABELS)["black"])
    light_radio = QRadioButton(ui_enum_text(getattr(window, 'text_catalog', None), THEME_LABELS)["light"])
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
    if language_settings is not None:
        log_card.layout().addWidget(LanguageSettingsWidget(language_settings, log_card))
    return log_card


def build_settings_page(
    window,
    app_version,
    app_context: AppContext,
    iter_image_files,
    netdisk_links=None,
    *,
    language_settings: LanguageSettingsService | None = None,
):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
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

    mode_card = build_work_mode_card(window)
    layout.addWidget(mode_card)

    layout.addWidget(build_tool_settings_card(window, language_settings))

    sync_card = _build_capture_diagnostics_card(window)
    plugin_card = _build_environment_card(window)
    hotkey_card = window._card(_ui_text('settings.page.build_settings_page.hotkey_card'))

    form = QFormLayout()
    form.setSpacing(10)

    cap_row = QHBoxLayout()
    cap_row.setSpacing(8)
    window._hk_capture_edit = QKeySequenceEdit(QKeySequence(window._hk_capture))
    window._hk_capture_edit.setMaximumWidth(160)
    cap_row.addWidget(plain_label(_ui_text('settings.page.build_settings_page.label')))
    cap_row.addWidget(window._hk_capture_edit)
    cap_row.addStretch()
    form.addRow(cap_row)

    finish_row = QHBoxLayout()
    finish_row.setSpacing(8)
    window._hk_finish_edit = QKeySequenceEdit(QKeySequence(window._hk_finish))
    window._hk_finish_edit.setMaximumWidth(160)
    finish_row.addWidget(plain_label(_ui_text('settings.page.build_settings_page.label_2')))
    finish_row.addWidget(window._hk_finish_edit)
    finish_row.addStretch()
    form.addRow(finish_row)

    stop_row = QHBoxLayout()
    stop_row.setSpacing(8)
    window._hk_stop_edit = QKeySequenceEdit(QKeySequence(window._hk_stop))
    window._hk_stop_edit.setMaximumWidth(160)
    stop_row.addWidget(plain_label(_ui_text('settings.page.build_settings_page.label_3')))
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
        plain_tooltip(_ui_text('settings.page.build_settings_page.hk_battle_rerecord_edit.hint'))
    )
    rerecord_row.addWidget(plain_label(_ui_text('settings.page.build_settings_page.label_4')))
    rerecord_row.addWidget(window._hk_battle_rerecord_edit)
    rerecord_row.addStretch()
    form.addRow(rerecord_row)

    def save_hotkeys_when_complete(_sequence) -> None:
        # 编辑快捷键时会暂时清空；仅全部字段有效才保存，不覆盖为中间空值。
        editors = (
            window._hk_capture_edit,
            window._hk_finish_edit,
            window._hk_stop_edit,
            window._hk_battle_rerecord_edit,
        )
        if all(editor.keySequence().toString().strip() for editor in editors):
            window._save_hotkeys()

    # 完整编辑即时保存；中间空值仍由上方统一入口拦截。
    for editor in (
        window._hk_capture_edit,
        window._hk_finish_edit,
        window._hk_stop_edit,
        window._hk_battle_rerecord_edit,
    ):
        editor.keySequenceChanged.connect(save_hotkeys_when_complete)

    hotkey_card.layout().addLayout(form)
    layout.addWidget(hotkey_card)

    update_card = window._card(_ui_text('settings.page.build_settings_page.update_card'))
    window._update_status = plain_label(_ui_text('settings.page.build_settings_page.update_status.state', app_version=f'{app_version}'))
    window._update_status.setWordWrap(True)
    update_card.layout().addWidget(window._update_status)
    update_row = QHBoxLayout()
    update_row.setSpacing(10)
    window._check_update_btn = QPushButton(_ui_text('settings.page.build_settings_page.check_update_btn'))
    window._check_update_btn.setObjectName("btnPrimary")
    window._check_update_btn.clicked.connect(lambda: window.calc_update_controller.check(manual=True))
    window._mirror_download_btn = QPushButton(_ui_text('settings.page.build_settings_page.mirror_download_btn'))
    window._mirror_download_btn.setObjectName("btnPrimary")
    window._mirror_download_btn.clicked.connect(window.calc_update_controller.start_download)
    home_btn = QPushButton(_ui_text('settings.page.build_settings_page.home_btn'))
    home_btn.clicked.connect(window._open_update_homepage)
    netdisk_btn = QPushButton(_ui_text('settings.page.build_settings_page.netdisk_btn'))
    netdisk_options = _normalize_netdisk_links(netdisk_links)
    netdisk_btn.clicked.connect(
        lambda: window._show_netdisk_download_dialog(netdisk_options)
        if hasattr(window, "_show_netdisk_download_dialog") and netdisk_options
        else None
    )
    window.calc_update_controller.configure_distribution_links(homepage_button=home_btn, netdisk_button=netdisk_btn)
    update_row.addWidget(window._check_update_btn)
    update_row.addWidget(window._mirror_download_btn)
    update_row.addWidget(netdisk_btn)
    update_row.addWidget(home_btn)
    update_row.addStretch()
    update_card.layout().addLayout(update_row)
    mirror_cdk_row = QHBoxLayout()
    mirror_cdk_row.setSpacing(10)
    mirror_cdk_label = plain_label("Mirror CDK")
    window._mirror_cdk_edit = QLineEdit()
    window._mirror_cdk_edit.setPlaceholderText(_ui_text('settings.page.build_settings_page.mirror_cdk_edit.placeholder'))
    window._mirror_cdk_edit.setEchoMode(QLineEdit.Password)
    window._mirror_cdk_edit.setFixedWidth(360)
    window._mirror_cdk_edit.editingFinished.connect(window.calc_update_controller.save_cdk)
    window.calc_update_controller.bind_view(
        status=window._update_status, check_button=window._check_update_btn,
        download_button=window._mirror_download_btn, cdk_editor=window._mirror_cdk_edit,
    )
    mirror_cdk_row.addWidget(mirror_cdk_label)
    mirror_cdk_row.addWidget(window._mirror_cdk_edit)
    mirror_cdk_row.addStretch()
    update_card.layout().addLayout(mirror_cdk_row)
    layout.addWidget(update_card)

    about_card = window._card(_ui_text('settings.page.build_settings_page.about_card'))
    about_row = QHBoxLayout()
    about_row.setSpacing(10)
    author_bilibili_btn = QPushButton(_ui_text('settings.page.build_settings_page.author_bilibili_btn'))
    author_bilibili_btn.clicked.connect(window._open_bilibili_homepage)
    project_btn = QPushButton(_ui_text('settings.page.build_settings_page.project_btn'))
    project_btn.clicked.connect(window._open_project_homepage)
    support_btn = QPushButton(_ui_text('settings.page.build_settings_page.support_btn'))
    support_btn.clicked.connect(window._open_support_homepage)
    group_chat_btn = QPushButton(_ui_text('settings.page.build_settings_page.group_chat_btn'))
    group_chat_btn.clicked.connect(window._show_group_chat_notice)
    about_row.addWidget(author_bilibili_btn)
    about_row.addWidget(project_btn)
    about_row.addWidget(support_btn)
    about_row.addWidget(group_chat_btn)
    about_row.addStretch()
    about_card.layout().addLayout(about_row)
    layout.addWidget(about_card)

    layout.addWidget(plugin_card)
    performance_card = window._card(_ui_text('settings.page.build_settings_page.performance_card'))
    performance_card.layout().addWidget(PerformanceCard(window.performance_controller, text_catalog=getattr(window, 'text_catalog', None)))
    layout.addWidget(performance_card)
    layout.addWidget(sync_card)

    paths = _settings_paths(app_context)
    screenshot_dir = paths.screenshot_dir
    screenshot_files = iter_image_files(screenshot_dir)
    count = len(screenshot_files)
    size_mb = sum(f.stat().st_size for f in screenshot_files) / (1024 * 1024) if screenshot_files else 0

    screenshot_card = window._card(_ui_text('settings.page.build_settings_page.screenshot_card'))
    window._ss_info = plain_label(_ui_text('settings.page.build_settings_page.ss_info.state', count=f'{count}', size_mb=f'{size_mb:.1f}'))
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

    quick_card = window._card(_ui_text('settings.page.build_settings_page.quick_card'))
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

    thanks_card = window._card(_ui_text('settings.page.build_settings_page.thanks_card'))
    thanks_card.layout().setSpacing(12)
    thanks_row = QHBoxLayout()
    thanks_row.setSpacing(8)
    thanks_name = plain_label(_ui_text('settings.page.build_settings_page.thanks_name'))
    thanks_name.setStyleSheet(
        themed_style(
            "color:#58a6ff;font-weight:700;background:#0d1f35;"
            "border:1px solid #1f6feb;border-radius:6px;padding:5px 10px"
        )
    )
    thanks_desc = plain_label(_ui_text('settings.page.build_settings_page.thanks_desc'))
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
    toolkit_name = rich_label(
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
    toolkit_desc = plain_label(_ui_text('settings.page.build_settings_page.toolkit_desc'))
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
    nteuid_row = QHBoxLayout()
    nteuid_row.setSpacing(8)
    nteuid_name = rich_label(
        '<a href="https://github.com/tyql688/NTEUID" '
        'style="color:#58a6ff;text-decoration:none;">NTEUID</a>'
    )
    nteuid_name.setTextFormat(Qt.RichText)
    nteuid_name.setTextInteractionFlags(Qt.TextBrowserInteraction)
    nteuid_name.setOpenExternalLinks(True)
    nteuid_name.setStyleSheet(toolkit_name.styleSheet())
    nteuid_desc = plain_label(_ui_text('settings.page.build_settings_page.nteuid_desc'))
    nteuid_desc.setStyleSheet(toolkit_desc.styleSheet())
    nteuid_row.addWidget(nteuid_name)
    nteuid_row.addWidget(nteuid_desc)
    nteuid_row.addStretch()
    thanks_card.layout().addLayout(nteuid_row)
    layout.addWidget(thanks_card)

    layout.addStretch()
    window.work_mode_controller.attach_settings_targets(
        scroll=scroll, mode_card=mode_card, component_card=plugin_card,
        component_focus=window._equipment_plugin_primary_button,
        game_path_focus=window._equipment_plugin_game_executable_edit,
        loading_method_focus=window._equipment_plugin_loading_method_combo,
        npcap_focus=window._npcap_install_button,
        core_focus=window._nte_core_diagnostic_button,
    )
    return scroll
