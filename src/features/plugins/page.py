# 展示两个插件卡片，详细选项在独立弹窗中编辑。
from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen


class PluginsPage(QWidget):
    def __init__(self, *, service, request_apply, open_settings, parent=None):
        super().__init__(parent)
        self.service, self.request_apply = service, request_apply
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        title = QLabel("插件")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("游戏内显示独立开关，不影响战报采集。设置在所有账号间共用。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.cards = {}
        for key, name, description in (
            ("cooldown", "冷却统计", "显示队伍技能剩余冷却、冷却遮罩与 E/Q 就绪光效。"),
            ("enemy_bars", "血量与倾陷", "显示敌人的当前／最大血量和倾陷数值。"),
        ):
            frame = QFrame()
            frame.setObjectName("card")
            card = QVBoxLayout(frame)
            card.setContentsMargins(20, 16, 20, 16)
            row = QHBoxLayout()
            heading = QLabel(name)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            row.addWidget(heading)
            row.addStretch()
            toggle = QCheckBox("启用")
            toggle.setObjectName(f"plugin_{key}")
            toggle.toggled.connect(lambda enabled, key=key: self._update(**{key: enabled}))
            row.addWidget(toggle)
            settings = QPushButton("设置")
            settings.clicked.connect(lambda _=False, key=key: self._settings(key))
            row.addWidget(settings)
            card.addLayout(row)
            label = QLabel(description)
            label.setWordWrap(True)
            card.addWidget(label)
            status = QLabel()
            status.setWordWrap(True)
            card.addWidget(status)
            self.cards[key] = toggle, status
            layout.addWidget(frame)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        retry = QPushButton("重新检测")
        retry.clicked.connect(request_apply)
        environment = QPushButton("组件与工作模式设置")
        environment.clicked.connect(open_settings)
        actions.addWidget(retry)
        actions.addWidget(environment)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.refresh()

    def refresh(self, _result=None):
        allowed = self.service.policy.allowed("native_load")
        settings = self.service.settings
        for key, (toggle, status) in self.cards.items():
            checked = getattr(settings, key)
            toggle.blockSignals(True)
            toggle.setChecked(checked)
            toggle.setEnabled(allowed or checked)
            toggle.blockSignals(False)
            detail = self.service.status if checked else "未启用"
            if key == "enemy_bars" and checked and not (settings.hp or settings.unbalance):
                detail = "未选择显示内容，请在设置中勾选血量或倾陷"
            status.setText(detail)
        self.notice.setText(self.service.load_error or (
            "退出 Calc 后停止显示；下次启动时按已保存的开关恢复。" if allowed else
            "插件需要中风险或开发模式。可以编辑显示选项，切换模式后再启用。"))

    def _update(self, **changes):
        try:
            self.service.update(**changes)
        except (PermissionError, OSError, ValueError) as error:
            QMessageBox.warning(self, "插件设置", str(error))
        else:
            self.request_apply()
        self.refresh()

    def _settings(self, key):
        dialog = QDialog(self)
        dialog.setWindowTitle("冷却统计设置" if key == "cooldown" else "血量与倾陷设置")
        layout = QVBoxLayout(dialog)
        fields = (("ready_cue", "显示 E/Q 就绪光效（Q 需能量充足）"),) if key == "cooldown" else (
            ("hp", "显示血量数值"), ("unbalance", "显示倾陷数值"))
        boxes = {}
        for field, label in fields:
            box = QCheckBox(label)
            box.setChecked(getattr(self.service.settings, field))
            boxes[field] = box
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        fit_dialog_to_available_screen(dialog, QSize(400, 180))
        if dialog.exec() == QDialog.Accepted:
            self._update(**{field: box.isChecked() for field, box in boxes.items()})
