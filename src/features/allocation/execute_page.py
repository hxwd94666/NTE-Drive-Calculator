# 构建执行页的扫描、解析和分配控件。
"""Execute page builder.

The execute page owns controls for scan mode, priority roles, allocation
strategy, and run/result actions. Business behavior stays on MainWindow; this
builder only wires UI widgets to existing callbacks.
"""

from __future__ import annotations

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def build_execute_page(window, role_selector_cls, scan_help, drone_help, offline_help, show_help):
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)

    scan_card = window._card("第一步 · 扫描模式")
    window.scan_group = QButtonGroup()
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
        help_btn.clicked.connect(lambda checked, v=value: show_help(window, "扫描模式说明", scan_help.get(v, "")))
        row.addWidget(help_btn)
        row.addStretch()
        scan_card.layout().addLayout(row)

    window.offline_frame = QWidget()
    window.offline_frame.setVisible(False)
    offline_layout = QHBoxLayout(window.offline_frame)
    offline_layout.setContentsMargins(28, 4, 0, 4)
    offline_layout.setSpacing(10)
    offline_layout.addWidget(QLabel("离线解析类型:"))
    window.offline_group = QButtonGroup()
    for key, text in [("full", "全量解析"), ("incremental", "增量解析"), ("all", "全部截图解析")]:
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        rb = QRadioButton(text)
        rb.setChecked(key == "incremental")
        rb.setProperty("offline_key", key)
        window.offline_group.addButton(rb)
        sub_row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(lambda checked, k=key: show_help(window, "离线解析说明", offline_help.get(k, "")))
        sub_row.addWidget(help_btn)
        offline_layout.addLayout(sub_row)
    offline_layout.addStretch()
    scan_card.layout().addWidget(window.offline_frame)

    window.total_count_frame = QWidget()
    window.total_count_frame.setVisible(False)
    total_count_layout = QHBoxLayout(window.total_count_frame)
    total_count_layout.setContentsMargins(28, 4, 0, 4)
    total_count_layout.setSpacing(8)
    total_count_layout.addWidget(QLabel("库存数量:"))
    window.total_count_edit = QLineEdit()
    window.total_count_edit.setPlaceholderText("请输入当前库存数量")
    window.total_count_edit.setValidator(QIntValidator(1, 2000, window.total_count_edit))
    window.total_count_edit.setMaximumWidth(180)
    total_count_layout.addWidget(window.total_count_edit)
    window.scan_post_action_btn = QPushButton("管理")
    window.scan_post_action_btn.setObjectName("btnPrimary")
    window.scan_post_action_btn.setMaximumWidth(82)
    if hasattr(window, "_open_scan_post_action_manager"):
        window.scan_post_action_btn.clicked.connect(window._open_scan_post_action_manager)
    total_count_layout.addWidget(window.scan_post_action_btn)
    total_count_layout.addStretch()
    scan_card.layout().addWidget(window.total_count_frame)

    window.scan_dual_thread_frame = QWidget()
    window.scan_dual_thread_frame.setVisible(False)
    dual_thread_layout = QHBoxLayout(window.scan_dual_thread_frame)
    dual_thread_layout.setContentsMargins(28, 0, 0, 4)
    dual_thread_layout.setSpacing(8)
    window.scan_dual_thread_check = QCheckBox("双线程处理")
    prefs = getattr(window, "_ui_preferences", {}) or {}
    window.scan_dual_thread_check.setChecked(bool(prefs.get("full_scan_dual_thread_processing", True)))
    window.scan_amd_compat_check = QCheckBox("AMD实验性兼容")
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
        lambda _checked=False: show_help(
            window,
            "双线程处理说明",
            "如果扫描负载大，出现重启或异常，请关闭这个功能。",
        )
    )
    dual_thread_layout.addWidget(dual_help_btn)
    dual_thread_layout.addSpacing(16)
    window.scan_discrete_gpu_check = QCheckBox("独显加速")
    window.scan_discrete_gpu_check.setChecked(bool(prefs.get("full_scan_discrete_gpu_acceleration", False)))

    def _save_scan_discrete_gpu_preference(enabled):
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_discrete_gpu_acceleration"] = bool(enabled)
            if enabled and hasattr(window, "scan_amd_compat_check") and window.scan_amd_compat_check.isChecked():
                window.scan_amd_compat_check.blockSignals(True)
                window.scan_amd_compat_check.setChecked(False)
                window.scan_amd_compat_check.blockSignals(False)
                preferences["full_scan_amd_compatibility"] = False
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()

    window.scan_discrete_gpu_check.toggled.connect(_save_scan_discrete_gpu_preference)
    dual_thread_layout.addWidget(window.scan_discrete_gpu_check)
    gpu_help_btn = QPushButton("?")
    gpu_help_btn.setObjectName("btnHelp")
    gpu_help_btn.clicked.connect(
        lambda _checked=False: show_help(
            window,
            "独显加速说明",
            "高端显卡可选，加速效果不定，可能还会负提升。",
        )
    )
    dual_thread_layout.addWidget(gpu_help_btn)
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
                if hasattr(window, "scan_discrete_gpu_check"):
                    window.scan_discrete_gpu_check.blockSignals(True)
                    window.scan_discrete_gpu_check.setChecked(False)
                    window.scan_discrete_gpu_check.blockSignals(False)
                    preferences["full_scan_discrete_gpu_acceleration"] = False
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()

    window.scan_amd_compat_check.toggled.connect(_save_scan_amd_compat_preference)
    dual_thread_layout.addWidget(window.scan_amd_compat_check)
    amd_help_btn = QPushButton("?")
    amd_help_btn.setObjectName("btnHelp")
    amd_help_btn.clicked.connect(
        lambda _checked=False: show_help(
            window,
            "AMD实验性兼容说明",
            "实验性低负载模式，不保证一定解决重启或异常。\n\n"
            "开启后会自动关闭双线程处理和独显加速，扫描完成后再解析截图，并降低 OCR/图像处理线程负载。"
            "适合 AMD CPU/核显/显卡机器在全量扫描中出现重启、蓝屏或异常时尝试。",
        )
    )
    dual_thread_layout.addWidget(amd_help_btn)
    if window.scan_amd_compat_check.isChecked():
        window.scan_dual_thread_check.blockSignals(True)
        window.scan_dual_thread_check.setChecked(False)
        window.scan_dual_thread_check.blockSignals(False)
        window.scan_discrete_gpu_check.blockSignals(True)
        window.scan_discrete_gpu_check.setChecked(False)
        window.scan_discrete_gpu_check.blockSignals(False)
    dual_thread_layout.addStretch()
    scan_card.layout().addWidget(window.scan_dual_thread_frame)

    window.drone_frame = QWidget()
    window.drone_frame.setVisible(False)
    drone_layout = QHBoxLayout(window.drone_frame)
    drone_layout.setContentsMargins(28, 4, 0, 4)
    drone_layout.addWidget(QLabel("无人机模式:"))
    window.drone_group = QButtonGroup()
    for value, text in [("2", "半自动模式（推荐）"), ("1", "全自动模式")]:
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        rb = QRadioButton(text)
        rb.setChecked(value == "2")
        window.drone_group.addButton(rb, int(value))
        sub_row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(lambda checked, v=value: show_help(window, "增量模式说明", drone_help.get(v, "")))
        sub_row.addWidget(help_btn)
        drone_layout.addLayout(sub_row)
    drone_layout.addStretch()
    scan_card.layout().addWidget(window.drone_frame)
    window.scan_group.idToggled.connect(window._on_scan_change)
    layout.addWidget(scan_card)

    priority_card = window._card("第二步 · 角色优先级配置")
    window.role_selector = role_selector_cls()
    window.role_selector.orderChanged.connect(window._on_priority_changed)
    priority_card.layout().addWidget(window.role_selector)
    layout.addWidget(priority_card)

    strategy_card = window._card("第三步 · 分配策略")
    window.strategy_group = QButtonGroup()
    strategy_options = [
        "角色优先 — 按角色顺序配装，优先照顾前排角色",
        "驱动优先 — 先处理高分装备，让好装备尽量有人用",
        "全局最优 — 追求全队总分最高，适合整体重排",
        "增量更新 — 保留已穿戴装备，只用闲置装备补配",
    ]
    for index, text in enumerate(strategy_options):
        rb = QRadioButton(text)
        rb.setChecked(index == 0)
        window.strategy_group.addButton(rb, index)
        strategy_card.layout().addWidget(rb)
    layout.addWidget(strategy_card)

    window.btn_run = QPushButton("⚡  开始执行")
    window.btn_run.setObjectName("btnPrimary")
    window.btn_run.setFixedHeight(46)
    window.btn_run.setStyleSheet("#btnPrimary{font-size:15px;font-weight:700;border-radius:10px}")
    window.btn_run.clicked.connect(window._do_exec)
    layout.addWidget(window.btn_run)

    window.result_card = QWidget()
    window.result_card.setVisible(False)
    window.result_card.setStyleSheet(
        "QWidget{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px}"
    )
    result_layout = QVBoxLayout(window.result_card)
    result_header = QHBoxLayout()
    result_header.addWidget(QLabel("计算结果"))
    result_header.addStretch()
    window.btn_save = QPushButton("保存装备锁定")
    window.btn_save.setObjectName("btnAction")
    window.btn_save.clicked.connect(lambda _checked=False: window._save_alloc())
    result_header.addWidget(window.btn_save)
    result_layout.addLayout(result_header)
    window.result_content = QWidget()
    window.result_content_layout = QVBoxLayout(window.result_content)
    result_layout.addWidget(window.result_content)
    layout.addWidget(window.result_card)

    layout.addStretch()
    return scroll
