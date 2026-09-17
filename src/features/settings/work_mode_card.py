# 构建统一工作模式入口、逐功能检查结果与必要处理操作。
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QScrollArea, QVBoxLayout,
)
from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.widgets import NoWheelComboBox

MODE_LABELS = {"offline": "离线", "low": "低风险", "medium": "中风险", "developer": "开发"}
MODE_DESCRIPTIONS = {
    "offline": "本地计算、配装、已保存数据与历史战报分析。",
    "low": "以上功能 + 抓包同步/战报 + 鼠标或手柄扫描；不使用游戏组件。",
    "medium": "以上功能 + 原生同步、原生战报、极速装配、锁定/弃置及插件。",
    "developer": "抓包与原生双线对比，仅供开发人员使用。",
}
MODE_CONFIRMATIONS = {
    "offline": {
        "warning_title": "切换后将断开游戏连接",
        "warning_detail": "停止采集与同步，并关闭两个插件开关。",
        "available": "本地计算、配装、已保存数据、历史战报分析",
        "unavailable": "数据同步、战报采集、游戏操作、插件",
        "after": "停止后台连接；游戏退出后清理已部署组件。",
        "note": "离线模式不会连接游戏。确认后会保存此模式。",
    },
    "low": {
        "warning_title": "此模式存在风险",
        "warning_detail": "抓包与模拟输入可能触发游戏保护或兼容问题。",
        "available": "抓包同步、抓包战报、鼠标或手柄扫描",
        "unavailable": "角色状态同步、原生同步、原生战报、极速装配、插件",
        "after": "停止原生连接；游戏退出后清理已部署组件。",
        "note": "自动同步仍由工作台开关控制。确认后会保存此模式，后续版本更新继续沿用。",
    },
    "medium": {
        "warning_title": "此模式存在风险",
        "warning_detail": "加载游戏组件并执行游戏内操作，可能触发游戏保护或兼容问题。",
        "available": "原生同步、原生战报、极速装配、锁定/弃置、插件",
        "unavailable": "抓包同步、抓包战报、双线战报对比",
        "after": "游戏关闭后自动部署或更新所需组件。",
        "note": "自动同步仍由工作台开关控制。确认后会保存此模式，后续版本更新继续沿用。",
    },
    "developer": {
        "warning_title": "仅供开发人员使用",
        "warning_detail": "抓包与原生双线同时运行，可能触发游戏保护或兼容问题。",
        "available": "中风险全部功能、抓包与原生双线战报对比",
        "unavailable": "不建议用于日常使用",
        "after": "游戏关闭后自动部署或更新所需组件。",
        "note": "自动同步仍由工作台开关控制。确认后会保存此模式，后续版本更新继续沿用。",
    },
}
STATE_LABELS = {"available": "可用", "waiting": "正常等待", "missing": "缺少条件",
                "fault": "故障", "cleanup_pending": "清理待完成"}


def report_text(report) -> str:
    rows = []
    for item in report.features:
        rows.append(f"{item.label}：{STATE_LABELS[item.state.value]}\n{item.detail}")
    return "\n\n".join(rows)


def report_summary(report) -> str:
    counts = {}
    for item in report.features:
        label = STATE_LABELS[item.state.value]
        counts[label] = counts.get(label, 0) + 1
    return " · ".join(f"{label} {count} 项" for label, count in counts.items()) or "暂无检测结果"


class ModeReportDialog(QDialog):
    """Keep the explicit check visible from queued work through its final report."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self._controller = controller
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignTop)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.label)
        layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        self.actions = QHBoxLayout()
        footer.addLayout(self.actions)
        footer.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.setFixedWidth(72)
        self.close_button.clicked.connect(self.reject)
        self.retry_button = QPushButton("重新检测")
        self.retry_button.setFixedWidth(88)
        self.retry_button.clicked.connect(lambda: self._controller.check(show=True))
        footer.addWidget(self.retry_button)
        footer.addWidget(self.close_button)
        self.footer = footer
        layout.addLayout(footer)
        fit_dialog_to_available_screen(self, QSize(700, 650))

    def _clear_actions(self):
        while self.actions.count():
            button = self.actions.takeAt(0).widget()
            button.hide()
            button.deleteLater()

    def begin(self, mode):
        self.setWindowTitle(f"{MODE_LABELS[mode]}模式检测")
        self._clear_actions()
        self.retry_button.setEnabled(False)
        detail = ("正在结束原生连接并清理游戏组件…" if mode in {"offline", "low"}
                  else "正在检测环境并部署或更新配套组件…")
        self.label.setText(detail + "\n完成后将在此显示检测结果。\n关闭窗口不会取消已确认的模式切换和组件处理。")
        self.progress.show()
        self.show()
        self.raise_()

    def _add_action(self, title, callback, *, close=False):
        button = QPushButton(title)
        def run():
            if close:
                self.accept()
            callback()
        button.clicked.connect(run)
        self.actions.addWidget(button)

    def set_report(self, report):
        self.progress.hide()
        self._clear_actions()
        self.retry_button.setEnabled(True)
        self.label.setText(report_text(report))
        available = {action for item in report.features for action in item.actions}
        for key, title, callback in (
            ("download_npcap", "下载 Npcap", lambda: self.parentWidget()._open_npcap_download()),
            ("detect_game_path", "重新检测路径", self._controller.detect_path),
            ("manual_deploy", "手动部署 DLL", lambda: self.parentWidget()._deploy_equipment_plugin()),
        ):
            if key in available:
                self._add_action(title, callback, close=True)

    def set_error(self, detail):
        self.progress.hide()
        self._clear_actions()
        self.retry_button.setEnabled(True)
        self.label.setText(detail)


def build_work_mode_card(window):
    controller = window.work_mode_controller
    service = window.work_mode_service
    card = window._card("工作模式")
    controls = QHBoxLayout()
    controls.setSpacing(10)
    current_label = QLabel("当前模式：")
    controls.addWidget(current_label)
    combo = NoWheelComboBox()
    for key, label in MODE_LABELS.items():
        combo.addItem(label, key)
    combo.setCurrentIndex(combo.findData(service.settings.mode.value))
    combo.setFixedWidth(150)
    controls.addWidget(combo)
    check = QPushButton("检测详情")
    check.setFixedWidth(96)
    check.clicked.connect(lambda: controller.check(show=True))
    controls.addWidget(check)
    controls.addStretch()
    card.layout().addLayout(controls)

    mode_labels = {}
    descriptions = QVBoxLayout()
    descriptions.setSpacing(8)
    for key, title in MODE_LABELS.items():
        row = QHBoxLayout()
        row.setSpacing(8)
        mode_label = QLabel(f"{title}：")
        mode_label.setObjectName(f"workModeDescription_{key}")
        mode_label.setFixedWidth(58)
        detail = QLabel(MODE_DESCRIPTIONS[key])
        detail.setWordWrap(False)
        detail.setStyleSheet(f"color:{theme_color('#c9d1d9')}")
        row.addWidget(mode_label)
        row.addWidget(detail)
        row.addStretch()
        descriptions.addLayout(row)
        mode_labels[key] = mode_label
    card.layout().addLayout(descriptions)

    def refresh_mode_emphasis(_index=None):
        selected = service.settings.mode.value
        for key, label in mode_labels.items():
            label.setProperty("confirmedMode", key == selected)
            color = theme_color("#58a6ff" if key == selected else "#f0f6fc")
            label.setStyleSheet(f"color:{color};font-weight:700")

    combo.currentIndexChanged.connect(refresh_mode_emphasis)
    refresh_mode_emphasis()

    # The detailed per-feature state now belongs to the explicit report dialog.
    # Retain a hidden projection target so the controller contract stays narrow.
    status = QLabel("正在核对当前模式…", card)
    status.hide()
    controller.attach_controls(combo, status, check)

    def select_mode(_index):
        controller.select_mode(combo.currentData())
        refresh_mode_emphasis()

    combo.activated.connect(select_mode)
    return card


def _confirmation_row(title: str, detail: str, tone: str, parent) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("workModeConfirmationRow")
    frame.setStyleSheet(
        f"QFrame#workModeConfirmationRow{{background:{theme_color('#161b22')};"
        f"border:1px solid {theme_color('#30363d')};border-radius:8px}}"
    )
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 11, 14, 11)
    row.setSpacing(12)
    heading = QLabel(title, frame)
    heading.setObjectName(f"workModeConfirmation_{tone}")
    heading.setFixedWidth(76)
    tone_color = {"available": "#3fb950", "unavailable": "#8b949e", "after": "#58a6ff"}[tone]
    heading.setStyleSheet(
        f"color:{theme_color(tone_color)};font-weight:700"
    )
    body = QLabel(detail, frame)
    body.setWordWrap(True)
    row.addWidget(heading)
    row.addWidget(body, 1)
    return frame


def confirm_mode(parent, mode: str) -> bool:
    copy = MODE_CONFIRMATIONS[mode]
    dialog = QDialog(parent)
    dialog.setObjectName("workModeConfirmationDialog")
    dialog.setWindowTitle("确认切换到" + MODE_LABELS[mode] + "模式")
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)

    warning = QFrame(dialog)
    warning.setObjectName("workModeWarning")
    warning.setStyleSheet(
        f"QFrame#workModeWarning{{background:{theme_color('#2d1117')};"
        f"border:1px solid {theme_color('#f85149')};border-radius:9px}}"
    )
    warning_row = QHBoxLayout(warning)
    warning_row.setContentsMargins(16, 14, 16, 14)
    warning_row.setSpacing(14)
    icon = QLabel("⚠", warning)
    icon.setObjectName("workModeWarningIcon")
    icon.setStyleSheet(f"color:{theme_color('#f85149')};font-size:30px;font-weight:700")
    warning_row.addWidget(icon)
    warning_text = QVBoxLayout()
    warning_text.setSpacing(3)
    warning_title = QLabel(copy["warning_title"], warning)
    warning_title.setObjectName("workModeWarningTitle")
    warning_title.setStyleSheet(f"color:{theme_color('#f85149')};font-size:16px;font-weight:700")
    warning_detail = QLabel(copy["warning_detail"], warning)
    warning_detail.setObjectName("workModeWarningDetail")
    warning_detail.setWordWrap(True)
    warning_text.addWidget(warning_title)
    warning_text.addWidget(warning_detail)
    warning_row.addLayout(warning_text, 1)
    layout.addWidget(warning)

    layout.addWidget(_confirmation_row("可以使用", copy["available"], "available", dialog))
    layout.addWidget(_confirmation_row("不可使用", copy["unavailable"], "unavailable", dialog))
    layout.addWidget(_confirmation_row("切换后", copy["after"], "after", dialog))

    note = QLabel(copy["note"], dialog)
    note.setObjectName("workModeConfirmationNote")
    note.setWordWrap(True)
    note.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
    layout.addWidget(note)

    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    cancel = buttons.button(QDialogButtonBox.Cancel)
    cancel.setText("取消")
    cancel.setFixedSize(88, 38)
    consent = buttons.addButton(
        "确认切换" if mode == "offline" else "确认风险并切换",
        QDialogButtonBox.AcceptRole,
    )
    consent.setObjectName("workModeConfirm" if mode == "offline" else "workModeRiskConsent")
    consent.setMinimumWidth(148)
    consent.setFixedHeight(38)
    consent.setAutoDefault(False)
    consent.setDefault(False)
    if mode != "offline":
        consent.setStyleSheet(
            f"QPushButton{{background:{theme_color('#da3633')};color:#ffffff;"
            f"border:1px solid {theme_color('#f85149')};border-radius:6px;padding:7px 16px;font-weight:700}}"
            f"QPushButton:hover{{background:{theme_color('#f85149')}}}"
            f"QPushButton:pressed{{background:{theme_color('#b42318')}}}"
            f"QPushButton:focus{{border:2px solid {theme_color('#fda29b')};padding:6px 15px}}"
        )
    button_layout = buttons.layout()
    button_layout.removeWidget(cancel)
    button_layout.removeWidget(consent)
    button_layout.addStretch()
    button_layout.addWidget(cancel)
    button_layout.addWidget(consent)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(720, 440))
    return dialog.exec() == QDialog.Accepted
