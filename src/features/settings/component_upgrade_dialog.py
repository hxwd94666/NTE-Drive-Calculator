# 展示简洁的旧版组件升级步骤，技术证据按需展开。
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen


_STEP_LABELS = ("核对旧组件", "清理旧部署", "确认工作模式", "部署当前组件（按需）")
_STAGES = {
    "cleanup": (1, "发现旧版组件", "旧部署与当前版本不一致。", "完全退出游戏；Loader 用户还需退出启动器。随后清理旧组件。", "清理旧组件"),
    "path": (0, "需要确认游戏位置", "旧部署的游戏路径尚未确认。", "前往环境设置，选择当前游戏的 HTGame.exe，再重新核对。", "前往环境设置"),
    "mode": (2, "旧组件已清理", "清理会暂停同步，原有账号数据仍保留。", "前往工作模式设置，确认需要的模式。", "确认工作模式"),
    "deploy": (3, "准备部署当前组件", "工作模式已确认，组件尚待部署。", "前往环境设置，按选定的 D3D 或 Loader 方式部署；部署前退出游戏，Loader 还需退出启动器。部署后可在检测详情查看同步状态。", "前往部署"),
    "done": (2, "清理已完成", "当前模式无需部署原生组件。", "如需同步，请按工作台提示开启同步。", "完成引导"),
}


class ComponentUpgradeDialog(QDialog):
    def __init__(self, parent, *, stage: str, detail: str, method: str,
                 on_action):
        super().__init__(parent)
        self.setObjectName("componentUpgradeDialog")
        self.setWindowTitle("旧版插件升级引导")
        self.setWindowModality(Qt.WindowModal)
        self._on_action = on_action
        self._stage = stage
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        self.steps = QLabel(self)
        self.steps.setObjectName("componentUpgradeSteps")
        self.steps.setWordWrap(True)
        layout.addWidget(self.steps)
        self.title = QLabel(self)
        self.title.setObjectName("componentUpgradeTitle")
        self.title.setStyleSheet(f"color:{theme_color('#f0f6fc')};font-size:17px;font-weight:700")
        layout.addWidget(self.title)
        self.reason = QLabel(self)
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.next_step = QLabel(self)
        self.next_step.setWordWrap(True)
        layout.addWidget(self.next_step)
        self.technical_toggle = QPushButton("查看核对依据 ▾", self)
        self.technical_toggle.setObjectName("componentUpgradeTechnicalToggle")
        self.technical_toggle.setFlat(True)
        self.technical_toggle.setFocusPolicy(Qt.StrongFocus)
        layout.addWidget(self.technical_toggle)
        self.technical = QLabel(detail or "本次没有额外诊断信息。", self)
        self.technical.setObjectName("componentUpgradeTechnical")
        self.technical.setTextFormat(Qt.PlainText)
        self.technical.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.technical.setWordWrap(True)
        self.technical.hide()
        layout.addWidget(self.technical)
        self.technical_toggle.clicked.connect(self._toggle_technical)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel = QPushButton("稍后处理", self)
        self.cancel.setDefault(True)
        self.cancel.clicked.connect(self.reject)
        actions.addWidget(self.cancel)
        self.action = QPushButton(self)
        self.action.setObjectName("btnNew")
        self.action.clicked.connect(self._act)
        actions.addWidget(self.action)
        layout.addLayout(actions)
        self.show_stage(stage, method=method)
        fit_dialog_to_available_screen(self, QSize(600, 310))

    def _toggle_technical(self):
        visible = not self.technical.isVisible()
        self.technical.setVisible(visible)
        self.technical_toggle.setText("收起核对依据 ▴" if visible else "查看核对依据 ▾")
        fit_dialog_to_available_screen(self, QSize(600, 400 if visible else 310))

    def show_stage(self, stage: str, *, method: str) -> None:
        self._stage = stage
        index, title, reason, next_step, action = _STAGES[stage]
        self.steps.setText("  ›  ".join(
            (f"● {label}" if position == index else f"○ {label}")
            for position, label in enumerate(_STEP_LABELS)
        ))
        self.title.setText(title)
        self.reason.setText("原因：" + reason)
        if stage == "deploy":
            next_step = next_step.replace("D3D 或 Loader", "Loader" if method == "loader" else "D3D")
        self.next_step.setText("下一步：" + next_step)
        self.action.setText(action)

    def _act(self) -> None:
        stage = self._stage
        self.accept()
        self._on_action(stage)
