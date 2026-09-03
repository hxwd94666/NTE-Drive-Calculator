# 展示战报原始角色配置与单一修改副本的切换状态。
"""Battle build edit control card."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

class BattleBuildSnapshotControl(QWidget):
    edit_requested = Signal()
    activation_requested = Signal(bool)
    role_page_import_requested = Signal()
    environment_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("等待战报角色配置")
        self.status.hide()
        self.edit_button = QPushButton("编辑角色")
        self.edit_button.clicked.connect(self.edit_requested)
        self.edit_button.setEnabled(False)
        layout.addWidget(self.edit_button)
        self.import_button = QPushButton("从角色页同步")
        self.import_button.setToolTip(
            "用当前角色页养成覆盖本场修改副本；保留副本已选边际空幕/驱动。"
        )
        self.import_button.clicked.connect(self.role_page_import_requested)
        self.import_button.setEnabled(False)
        self.import_button.hide()
        self.environment_button = QPushButton("环境配置 · 未配置")
        self.environment_button.setToolTip(
            "确认战斗模式、对象、难度、争锋加成与魔女赐福。"
        )
        self.environment_button.clicked.connect(self.environment_requested)
        self.environment_button.setEnabled(False)
        self.environment_button.hide()
        self.activation_button = QPushButton("恢复原始快照")
        self.activation_button.clicked.connect(self._request_activation)
        self.activation_button.setEnabled(False)
        self.activation_button.hide()

    def set_state(
        self,
        *,
        has_edit: bool,
        active: bool,
        available: bool = True,
    ) -> None:
        self.edit_button.setEnabled(available)
        self.import_button.setEnabled(available)
        self.activation_button.setEnabled(available and has_edit)
        if not available:
            text = "当前记录没有可编辑的角色配置快照。"
        elif not has_edit:
            text = "当前使用原始快照；首次编辑会复制本场原始冻结配置。"
        elif active:
            text = "当前使用修改副本；它只用于逐击重放和边际计算，主页面实测数据不变。"
        else:
            text = "当前已恢复原始快照；修改副本仍保留，可继续编辑或重新启用。"
        self.status.setText(text)
        self.edit_button.setToolTip(text)
        self.activation_button.setText(
            "恢复原始快照" if active else "使用修改副本"
        )

    def set_environment_state(self, *, status: str, summary: str = "") -> None:
        labels = {
            "configured": "已配置",
            "inferred": "已推理",
            "unconfigured": "未配置",
        }
        label = labels.get(status, "未配置")
        self.environment_button.setText(f"环境配置 · {label}")
        self.environment_button.setToolTip(
            summary or "确认战斗模式、对象、难度、争锋加成与魔女赐福。"
        )

    def _request_activation(self) -> None:
        self.activation_requested.emit(
            self.activation_button.text() == "使用修改副本"
        )
