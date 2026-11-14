# 构建执行页的扫描、解析和分配控件。
"""Execute page builder.

The execute page owns controls for scan mode, priority roles, allocation
strategy, and run/result actions. Business behavior stays on MainWindow; this
builder only wires UI widgets to existing callbacks.
"""

from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import theme_color, theme_rgba, themed_style

def _build_scan_mode_card(window, layout, scan_help, drone_help, offline_help, show_help):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    scan_card = window._card(_ui_text('allocation.page.build_scan_mode_card.scan_card'))
    window.scan_group = QButtonGroup()
    _add_scan_mode_options(window, scan_card, scan_help, show_help)
    _build_offline_frame(window, scan_card, offline_help, show_help)
    _build_total_count_frame(window, scan_card)
    _build_full_scan_driver_frame(window, scan_card)
    build_scan_processing_options(window, scan_card, show_help)
    _build_drone_frame(window, scan_card, drone_help, show_help)
    window._confirmed_scan_mode_id = window.scan_group.checkedId()
    window.scan_group.idToggled.connect(window._on_scan_change)
    # 先走既有选择确认，再读实际选中项；被拒绝的模式不会残留在收起摘要中。
    window.scan_group.idToggled.connect(lambda *_: _refresh_reading_summary(window))
    layout.addWidget(scan_card)


def _add_scan_mode_options(window, scan_card, scan_help, show_help):
    window.reading_options_frame = QWidget()
    other_layout = QVBoxLayout(window.reading_options_frame)
    other_layout.setContentsMargins(24, 0, 0, 0)
    other_layout.setSpacing(6)
    window.reading_options_toggle = QPushButton()
    toggle = window.reading_options_toggle
    toggle.setCheckable(True)
    toggle.setChecked(window.load_reading_options_expanded())
    window.reading_mode_summary = plain_label()
    window.reading_mode_summary.setWordWrap(True)
    scan_options = [
        ("4", "直接读取库存 — 不扫描，直接重新配装"),
        ("3", "离线解析 — 解析已有截图并生成库存"),
        ("2", "增量扫描 — 只录入新获得的装备"),
        ("1", "全量扫描 — 重扫背包并重建库存"),
    ]
    for value, text in scan_options:
        row = QHBoxLayout()
        row.setSpacing(6)
        rb = QRadioButton(text)
        rb.setChecked(value == "4")
        window.scan_group.addButton(rb, int(value))
        row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(
            lambda checked, v=value, parent=help_btn: show_help(
                parent, resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.add_scan_mode_options.caption'), scan_help.get(v, ""),
            )
        )
        row.addWidget(help_btn)
        row.addStretch()
        if value == "4":
            row.addWidget(toggle)
            scan_card.layout().addLayout(row)
        else:
            other_layout.addLayout(row)
    scan_card.layout().addWidget(window.reading_options_frame)
    scan_card.layout().addWidget(window.reading_mode_summary)

    def set_expanded(expanded):
        # 折叠只改变可见性；绝不切换扫描模式、触发读取或重置扫描参数。
        if not window.save_reading_options_expanded(expanded):
            toggle.blockSignals(True)
            toggle.setChecked(not expanded)
            toggle.blockSignals(False)
        _refresh_reading_summary(window)

    toggle.toggled.connect(set_expanded)
    _refresh_reading_summary(window)


def _refresh_reading_summary(window):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    expanded = window.reading_options_toggle.isChecked()
    window.reading_options_frame.setVisible(expanded)
    window.reading_options_toggle.setText(_ui_text('allocation.page.refresh_reading_summary.reading_options_toggle.state') if expanded else _ui_text('allocation.page.refresh_reading_summary.reading_options_toggle.state_2'))
    window.reading_options_toggle.setAccessibleName(_ui_text('allocation.page.refresh_reading_summary.reading_options_toggle.state_3') if expanded else _ui_text('allocation.page.refresh_reading_summary.reading_options_toggle.state_4'))
    selected = window.scan_group.checkedId()
    names = {1: "全量扫描", 2: "增量扫描", 3: "离线解析"}
    window.reading_mode_summary.setText(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.refresh_reading_summary.reading_mode_summary') + names[selected] if selected in names else "")
    window.reading_mode_summary.setVisible(not expanded and selected in names)


def _build_offline_frame(window, scan_card, offline_help, show_help):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.offline_frame = QWidget()
    window.offline_frame.setVisible(False)
    offline_layout = QHBoxLayout(window.offline_frame)
    offline_layout.setContentsMargins(28, 4, 0, 4)
    offline_layout.setSpacing(10)
    offline_layout.addWidget(plain_label(_ui_text('allocation.page.build_offline_frame.label')))
    window.offline_group = QButtonGroup()
    for key, text in [("full", "全量解析"), ("incremental", "增量解析"), ("all", "全部截图解析")]:
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        rb = QRadioButton(ui_enum_text(getattr(window, 'text_catalog', None), text))
        rb.setChecked(key == "incremental")
        rb.setProperty("offline_key", key)
        window.offline_group.addButton(rb)
        sub_row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(
            lambda checked, k=key, parent=help_btn: show_help(
                parent, resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.build_offline_frame.caption'), offline_help.get(k, ""),
            )
        )
        sub_row.addWidget(help_btn)
        offline_layout.addLayout(sub_row)
    offline_layout.addStretch()
    scan_card.layout().addWidget(window.offline_frame)


def _build_total_count_frame(window, scan_card):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.total_count_frame = QWidget()
    window.total_count_frame.setVisible(False)
    total_count_layout = QHBoxLayout(window.total_count_frame)
    total_count_layout.setContentsMargins(28, 4, 0, 4)
    total_count_layout.setSpacing(8)
    total_count_layout.addWidget(plain_label(_ui_text('allocation.page.build_total_count_frame.label')))
    window.total_count_edit = QLineEdit()
    window.total_count_edit.setPlaceholderText(_ui_text('allocation.page.build_total_count_frame.total_count_edit.placeholder'))
    window.total_count_edit.setValidator(QIntValidator(1, 2000, window.total_count_edit))
    window.total_count_edit.setMaximumWidth(180)
    total_count_layout.addWidget(window.total_count_edit)
    window.scan_post_action_btn = QPushButton(_ui_text('allocation.page.build_total_count_frame.scan_post_action_btn'))
    window.scan_post_action_btn.setObjectName("btnPrimary")
    window.scan_post_action_btn.setMaximumWidth(82)
    if hasattr(window, "_open_scan_post_action_manager"):
        window.scan_post_action_btn.clicked.connect(window._open_scan_post_action_manager)
    total_count_layout.addWidget(window.scan_post_action_btn)
    total_count_layout.addStretch()
    scan_card.layout().addWidget(window.total_count_frame)


def _build_full_scan_driver_frame(window, scan_card):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.full_scan_driver_frame = QWidget()
    window.full_scan_driver_frame.setVisible(False)
    driver_layout = QHBoxLayout(window.full_scan_driver_frame)
    driver_layout.setContentsMargins(28, 4, 0, 4)
    driver_layout.setSpacing(10)
    driver_layout.addWidget(plain_label(_ui_text('allocation.page.build_full_scan_driver_frame.label')))
    window.full_scan_driver_group = QButtonGroup()
    prefs = getattr(window, "_ui_preferences", {}) or {}
    preferred = str(prefs.get("full_scan_capture_driver", "mouse"))
    if preferred not in {"mouse", "gamepad"}:
        preferred = "mouse"
    for button_id, key, text in (
        (1, "mouse", "鼠标扫描（默认）"),
        (2, "gamepad", "虚拟手柄扫描（兜底）"),
    ):
        rb = QRadioButton(ui_enum_text(getattr(window, 'text_catalog', None), text))
        rb.setProperty("capture_driver", key)
        rb.setChecked(key == preferred)
        window.full_scan_driver_group.addButton(rb, button_id)
        driver_layout.addWidget(rb)

    def _save_capture_driver(button_id, checked):
        if not checked:
            return
        key = "gamepad" if button_id == 2 else "mouse"
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_capture_driver"] = key
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()
        if hasattr(window, "scan_post_action_btn"):
            window.scan_post_action_btn.setVisible(key in {"mouse", "gamepad"})

    window.full_scan_driver_group.idToggled.connect(_save_capture_driver)
    if hasattr(window, "scan_post_action_btn"):
        window.scan_post_action_btn.setVisible(preferred in {"mouse", "gamepad"})
    driver_layout.addStretch()
    scan_card.layout().addWidget(window.full_scan_driver_frame)


def build_scan_processing_options(window, scan_card, show_help):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.scan_dual_thread_frame = QWidget()
    window.scan_dual_thread_frame.setVisible(False)
    dual_thread_layout = QHBoxLayout(window.scan_dual_thread_frame)
    dual_thread_layout.setContentsMargins(28, 0, 0, 4)
    dual_thread_layout.setSpacing(8)
    window.scan_dual_thread_check = QCheckBox(_ui_text('allocation.page.build_scan_processing_options.scan_dual_thread_check'))
    prefs = getattr(window, "_ui_preferences", {}) or {}
    window.scan_dual_thread_check.setChecked(bool(prefs.get("full_scan_dual_thread_processing", True)))
    window.scan_amd_compat_check = QCheckBox(_ui_text('allocation.page.build_scan_processing_options.scan_amd_compat_check'))
    window.scan_amd_compat_check.setChecked(bool(prefs.get("full_scan_amd_compatibility", False)))

    def _save_scan_dual_thread_preference(enabled):
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_dual_thread_processing"] = bool(enabled)
            if enabled and hasattr(window, "scan_amd_compat_check") and window.scan_amd_compat_check.isChecked():
                window.scan_amd_compat_check.blockSignals(True)
                window.scan_amd_compat_check.setChecked(False)
                window.scan_amd_compat_check.blockSignals(False)
                preferences["full_scan_amd_compatibility"] = False
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()

    window.scan_dual_thread_check.toggled.connect(_save_scan_dual_thread_preference)
    dual_thread_layout.addWidget(window.scan_dual_thread_check)
    dual_help_btn = QPushButton("?")
    dual_help_btn.setObjectName("btnHelp")
    dual_help_btn.clicked.connect(
        lambda _checked=False, parent=dual_help_btn: show_help(
            parent,
            resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.build_scan_processing_options.caption'),
            resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.build_scan_processing_options.caption_2'),
        )
    )
    dual_thread_layout.addWidget(dual_help_btn)
    dual_thread_layout.addSpacing(16)
    def _save_scan_amd_compat_preference(enabled):
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_amd_compatibility"] = bool(enabled)
            if enabled:
                if hasattr(window, "scan_dual_thread_check"):
                    window.scan_dual_thread_check.blockSignals(True)
                    window.scan_dual_thread_check.setChecked(False)
                    window.scan_dual_thread_check.blockSignals(False)
                    preferences["full_scan_dual_thread_processing"] = False
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()

    window.scan_amd_compat_check.toggled.connect(_save_scan_amd_compat_preference)
    dual_thread_layout.addWidget(window.scan_amd_compat_check)
    amd_help_btn = QPushButton("?")
    amd_help_btn.setObjectName("btnHelp")
    amd_help_btn.clicked.connect(
        lambda _checked=False, parent=amd_help_btn: show_help(
            parent,
            resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.build_scan_processing_options.caption_3'),
            resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.build_scan_processing_options.caption_4'),
        )
    )
    dual_thread_layout.addWidget(amd_help_btn)
    if window.scan_amd_compat_check.isChecked():
        window.scan_dual_thread_check.blockSignals(True)
        window.scan_dual_thread_check.setChecked(False)
        window.scan_dual_thread_check.blockSignals(False)
    dual_thread_layout.addStretch()
    scan_card.layout().addWidget(window.scan_dual_thread_frame)


def _build_drone_frame(window, scan_card, drone_help, show_help):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.drone_frame = QWidget()
    window.drone_frame.setVisible(False)
    drone_layout = QHBoxLayout(window.drone_frame)
    drone_layout.setContentsMargins(28, 4, 0, 4)
    drone_layout.addWidget(plain_label(_ui_text('allocation.page.build_drone_frame.label')))
    window.drone_group = QButtonGroup()
    for value, text in [("2", "半自动模式（推荐）"), ("1", "全自动模式")]:
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        rb = QRadioButton(ui_enum_text(getattr(window, 'text_catalog', None), text))
        rb.setChecked(value == "2")
        window.drone_group.addButton(rb, int(value))
        sub_row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(
            lambda checked, v=value, parent=help_btn: show_help(
                parent, resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.allocation.build_drone_frame.caption'), drone_help.get(v, ""),
            )
        )
        sub_row.addWidget(help_btn)
        drone_layout.addLayout(sub_row)
    drone_layout.addStretch()
    scan_card.layout().addWidget(window.drone_frame)


def _build_priority_card(window, layout, role_selector_cls):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    priority_card = window._card(_ui_text('allocation.page.build_priority_card.priority_card'))
    window.role_selector = role_selector_cls()
    window.role_selector.orderChanged.connect(window._on_priority_changed)
    priority_card.layout().addWidget(window.role_selector)
    layout.addWidget(priority_card)


def _build_strategy_card(window, layout):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    strategy_card = window._card(_ui_text('allocation.page.build_strategy_card.strategy_card'))
    title_item = strategy_card.layout().takeAt(0)
    title_label = title_item.widget() if title_item is not None else None
    if title_label is None:
        title_label = plain_label(_ui_text('allocation.page.build_strategy_card.title_label'))
        title_label.setObjectName("cardTitle")
    header = QHBoxLayout()
    header.setSpacing(0)
    header.addWidget(title_label)
    header.addSpacing(max(1, title_label.fontMetrics().horizontalAdvance("　") // 2))
    window.allocation_filter_settings_button = QPushButton(_ui_text('allocation.page.build_strategy_card.allocation_filter_settings_button'))
    window.allocation_filter_settings_button.setObjectName("allocationFilterSettingsButton")
    window.allocation_filter_settings_button.setFixedSize(54, 28)
    window.allocation_filter_settings_button.clicked.connect(
        window._open_allocation_filter_settings
    )
    header.addWidget(window.allocation_filter_settings_button)
    strategy_card.layout().addLayout(header)
    from src.features.allocation.team_axis.team_axis_controls import build_team_axis_controls
    build_team_axis_controls(window, strategy_card.layout())
    header.addStretch(1)

    window.strategy_group = QButtonGroup()
    text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    strategy_options = [text('allocation.strategy.' + key) for key in ('role', 'locked', 'direct')]
    for index, caption in enumerate(strategy_options):
        rb = QRadioButton(caption)
        rb.setChecked(index == 0)
        window.strategy_group.addButton(rb, index)
        strategy_card.layout().addWidget(rb)
    layout.addWidget(strategy_card)


def _build_run_button(window, layout):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.btn_run = QPushButton(_ui_text('allocation.page.build_run_button.btn_run'))
    window.btn_run.setObjectName("btnPrimary")
    window.btn_run.setFixedHeight(46)
    window.btn_run.setStyleSheet("#btnPrimary{font-size:15px;font-weight:700;border-radius:10px}")
    window.btn_run.clicked.connect(window._do_exec)
    layout.addWidget(window.btn_run)
    row = QHBoxLayout()
    window.btn_resume_capture = QPushButton(_ui_text('allocation.page.build_run_button.btn_resume_capture'))
    window.btn_resume_capture.setObjectName("resumeCaptureStageButton")
    window.btn_resume_capture.setToolTip(plain_tooltip(_ui_text('allocation.page.build_run_button.btn_resume_capture.hint')))
    window.btn_resume_capture.setVisible(False)
    window.btn_resume_capture.clicked.connect(window.resume_capture_stage)
    row.addWidget(window.btn_resume_capture)
    window.btn_retry_publication = QPushButton(_ui_text('allocation.page.build_run_button.btn_retry_publication'))
    window.btn_retry_publication.setObjectName("retryCapturePublicationButton")
    window.btn_retry_publication.setToolTip(plain_tooltip(_ui_text('allocation.page.build_run_button.btn_retry_publication.hint')))
    window.btn_retry_publication.setVisible(False)
    window.btn_retry_publication.clicked.connect(window.retry_capture_publication)
    row.addWidget(window.btn_retry_publication)
    row.addStretch()
    layout.addLayout(row)


def _build_result_card(window, layout):
    _ui_text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    window.result_card = QWidget()
    window.result_card.setVisible(False)
    window.result_card.setStyleSheet(
        themed_style("QWidget{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px}")
    )
    result_layout = QVBoxLayout(window.result_card)
    result_header = QHBoxLayout()
    badge_width, badge_height = 112, 34
    result_title = plain_label(_ui_text('allocation.page.build_result_card.result_title'))
    result_title.setObjectName("allocationResultTitle")
    result_title.setAlignment(Qt.AlignCenter)
    result_title.setFixedSize(badge_width, badge_height)
    result_title.setStyleSheet(
        f"color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};"
        f"border-radius:7px;background:{theme_rgba('#4dd0e1', 0.10)};"
        "font-size:15px;font-weight:800;padding:0 14px;"
    )
    result_header.addWidget(result_title)
    result_header.addStretch()
    window.btn_clear_result = QPushButton(_ui_text('allocation.page.build_result_card.btn_clear_result'))
    window.btn_clear_result.setObjectName("allocationClearResultButton")
    window.btn_clear_result.setFixedSize(badge_width, badge_height)
    window.btn_clear_result.setToolTip(plain_tooltip(_ui_text('allocation.page.build_result_card.btn_clear_result.hint')))
    window.btn_clear_result.setStyleSheet(
        f"QPushButton#allocationClearResultButton{{color:{theme_color('#f85149')};"
        f"border:1px solid {theme_color('#f85149')};border-radius:7px;"
        f"background:{theme_rgba('#f85149', 0.10)};"
        "font-size:15px;font-weight:800;padding:0 14px;}"
        "QPushButton#allocationClearResultButton:hover{"
        f"background:{theme_rgba('#f85149', 0.20)};}}"
        "QPushButton#allocationClearResultButton:disabled{"
        f"color:{theme_color('#8b949e')};border-color:{theme_color('#30363d')};"
        "background:transparent;}"
    )
    window.btn_clear_result.clicked.connect(window.clear_calculation)
    result_header.addWidget(window.btn_clear_result)
    window.btn_save = QPushButton(_ui_text('allocation.page.build_result_card.btn_save'))
    window.btn_save.setObjectName("allocationSaveButton")
    window.btn_save.setFixedSize(badge_width, badge_height)
    window.btn_save.setToolTip('')
    window.btn_save.setStyleSheet(
        f"QPushButton#allocationSaveButton{{color:{theme_color('#3fb950')};"
        f"border:1px solid {theme_color('#3fb950')};border-radius:7px;"
        f"background:{theme_rgba('#3fb950', 0.10)};"
        "font-size:15px;font-weight:800;padding:0 14px;}"
        "QPushButton#allocationSaveButton:hover{"
        f"background:{theme_rgba('#3fb950', 0.20)};}}"
        "QPushButton#allocationSaveButton:disabled{"
        f"color:{theme_color('#8b949e')};border-color:{theme_color('#30363d')};"
        "background:transparent;}"
    )
    window.btn_save.clicked.connect(lambda _checked=False: window._save_alloc())
    result_header.addWidget(window.btn_save)
    result_layout.addLayout(result_header)
    window.result_content = QWidget()
    window.result_content_layout = QVBoxLayout(window.result_content)
    result_layout.addWidget(window.result_content)
    layout.addWidget(window.result_card)


def build_execute_page(window, role_selector_cls, scan_help, drone_help, offline_help, show_help):
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)

    _build_scan_mode_card(window, layout, scan_help, drone_help, offline_help, show_help)
    _build_priority_card(window, layout, role_selector_cls)
    _build_strategy_card(window, layout)
    _build_run_button(window, layout)
    _build_result_card(window, layout)
    layout.addStretch()
    return scroll
