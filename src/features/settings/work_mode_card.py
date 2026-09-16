# 构建统一工作模式入口、逐功能检查结果与必要处理操作。
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QScrollArea, QVBoxLayout,
)
from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.widgets import NoWheelComboBox

MODE_LABELS = {"offline": "离线", "low": "低风险", "medium": "中风险", "developer": "开发"}
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
    form = QFormLayout()
    combo = NoWheelComboBox()
    for key, label in MODE_LABELS.items():
        combo.addItem(label, key)
    combo.setCurrentIndex(combo.findData(service.settings.mode.value))
    form.addRow("当前模式", combo)
    hint = QLabel("工作模式决定数据来源和可用操作；自动同步在首页控制。")
    hint.setWordWrap(True)
    form.addRow(hint)
    status = QLabel("正在核对当前模式…")
    status.setWordWrap(True)
    form.addRow(status)
    buttons = QHBoxLayout()
    check = QPushButton("检测详情")
    check.clicked.connect(lambda: controller.check(show=True))
    buttons.addWidget(check)
    form.addRow(buttons)
    card.layout().addLayout(form)
    controller.attach_controls(combo, status, check)
    combo.activated.connect(lambda _index: controller.select_mode(combo.currentData()))
    return card


def confirm_mode(parent, mode: str) -> bool:
    description = {
        "offline": (
            "仅使用本地数据、已保存战报与离线计算，不连接游戏或采集新数据。\n\n"
            "切换后会停止采集与同步，关闭两个插件开关，并清理已部署的游戏组件。"
            "游戏仍在运行时，组件清理会等待游戏退出。"
        ),
        "low": (
            "使用抓包同步背包和当前装备，并通过模拟输入执行游戏界面操作；"
            "不读取游戏内存或调用游戏内部方法。\n\n"
            "抓包和模拟输入可能触发游戏保护或兼容问题。角色养成需手动维护，"
            "原生同步、战报和极速装配等功能不可用。"
        ),
        "medium": (
            "加载游戏内组件，读取同步所需数据，并支持装配、锁定和弃置等操作。\n\n"
            "这些操作会更新游戏中的对应状态；组件加载可能触发游戏保护或兼容问题。"
        ),
        "developer": "同时启用原生同步与抓包，用于双路对照和问题排查。",
    }[mode]
    if mode != "offline":
        description += "\n\n自动同步开启时会连接游戏；可在首页随时关闭。"
    if mode in {"medium", "developer"}:
        description += "游戏关闭后会自动部署或更新所需组件。"
    dialog = QDialog(parent)
    dialog.setWindowTitle("确认工作模式 · " + MODE_LABELS[mode])
    layout = QVBoxLayout(dialog)
    label = QLabel(description, dialog)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    layout.addWidget(label)
    layout.addStretch()
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    cancel = buttons.button(QDialogButtonBox.Cancel)
    cancel.setText("取消")
    consent = buttons.addButton(
        "切换到离线模式" if mode == "offline" else "自愿承担风险并使用此模式\n同时接受后续更新依旧使用此模式",
        QDialogButtonBox.AcceptRole,
    )
    consent.setObjectName("workModeConfirm" if mode == "offline" else "workModeRiskConsent")
    consent.setAutoDefault(False)
    consent.setDefault(False)
    if mode != "offline":
        consent.setStyleSheet(
            "QPushButton { background-color:#b42318; color:#ffffff; border:1px solid #b42318;"
            "border-radius:6px; padding:8px 16px; font-weight:600; }"
            "QPushButton:hover { background-color:#912018; border-color:#912018; }"
            "QPushButton:pressed { background-color:#7a1b14; border-color:#7a1b14; }"
            "QPushButton:focus { border:2px solid #fda29b; padding:7px 15px; }"
        )
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(660, 390 if mode == "medium" else 310))
    return dialog.exec() == QDialog.Accepted
